# TASK-041 — Wave E E-J2 — superseded, owner_hold, external tasks, ORCH-SOLO lane, Blocked_Reason vocabulary

## Brief
(2) `superseded` terminal status requiring `Superseded_By:`; excluded from awaiting-ORCH banners. (5) `owner_hold` status with required `Hold_On:` (CREDENTIALS | HARDWARE | ACCOUNT | DECISION | EXTERNAL: <detail>) — never dispatched, never triaged, listed in the digest with age. `Type: external` tasks: no Owned_Paths, checklist acceptance, evidence line, still get a REVIEW row. Solo lane `Assigned_To: ORCH-SOLO`: direct to base with [TASK-NNN] commits; cannot reach done without a REVIEW row whose reviewer model differs from the solo session's; capped by `plan.solo_max_files`=5. (6) Blocked_Reason must be `CATEGORY: detail` from the vocabulary (+ CAPACITY); prose-only fails validation. Protocol text (docs/COORDINATION_PROTOCOL.md) is ORCH-applied at wave close.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §12 (E-J.2, E-J.5, E-J.6)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/validate_plan.py, tests/test_validate_plan.py, scripts/supervisor.py, tests/test_supervisor.py, scripts/control.py, tests/test_control.py, tests/test_lanes.py (new)
- Protected-path grants: scripts/validate_plan.py, scripts/supervisor.py, scripts/control.py
- Depends_On: TASK-040, TASK-036

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log
