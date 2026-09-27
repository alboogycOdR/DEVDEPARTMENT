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

- [2026-09-26T15:10:00Z] [GB] Implemented E-A.1–4 on task/TASK-027-gb. `mainRoot()` (`git rev-parse --git-common-dir`, fallback `repoRoot()`) is what PLAN.md, autopilot.json, and `.devteam/**` resolve through; `relPath` stays worktree-relative. Dispatch exports `DEVTEAM_TASK` (strict claim, or a unique legacy candidate; left unset when two tasks are already active so the first PLAN block is not pinned) and `DEVTEAM_DELEGATED=1`. Firewall and session-start treat `DEVTEAM_TASK` as the only active task. autopilot.json / autopilot.local.json writes are denied when `DEVTEAM_UNIT` is set or `DEVTEAM_DELEGATED=1`. `Protected_Grants` must be a subset of `Owned_Paths` (`(new)` stripped); the firewall applies grants only while the task is claimed/in_progress/needs_review. `PROTECTED_EXCEPTIONS` is unchanged. Nothing from an origin SHA — this task is not a port.
- [2026-09-27T14:13:23Z] [GB] Rework on task/TASK-027-gb: firewall now applies a PLAN.md grant only when `grantWithinOwned` (port of `validate_plan.grant_within_owned`) says it sits inside that task's Owned_Paths. Legacy-mode builder Edit/Write/MultiEdit of PLAN.md that adds or changes `Protected_Grants` or `Owned_Paths` is denied with an ORCH-only message; ORCH and interactive sessions are unchanged. `mainRoot()` is memoized per process per repoRoot(). Tests: builder self-grant denied; in-Owned_Paths grant allows the write; out-of-Owned_Paths grant ignored; ORCH/interactive field edits allowed. Full suites green.
