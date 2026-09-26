# TASK-024 — Wave E E-0b — port oikonomos bceb8eb2, d3f5fc08, 7baeedf3, 3e8c3e79 (base-tip branch, portable tests, tick runner, env scrub)

## Brief
Port four oikonomos commits (read-only source: `git -C C:/CLAUDECODE_TOOLSETS/oikonomos show <sha>`, pulled 2026-09-26; never write to that repo), one pack commit per origin SHA, each citing it. (1) bceb8eb2: dispatch.ps1 creates `task/<id>-<suffix>` from the base tip on a fresh claim — port to dispatch.ps1 AND mirror in dispatch.sh. (2) d3f5fc08: conftest bash redirect, CR stripping, cygpath handling; the hardened builder agent (.claude/agents/devteam-builder.md); the dispatch.sh and test_validate_plan.py portability edits. Its PLAN.md/REVIEW.md hunks are oikonomos state — not ported. Its 1-line tests/test_sync_from_pack.py hunk is NOT yours (TASK-025 owns that file and ports it). (3) 7baeedf3 + its origin 2484988: `scripts/autopilot-tick.ps1` does not exist in the pack — port the whole file as of 7baeedf3, location-independent, stripped of oikonomos names/roster. (4) 3e8c3e79 modified oikonomos's project-specific `test-isolated.ps1` (Postgres test DB — NOT ported; that is Wave G-C territory). Port only the portable idea: `scripts/test_env_scrub.py` removes operator env vars matching a configurable pattern (default covering DEVTEAM_*, TELEGRAM_*, SLACK_*, ANTHROPIC_*, OPENAI_*, GEMINI_*) from the test process, with the liveness check (fail if any match survives), wired into tests/conftest.py as a session fixture.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §0, §2 (E-0.1)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/dispatch.ps1, scripts/dispatch.sh, tests/conftest.py, tests/test_validate_plan.py, tests/test_dispatch_worktree.py, .claude/agents/devteam-builder.md, scripts/autopilot-tick.ps1 (new), scripts/test_env_scrub.py (new), tests/test_test_env_scrub.py (new)
- Protected-path grants: scripts/dispatch.ps1, scripts/dispatch.sh, .claude/agents/devteam-builder.md, scripts/autopilot-tick.ps1, scripts/test_env_scrub.py
- Depends_On: —

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log
