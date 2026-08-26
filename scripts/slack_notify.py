#!/usr/bin/env python3
"""slack_notify.py — Slack Web API sender with thread tracking (P1b-1).

specs/DEVDEPARTMENT_SLACK_SPEC.md §3 (message designs), §5 (sender behaviours),
§2 (channel routing), §9 (Telegram preserved as fallback), §10 (--test).

Not a rename of notify.py's telegram sender, and not an incoming webhook — a
proper new module using the Slack **Web API** (`chat.postMessage` with
`blocks=`, `chat.update`, `reactions.add`), because the Web API is strictly
more capable (message updates, thread replies, reactions) and the same token
is already needed for slash commands / Home tab later. stdlib `urllib` only.

Key behaviours (§5), each earned by a real requirement:
    - Thread tracking: `.devteam/slack_threads.json` keyed by task_id stores
      the anchor message's {channel, ts}. Follow-up events for the same task
      post as `thread_ts=<anchor ts>` replies, so a task's full history reads
      as one thread.
    - Message updating: a decided alert (blocked task answered, review
      decided) is updated IN PLACE via `chat.update`, never re-posted.
    - Task completion: a ✅ `reactions.add` on the thread anchor.
    - Rate limits: exponential backoff on 429 (honouring `Retry-After` when
      Slack sends one); Slack unreachable/erroring → fail OPEN, never raise —
      callers (notify.py, and later supervisor.py) can always fall back to
      the `file` channel.

Credentials: `DEVTEAM_SLACK_TOKEN` env var only — never a tracked file (§8).
Channel IDs and the enabled flag live in `autopilot.json`'s "slack" block
(§2) — this module only reads that block, never autopilot.json's structure
beyond it.

CLI:
    python scripts/slack_notify.py --test [--repo PATH]
        Posts one smoke message to each configured channel and reports
        delivery (§10 step 4 of the human enable checklist).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SLACK_API_BASE = "https://slack.com/api"
TASK_ID_RE = re.compile(r"\bTASK-\d+\b")
PRIORITY_BADGE = {"P0": "🟢 DIGEST", "P1": "🔴 STOP-THE-LINE", "P2": "🟠 DECISION NEEDED"}
DEFAULT_MAX_RETRIES = 3
DEFAULT_TIMEOUT = 15
MAX_BACKOFF_SECONDS = 30.0


# --------------------------------------------------------------------------- config

def load_config(repo: Path) -> dict:
    """Read the "slack" block from autopilot.json.

    Returns {} (== disabled/unconfigured) on any error — a missing or
    malformed config must never crash a caller, same fail-open posture as
    every other optional integration point in this pack.
    """
    path = Path(repo) / "autopilot.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    cfg = data.get("slack")
    return cfg if isinstance(cfg, dict) else {}


def is_enabled(cfg: dict) -> bool:
    return bool(cfg.get("enabled"))


def get_token() -> str:
    """DEVTEAM_SLACK_TOKEN only — never read from a tracked file (§8)."""
    return os.environ.get("DEVTEAM_SLACK_TOKEN", "")


# ------------------------------------------------------------------- channel routing

def route_channels(kind: str, cfg: dict) -> list[str]:
    """§2 routing rule, exact:
        P0 digests + P1 stop-the-line -> ops_channel
        P2 + usage/status             -> project_channel
        wave-complete                 -> both (deduplicated)
    `kind` is one of "P0", "P1", "P2", "status", "usage", "wave_complete".
    Unconfigured channels are silently dropped (empty string in config).
    """
    ops = cfg.get("ops_channel") or ""
    proj = cfg.get("project_channel") or ""
    if kind == "wave_complete":
        seen: list[str] = []
        for c in (ops, proj):
            if c and c not in seen:
                seen.append(c)
        return seen
    if kind in ("P0", "P1"):
        return [ops] if ops else []
    # P2, status, usage
    return [proj] if proj else []


# ------------------------------------------------------------------------- transport

def _sleep_backoff(attempt: int, retry_after: str | None) -> None:
    try:
        delay = float(retry_after) if retry_after is not None else float(2 ** attempt)
    except (TypeError, ValueError):
        delay = float(2 ** attempt)
    time.sleep(min(delay, MAX_BACKOFF_SECONDS))


def api_call(
    token: str,
    method: str,
    payload: dict,
    timeout: int = DEFAULT_TIMEOUT,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> dict | None:
    """POST JSON to https://slack.com/api/<method> with Bearer auth.

    Exponential backoff on 429 (§5), honouring Slack's `Retry-After` header
    when present. Any failure after retries — network error, non-200,
    `{"ok": false}` — returns None. NEVER raises: this is the module's single
    fail-open chokepoint, so every caller above it degrades cleanly.
    """
    if not token:
        return None
    url = f"{SLACK_API_BASE}/{method}"
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    attempt = 0
    while True:
        attempt += 1
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            result = json.loads(raw)
            if result.get("ok"):
                return result
            if result.get("error") == "ratelimited" and attempt <= max_retries:
                _sleep_backoff(attempt, None)
                continue
            print(f"[slack_notify] Slack API error on {method}: {result.get('error')}", file=sys.stderr)
            return None
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and attempt <= max_retries:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                _sleep_backoff(attempt, retry_after)
                continue
            print(f"[slack_notify] Slack API HTTP error on {method}: {exc}", file=sys.stderr)
            return None
        except Exception as exc:  # noqa: BLE001 — network failure must never crash the caller
            print(f"[slack_notify] Slack API call failed on {method}: {exc}", file=sys.stderr)
            return None


def post_message(token: str, channel: str, text: str, blocks: list | None = None, thread_ts: str | None = None) -> dict | None:
    if not channel:
        return None
    payload: dict = {"channel": channel, "text": text}
    if blocks:
        payload["blocks"] = blocks
    if thread_ts:
        payload["thread_ts"] = thread_ts
    return api_call(token, "chat.postMessage", payload)


def update_message(token: str, channel: str, ts: str, text: str, blocks: list | None = None) -> dict | None:
    if not channel or not ts:
        return None
    payload: dict = {"channel": channel, "ts": ts, "text": text}
    if blocks:
        payload["blocks"] = blocks
    return api_call(token, "chat.update", payload)


def add_reaction(token: str, channel: str, ts: str, emoji: str = "white_check_mark") -> bool:
    if not channel or not ts:
        return False
    result = api_call(token, "reactions.add", {"channel": channel, "timestamp": ts, "name": emoji})
    return result is not None


# --------------------------------------------------------------------- thread tracking

def _threads_path(repo: Path) -> Path:
    return Path(repo) / ".devteam" / "slack_threads.json"


def load_threads(repo: Path) -> dict:
    path = _threads_path(repo)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_threads(repo: Path, data: dict) -> None:
    """Atomic write (tmp file + os.replace) — a crash mid-write must never
    leave slack_threads.json truncated or invalid JSON for the next reader."""
    path = _threads_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def get_thread(repo: Path, task_id: str) -> dict | None:
    return load_threads(repo).get(task_id)


def record_thread(repo: Path, task_id: str, channel: str, ts: str) -> None:
    data = load_threads(repo)
    data[task_id] = {"channel": channel, "ts": ts}
    save_threads(repo, data)


def post_or_thread(token: str, repo: Path, channel: str, task_id: str | None, text: str, blocks: list | None = None) -> dict | None:
    """Post a new message, or — if `task_id` already has a tracked thread —
    post as a reply in that thread instead (posting to the ANCHOR's original
    channel, never a second thread in a different channel). Records a fresh
    anchor the first time a given task_id is seen."""
    existing = get_thread(repo, task_id) if task_id else None
    target_channel = existing["channel"] if existing else channel
    thread_ts = existing["ts"] if existing else None
    result = post_message(token, target_channel, text, blocks=blocks, thread_ts=thread_ts)
    if result is None:
        return None
    if not existing and task_id and result.get("ts"):
        record_thread(repo, task_id, target_channel, result["ts"])
    return result


def update_decided(token: str, repo: Path, task_id: str, text: str, blocks: list | None = None) -> bool:
    """Update a task's thread-anchor message in place (§5: "the original
    alert message is updated, not a new message"). False (no-op) if the task
    has no tracked anchor — nothing to update, never invents one."""
    existing = get_thread(repo, task_id) if task_id else None
    if not existing:
        return False
    result = update_message(token, existing["channel"], existing["ts"], text, blocks=blocks)
    return result is not None


def mark_task_done(token: str, repo: Path, task_id: str) -> bool:
    """✅ reaction on the task's thread anchor when it reaches done (§5)."""
    existing = get_thread(repo, task_id) if task_id else None
    if not existing:
        return False
    return add_reaction(token, existing["channel"], existing["ts"])


