# TASK-031 — Wave E E-K — commands through the durable inbox; source-missing once; template CONTROL = UNREPORTED

## Brief
(1) Listeners write each accepted command to `.devteam/inbox/<ts>-<update_id>.json` and only then persist the Telegram offset; supervisor drains via inbox.drain_inbox → handler → inbox.ack; remove the in-memory queue. (2) Under --once, one bounded long-poll (≤ `telegram.once_poll_seconds`=10) before the drain. (3) `SOURCE_MISSING <name> <path>` logged once per process start and once per day by _dossier_heartbeats, inbox.drain_inbox, usage_probe and the gateguard reader. (4) Template CONTROL blocks (TASK-NNN / prompt-example fields) → UNREPORTED with a provider-error hint; run log grepped for `at capacity` / `402` / `usage limit` → blocked_reason CAPACITY (Wave G registry when present; plain string until then). (5) Supervisor start warns loudly when telegram is in notify_channels but env vars are unset, and when running --once without the inbox path. Use tests/tick_harness.py for the --once process tests.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §1 H3/H6, §13 (E-K.1–5), §5 Acceptance (/resume from inbox)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/tg_listener.py, scripts/slack_listener.py, scripts/inbox.py, scripts/control.py, scripts/supervisor.py, scripts/usage_probe.py, tests/test_tg_listener.py, tests/test_slack_listener.py, tests/test_inbox.py, tests/test_control.py, tests/test_usage.py, tests/test_supervisor_once_inbox.py (new)
- Protected-path grants: scripts/tg_listener.py, scripts/slack_listener.py, scripts/inbox.py, scripts/control.py, scripts/supervisor.py, scripts/usage_probe.py
- Depends_On: TASK-030

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log
