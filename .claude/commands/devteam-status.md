---
description: Sync scan — validate plan, cross-check git, report health
---

You are ORCH executing **Phase 3 — Monitoring & Dynamic Re-planning**.

1. Run `python scripts/validate_plan.py`. Report violations first — an illegal plan means a unit broke protocol; find the offending edit via `git log -p -- PLAN.md` and identify the unit.
   Also run `python scripts/sync_from_pack.py --behind-pack --project .`; if it prints a warning, include it verbatim in the report.
2. Cross-check reality vs claims:
   - For each active task: does its `Branch` exist (`git branch --list "task/*"`)? Do `Artifacts` files exist on that branch (`git ls-tree -r <branch> --name-only`)? Do commit messages carry the `[TASK-NNN]` suffix?
   - `git diff main...<branch> --stat` for each active branch: **any path outside the task's Owned_Paths is a critical finding** — flag immediately, don't wait for review.
3. Detect drift: tasks `claimed`/`in_progress` with no heartbeat source in a long window (the newest of `Updated_At`, task-branch commit, and dossier mtime; surface `in_progress` after `stale_minutes × 4` even when redispatch is exhausted); `blocked` tasks awaiting triage; done dependencies that unlock pending work.
4. Triage `blocked` per protocol §7 (spec clarification → re-sequence → re-assign → escalate with a concrete recommendation).
5. Update frontmatter (`overall_status`, `last_updated`, `orchestrator_notes`) and commit if anything changed: `chore(plan): status scan [ORCH]`.
6. Report: table of tasks (ID | status | assignee | last update | health), critical findings, blocked triage decisions, and the recommended next action. Include the status digest's learning line: distiller last-run, completed runs, and instincts produced.

$ARGUMENTS
