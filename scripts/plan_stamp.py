#!/usr/bin/env python3
"""plan_stamp.py — stamp unsafe Updated_At values in the task blocks that a
pending PLAN.md diff actually touched (LOOP_HYGIENE_2026-09 §7, E-E; H5).

Shared by scripts/plan_commit.sh and scripts/plan_commit.ps1 so both platform
mirrors stamp exactly the same blocks from exactly the same rule — a value is
retained only when it is a well-formed UTC timestamp that is neither more
than 5 minutes ahead of the clock nor older than PLAN.md's previous commit.
Blocks the diff did not touch are never rewritten.

Reads the pending diff and the previous commit time from the environment
(PLAN_COMMIT_DIFF, PLAN_COMMIT_PREVIOUS) rather than argv, so callers can pass
a `git diff --unified=0 -- PLAN.md` and `git log -1 --format=%cI` output of
any size without a shell quoting/length concern.

Usage:
    PLAN_COMMIT_DIFF="$(git diff --unified=0 -- PLAN.md)" \
    PLAN_COMMIT_PREVIOUS="$(git log -1 --format=%cI HEAD -- PLAN.md)" \
    python scripts/plan_stamp.py PLAN.md
"""
from __future__ import annotations

import datetime as dt
import os
import re
import sys
from pathlib import Path


def stamp(plan_path: Path, diff_text: str, previous_raw: str,
          now: dt.datetime | None = None) -> bool:
    """Rewrite unsafe Updated_At values in diff-touched blocks. Returns True
    if the file was changed."""
    text = plan_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    headers = [i for i, line in enumerate(lines) if re.match(r"^### TASK-\d+\s*$", line)]
    if not headers:
        return False

    changed_lines: list[int] = []
    for match in re.finditer(r"^@@ -[^ ]+ \+(\d+)(?:,(\d+))? @@", diff_text, re.M):
        start = int(match.group(1))
        length = int(match.group(2) or "1")
        # A deletion-only hunk has no new line. Its insertion point still
        # belongs to the block that changed.
        changed_lines.extend(range(start, start + max(length, 1)))

    affected: set[int] = set()
    for lineno in changed_lines:
        index = max(0, lineno - 1)
        for header_index, header in enumerate(headers):
            next_header = headers[header_index + 1] if header_index + 1 < len(headers) else len(lines)
            if header <= index < next_header:
                affected.add(header_index)
                break

    if not affected:
        return False

    previous = previous_raw.strip()
    try:
        previous_time = dt.datetime.fromisoformat(previous.replace("Z", "+00:00")) if previous else None
    except ValueError:
        previous_time = None
    now = now or dt.datetime.now(dt.timezone.utc)
    now = now.replace(microsecond=0)
    stamp_text = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    changed = False
    for header_index in sorted(affected, reverse=True):
        start = headers[header_index]
        end = headers[header_index + 1] if header_index + 1 < len(headers) else len(lines)
        value_index = next((i for i in range(start, end) if lines[i].startswith("**Updated_At:**")), None)
        valid = False
        if value_index is not None:
            raw = lines[value_index].split(":", 1)[1].strip()
            try:
                value = dt.datetime.strptime(raw, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
                valid = value <= now + dt.timedelta(minutes=5) and (previous_time is None or value >= previous_time)
            except ValueError:
                pass
        if valid:
            continue
        changed = True
        if value_index is None:
            ending = "\r\n" if "\r\n" in text else "\n"
            lines.insert(end, f"**Updated_At:** {stamp_text}{ending}")
            continue
        ending = "\r\n" if lines[value_index].endswith("\r\n") else "\n"
        lines[value_index] = f"**Updated_At:** {stamp_text}{ending}"

    if changed:
        plan_path.write_text("".join(lines), encoding="utf-8", newline="")
    return changed


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: plan_stamp.py PLAN.md", file=sys.stderr)
        return 2
    plan_path = Path(argv[1])
    diff_text = os.environ.get("PLAN_COMMIT_DIFF", "")
    previous_raw = os.environ.get("PLAN_COMMIT_PREVIOUS", "")
    stamp(plan_path, diff_text, previous_raw)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