# --------------------------------------------------------------------- Block Kit designs
# specs/DEVDEPARTMENT_SLACK_SPEC.md §3 — four designs, each mapping to a real event.

def build_blocked_blocks(
    project: str, task_id: str, title: str, assignee: str,
    blocked_minutes: int, blocked_reason: str, dossier_tail: str = "",
) -> tuple[str, list]:
    """P2-BLOCKED — with Approve / Send to rework / Answer builder buttons."""
    text = f"🟠 DECISION NEEDED — {project}: {task_id} blocked"
    blocks: list = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🟠 DECISION NEEDED — {project}", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"*{task_id}* — {title}\n👷 {assignee}  •  blocked {blocked_minutes} min"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f">{blocked_reason}"}},
    ]
    if dossier_tail:
        blocks.append({"type": "section", "text": {"type": "mrkdwn",
            "text": f"Last dossier entry:\n>{dossier_tail}"}})
    blocks.append({
        "type": "actions",
        "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "✅ Approve as-is"}, "action_id": "slack_approve", "value": task_id},
            {"type": "button", "text": {"type": "plain_text", "text": "❌ Send to rework"}, "action_id": "slack_rework", "value": task_id},
            {"type": "button", "text": {"type": "plain_text", "text": "💬 Answer builder"}, "action_id": "slack_answer", "value": task_id},
        ],
    })
    return text, blocks


