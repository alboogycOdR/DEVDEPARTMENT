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

- 2026-09-30: Reproduced the carried TASK-036 regression with a real local bare remote: three supervisor `--once` ticks published an unrelated ahead commit when `git.push_policy` was absent. Added the per-tick `only_if_configured` guard; the same fixture now keeps the remote ref unchanged.
- Added validation for `superseded`, `owner_hold`, external work, ORCH-SOLO file limits and independent review, and `CATEGORY: detail` blocked reasons. Supervisor now treats superseded as terminal, leaves owner holds and solo work out of dispatch/triage, and includes owner holds with age in its generated status digest. CONTROL blocks use the same blocked-reason grammar.
- REVIEW data is supplied to the plan validator by its CLI and by supervisor ticks. Other pure callers without REVIEW data retain structural validation; this avoids making maintenance's existing `validate(plan_text)` call reject a completed solo task solely because it cannot see REVIEW.md.
- Full `python -m pytest -q` run on 2026-09-30: 1202 passed, 3 failed in 561.95s. The three failures are fixture drift caused by the required `CATEGORY: detail` rule: `tests/test_supervisor_ledgers.py::test_escalation_ledger_holds_p2s_and_renotifies_after_four_hours` uses three bare `SPEC_AMBIGUITY` reasons (lines 138–142); `tests/test_supervisor_ledgers.py::test_tooling_failure_triage_is_durable_and_attempt_is_real` uses bare `TOOLING_FAILURE` (line 170); `tests/test_token_efficiency.py::test_missing_dependency_triage_is_capped` uses bare `MISSING_DEPENDENCY` (line 137). Both test files are outside TASK-041 Owned_Paths. ORCH must grant them before the fixtures can be updated and the full suite rerun. No source regression was indicated by these failures.
