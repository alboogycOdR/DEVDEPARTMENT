# TASK-027 — Wave E E-A — hooks resolve main checkout; DEVTEAM_TASK; human-only config; Protected_Grants field

## Brief
(1) `hooks/lib.js` `mainRoot()` via `git rev-parse --git-common-dir` (fallback repoRoot()), used for PLAN.md, autopilot.json and .devteam/** incl. gateguard denials; relPath stays worktree-relative. (2) dispatch.sh/.ps1 export `DEVTEAM_TASK=<id>` beside DEVTEAM_UNIT, and `DEVTEAM_DELEGATED=1`; session-start.js and the firewall treat DEVTEAM_TASK as the active task. (3) Firewall denies writes to autopilot.json / autopilot.local.json when DEVTEAM_UNIT or DEVTEAM_DELEGATED=1 is set. (The supervisor's own headless launches export DEVTEAM_DELEGATED in TASK-029, which owns supervisor.py then.) (4) Optional-in-spec, IN SCOPE here: `**Protected_Grants:**` task field (subset of Owned_Paths, ORCH-written only; validate_plan enforces subset), read by the firewall from the main-checkout PLAN.md, auto-expiring when the task leaves claimed/in_progress/needs_review; PROTECTED_EXCEPTIONS stays for permanent project exceptions. This removes the grant-edit/merge-master ritual for the rest of Wave E. Keep the ` (new)` suffix stripping ORCH added pre-wave.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §3 (E-A.1–4)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: hooks/**, tests/test_gateguard.js, scripts/validate_plan.py, tests/test_validate_plan.py, scripts/dispatch.sh, scripts/dispatch.ps1
- Protected-path grants: hooks/**, scripts/validate_plan.py, scripts/dispatch.sh, scripts/dispatch.ps1
- Depends_On: TASK-024, TASK-025

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log
