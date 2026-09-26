# TASK-033 — Wave E E-E — plan_commit stamps Updated_At from the clock; tests isolated from the live checkout

## Brief
plan_commit.sh/.ps1 rewrite missing, unparseable, future or stale `Updated_At` values in the changed task blocks to the system UTC time before committing. Also fix the known flake (TASK-022 review): tests/test_plan_commit.py must resolve REPO_ROOT to a tmp fixture repo, never the live main checkout, so suites don't contend with concurrent sessions.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §1 H5, §7 (E-E)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/plan_commit.sh, scripts/plan_commit.ps1, tests/test_plan_commit.py
- Protected-path grants: scripts/plan_commit.sh, scripts/plan_commit.ps1
- Depends_On: TASK-024

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log
