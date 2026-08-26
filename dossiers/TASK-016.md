# Dossier — TASK-016 · slack_listener.py (Socket Mode, optional-dependency)

**Brief:** The command-receiving half of P1b-2 (the other half, commands.py, is TASK-013 — your own prior task, so you know its surface). A `SlackListener` class mirroring `TelegramListener`'s interface exactly — daemon thread, `queue.Queue` of validated commands, start/stop — so TASK-018 wires it symmetrically.

**Spec:** SLACK §5 (slack_listener.py), §1 (transport note), §8 (env vars). Plus the two ORCH resolutions in your task block — read them before writing a line, they change the design:

1. **Socket Mode vs Tower URLs.** The §1 manifest points slash-commands/interactions at Tower's HTTP endpoints, but Socket Mode and request URLs are mutually exclusive per Slack app — and Tower doesn't exist yet. Resolution: **Socket Mode is the pre-Tower command path**; when Tower's P1b-3 lands the app flips to request URLs and this listener becomes the no-Tower fallback. Build for that reality — don't try to satisfy the manifest's end-state wiring now.
2. **slack_sdk is the pack's FIRST optional runtime dependency.** stdlib has no WebSocket client, so Socket Mode needs it. Import-guard it: absent → the listener refuses to start with ONE warning naming `pip install slack_sdk`, and nothing else in the pack is affected. Never a hard requirement. (Alister may veto the dependency entirely at review — keep the transport isolated behind one small adapter so a re-scope is cheap.)

**Intended approach:**
- Mirror TelegramListener structurally (read tg_listener.py first — its polling loop, queue contract, and stop event are the template; §5: "the architecture does not change, only the transport").
- Socket Mode: `apps.connections.open` with `DEVTEAM_SLACK_APP_TOKEN` → WebSocket → envelope ack → payload → **enqueue the raw command-dict in TelegramListener's exact shape** (read `tg_listener.py:132` — that dict is your contract). FABLE-RATIFICATION CORRECTION (supersedes any validate-then-enqueue wording elsewhere): validation's real locus is the shared DRAIN in supervisor.py (line 916 today; TASK-018 unifies it), which validates both queues once through commands.py. Your listener drops only transport-level garbage (unparseable envelopes) — it never makes command-vocabulary judgments, or validation would run in two places with two failure modes.
- Reconnect with backoff on socket drop (Slack rotates socket URLs; treat every disconnect as routine).

**Tests:** stub the socket layer entirely — CI has no slack_sdk and no network. Explicitly test: absent-dependency path (warning + disabled), validation routing through commands.py, queue contract parity with TelegramListener.

**Territory note:** Depends on TASK-013 (merged before you start). TASK-017 (CX) runs CONCURRENTLY — your two files only; tg_listener.py is out of territory and untouched.

**Reassignment note (2026-08-26):** originally assigned to GB; reassigned to S5 before claim (GB hit its weekly rate limit, ~15:00Z reset — see orchestrator_notes). Everything above is unit-agnostic and unchanged. You just built TASK-015's slack_notify.py, so the Slack spec context (Block Kit, thread tracking, the four message designs) is fresh — this task's Socket Mode listener is the sibling module on the receiving side of what you already sent.

## Work Log
