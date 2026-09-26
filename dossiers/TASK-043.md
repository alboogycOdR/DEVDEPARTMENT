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
