# TASK-025 — Wave E E-0c — tracked role marker, --diverged report, legacy first-sync, version stamp

## Brief
Make sync real (items 2–5). (2) `sync-manifest.json` gains `"role": "pack"|"project"`; `tests/test_sync_from_pack.py::_is_pack_repo()` reads it (not the gitignored .devteam/sync_state.json); onboarding/first sync writes `project`. Also port the 1-line d3f5fc08 hunk to this test file (cite SHA). (3) `sync_from_pack.py --diverged`: read-only, lists framework-owned files differing between project and pack with a unified diff. (4) Legacy sync: empty/absent sync_state → every diverged framework-owned file is a conflict to review, never overwritten. (5) `framework_version` = pack semver + pack commit SHA, written into the project's autopilot.json on onboarding/every sync; `sync.pack_path` config key. Expose one function `behind_pack(project) -> str|None` returning `framework vX behind pack vY — run: python <pack>/scripts/sync_from_pack.py --project .`; call it from /devteam-status and hooks/session-start.js. (The P0-digest call is TASK-028's, which consumes this function.) Validate read-only against real copies: KERYX (C:/CLAUDECODE_TOOLSETS/walkietalkie-keryx, has manifest) and oikonomos (C:/CLAUDECODE_TOOLSETS/oikonomos) with `--diverged` only — NEVER write to those repos; paste the summary into Test_Evidence.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §2 (E-0.2–E-0.5)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/sync_from_pack.py, sync-manifest.json, tests/test_sync_from_pack.py, tests/fixtures/sync/** (new), autopilot.json, .claude/commands/devteam-status.md, hooks/session-start.js
- Protected-path grants: scripts/sync_from_pack.py, autopilot.json, .claude/commands/devteam-status.md, hooks/session-start.js
- Depends_On: —

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log
