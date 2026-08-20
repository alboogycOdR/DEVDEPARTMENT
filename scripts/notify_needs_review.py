#!/usr/bin/env python3
"""notify_needs_review.py — fire a Telegram/console ping the instant a
PLAN.md commit transitions any task INTO Status: needs_review.

Called by plan_commit.sh / plan_commit.ps1 immediately after a successful
PLAN.md commit — that script is the single choke point every builder (GB,
S5, CX, manual ORCH dispatch, or the autopilot supervisor loop) already
goes through to record a status transition, so it's the correct place to
hook this rather than duplicating the check inside supervisor.py's tick
loop (which only fires during the autonomous --loop path, not manual
/devteam-dispatch sessions — the gap this script closes: needs_review is
not one of the P0/P1/P2 escalation-contract triggers by design, since the
autopilot handles it silently via auto-review, so a manually-driven
session previously had no notification path for it at all).

Never raises and never exits non-zero: a notification failure must not
block or fail the coordination commit that has already succeeded.

Usage:
    python scripts/notify_needs_review.py <repo_root>
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

TASK_RE = re.compile(
    r"^### (TASK-\d+)\r?\n\*\*Title:\*\* (.*?)\r?\n\*\*Status:\*\* (\S+)",
    re.MULTILINE,
)


def parse_statuses(text: str) -> dict[str, tuple[str, str]]:
    """task_id -> (title, status), read straight off each block's own header."""
    return {m.group(1): (m.group(2), m.group(3)) for m in TASK_RE.finditer(text)}


def main(argv: list[str]) -> int:
    if not argv:
        return 0
    repo_root = Path(argv[0])
    plan_path = repo_root / "PLAN.md"
    try:
        new_text = plan_path.read_text(encoding="utf-8")
        old_proc = subprocess.run(
            ["git", "-C", str(repo_root), "show", "HEAD~1:PLAN.md"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        old_text = old_proc.stdout if old_proc.returncode == 0 else ""
        old_statuses = parse_statuses(old_text)
        new_statuses = parse_statuses(new_text)

        notify_py = repo_root / "scripts" / "notify.py"
        if not notify_py.exists():
            return 0

        for task_id, (title, status) in new_statuses.items():
            if status != "needs_review":
                continue
            prior_status = old_statuses.get(task_id, (None, None))[1]
            if prior_status == "needs_review":
                continue  # already notified on the earlier transition into it
            message = f"{task_id} -> needs_review: {title}"
            subprocess.run(
                [
                    sys.executable,
                    str(notify_py),
                    "--priority",
                    "P0",
                    "--message",
                    message,
                    "--channels",
                    "telegram,console",
                ],
                timeout=20,
            )
    except Exception as exc:  # never let this break the caller
        print(f"[notify_needs_review] skipped (non-fatal): {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
