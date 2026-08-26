#!/usr/bin/env python3
"""tg_commands.py — Command grammar, PLAN.md micro-transaction editing, and
reply rendering for DEVDEPARTMENT's two-way Telegram integration (Wave
A-remainder, completes Pillar 2 of the v3 design).

Design principles (mirrors the rest of this codebase):
  - Pure functions wherever possible: parsing and PLAN.md text mutation take
    strings in, return strings/results out — no I/O, no globals, fully
    unit-testable without a repo, git, or a network connection.
  - validate_plan.py remains THE PLAN.md parser/schema authority. This module
    does not reimplement it — it reuses the exact same field/header regex
    shapes so a PLAN.md written here is always re-parseable by validate_plan.
  - Free text from Telegram (the argument of /answer and /rework) is treated
    strictly as inert data. It is written into PLAN.md as a single-line bullet
    and is NEVER eval'd, shelled out, or interpreted as a path or command. See
    _sanitize_free_text() for exactly how it is neutralised before writing.
  - Updated_By is set to "ORCH" on every Telegram-driven PLAN.md edit, never
    "TG". validate_plan.py's VALID_UNITS vocabulary is {ORCH, GB, CX} and this
    module deliberately does not touch that invariant (Wave A-remainder does
    not redesign existing APIs). Telegram is a remote *channel* for
    human/ORCH-level decisions, not a fourth autonomous unit that writes code.
    Full provenance is still preserved end-to-end via three independent,
    higher-resolution trails that DO carry a distinct "TG" marker: the git
    commit message tag "[TG]", the AUTOPILOT_LOG.md line
    ("TG_COMMAND unit=TG cmd=... task=..."), and the "[TG-DECISION]" /
    "[TG-REWORK]" prefix on the Progress_Notes / Review_Findings bullet
    itself. Nothing about who-really-typed-this is lost; it just isn't
    encoded in the one field the protocol validator constrains.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from commands import (  # noqa: E402 — shared validator (TASK-013); git/render stay here
    COMMANDS,
    VOCABULARY,
    canonical_name,
    parse_amend_and_text,
    parse_amend_args,
    parse_answer_args,
    parse_approve_args,
    parse_dispatch_args,
    parse_mute_args,
    parse_rework_args,
    parse_task_and_text,
    validate,
)

# ---------------------------------------------------------------- constants --
# TASK-\d+ (TASK-001) is the common shape; TASK-[A-Z0-9-]+ also accepts
# self-generated escalation IDs like TASK-MAINT-2026-07-19 (Wave B) so
# /answer, /approve, /rework can target those from Telegram too.
TASK_HEADER_RE = re.compile(r"^###\s+(TASK-[A-Z0-9-]+)\s*$")
FIELD_RE = re.compile(r"^\*\*([A-Za-z_]+):\*\*\s*(.*)$")
EMPTY_VALUES = {"", "\u2014", "-", "--", "n/a", "none"}  # \u2014 = em dash "—", matches validate_plan.py

MAX_FREE_TEXT_LEN = 2000

HELP_TEXT = (
    "DEVDEPARTMENT commands:\n"
    "/status \u2014 task board summary\n"
    "/board \u2014 board URL\n"
    "/answer TASK-NNN <text> \u2014 unblock / append a decision\n"
    "/approve TASK-NNN | AMEND-NNN \u2014 review a task, or approve a pending "
    "constitutional-amendment proposal (status flip only \u2014 ORCH applies the edit)\n"
    "/rework TASK-NNN <reason> | AMEND-NNN <reason> \u2014 send back to rework, "
    "or reject a pending amendment proposal\n"
    "/stop \u2014 halt the supervisor loop\n"
    "/resume \u2014 clear STOP and resume\n"
    "/wave \u2014 wake the loop early (skip remaining sleep)\n"
    "/digest \u2014 send a digest now\n"
    "/mute <duration e.g. 2h, 30m> \u2014 suppress P0/P2 alerts (P1 always gets through)\n"
    "/usage \u2014 claude/codex 5h + 7d usage-window percentages (cached, refreshed every "
    "usage.cache_ttl_minutes)\n"
)

STATUS_ICON = {
    "blocked": "\U0001F534", "needs_review": "\U0001F7E0", "in_progress": "\U0001F7E3",
    "claimed": "\U0001F535", "pending": "\u26AA", "done": "\u2705",
}
DIGEST_ICON = "\U0001F4CA"


@dataclass
class ApplyResult:
    text: str
    changed: bool
    detail: str


# ------------------------------------------------------ Wave C: AMEND-NNN ---
# The distiller (scripts/distiller.py) writes constitutional-amendment
# proposals to .devteam/pending_amendments/AMEND-NNN.md when it believes a
# root cause is a gap in AGENTS.md/CLAUDE.md/briefings rather than something
# a per-task instinct can fix. /approve and /rework also accept an AMEND-NNN
# target so those proposals can be triaged from Telegram — but /approve only
# flips the proposal's own Status field. It NEVER edits AGENTS.md, CLAUDE.md,
# or briefings/*.md itself: the actual constitutional edit is always applied
# by ORCH in a human-supervised session. This is the second lock on the gate
# (the distiller never writing those files directly is the first).
AMEND_DIR_REL = Path(".devteam") / "pending_amendments"


def amend_path(repo: Path, amend_id: str) -> Path:
    return repo / AMEND_DIR_REL / f"{amend_id}.md"


def apply_amend_approve(text: str) -> ApplyResult:
    """Flip Status: pending -> approved. Pure string operation — the caller
    is responsible for reading/writing the file; this never touches
    AGENTS.md/CLAUDE.md/briefings, only the proposal file's own text."""
    if "**Status:** pending" not in text:
        return ApplyResult(text, False, "amendment is not pending (already decided, or malformed)")
    new_text = text.replace("**Status:** pending", "**Status:** approved", 1)
    return ApplyResult(new_text, True, "approved — ORCH applies the edit in a supervised session")


