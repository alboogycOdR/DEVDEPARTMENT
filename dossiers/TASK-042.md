# TASK-042 — Wave E E-J3 — review rules into the command file, clock-stamped verdicts, frontmatter freshness, untracked-work detector

## Brief
(4) Move the review standing rules out of the review_cmd JSON string into .claude/commands/devteam-review.md; review_cmd only points at it. The review command writes each verdict timestamp from the system clock (`date -u` / `[DateTime]::UtcNow`) and records the reviewing model. (7) `plan_health.py freshness`: flag frontmatter last_updated/overall_status/orchestrator_notes older than the newest task Updated_At or newest [TASK-…] commit; called by /devteam-status and session-start.js. (8) `plan_health.py untracked`: count base-branch commits in the last 14 days with no [TASK-NNN]/[ORCH]/[MAINT] tag → 'work outside the plan'. devteam-decompose.md: replace the claude-fable-5 model line with claude-opus-5-5 (owner decision 2026-09-26, LIVE_CHECKS).

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §12 (E-J.4, E-J.7, E-J.8), §6 (review timestamp bullet)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/plan_health.py (new), tests/test_plan_health.py (new), .claude/commands/devteam-review.md, .claude/commands/devteam-status.md, .claude/commands/devteam-decompose.md, hooks/session-start.js, autopilot.json
- Protected-path grants: scripts/plan_health.py, .claude/commands/**, hooks/session-start.js, autopilot.json
- Depends_On: TASK-040, TASK-039

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log
