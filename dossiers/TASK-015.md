# Dossier — TASK-015 · slack_notify.py (P1b-1: Block Kit sender + thread tracking)

**Brief:** The biggest module of the wave and the one that ships standalone: "Slack notifications with thread tracking work the moment the token is set" (SLACK §7) — before any listener, inbox, or Tower exists. Web-API sender, four Block Kit message designs, thread tracking, in-place updates, and the `slack` channel registered in notify.py's CHANNELS dict.

**Spec:** SLACK §3 (all four message designs — implement the specced fields, they are deliberate), §5 (sender behaviours: thread tracking, message updating, rate limits), §2 (channel routing rule), §9 (telegram preserved as-is), §10 (the --test smoke subcommand).

**Intended approach:**
- **Web API, not webhook** (§5 is explicit): `chat.postMessage` with `blocks=`, `chat.update` for resolved alerts, `reactions.add` for the done-✅. Plain HTTPS+JSON via stdlib urllib — no SDK needed for sending. Token from `DEVTEAM_SLACK_TOKEN` env only.
- Four builders for §3's designs: P2-BLOCKED (blocked reason + dossier tail + Approve/Rework/Answer buttons), P2-NEEDS-REVIEW (test counts, rework count), P1-STOP-THE-LINE (violations list, never muted), P0-WAVE-COMPLETE (wave stats, builder first-pass table). Buttons RENDER now; their interactive delivery is Tower-side (P1b-3) — rich notifications are the deliverable, and that is fully useful.
- Routing (§2, exact): P0 + P1 → `ops_channel`; P2/status/usage → `project_channel`; wave-complete → both.
- Thread tracking (§5): `.devteam/slack_threads.json` keyed by task_id; follow-ups post with `thread_ts`; task done → ✅ reaction; a decided alert is **updated in place**, not re-posted.
- Resilience: exponential backoff on 429; Slack unreachable → fail open to the `file` channel; never raise.
- **notify.py is in your territory** for one purpose: add `send_slack` to the CHANNELS dict with a lazy import so notify.py works untouched when slack is unconfigured. The telegram sender is byte-preserved (§9: "no code is deleted"). Extend tests/test_notify.py for the routing; its existing cases stay green.
- `--test` (§10 step 4): posts one smoke message to each configured channel, reports delivery — this is the human enable-checklist's verification step.

**Tests:** stubbed transport, no live Slack. Cover all four designs' block structure, routing rule, thread lifecycle (post → thread reply → update → reaction), 429 backoff, fail-open.

**Territory note:** TASK-013 (GB) and TASK-014 (CX) run CONCURRENTLY. autopilot.json is NOT yours (config pre-added by ORCH). scripts/** grants live in hooks/lib.js for your two script paths, removed at done.

## Work Log