def apply_amend_rework(text: str, reason: str, ts: str) -> ApplyResult:
    """Append the rework reason and flip Status: pending -> rejected."""
    if "**Status:** pending" not in text:
        return ApplyResult(text, False, "amendment is not pending (already decided, or malformed)")
    clean_reason = _sanitize_free_text(reason)
    new_text = text.rstrip("\n") + f"\n\n**Rework ({ts}):** {clean_reason}\n"
    new_text = new_text.replace("**Status:** pending", "**Status:** rejected", 1)
    return ApplyResult(new_text, True, "marked rejected with your reason")


# --------------------------------------------------------------- grammar ----
def parse_command(text: str) -> tuple[str, str]:
    """Split a raw Telegram message into (command, argstring).

    Unknown / malformed / non-command text -> ("help", original_text), per the
    grammar table's "anything else -> reply with help; do NOT execute" rule.
    """
    raw = (text or "").strip()
    if not raw.startswith("/"):
        return "help", raw
    parts = raw.split(None, 1)
    cmd = parts[0].lower()
    if "@" in cmd:  # tolerate "/status@MyBotName" (Telegram group-chat convention)
        cmd = cmd.split("@", 1)[0]
    args = parts[1].strip() if len(parts) > 1 else ""
    if cmd not in COMMANDS:
        return "help", raw
    return cmd, args


# ------------------------------------------------------------- allowlist ----
def is_allowed(chat_id: str, allowlist: list[str], default_chat: str) -> bool:
    """True iff chat_id may issue commands.

    If chat_allowlist is configured (multi-person), it is used ADDITIVELY with
    DEVTEAM_TG_CHAT (the owner never gets silently locked out by their own
    allowlist edit) rather than replacing it. If chat_allowlist is empty, only
    DEVTEAM_TG_CHAT may command the bot — the single-operator default.
    """
    allowed = {str(c) for c in (allowlist or []) if str(c).strip()}
    if default_chat:
        allowed.add(str(default_chat))
    return bool(chat_id) and str(chat_id) in allowed


