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

