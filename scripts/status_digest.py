#!/usr/bin/env python3
"""Scripted status digest: the wave status the owner used to get from an LLM turn, built from
PLAN.md, git and the builder run markers with NO model call.

Layout is fixed (owner's preferred format, 2026-09-19): Merged / In progress / Queue /
Pending action / Prod, fragment bullets, local time last.

  python scripts/status_digest.py            # print the digest and rewrite .devteam/STATUS.md
  python scripts/status_digest.py --send     # also notify, but only when the content changed
  python scripts/status_digest.py --send --force

The supervisor calls run() every status_digest_minutes (default 30). STATUS.md is always
rewritten, so it is the guaranteed channel; Telegram needs DEVTEAM_TG_TOKEN and DEVTEAM_TG_CHAT.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_plan import parse_tasks, Report  # noqa: E402

UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"
DEFAULT_LOOKBACK_HOURS = 6
MAX_LINES = 8            # per section; the rest is summarised as "+N more"
SHORT = 64


def _short(text: str, n: int = SHORT) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "..."


def _deps_done(task, by_id: dict) -> bool:
    raw = task.get("Depends_On")
    if task.is_empty("Depends_On"):
        return True
    for dep in re.split(r"[,\s]+", raw):
        if re.match(r"^TASK-[A-Z0-9-]+$", dep):
            d = by_id.get(dep)
            if d is None or d.get("Status") != "done":
                return False
    return True


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, UTC_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _merged_since(repo: Path, since: datetime) -> list[str]:
    out = subprocess.run(
        ["git", "log", "--merges", "--since", since.strftime(UTC_FMT), "--format=%s"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace",
    ).stdout
    merged = []
    for line in out.splitlines():
        m = re.match(r"^merge: (TASK-[A-Z0-9-]+)\s*(.*?)(?:\s*\[ORCH\])?$", line)
        if m:
            merged.append(f"{m.group(1)} {_short(m.group(2))}")
    return merged


def _section(lines: list[str]) -> list[str]:
    if not lines:
        return ["- none"]
    shown = [f"- {x}" for x in lines[:MAX_LINES]]
    if len(lines) > MAX_LINES:
        shown.append(f"- +{len(lines) - MAX_LINES} more")
    return shown


def build(repo: Path, now: datetime, since: datetime | None = None) -> str:
    """The digest body WITHOUT the trailing local-time line (so it can be hashed for change detection)."""
    tasks = parse_tasks((repo / "PLAN.md").read_text(encoding="utf-8"), Report())
    by_id = {t.task_id: t for t in tasks}
    since = since or (now - timedelta(hours=DEFAULT_LOOKBACK_HOURS))

    in_progress, in_review, ready, waiting, unassigned, pending_action = [], [], [], 0, 0, []
    for t in tasks:
        status, tid, unit = t.get("Status"), t.task_id, t.get("Assigned_To")
        label = f"{tid} {_short(t.get('Title'))}"
        if status in ("claimed", "in_progress"):
            in_progress.append(f"{label} ({unit}, building)")
        elif status == "needs_review":
            in_review.append(f"{label} (in review)")
        elif status == "pending":
            if unit in ("", "TBD", "—"):
                unassigned += 1
            elif _deps_done(t, by_id):
                ready.append(f"{tid} {_short(t.get('Title'), 48)}")
            else:
                waiting += 1
        elif status == "blocked":
            pending_action.append(f"{tid} blocked: {_short(t.get('Blocked_Reason'), 90)}")

    queue = [f"ready: {x}" for x in ready]
    if waiting:
        queue.append(f"{waiting} waiting on dependencies")
    if unassigned:
        queue.append(f"{unassigned} unassigned (backlog/maintenance)")

    lines = ["Merged (since last update):", *_section(_merged_since(repo, since)),
             "In progress:", *_section(in_progress + in_review),
             "Queue:", *_section(queue),
             "Pending action:", *_section(pending_action),
             "Prod: not touched by the autopilot (deploys are manual)"]
    return "\n".join(lines)


def _local_time_line(now: datetime) -> str:
    return "Local time: " + now.astimezone().strftime("%H:%M %Z")


def _notify(cfg: dict, repo: Path, text: str) -> None:
    channels = ",".join(cfg.get("notify_channels", ["console", "file"]))
    script = repo / "scripts" / "notify.py"
    if script.exists():
        subprocess.run([sys.executable, str(script), "--priority", "P0", "--message", text,
                        "--channels", channels], cwd=repo)


def run(repo: Path, cfg: dict | None = None, now: datetime | None = None,
        send: bool = False, force: bool = False) -> str:
    """Write .devteam/STATUS.md; optionally notify when the digest changed. Returns the text."""
    cfg = cfg or {}
    now = now or datetime.now(timezone.utc)
    state_path = repo / ".devteam" / "last_status_digest.json"
    try:
        last = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        last = {}
    body = build(repo, now, since=_parse_ts(last.get("ts", "")))
    digest = f"{body}\n{_local_time_line(now)}"
    (repo / ".devteam").mkdir(exist_ok=True)
    (repo / ".devteam" / "STATUS.md").write_text(digest + "\n", encoding="utf-8")

    body_hash = hashlib.sha1(body.encode("utf-8")).hexdigest()
    if send and (force or body_hash != last.get("hash")):
        _notify(cfg, repo, digest)
        state_path.write_text(json.dumps({"ts": now.strftime(UTC_FMT), "hash": body_hash}), encoding="utf-8")
    return digest


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--send", action="store_true", help="notify too, but only if the content changed")
    ap.add_argument("--force", action="store_true", help="with --send: notify even if unchanged")
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[1]))
    ns = ap.parse_args(argv)
    repo = Path(ns.repo)
    cfg = {}
    cfg_path = repo / "autopilot.json"
    if cfg_path.exists():
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    text = run(repo, cfg, send=ns.send, force=ns.force)
    sys.stdout.buffer.write((text + "\n").encode("utf-8", errors="replace"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
