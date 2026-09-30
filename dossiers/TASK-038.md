# TASK-038 — Wave E E-H2 — CLI launch smoke test in harness-audit

## Brief
harness-audit.sh/.ps1 launch every ACTIVE unit's CLI through the real dispatch argv, in a scratch worktree, with a no-op prompt that must write one file under its territory and exit; check exit 0, file written, no TTY prompt, CLI version recorded. Runs on builder_registry changes and before an onboarding's first dispatch. Fails on a CR in any *.sh in the checkout. From LIVE_CHECKS: assert `--max-turns` is still accepted (hidden flag), and that Windows Git Bash launch paths set MSYS_NO_PATHCONV=1 or never pass a leading-slash prompt. Fixture tests use stub CLIs; one real run per active unit recorded in Test_Evidence.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §10 (E-H.4, E-H.5), docs/reviews/LIVE_CHECKS_2026-09.md
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/harness-audit.sh, scripts/harness-audit.ps1, tests/test_harness_smoke.py (new), tests/fixtures/smoke/** (new)
- Protected-path grants: scripts/harness-audit.sh, scripts/harness-audit.ps1
- Depends_On: TASK-035

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log

- [2026-09-30T14:27:15Z] [CX] Reworked ORCH review findings: Windows smoke now resolves Git Bash explicitly for version, flag, and dispatch probes; the live entrypoint rejects CR bytes in checkout shell scripts; `--units`/`-SmokeUnits` and `DEVTEAM_SMOKE_UNITS` select active units with all active as the default; live scratch registry copies model and auth; sync-manifest includes the smoke test. No nested live CLI launched per ORCH directive. `python -m pytest -q tests/test_harness_smoke.py tests/test_sync_from_pack.py` -> 72 passed; `python -m pytest -q` -> 1191 passed in 514.52s; `node hooks/run-tests.js` -> 47 passed, 0 failed. ORCH retains the live CX acceptance run.
