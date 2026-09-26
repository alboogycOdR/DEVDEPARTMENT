# TASK-029 — Wave E E-B2 — escalation ledger (H1), triage ledger (H2), one judgment-prompt form

## Brief
Escalation ledger: key `kind|task_id|reason_prefix|digit-masked detail` (masking per a14f8976); send on first sight or change; re-send after `escalation.renotify_hours` (4 for P2, 1 for P1); otherwise `ESCALATION_HELD` once per hold period; HALT from a STOP file logs once per STOP-file mtime; clear key when the condition goes. Triage ledger `triage_counts[task_id][reason_prefix]` incremented in the executor for EVERY reason, logged detail shows real attempt number, ceiling `max_triage_attempts` (1; OWNERSHIP_CONFLICT 1; MISSING_DEPENDENCY 2), then one P2 via the escalation ledger; resets when the task leaves blocked. BEFORE coding the triage fix, investigate oikonomos SB-8 ('attempt 1 forever'): state loss between --once processes vs the stale_resets mix-up — record the finding in the dossier; the regression test reproduces whichever it was. One `judgment_prompt(command, args)` helper renders the explicit 'Read .claude/commands/<cmd>.md and execute …' form for review, REVIEW_TG and triage; no -p argument starts with `/` (LIVE_CHECKS: Git Bash rewrites a leading slash into a Windows path). Fix DEFAULT_CONFIG review_cmd and its stale comment; every supervisor headless launch exports `DEVTEAM_DELEGATED=1`.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §1 H1/H2, §4 (E-B.3–E-B.5), docs/reviews/LIVE_CHECKS_2026-09.md (slash row)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/supervisor.py, scripts/status_digest.py, tests/test_supervisor.py, tests/test_supervisor_ledgers.py, tests/test_supervisor_telegram.py, autopilot.json
- Protected-path grants: scripts/supervisor.py, scripts/status_digest.py, autopilot.json
- Depends_On: TASK-028

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log
