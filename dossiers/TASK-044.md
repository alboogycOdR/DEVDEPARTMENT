# TASK-044 — New-project bookkeeping push policy default

## Brief
New project configuration should default plan-only bookkeeping pushes to `batch`, with a 30-minute window. Existing project configuration is human-owned: syncing must leave an absent `git.push_policy` absent, retain explicit values unchanged, and explain the opt-in choice without applying it.

## Spec pointers
- `specs/LOOP_HYGIENE_2026-09.md` §9 (E-G): new-project default `batch`; batch window defaults to 30 minutes.
- `docs/SYNC.md`: autopilot configuration is add-only/project-owned and sync should not overwrite existing project choices.

## Territory
- `scripts/sync_from_pack.py`
- `tests/test_sync_from_pack.py`
- `dossiers/TASK-044.md`

## Intended approach
Write failing fixture tests for a missing autopilot file and existing files with absent/explicit policy values. In sync's merge-special handling, initialize a missing new-project config with the pack defaults while omitting project-specific base-branch configuration and adding `git.push_policy: batch` plus `git.push_batch_minutes: 30`; for existing configs, report the option but do not add or modify either key. Run the focused tests, then both full suites.

## Work Log
- [2026-09-30T21:17:23Z] [CX] Final verification: 1202 Python tests and 47 Node tests pass; focused sync suite 71/71. New project receives batch/30 without pack base-branch leakage; existing settings remain unchanged and absent settings get an unapplied opt-in offer. Submitted for review.
- [2026-09-30T21:03:28Z] [CX] Added a dry-run regression proving a missing new-project autopilot file is not written unless `apply=True`; focused sync suite now passes 71/71. Full suites pending.
- [2026-09-30T21:02:52Z] [CX] Added three regression cases first; two failed against current behavior (missing new-project config and absent upgrade offer) while the explicit-policy preservation case passed. Implemented the new-project batch seed and read-only existing-project offer. Focused sync suite: 69 passed; no code was ported (origin SHA N/A). Full Python and Node suites remain required.