def build_needs_review_blocks(
    project: str, task_id: str, title: str, assignee: str,
    submitted_minutes: int, tests_passed: int, rework_count: int,
) -> tuple[str, list]:
    """P2-NEEDS-REVIEW — test counts + rework count, Open in Tower / Approve / Rework."""
    text = f"👁 REVIEW REQUESTED — {project}: {task_id}"
    pass_note = " (first pass)" if rework_count == 0 else ""
    blocks: list = [
        {"type": "header", "text": {"type": "plain_text", "text": f"👁 REVIEW REQUESTED — {project}", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": (
                f"*{task_id}* — {title}\n👷 {assignee}  •  submitted {submitted_minutes} min ago\n"
                f"Tests: {tests_passed} passed · Rework count: {rework_count}{pass_note}"
            )}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "🔍 Open in Tower"}, "action_id": "slack_open_tower", "value": task_id},
            {"type": "button", "text": {"type": "plain_text", "text": "✅ Approve"}, "action_id": "slack_approve", "value": task_id},
            {"type": "button", "text": {"type": "plain_text", "text": "❌ Rework"}, "action_id": "slack_rework", "value": task_id},
        ]},
    ]
    return text, blocks


def build_stop_the_line_blocks(project: str, violations: list[str]) -> tuple[str, list]:
    """P1-STOP-THE-LINE — violations list, ops channel, never muted."""
    text = f"🔴 STOP-THE-LINE — {project}: {len(violations)} violation(s)"
    lines = "\n".join(f"• {v}" for v in violations) if violations else "• (none listed)"
    blocks: list = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🔴 STOP-THE-LINE — {project}", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn",
            "text": f"validate_plan.py: PLAN.md has {len(violations)} violation(s)\n{lines}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "Loop halted. Fix PLAN.md before resuming."}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "▶ Resume after fix"}, "action_id": "slack_resume", "value": project},
        ]},
    ]
    return text, blocks


def build_wave_complete_blocks(
    project: str, tasks_done: int, tasks_total: int, wave_duration: str,
    prev_wave_duration: str = "", builder_stats: list[str] | None = None,
    instincts_drafted: int = 0, usage_summary: str = "",
) -> tuple[str, list]:
    """P0-WAVE-COMPLETE — wave stats, builder first-pass table, both channels."""
    text = f"🟢 WAVE COMPLETE — {project}: {tasks_done}/{tasks_total} done"
    duration_line = f"{tasks_done}/{tasks_total} tasks done  •  Wave duration: {wave_duration}"
    if prev_wave_duration:
        duration_line += f"\n(prev wave: {prev_wave_duration})"
    stats_lines = "\n".join(f"• {b}" for b in builder_stats) if builder_stats else "• (no builder stats)"
    footer_bits = [f"Instincts drafted: {instincts_drafted} new candidates pending /approve"]
    if usage_summary:
        footer_bits.append(usage_summary)
    blocks: list = [
        {"type": "header", "text": {"type": "plain_text", "text": f"🟢 WAVE COMPLETE — {project}", "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": duration_line}},
        {"type": "section", "text": {"type": "mrkdwn", "text": f"Builder performance this wave:\n{stats_lines}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text": "\n".join(footer_bits)}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "📋 View PLAN.md"}, "action_id": "slack_view_plan", "value": project},
            {"type": "button", "text": {"type": "plain_text", "text": "🗂 Open in Tower"}, "action_id": "slack_open_tower_wave", "value": project},
            {"type": "button", "text": {"type": "plain_text", "text": "📊 Full stats"}, "action_id": "slack_full_stats", "value": project},
        ]},
    ]
    return text, blocks


# ------------------------------------------------------------------- high-level senders
# Thin wrappers combining a Block Kit design + §2 routing + thread tracking.
# Intended for future direct callers (e.g. supervisor.py) that have full
# structured context. notify.py's generic priority+message path uses
# send_simple() below instead.

