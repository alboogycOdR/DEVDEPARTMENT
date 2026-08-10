#!/usr/bin/env python3
"""Pre-flight check of a task's Owned_Paths — generates the evidence by construction.

Background
----------
The coordination protocol has required, since c8b9872, that a builder inspect every
entry in its ``Owned_Paths`` before writing code — the point being that you cannot
sensibly extend a file you have not confirmed exists, and "I assumed there was a test
file" has caused real rework here.

The requirement was enforced at review by looking for an ``ls``/``find`` in the commit
history or a Progress_Note. That check has now been missed three times by the same unit
across three separate sessions, which is the signature of a mechanism problem rather
than a discipline problem (the same conclusion the ``plan_commit`` defect reached: see
REVIEW.md, 2026-08-01). Asking a builder to remember a ritual and then attest to it in
prose produces an unfalsifiable claim; running the check as a script produces evidence.

So: run this, paste the output into your first Progress_Note. The output *is* the
evidence, and it is real — it reports what is actually on disk, including the surprises
(a path you thought existed but does not, or a glob matching nothing).

Usage
-----
    python scripts/preflight_paths.py TASK-019
    python scripts/preflight_paths.py TASK-019 --repo /path/to/worktree

Exit status is 0 whenever the task is found and parsed; a missing path is NOT an error,
because "this file does not exist yet, I am creating it" is the normal case for new
territory. The output distinguishes the two so the reader can tell them apart.
"""

from __future__ import annotations

import argparse
import glob as globlib
import os
import re
import sys
from pathlib import Path


def find_repo_root(start: Path) -> Path:
    """Walk up until a PLAN.md is found; fall back to the starting directory."""
    for candidate in [start, *start.parents]:
        if (candidate / "PLAN.md").is_file():
            return candidate
    return start


def owned_paths_for(plan_text: str, task_id: str) -> list[str]:
    """Extract the Owned_Paths entries for one task block."""
    start = plan_text.find(f"### {task_id}")
    if start == -1:
        raise SystemExit(f"[preflight] {task_id} not found in PLAN.md")
    nxt = plan_text.find("\n### ", start + 5)
    block = plan_text[start : nxt if nxt != -1 else len(plan_text)]

    match = re.search(r"^\*\*Owned_Paths:\*\*\s*(.*)$", block, re.M)
    if not match:
        raise SystemExit(f"[preflight] {task_id} has no Owned_Paths line")

    return [p.strip() for p in match.group(1).split(",") if p.strip() and p.strip() != "—"]


def describe(root: Path, entry: str) -> list[str]:
    """Report what `entry` actually resolves to on disk, one line per finding."""
    # Trailing /** or /* — a directory territory.
    bare = entry.rstrip("*").rstrip("/")
    target = root / bare

    if any(ch in entry for ch in "*?["):
        matches = sorted(globlib.glob(str(root / entry), recursive=True))
        files = [m for m in matches if os.path.isfile(m)]
        if not files:
            return [f"  GLOB   {entry}  -> matches nothing yet (new territory)"]
        shown = [f"  GLOB   {entry}  -> {len(files)} file(s):"]
        shown += [f"           {Path(f).relative_to(root).as_posix()}" for f in files[:12]]
        if len(files) > 12:
            shown.append(f"           ... and {len(files) - 12} more")
        return shown

    if target.is_dir():
        children = sorted(p for p in target.iterdir())
        head = [f"  DIR    {entry}  -> exists, {len(children)} entr(y/ies):"]
        head += [f"           {c.name}{'/' if c.is_dir() else ''}" for c in children[:12]]
        if len(children) > 12:
            head.append(f"           ... and {len(children) - 12} more")
        return head

    if target.is_file():
        lines = sum(1 for _ in target.open("r", encoding="utf-8", errors="replace"))
        return [f"  FILE   {entry}  -> exists, {lines} line(s), {target.stat().st_size} bytes"]

    parent = target.parent
    if parent.is_dir():
        return [f"  NEW    {entry}  -> does not exist; parent {parent.relative_to(root).as_posix()}/ exists"]
    # as_posix(): a native Windows path here prints backslashes, and the builder pastes this
    # line into PLAN.md — where "...\tests\e2e" survives at least one round trip as a literal
    # tab (seen on TASK-024). Forward slashes make the evidence readable wherever it lands.
    return [f"  NEW    {entry}  -> does not exist; parent directory {parent.as_posix()} does NOT exist either"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Inspect a task's Owned_Paths before writing code.")
    ap.add_argument("task_id", help="e.g. TASK-019")
    ap.add_argument("--repo", default=None, help="repo/worktree root (default: search upward from cwd)")
    args = ap.parse_args()

    task_id = args.task_id if args.task_id.startswith("TASK-") else f"TASK-{args.task_id}"
    root = Path(args.repo).resolve() if args.repo else find_repo_root(Path.cwd().resolve())

    plan = root / "PLAN.md"
    if not plan.is_file():
        raise SystemExit(f"[preflight] no PLAN.md at {root.as_posix()} — pass --repo")

    entries = owned_paths_for(plan.read_text(encoding="utf-8"), task_id)

    print(f"[preflight] {task_id} Owned_Paths inspected in {root.as_posix()}")
    print(f"[preflight] {len(entries)} entr(y/ies). FILE/DIR/GLOB = exists, NEW = you are creating it.")
    for entry in entries:
        for line in describe(root, entry):
            print(line)
    print("[preflight] Paste this output into your first Progress_Note as the c8b9872 filesystem check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
