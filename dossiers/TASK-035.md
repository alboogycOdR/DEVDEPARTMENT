# TASK-035 — Wave E E-F2 — verified claim, pinned base, dirty-PLAN refusal, strict Owned_Paths grammar, strict-by-default onboarding

## Brief
(3) Legacy mode: after launch, dispatch polls main-checkout PLAN.md up to `dispatch.claim_verify_seconds`=120 for the unit's claim flip; none → log CLAIM_UNVERIFIED, hold the builder's first commit for next tick's reconciliation (strict mode: dispatch claims itself). (4) Extend TASK-024's base-tip port: branch created from <base> tip in the worktree on every fresh claim, both scripts; refuse if PLAN.md has uncommitted changes in the main checkout. (5) validate_plan: Owned_Paths is a comma-separated list of globs only; reject prose, parentheses (other than the single permitted ` (new)` suffix) and TBD. (7) Onboarding writes control.mode strict only for projects whose active units are all verified CONTROL emitters; existing projects are OFFERED strict in the upgrade checklist, never flipped (ask-don't-auto-flip).

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §8 (E-F.3, E-F.4, E-F.5, E-F.7)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/dispatch.sh, scripts/dispatch.ps1, tests/test_dispatch_worktree.py, scripts/validate_plan.py, tests/test_validate_plan.py, scripts/sync_from_pack.py, tests/test_sync_from_pack.py
- Protected-path grants: scripts/dispatch.sh, scripts/dispatch.ps1, scripts/validate_plan.py, scripts/sync_from_pack.py
- Depends_On: TASK-027, TASK-032, TASK-026

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log