# ------------------------------------------------------- free-text safety ---
def _sanitize_free_text(text: str) -> str:
    """Neutralise Telegram free text before it is written into PLAN.md.

    PLAN.md's structure is entirely line-based: a new "### TASK-NNN" or
    "**Field:**" only means something at the START of a line. This function
    guarantees the sanitised text can never occupy the start of a line inside
    PLAN.md by collapsing every newline/control character to a single space,
    so the entire message — however it's formatted, however many shell
    metacharacters or path-traversal strings it contains — stays inert data
    inside one Markdown bullet. It is never eval'd, shelled out to, or opened
    as a path; this function's only job is markdown-structure containment.
    """
    if text is None:
        return ""
    clean = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    clean = "".join(ch if (ch.isprintable() or ch == " ") else " " for ch in clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) > MAX_FREE_TEXT_LEN:
        clean = clean[: MAX_FREE_TEXT_LEN].rstrip() + "\u2026 [truncated]"
    return clean


# --------------------------------------------------- PLAN.md block editing --
def _task_span(lines: list[str], task_id: str) -> tuple[int, int] | None:
    """(start, end) line-index span [start, end) for a single ### TASK-NNN block."""
    start = None
    for i, raw in enumerate(lines):
        m = TASK_HEADER_RE.match(raw.strip())
        if m:
            if start is not None:
                return start, i
            if m.group(1) == task_id:
                start = i
    if start is not None:
        return start, len(lines)
    return None


def _field_line_index(lines: list[str], start: int, end: int, field: str) -> int | None:
    for i in range(start, end):
        m = FIELD_RE.match(lines[i].strip())
        if m and m.group(1) == field:
            return i
    return None


def _field_block_end(lines: list[str], field_line: int, end: int) -> int:
    """Index one-past the last continuation line of a (possibly multi-line) field."""
    j = field_line + 1
    while j < end:
        stripped = lines[j].strip()
        if not stripped:
            break
        if FIELD_RE.match(stripped) or TASK_HEADER_RE.match(stripped):
            break
        j += 1
    return j


def _get_field_value(lines: list[str], start: int, end: int, field: str) -> str:
    idx = _field_line_index(lines, start, end, field)
    if idx is None:
        return ""
    m = FIELD_RE.match(lines[idx].strip())
    return m.group(2).strip() if m else ""


def _set_field(lines: list[str], start: int, end: int, field: str, value: str) -> list[str]:
    """Replace a single-line field's value in place. No-op if the field is missing."""
    idx = _field_line_index(lines, start, end, field)
    if idx is None:
        return lines
    return lines[:idx] + [f"**{field}:** {value}"] + lines[idx + 1:]


def _append_to_field(lines: list[str], start: int, end: int, field: str, bullet_line: str) -> list[str]:
    """Append a bullet as a new continuation line of a multi-line field.

    If the field currently holds an empty-value marker ("—" etc.) on the
    header line itself, the header is rewritten bare ("**Field:**") and the
    bullet becomes its first continuation line — matching the convention used
    throughout the rest of PLAN.md (see Progress_Notes in the schema).
    """
    idx = _field_line_index(lines, start, end, field)
    if idx is None:
        return lines
    block_end = _field_block_end(lines, idx, end)
    header_val = _get_field_value(lines, start, end, field)
    if block_end == idx + 1 and header_val.lower() in EMPTY_VALUES:
        return lines[:idx] + [f"**{field}:**", bullet_line] + lines[idx + 1:]
    return lines[:block_end] + [bullet_line] + lines[block_end:]


