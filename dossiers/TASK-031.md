# TASK-031 — durable command inbox

## 2026-09-28

- Replaced Telegram's in-memory command handoff with atomic inbox envelopes.
- Telegram advances its offset only after the envelope is visible in the inbox.
- `--once` now performs one bounded Telegram poll before the durable inbox drain.
- Slack listener now requires the durable inbox repository supplied by the supervisor.

Next: convert Slack fixture assertions from queue contents to persisted inbox envelopes, then add the cross-process `--once` coverage and source-missing observability.
