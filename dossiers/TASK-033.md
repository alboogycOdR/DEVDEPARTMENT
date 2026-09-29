# TASK-033 — clock-stamped plan commits

## Scope

`plan_commit.sh` and `plan_commit.ps1` validate `Updated_At` only in the task
blocks being committed. Missing, malformed, future, and stale values are
replaced with the current UTC clock value before the existing guard and commit
path run. The test fixture copies both tools into a temporary repository, so
the live checkout is never a commit target.

## Verification

- `python -m pytest -q tests/test_plan_commit.py::TestClockStampedUpdatedAt`
- `python -m pytest -q tests/test_plan_commit.py::TestCannotCarryCode tests/test_plan_commit.py::TestGuardRails tests/test_plan_commit.py::TestRunsFromALinkedWorktree`


## Rework (2026-09-29)

Review finding: `.sh` picked affected blocks from the pending PLAN.md diff
hunks; `.ps1` picked them from TASK IDs parsed out of the commit message.
Extracted the shared rule into `scripts/plan_stamp.py` (env-var handoff of
`PLAN_COMMIT_DIFF`/`PLAN_COMMIT_PREVIOUS`); both wrappers call it and fall
open to an unstamped commit if it's missing. Added
`test_message_naming_an_untouched_block_does_not_stamp_it` (both mirrors) and
parametrized the PowerShell test over all four unsafe-timestamp cases to
match the shell test.

Verification: `python -m pytest -q tests/test_plan_commit.py` → 22 passed.
Full suite after merging master: `python -m pytest -q` → 1113 passed in
249.52s; `node hooks/run-tests.js` → 47 passed, 0 failed.