def apply_answer(plan_text: str, task_id: str, free_text: str, ts: str) -> ApplyResult:
    """/answer TASK-NNN <text>

    Appends a [TG-DECISION] Progress_Note. If the task is currently blocked,
    flips Status -> pending and clears Blocked_Reason. Always bumps
    Updated_By/Updated_At. Touches ONLY this task's block — every other byte
    of plan_text is preserved verbatim (territorial isolation, applied to a
    single writer instead of two builders).
    """
    lines = plan_text.split("\n")
    span = _task_span(lines, task_id)
    if span is None:
        return ApplyResult(plan_text, False, f"/answer {task_id}: unknown task ID — no edit made")
    start, end = span

    if _field_line_index(lines, start, end, "Progress_Notes") is None:
        return ApplyResult(plan_text, False, f"/answer {task_id}: malformed task block (no Progress_Notes field) — no edit made")

    was_blocked = _get_field_value(lines, start, end, "Status").lower() == "blocked"
    clean = _sanitize_free_text(free_text)
    if not clean:
        return ApplyResult(plan_text, False, f"/answer {task_id}: empty text after sanitisation — no edit made")

    bullet = f"- [{ts}] [TG-DECISION] {clean}"
    lines = _append_to_field(lines, start, end, "Progress_Notes", bullet)
    start, end = _task_span(lines, task_id)

    if was_blocked:
        lines = _set_field(lines, start, end, "Status", "pending")
        start, end = _task_span(lines, task_id)
        lines = _set_field(lines, start, end, "Blocked_Reason", "\u2014")
        start, end = _task_span(lines, task_id)

    lines = _set_field(lines, start, end, "Updated_By", "ORCH")
    start, end = _task_span(lines, task_id)
    lines = _set_field(lines, start, end, "Updated_At", ts)

    detail = f"/answer {task_id}" + (" \u2014 unblocked (pending)" if was_blocked else " \u2014 note appended")
    return ApplyResult("\n".join(lines), True, detail)


def apply_rework(plan_text: str, task_id: str, reason: str, ts: str) -> ApplyResult:
    """/rework TASK-NNN <reason>

    Writes the reason into Review_Findings as a [TG-REWORK] bullet and moves
    Status needs_review -> in_progress. Same single-task-block discipline as
    apply_answer.
    """
    lines = plan_text.split("\n")
    span = _task_span(lines, task_id)
    if span is None:
        return ApplyResult(plan_text, False, f"/rework {task_id}: unknown task ID — no edit made")
    start, end = span

    if _field_line_index(lines, start, end, "Review_Findings") is None:
        return ApplyResult(plan_text, False, f"/rework {task_id}: malformed task block (no Review_Findings field) — no edit made")

    clean = _sanitize_free_text(reason)
    if not clean:
        return ApplyResult(plan_text, False, f"/rework {task_id}: empty reason after sanitisation — no edit made")

    bullet = f"- [{ts}] [TG-REWORK] {clean}"
    lines = _append_to_field(lines, start, end, "Review_Findings", bullet)
    start, end = _task_span(lines, task_id)

    lines = _set_field(lines, start, end, "Status", "in_progress")
    start, end = _task_span(lines, task_id)
    lines = _set_field(lines, start, end, "Updated_By", "ORCH")
    start, end = _task_span(lines, task_id)
    lines = _set_field(lines, start, end, "Updated_At", ts)

    return ApplyResult("\n".join(lines), True, f"/rework {task_id} \u2014 sent back for rework")


