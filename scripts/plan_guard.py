#!/usr/bin/env python3
"""Refuse a PLAN.md coordination commit that reaches outside its own task block.

Why this exists
---------------
`plan_commit` commits the WHOLE working-tree PLAN.md (by pathspec, which is what
stops it carrying code — see plan_commit.sh's header). That is safe for code and
unsafe for concurrency: a builder that read PLAN.md before another builder's edit
and writes after it silently discards that edit. PLAN.md is a shared blackboard
with no merge step, so last-writer-wins is the whole conflict-resolution story.

Observed on 2026-08-02: GB's TASK-011 claim (48f3f71) was reverted to `pending`
by an uncommitted overwrite and had to be re-applied in fb5f3f3. It self-healed
only because GB happened to re-check. The dangerous version of the same race
reverts a claim while its owner is still working, letting a second builder claim
the same task — two units in one territory, which is the single thing the whole
protocol exists to prevent.

Separately, on the same day, CX wrote its TASK-025 claim into TASK-014's block
outright (6883452) and corrected it a commit later. Different cause — mis-target
rather than concurrency — same signature in the diff, and the same fix catches
both.

What it checks
--------------
Given the intended commit message, it extracts the TASK-NNN that message names,
then diffs working-tree PLAN.md against HEAD and asks which task blocks changed.
Any changed block that is not the named one is a violation.

Frontmatter changes are allowed only for ORCH (message tagged `[ORCH]`), since
frontmatter is ORCH-owned by protocol.

Exit codes: 0 = clean, 1 = violation (caller must abort the commit), 2 = usage
or environment error. On anything it cannot parse it FAILS OPEN with a warning
and exit 0 — a guard that blocks coordination writes because it could not read a
diff would strand every builder, which is worse than the race it prevents.

Usage:
    python scripts/plan_guard.py --message "chore(plan): claim TASK-011 [GB]" [--repo <root>]
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

TASK_RE = re.compile(r"TASK-\d+")
BLOCK_RE = re.compile(r"^### (TASK-\d+)\s*$", re.M)


def _run(args: list[str], cwd: Path) -> tuple[int, str]:
    p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout or "")


def block_ranges(text: str) -> list[tuple[str, int, int]]:
    """[(task_id, start_line, end_line)] over PLAN.md, 1-indexed, end exclusive."""
    lines = text.splitlines()
    marks: list[tuple[str, int]] = []
    for idx, line in enumerate(lines, start=1):
        m = re.match(r"^### (TASK-\d+)\s*$", line)
        if m:
            marks.append((m.group(1), idx))
    out = []
    for i, (tid, start) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(lines) + 1
        out.append((tid, start, end))
    return out


def changed_line_numbers(repo: Path) -> set[int]:
    """Line numbers in the NEW (working-tree) PLAN.md touched by the pending diff."""
    code, diff = _run(["git", "diff", "-U0", "--", "PLAN.md"], repo)
    if code != 0:
        raise RuntimeError("git diff failed")
    touched: set[int] = set()
    for line in diff.splitlines():
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line)
        if m:
            start = int(m.group(1))
            count = int(m.group(2)) if m.group(2) is not None else 1
            # count 0 == pure deletion; attribute it to the line it sits after.
            for n in range(start, start + max(count, 1)):
                touched.add(n)
    return touched


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--message", required=True, help="the intended commit message")
    ap.add_argument("--repo", default=".", help="repo root containing PLAN.md")
    args = ap.parse_args()

    repo = Path(args.repo).resolve()
    plan = repo / "PLAN.md"
    if not plan.is_file():
        print(f"[plan_guard] no PLAN.md at {repo} - skipping check", file=sys.stderr)
        return 0

    named = TASK_RE.findall(args.message)
    is_orch = "[ORCH]" in args.message.upper()

    try:
        text = plan.read_text(encoding="utf-8")
        touched = changed_line_numbers(repo)
        ranges = block_ranges(text)
    except Exception as exc:  # noqa: BLE001 - fail open, see module docstring
        print(f"[plan_guard] could not inspect the diff ({exc}) - allowing commit", file=sys.stderr)
        return 0

    if not touched:
        return 0

    first_block_start = ranges[0][1] if ranges else None
    frontmatter_touched = first_block_start is not None and any(n < first_block_start for n in touched)

    blocks_touched: set[str] = set()
    for tid, start, end in ranges:
        if any(start <= n < end for n in touched):
            blocks_touched.add(tid)

    # ORCH may touch anything; it owns the plan's structure.
    if is_orch:
        return 0

    problems: list[str] = []
    if frontmatter_touched:
        problems.append(
            "the frontmatter (plan_version / overall_status / orchestrator_notes / last_updated) "
            "- that is ORCH-owned by protocol"
        )

    if named:
        strays = sorted(b for b in blocks_touched if b not in named)
        if strays:
            problems.append(
                f"task block(s) {', '.join(strays)}, but this message names {', '.join(sorted(set(named)))}"
            )
    elif len(blocks_touched) > 1:
        problems.append(
            f"{len(blocks_touched)} task blocks ({', '.join(sorted(blocks_touched))}) and the message names none"
        )

    if not problems:
        return 0

    print("[plan_guard] REFUSING this PLAN.md commit - it changes " + "; and ".join(problems), file=sys.stderr)
    print(
        "[plan_guard] PLAN.md is a shared blackboard with no merge step: whatever you commit\n"
        "[plan_guard] REPLACES what is on disk, so an edit outside your own block silently\n"
        "[plan_guard] discards another unit's work - including, in the worst case, reverting a\n"
        "[plan_guard] live claim to 'pending' and letting a second builder take the same task.\n"
        "[plan_guard] \n"
        "[plan_guard] Almost always this means your PLAN.md is STALE. Do this:\n"
        "[plan_guard]   1. git -C <repo-root> diff -- PLAN.md      # see exactly what you would overwrite\n"
        "[plan_guard]   2. git -C <repo-root> checkout -- PLAN.md  # discard, then re-read it fresh\n"
        "[plan_guard]   3. re-apply ONLY your own task block's change, and re-run plan_commit\n"
        "[plan_guard] \n"
        "[plan_guard] If you genuinely must change another block, that is an ORCH decision - stop\n"
        "[plan_guard] and report it rather than working around this check.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
