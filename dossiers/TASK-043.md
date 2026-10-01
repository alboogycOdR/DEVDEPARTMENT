# TASK-043 — Wave E exit — scripted exit-criteria scenario (10 × --once then accelerated 12 h --loop)

## Brief
Build the §15 scenario on a fixture project using tests/tick_harness.py: 10 scheduled --once processes, then a 12 h --loop on an advanced clock, with one unreviewable task, three SPEC_AMBIGUITY tasks, one frozen task, and one Telegram /answer injected mid-run. Assert every §15 bound and print a one-screen evidence table ORCH pastes into the wave-close handover. Marked slow if >60 s; still part of the full suite.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §15, §16
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: tests/test_wave_e_exit.py (new), tests/fixtures/wave_e_exit/** (new)
- Protected-path grants: —
- Depends_On: TASK-031, TASK-032, TASK-036, TASK-041

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log

- Implemented `tests/test_wave_e_exit.py` and a test-only clock/transport
  adapter in `tests/fixtures/wave_e_exit/supervisor.py`. The actual supervisor
  entry point, ledgers, durable inbox and Git push policy run throughout.
- `python -m pytest -q -s tests/test_wave_e_exit.py`: **4 passed, 1 failed**
  in 177.01 seconds, exit 1. The single failure is
  `test_escalation_conditions_and_reminder_ceiling`: 13 P1 sends exceed 2.
- Other evidence: 10 separate processes, 145 loop ticks, 1 review launch,
  3 distinct P2 conditions, 1 P1 condition, exactly 1 `/answer` application,
  2,509-byte PLAN, and 1 successful push to a temporary bare remote.
- Blocker: spec section 4 requires hourly P1 reminders; section 15 allows
  only one repeat over 12 hours. ORCH must clarify whether to configure a
  longer timer in the exit fixture or grant production reminder-policy work.
  No production files were changed and the failing assertion is retained.

- ORCH resolved the ambiguity in spec v2.2: production timer re-sends are
  capped by `escalation.max_timer_resends` (default 1), including parked-loop
  frozen-task P1 reminders; the one-hour interval remains unchanged. Added a
  durable per-key resend counter to `RuntimeState`, applied the cap through
  both normal and parked escalation paths, and added regression coverage for
  persistence, one resend over 12 simulated hours, changed-key reset, and the
  parked path. TASK-043 changes committed as `06b8517` and task branch synced
  with current `master`.
- Verification so far: `python -m pytest -q tests/test_supervisor_ledgers.py`
  → 13 passed; `python -m pytest -q -s tests/test_wave_e_exit.py` → 5 passed
  (scenario reports 10 once processes, 145 loop ticks, three P2s, one P1,
  max 2 sends per condition, one answer, 2,509-byte PLAN, one push);
  `node hooks/run-tests.js` → 47 passed. Full Python suite is running.
