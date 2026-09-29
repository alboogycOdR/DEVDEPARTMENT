# TASK-034 — Wave E E-F1 — plan_commit compare-and-swap, idempotent claim, legacy-mode blackboard guard

## Brief
(1) CAS: record PLAN.md blob SHA at read; at commit, if HEAD's PLAN.md differs, re-read, re-apply only this unit's task-block change (3-way at block granularity), retry ≤3, else fail loudly — never commit a lost update. (2) Claim for a task already claimed/in_progress by the same unit = no-op exit 0, no commit. (6) Legacy-mode guard (until strict): plan_commit and the dispatch post-run validation (plan_guard.py) reject a builder PLAN.md change touching any block other than the unit's claimed task, changing more than `plan.max_builder_diff_lines`=40 lines, or changing line endings; the error tells the builder to use plan_commit for its own block only.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §1 H4, §8 (E-F.1, E-F.2, E-F.6)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/plan_commit.sh, scripts/plan_commit.ps1, tests/test_plan_commit.py, scripts/plan_guard.py, tests/test_plan_guard.py
- Protected-path grants: scripts/plan_commit.sh, scripts/plan_commit.ps1, scripts/plan_guard.py
- Depends_On: TASK-033, TASK-027

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log

- 2026-09-29: Added block-granular CAS replay/rejection to both `plan_commit` mirrors, plus legacy guard checks for LF line endings, configurable diff limits, task ownership, and immutable builder fields. Fixture-repo tests cover independent-block replay, same-block rejection, duplicate no-op, and guard violations.
