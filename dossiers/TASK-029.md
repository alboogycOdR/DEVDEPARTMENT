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

- [2026-09-27T16:00:00Z] [CX] Preflight: `scripts/supervisor.py` (1704 lines, 87862 bytes), `scripts/status_digest.py` (184 lines, 7100 bytes), `tests/test_supervisor.py` (820 lines, 37416 bytes), `tests/test_supervisor_ledgers.py` (134 lines, 6302 bytes), `tests/test_supervisor_telegram.py` (311 lines, 13824 bytes), and `autopilot.json` (146 lines, 5403 bytes) all exist and are within TASK-029 territory.
- [2026-09-27T16:00:00Z] [CX] SB-8 root cause: `decide()` uses `state.stale_resets` for `TOOLING_FAILURE`, but `execute(TRIAGE_UNBLOCK)` does not increment that state (it only increments `conflict_counts` or `triage_counts` for a text-specific MISSING_DEPENDENCY path). Consequently a durable state file survives a `--once` boundary but still has no TOOLING_FAILURE attempt count, so every new process chooses and logs attempt 1. This is the stale_resets mix-up, not state loss. The regression will drive two ticks through one persisted RuntimeState and require exactly one triage followed by a P2.
- [2026-09-27T19:16:25Z] [CX] Verification complete. `python -m pytest -q` passed 1081 tests in 197.54s and `node hooks/run-tests.js` passed 47 tests. The final compatibility correction keeps `DEVTEAM_DELEGATED=1` child-scoped through the shell command, avoiding a parent-environment race and supporting existing minimal Popen test doubles.
