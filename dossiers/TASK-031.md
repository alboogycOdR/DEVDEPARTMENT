# TASK-031 — durable command inbox

## 2026-09-28

- Replaced Telegram's in-memory command handoff with atomic inbox envelopes.
- Telegram advances its offset only after the envelope is visible in the inbox.
- `--once` now performs one bounded Telegram poll before the durable inbox drain.
- Slack listener now requires the durable inbox repository supplied by the supervisor.

Next: convert Slack fixture assertions from queue contents to persisted inbox envelopes, then add the cross-process `--once` coverage and source-missing observability.

## 2026-09-28 — completion pass

- Added explicit `SOURCE_MISSING` reporting for the inbox, dossier heartbeat,
  usage cache, and gateguard readers.  A long-running process reports again
  after a UTC-day change; a fresh `--once` process reports its startup source
  state without treating it as an empty source.
- Added process-boundary tests proving `/resume` is consumed exactly once
  across two `--once` invocations and that a simulated death before `ack()`
  leaves the envelope for the next drain.

## Rework (2026-09-29)

Addressed all seven review findings:
1. Regression fix: `_start_slack_listener(repo, cfg)` broke 3 tests in
   tests/test_supervisor.py — updated call sites + `_FakeSlackListener`.
2. Offset-not-advanced-on-persist-failure — regression test added.
3. Supervisor's own `--once` poll path — exercised via a real subprocess
   against a local fake Telegram HTTP server (new `DEVTEAM_TG_API_ROOT`
   test seam in tg_listener.py), proving exactly-once execution across two
   `--once` processes and a bounded `once_poll_seconds` timeout.
4. Process-killed-before-offset-save — regression test: two durable
   envelopes for one command id still execute exactly once via drain_inbox.
5. SOURCE_MISSING coverage for dossiers, gateguard and usage_probe.load_cache,
   each proven once-per-process and again-after-a-day-boundary.
6. E-K.5: `--once` with no `.devteam/inbox/` yet now warns loudly.
7. E-K.4: capacity-flagged UNREPORTED now sets `Blocked_Reason: CAPACITY`
   (Status untouched); the `' 402'` match replaced with HTTP/payment-required/
   status-code-context patterns.

Verification: focused suite (test_tg_listener/test_slack_listener/test_inbox/
test_control/test_usage/test_supervisor_once_inbox/test_supervisor) → 218
passed. After merging master (twice — the second time to pick up a manifest
correction): full suite `python -m pytest -q` → 1118 passed in 230.96s;
`node hooks/run-tests.js` → 47 passed, 0 failed.