def notify_blocked(token: str, repo: Path, cfg: dict, project: str, task_id: str, title: str,
                    assignee: str, blocked_minutes: int, blocked_reason: str, dossier_tail: str = "") -> bool:
    text, blocks = build_blocked_blocks(project, task_id, title, assignee, blocked_minutes, blocked_reason, dossier_tail)
    ok = False
    for ch in route_channels("P2", cfg):
        ok = post_or_thread(token, repo, ch, task_id, text, blocks) is not None or ok
    return ok


def notify_needs_review(token: str, repo: Path, cfg: dict, project: str, task_id: str, title: str,
                         assignee: str, submitted_minutes: int, tests_passed: int, rework_count: int) -> bool:
    text, blocks = build_needs_review_blocks(project, task_id, title, assignee, submitted_minutes, tests_passed, rework_count)
    ok = False
    for ch in route_channels("P2", cfg):
        ok = post_or_thread(token, repo, ch, task_id, text, blocks) is not None or ok
    return ok


def notify_stop_the_line(token: str, cfg: dict, project: str, violations: list[str]) -> bool:
    text, blocks = build_stop_the_line_blocks(project, violations)
    ok = False
    for ch in route_channels("P1", cfg):
        ok = post_message(token, ch, text, blocks=blocks) is not None or ok
    return ok


def notify_wave_complete(token: str, cfg: dict, project: str, **stats) -> bool:
    text, blocks = build_wave_complete_blocks(project, **stats)
    ok = False
    for ch in route_channels("wave_complete", cfg):
        ok = post_message(token, ch, text, blocks=blocks) is not None or ok
    return ok


def notify_decided(token: str, repo: Path, task_id: str, decided_text: str) -> bool:
    """A blocked/needs-review alert's outcome — update the anchor in place."""
    return update_decided(token, repo, task_id, decided_text)


def notify_task_done(token: str, repo: Path, task_id: str) -> bool:
    return mark_task_done(token, repo, task_id)


# ------------------------------------------------------------- notify.py generic sender

def send_simple(repo: Path, cfg: dict, token: str, priority: str, message: str) -> bool:
    """The Slack equivalent of notify.py's send_telegram: takes the generic
    priority+message shape every other channel uses (no structured Block Kit
    context available here), applies §2 routing, and threads P2 messages
    that reference a TASK-NNN id so they land in that task's existing
    thread rather than starting a new one."""
    badge = PRIORITY_BADGE.get(priority, priority)
    text = f"{badge}\n{message}"
    channels = route_channels(priority, cfg)
    if not channels:
        return False
    task_id = None
    if priority == "P2":
        m = TASK_ID_RE.search(message)
        task_id = m.group(0) if m else None
    ok = False
    for ch in channels:
        if task_id:
            result = post_or_thread(token, repo, ch, task_id, text)
        else:
            result = post_message(token, ch, text)
        ok = ok or result is not None
    return ok


# --------------------------------------------------------------------------------- CLI

def test_channels(repo: Path, cfg: dict, token: str) -> int:
    """§10 step 4: post one smoke message per configured channel, report
    delivery. Exit 0 iff every configured channel received it, 1 otherwise
    (missing config/token counts as failure), never 2."""
    channels = {}
    if cfg.get("ops_channel"):
        channels["ops_channel"] = cfg["ops_channel"]
    if cfg.get("project_channel"):
        channels["project_channel"] = cfg["project_channel"]
    if not channels:
        print("[slack_notify] --test: no channels configured (autopilot.json slack.ops_channel / slack.project_channel)", file=sys.stderr)
        return 1
    if not token:
        print("[slack_notify] --test: DEVTEAM_SLACK_TOKEN is not set", file=sys.stderr)
        return 1
    all_ok = True
    for label, channel_id in channels.items():
        result = post_message(token, channel_id, f"✅ DEVDEPARTMENT slack_notify.py --test smoke message ({label})")
        if result is not None:
            print(f"[slack_notify] {label} ({channel_id}): delivered, ts={result.get('ts')}")
        else:
            print(f"[slack_notify] {label} ({channel_id}): FAILED", file=sys.stderr)
            all_ok = False
    return 0 if all_ok else 1


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="slack_notify.py")
    ap.add_argument("--test", action="store_true", help="post one smoke message to each configured channel")
    ap.add_argument("--repo", default=".", help="repository root (for autopilot.json + thread store)")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    cfg = load_config(repo)
    token = get_token()

    if args.test:
        return test_channels(repo, cfg, token)

    print("slack_notify.py: specify --test", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
