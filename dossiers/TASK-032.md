# TASK-032 — Wave E E-D — plan archive, notes cap, generated REVIEW tallies, machine-readable REVIEW.md

## Brief
`plan_archive.py` moves done blocks older than the current wave to `plan/archive/<YYYY-MM>.md` (append-only), leaving one stub per task; validate_plan.parse_tasks treats stubs as done (every module imports that parser — check control._deps_done and instincts._deps_done see archived IDs as done). Nightly via maintenance.py when PLAN.md exceeds `maintenance.plan_archive_kb`=60. Notes cap: `plan.notes_max_chars`=4000, overflow rotates to `docs/handovers/<date>-notes.md` with a pointer (the tool writes it; builders never commit into docs/ — tests use tmp dirs), validate_plan warns over the cap. validate_plan warns when PLAN.md >150 KB. `team_stats.py --write-tallies` generates REVIEW.md's tallies block from rows. `validate_plan.py --review` rejects blank lines/broken rows inside the verdict table; team_stats flags `:00:00Z`-rounded stamps. (The review command's clock-stamped verdict time is a .claude/commands edit — TASK-042.) Do NOT run the archiver on this repo's PLAN.md; ORCH does that at wave close. Oikonomos's 11,134-line PLAN.md may be copied read-only from C:/CLAUDECODE_TOOLSETS/oikonomos/PLAN.md into a tmp dir, or use a synthetic 366-done-task fixture.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §6 (E-D)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/plan_archive.py (new), tests/test_plan_archive.py (new), scripts/team_stats.py, tests/test_team_stats.py (new), scripts/maintenance.py, tests/test_maintenance.py, scripts/validate_plan.py, tests/test_validate_plan.py, tests/fixtures/plan_archive/** (new)
- Protected-path grants: scripts/plan_archive.py, scripts/team_stats.py, scripts/maintenance.py, scripts/validate_plan.py
- Depends_On: TASK-027

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log
