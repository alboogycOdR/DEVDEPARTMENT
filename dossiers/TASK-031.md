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
