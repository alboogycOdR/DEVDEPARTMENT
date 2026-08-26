# Dossier — TASK-018 · supervisor.py integration (the wave's single-owner closer)

**Brief:** The only task that edits supervisor.py, after all five modules are merged — the proven ATLAS-A5 pattern. Four wirings, each thin because the modules did the work: tower tick sync, inbox drain before decide(), Slack listener startup, and the unified command-queue drain.

**Spec:** TOWER §1 P1+P2, H1/H4/H5; SLACK §5 (listener wiring + `_drain_command_queue` rename), §9. Your task block lists all four wirings with their constraints — this dossier adds the how.

**Intended approach:**
1. **Tower tick (P1):** inside the tick, after the existing snapshot/board work, `tower_sync.sync_tick(repo, cfg)` when `tower.enabled`. Fail-open is already inside the module; your job is placement + making sure a tower exception (there should be none, but belt-and-braces) cannot kill a tick — ONE warning line max (H5). Per H4 the push and queue-pull are one round-trip pair inside the same tick.
2. **Inbox (P2):** `inbox.drain_inbox` BEFORE `decide()` (spec wording is exact), routed through the SAME action-handler path the tg queue uses — you are wiring a pass-through, not writing handlers. Honour the two-phase ack: ack each command only after its handler ran.
3. **Slack listener:** start `SlackListener` alongside `TelegramListener` when `"slack"` in notify_channels and env vars present; missing env → warning + not started (exactly the tg posture). **Telegram start logic byte-preserved** (§9).
4. **The rename (§5):** `_drain_tg_queue` → `_drain_command_queue`, draining both listeners' queues through one path. tg_listener.py is out of territory — the rename is supervisor-side only.

**Standing lessons that bind you (all earned in this repo):**
- Reuse the existing reap/notify machinery; never rebuild what exists (L2 spec §0 — a prior ORCH misreading is recorded there precisely so nobody repeats it).
- **Byte-identical-when-disabled** is the graded criterion (ATLAS A5 precedent): with tower+slack+inbox all disabled/absent, a tick must behave identically to pre-wave master — write the test that proves it, don't argue it structurally (TASK-006's rework came from arguing structurally).
- DEFAULT_CONFIG gains tower/slack keys mirroring the template ORCH committed — keep them in exact sync with autopilot.json's blocks.

**Verification:** full pytest + node suites; the disabled-path parity test; one fail-open test per wiring (tower down, inbox malformed file, slack env absent).

**Territory note:** you run ALONE (Wave C) — everything else is merged by the time you start. scripts/supervisor.py grant lives in hooks/lib.js, removed at done. Read every module's actual merged surface before wiring — do not code against this dossier's function names if the merged reality differs.

## Work Log