# ------------------------------------------------------------- git plumbing -
def git_pull(repo: Path) -> bool:
    """Best-effort `git pull --rebase --autostash`. False (not raised) on any failure
    or if repo isn't a git working tree — the caller proceeds on the working copy
    either way; a stale pull just means a slightly higher chance of a push conflict,
    never a crash."""
    try:
        r = subprocess.run(["git", "pull", "--rebase", "--autostash"],
                            cwd=repo, capture_output=True, encoding="utf-8", errors="replace", timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def git_commit_and_push_detailed(repo: Path, message: str) -> tuple[bool, bool, str]:
    """Stage+commit+push ONLY PLAN.md. Never touches any other file.

    Returns (committed, pushed, note). The two outcomes are reported
    SEPARATELY because collapsing them is actively misleading: on a repo with
    no remote configured — the normal state of a project for most of its
    early life — the commit always succeeds and only the push fails, and a
    single boolean rendered that as "git commit/push failed". Every control
    drain printed it, reading as "your PLAN.md edit may not have committed"
    when it always had. Reported by oikonomos 2026-08-16 after it sent review
    effort chasing a phantom failure more than once.

    "Committed locally, no remote configured" is benign; "the commit itself
    failed" is serious. Callers must be able to tell them apart.
    """
    try:
        # FAIL CLOSED if `repo` is not itself a git work tree. git walks UP
        # from cwd, so pointing this at a non-repo silently commits into
        # whatever ancestor repo exists — and on a machine where the user's
        # HOME is a git repo (observed 2026-08-16), that means every stray
        # invocation lands a commit in the user's home history. Same class as
        # the wrong-checkout incident the worktree rule exists to prevent:
        # "which repo am I actually writing to?" must never be implicit.
        top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=repo,
                             capture_output=True, encoding="utf-8", errors="replace", timeout=30)
        resolved = (top.stdout or "").strip()
        if top.returncode != 0 or not resolved:
            return False, False, f"{repo} is not a git work tree — refusing to commit"
        if Path(resolved).resolve() != Path(repo).resolve():
            return False, False, (
                f"{repo} is not the root of its git work tree (git resolves to {resolved}) — "
                f"refusing to commit into an ancestor repository")
        subprocess.run(["git", "add", "PLAN.md"], cwd=repo, capture_output=True,
                       encoding="utf-8", errors="replace", timeout=30)
        r = subprocess.run(["git", "commit", "-m", message], cwd=repo, capture_output=True,
                           encoding="utf-8", errors="replace", timeout=30)
        if r.returncode != 0:
            combined = ((r.stdout or "") + (r.stderr or "")).lower()
            # "nothing to commit" is a benign no-op (the desired state was
            # already recorded), not a failure — reporting it as one is the
            # same misleading-message class this function was fixed for.
            if "nothing to commit" in combined or "no changes added" in combined:
                return True, False, "nothing to commit (PLAN.md already at the desired state)"
            detail = (r.stderr or r.stdout or "").strip().splitlines()
            return False, False, f"commit failed: {detail[-1] if detail else 'unknown error'}"
        # No remote at all is a configuration fact, not a failure — say so.
        remotes = subprocess.run(["git", "remote"], cwd=repo, capture_output=True,
                                 encoding="utf-8", errors="replace", timeout=30)
        if not (remotes.stdout or "").strip():
            return True, False, "committed locally (no remote configured — nothing to push)"
        r2 = subprocess.run(["git", "push"], cwd=repo, capture_output=True,
                            encoding="utf-8", errors="replace", timeout=30)
        if r2.returncode != 0:
            detail = (r2.stderr or r2.stdout or "").strip().splitlines()
            return True, False, f"committed locally; push failed: {detail[-1] if detail else 'unknown error'}"
        return True, True, "committed and pushed"
    except Exception as exc:
        return False, False, f"git invocation failed: {exc}"


def git_commit_and_push(repo: Path, message: str) -> bool:
    """Back-compat wrapper: True when the commit landed, regardless of push.

    Deliberately keyed on COMMIT, not push: every caller's failure branch says
    "applied locally but not persisted", and a commit that landed IS persisted
    locally. Callers wanting the distinction use git_commit_and_push_detailed.
    """
    committed, _pushed, _note = git_commit_and_push_detailed(repo, message)
    return committed


