# Wave E exit scenario

Run from the task checkout:

```text
python -m pytest -q -s tests/test_wave_e_exit.py
```

The module fixture uses `tests/tick_harness.py` to create an isolated Git
project and launch ten separate `--once` processes, then drives the real
supervisor `main()` in `--loop` for 145 five-minute ticks (12 simulated hours).
The driver replaces wall-clock waiting and external model/notification
transports. It does not replace the decision engine, state persistence,
review/escalation ledgers, command handler, inbox acknowledgement, or push
policy. A real temporary bare Git remote receives the pushes.

The plan includes an unreviewable submission, a task at the rework ceiling,
and three SPEC_AMBIGUITY tasks. A Telegram listener receives one fake `/answer`
update at simulated hour six; supervisor consumes it through the durable
inbox. Each pytest temporary scenario directory retains `once-N.log`,
`loop.log`, `evidence.json`, and `project/events.jsonl`.

## Current acceptance conflict

On integration base `8a21e4a`, the five assertions produce four passes and
one failure. Evidence: 10 processes, 145 loop ticks, 1 review, 3 distinct P2
conditions, 1 distinct P1 condition sent **13 times**, 1 answer application,
2,509-byte PLAN, and 1 successful local push.

`LOOP_HYGIENE_2026-09.md` section 4 explicitly specifies hourly P1 reminders;
section 15 permits at most one repeat over the exit scenario. The fixture
keeps the production default and asserts the section 15 bound, exposing
this inconsistency rather than changing the timer to make the test pass.
ORCH must decide whether section 15 uses an explicit longer timer, or the
production policy requires a reminder ceiling. Production changes would
need ownership of `scripts/supervisor.py` and its matching tests.

The scenario takes about three minutes on the Windows development host and
is marked `slow`; it remains in normal pytest collection. The shared pytest
configuration can register that marker if strict marker validation is added.
