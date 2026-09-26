# TASK-036 — Wave E E-G — bookkeeping push policy (every | batch | merge_only)

## Brief
`git.push_policy`: every | batch | merge_only. Default `batch` for NEW projects only; existing projects keep `every` unless the owner opts in (ask-don't-auto-flip). batch: plan-only commits (chore(plan), CONTROL applications, status scans) pushed at most every `git.push_batch_minutes`=30, always on merge or park. merge_only: only on merge or park. Status scans that change nothing produce no commit. Implement the policy once in push_policy.py; plan_commit, control and the supervisor's merge/park paths call it.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §9 (E-G)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/plan_commit.sh, scripts/plan_commit.ps1, scripts/control.py, tests/test_control.py, tests/test_plan_commit.py, scripts/push_policy.py (new), tests/test_push_policy.py (new), scripts/supervisor.py
- Protected-path grants: scripts/plan_commit.sh, scripts/plan_commit.ps1, scripts/control.py, scripts/push_policy.py, scripts/supervisor.py
- Depends_On: TASK-034, TASK-031

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log