# ------------------------------------------------------------ rendering -----
def render_status(board: dict) -> str:
    """Compact plain-text task table from board_publisher.build_board()'s output.
    Telegram-safe: no Markdown tables (Telegram's plain-text mode doesn't render
    GFM tables), just short lines."""
    b = board.get("burndown", {}) or {}
    project = board.get("project", "") or "project"
    lines = [f"\U0001F4CB {project} \u2014 {b.get('done', 0)}/{b.get('total', 0)} done ({b.get('pct', 0)}%)"]
    cols = board.get("columns", {}) or {}
    for status in ("blocked", "needs_review", "in_progress", "claimed", "pending"):
        items = cols.get(status) or []
        if not items:
            continue
        icon = STATUS_ICON.get(status, "\u2022")
        shown = ", ".join(f"{c.get('id', '?')}({c.get('assignee', '')})" for c in items[:8])
        more = "" if len(items) <= 8 else f" +{len(items) - 8} more"
        lines.append(f"{icon} {status}: {shown}{more}")
    done_n = len(cols.get("done") or [])
    if done_n:
        lines.append(f"{STATUS_ICON['done']} done: {done_n}")
    if (board.get("autopilot") or {}).get("stop_file"):
        lines.append("\u26D4 STOP file present \u2014 supervisor halted.")
    usage = board.get("usage")
    if usage:
        lines.append("\U0001F4CA " + _usage_one_liner(usage))
    return "\n".join(lines)


def _usage_pct_str(entry: dict, key: str) -> str:
    v = (entry or {}).get(key)
    return f"{v:.0f}%" if isinstance(v, (int, float)) else "\u2014"


def _usage_one_liner(usage: dict) -> str:
    parts = []
    for provider in ("claude", "codex"):
        entry = usage.get(provider) or {}
        parts.append(f"{provider} 5h={_usage_pct_str(entry, 'pct_5h')} 7d={_usage_pct_str(entry, 'pct_7d')}")
    return " \u00b7 ".join(parts)


def render_usage(usage: dict) -> str:
    """/usage command reply — same table shape as usage_probe.py's own CLI,
    just Telegram-formatted. Missing/None values render as \u2014 and never
    imply anything (the fail-open contract holds all the way to the phone)."""
    lines = ["\U0001F4CA Usage windows (cached):"]
    for provider in ("claude", "codex"):
        entry = usage.get(provider) or {}
        p5 = _usage_pct_str(entry, "pct_5h")
        p7 = _usage_pct_str(entry, "pct_7d")
        reset7 = entry.get("reset_7d") or "\u2014"
        lines.append(f"{provider}: 5h={p5}  7d={p7}  reset_7d={reset7}")
        probed_at = entry.get("probed_at")
        if probed_at:
            lines.append(f"  (probed {probed_at})")
    return "\n".join(lines)


def render_board_url(cfg: dict) -> str:
    url = ((cfg or {}).get("board") or {}).get("url", "")
    return url if url else "No board URL configured \u2014 set autopilot.json \u2192 board.url"


def render_digest(board: dict) -> str:
    """One-paragraph on-demand digest (same P0 channel as the wave-complete digest)."""
    b = board.get("burndown", {}) or {}
    esc = board.get("escalations_open", []) or []
    team = board.get("team", {}) or {}
    lines = [f"{DIGEST_ICON} Digest \u2014 {board.get('project', '')}: "
             f"{b.get('done', 0)}/{b.get('total', 0)} done ({b.get('pct', 0)}%)."]
    if esc:
        esc_str = ", ".join(f"{e.get('task')} ({e.get('question')})" for e in esc[:5])
        lines.append(f"Open escalations: {esc_str}")
    else:
        lines.append("No open escalations.")
    for unit in ("GB", "CX"):
        u = team.get(unit)
        if u and u.get("reviews"):
            lines.append(f"{unit}: {u.get('reviews', 0)} reviews, first-pass rate {u.get('first_pass_rate')}")
    return "\n".join(lines)


# -------------------------------------------------------------- outbound ----
def send_reply(token: str, chat_id: str, text: str) -> bool:
    """POST sendMessage. Fail-open: any network/API error is caught, logged to
    stderr, and swallowed — a failed reply must never crash the supervisor tick."""
    if not token or not chat_id or not text:
        return False
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
            if not body.get("ok"):
                print(f"[tg_commands] reply send failed: {body}", file=sys.stderr)
                return False
            return True
    except Exception as exc:  # noqa: BLE001 — network failures must never crash the tick
        print(f"[tg_commands] reply send failed: {exc}", file=sys.stderr)
        return False
