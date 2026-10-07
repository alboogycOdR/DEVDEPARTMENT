# DEVDEPARTMENT — Slack as Primary Channel (P1b)

**Status:** SPEC ONLY.
**Decision (2026-08-16):** Slack replaces Telegram as the primary notification and
command channel. Telegram is demoted to a redundant fallback — kept in the codebase,
never removed, but not the primary surface. Slack is used to its full capability: rich
Block Kit messages, interactive components, slash commands, per-project channels,
thread-based conversations, and the Slack app manifest for repeatable deployment. This
is not a Telegram port; it is a ground-up Slack design.

**Fits into TOWER phasing as:** P1b — runs in parallel with P1 (snapshot push) and P2
(inbox consumer) since all three are pack-side integration points. P1b can ship before
Tower T1 exists, exactly as Telegram shipped before any dashboard.

---

## 0. Why Slack is strictly better for this workload

Telegram was chosen because it is simple and reliable — both true, and both irrelevant
to the capability ceiling. The workload is **structured, multi-project, action-gated**:
a blocked task needs a decision *with context* (what is blocked, why, what the builder
tried), an approval needs to know what it is approving (the diff summary, the test
results), and a digest needs to be skimmable across three simultaneous projects without
one alert drowning another. Telegram text-chat is exactly the wrong medium for this;
Slack Block Kit was designed for precisely this interaction shape.

Specific Slack capabilities used, each earning its place:

- **Per-project channels.** `#proj-orb`, `#proj-rwc`, `#proj-devdept` — alerts stay
  scoped; a blocked task on ORB does not interrupt your RWC focus. Cross-project
  `#devdept-ops` for P1 stop-the-line and wave digests. Telegram has one chat per bot.
- **Block Kit rich messages.** A blocked-task alert is a structured card: project badge,
  task title, assignee avatar emoji, blocked reason, context from the dossier tail,
  and — directly in the message — an `Approve` / `Send to rework` / `Answer` button row
  with a free-text input. No slash-command syntax to remember. Telegram has inline
  buttons but no structured layout and no modal.
- **Modals for free-text.** Clicking `Answer` opens a Slack modal with a proper text
  area, pre-populated with the task context, and a submit button. The answer arrives via
  the Interactions endpoint already authenticated and structured. Telegram's free-text
  reply is a raw message the bot has to parse.
- **Thread replies keep context.** The bot posts the initial alert; all follow-up
  (rework findings, second builder attempt, eventual approval) threads under it. A week
  later you can read the full history of TASK-117 from one thread. Telegram has threads
  but they are rarely used.
- **Slash commands as a first-class primitive.** `/approve TASK-117`, `/status`,
  `/wave`, `/usage` — visible in Slack's autocomplete, documented in the slash command
  config, not something you have to know or remember.
- **Home tab for live status.** Slack's App Home gives each user a private tab that
  renders a real-time status view — essentially TOWER's T1 board, available inside Slack
  itself before TOWER is built, and after TOWER is built, it stays as a "check without
  opening a browser" surface.
- **Workflow Builder hooks (no code required for some automations).** Out of scope for
  v1, noted as future capability.

---

## 1. Slack app setup (done once, not per-project)

Create one Slack app via `api.slack.com/apps`, deploying the manifest below. One app,
one bot token (`DEVTEAM_SLACK_TOKEN`), one signing secret (`DEVTEAM_SLACK_SIGNING_SECRET`).
Both stored in the supervisor's environment — never in a tracked file, same convention
as `DEVTEAM_TG_TOKEN`.

**Manifest** (create and commit as `slack-app-manifest.yaml` in the Tower repo,
not in the pack — it is a service config, not per-project):
```yaml
display_information:
  name: DEVDEPARTMENT
  description: Multi-agent dev framework mission control
  background_color: "#1a1a2e"
features:
  bot_user:
    display_name: ORCH
    always_online: true
  slash_commands:
    - command: /approve
      description: Approve a needs_review task or pending amendment
      usage_hint: "TASK-NNN [or AMEND-NNN]"
      should_escape: false
    - command: /rework
      description: Send a task back for rework
      usage_hint: "TASK-NNN <reason>"
      should_escape: false
    - command: /answer
      description: Unblock a blocked task with a decision
      usage_hint: "TASK-NNN <decision text>"
      should_escape: false
    - command: /status
      description: Task board summary for this project channel
      should_escape: false
    - command: /wave
      description: Wake the supervisor loop early
      should_escape: false
    - command: /stop
      description: Halt the supervisor loop
      should_escape: false
    - command: /resume
      description: Clear STOP and resume the loop
      should_escape: false
    - command: /digest
      description: Trigger a digest now
      should_escape: false
    - command: /mute
      description: Suppress P2 alerts for a duration
      usage_hint: "2h or 30m"
      should_escape: false
    - command: /usage
      description: Show claude/codex usage-window percentages
      should_escape: false
  app_home:
    home_tab_enabled: true
    messages_tab_enabled: false
oauth_config:
  scopes:
    bot:
      - channels:read
      - chat:write
      - chat:write.customize
      - commands
      - files:write
      - im:write
      - reactions:write
      - users:read
settings:
  interactivity:
    is_enabled: true
    request_url: https://<tower-tailnet-host>/slack/interactions
  slash_commands_request_url: https://<tower-tailnet-host>/slack/commands
  event_subscriptions:
    request_url: https://<tower-tailnet-host>/slack/events
    bot_events:
      - app_home_opened
```

The Interactions endpoint, slash commands URL, and Events URL all point at Tower.
This is right: the Slack listener lives in Tower (it is always-on on clawsrv), not in
the per-project supervisor (which sleeps when idle). Per-project commands are routed by
channel name — the bot knows which project each channel maps to.

---

## 2. Channel architecture

Channels are created **once per project** during DEVDEPARTMENT onboarding for that
project. The bot is invited to each. Channel IDs are stored in `autopilot.json`
(add-only, ships empty):

```jsonc
"slack": {
  "enabled": false,
  "project_channel": "",          // e.g. "C08XXXXX" — #proj-orb-jun-26
  "ops_channel": "",              // e.g. "C08YYYYY" — #devdept-ops (cross-project)
  "_note": "get IDs from: curl -H 'Authorization: Bearer $DEVTEAM_SLACK_TOKEN' https://slack.com/api/conversations.list"
}
```

**Rule:** P0 digests and P1 stop-the-line → `ops_channel` (the one channel you always
have open). P2 escalations (blocked task, review needed, builder stale) → `project_channel`
(scoped, doesn't bleed across projects). Usage summaries and status → `project_channel`.
Wave-complete digests → both.

---

## 3. Message designs (Block Kit — each maps to a real P0/P1/P2 event)

### P2-BLOCKED (the most important one — needs a decision from you)

```
┌─────────────────────────────────────────────────────────────┐
│ 🟠 DECISION NEEDED — ORB-JUN-26                             │
│                                                             │
│ *TASK-117* — Candle Vault: persistent per-symbol store      │
│ 👷 GB (Grok Build)  •  blocked 23 min                      │
│                                                             │
│ > SPEC_AMBIGUITY: The spec references "existing candle      │
│ > schema" but no schema file exists in Owned_Paths.         │
│ > Should I create it, or is there a shared schema I         │
│ > should import from lib/core?                              │
│                                                             │
│ Last dossier entry (8 min ago):                            │
│ > Attempted to locate schema in lib/models — not found.    │
│ > Checking lib/core/candle.dart — also absent.             │
│                                                             │
│ [✅ Approve as-is] [❌ Send to rework] [💬 Answer builder] │
└─────────────────────────────────────────────────────────────┘
```

Clicking `Answer builder` opens a **Slack modal**:
- Pre-populated task context in a read-only section block
- Free-text area: "Your answer / decision for GB:"
- Submit → hits Tower `/slack/interactions` → Tower enqueues the answer command →
  `.devteam/inbox/` → supervisor next tick → builder unblocked

No slash-command syntax to remember. Context is already there. Works from the phone.

### P2-NEEDS-REVIEW

```
┌─────────────────────────────────────────────────────────────┐
│ 👁 REVIEW REQUESTED — ORB-JUN-26                            │
│                                                             │
│ *TASK-114* — Server correctness sweep                       │
│ 👷 CX (Codex AI)  •  submitted 4 min ago                   │
│ Tests: 260 passed · Rework count: 0 (first pass)           │
│                                                             │
│ [🔍 Open in Tower] [✅ Approve] [❌ Rework]                 │
└─────────────────────────────────────────────────────────────┘
```

Follow-up events (rework verdict, second submission, merge) arrive as **thread replies**
to this message — the full task history in one thread.

### P1-STOP-THE-LINE (ops channel, always gets through — never muted)

```
┌─────────────────────────────────────────────────────────────┐
│ 🔴 STOP-THE-LINE — ORB-JUN-26                              │
│                                                             │
│ validate_plan.py: PLAN.md has 2 violation(s)               │
│ • TASK-117: Owned_Paths overlaps TASK-119 (lib/core/**)    │
│ • Missing Required_Artifacts on TASK-119                   │
│                                                             │
│ Loop halted. Fix PLAN.md before resuming.                  │
│ [▶ Resume after fix]                                        │
└─────────────────────────────────────────────────────────────┘
```

### P0-WAVE-COMPLETE (ops channel + project channel)

```
┌─────────────────────────────────────────────────────────────┐
│ 🟢 WAVE COMPLETE — ORB-JUN-26                              │
│                                                             │
│ 16/16 tasks done  •  Wave duration: 3h 42m                 │
│ (prev wave: 4h 15m  ↓ 27%)                                 │
│                                                             │
│ Builder performance this wave:                              │
│ • GB: 6 tasks  •  83% first-pass approval                  │
│ • CX: 6 tasks  •  67% first-pass approval                  │
│ • S5: 4 tasks  •  100% first-pass approval                 │
│                                                             │
│ Instincts drafted: 3 new candidates pending /approve       │
│ Claude usage: 5h window 71% · 7d window 44%               │
│                                                             │
│ [📋 View PLAN.md] [🗂 Open in Tower] [📊 Full stats]      │
└─────────────────────────────────────────────────────────────┘
```

---

## 4. The App Home tab (live status without opening Tower)

When you click ORCH's Home tab in Slack, Tower pushes a `views.publish` call rendering:

```
DEVDEPARTMENT — Mission Control
Last updated: 14:36:22

ORB-JUN-26    🟢 Wave running    GB ▶ CX ▶ S5 ▶    11/16 done
              Review queue: 1 (4 min)  •  Usage: 62% / 41%

RWC-ADMIN     🟡 Waiting on you  TASK-092 blocked 23 min
              [💬 Answer now]

DEVDEPARTMENT 🟢 Wave running    GB ▶ CX ▶           7/12 done
```

Updated on every `app_home_opened` event and on each snapshot push. Not real-time SSE
(Slack's Home tab is event-driven, not streaming) but fresh every time you open it.
Functionally a pocket version of TOWER's T1 board, available before Tower is built.

---

## 5. Pack-side changes (what moves into the DEVDEPARTMENT pack)

### `scripts/slack_notify.py` (new — replaces `scripts/notify.py`'s telegram sender)

Not a rename — a proper new module, keeping Telegram's sender alive (fail-open
fallback). Slack sender uses the **Web API** (`chat.postMessage` with `blocks=`), not
an incoming webhook — the Web API is strictly more capable (message updates, thread
replies, reactions, file uploads) and the token is already needed for slash commands
and the Home tab.

Key behaviors, each earned by a real requirement:
- **Thread tracking.** On P2 escalations, stores the `ts` (Slack message timestamp)
  in `.devteam/slack_threads.json` keyed by task_id. Subsequent events for that task
  post as `thread_ts=<original_ts>` replies. When the task reaches `done`, the thread
  gets a ✅ reaction. This is what makes the task's full history readable in one place.
- **Message updating.** When a blocked task is answered or a review is decided, the
  original alert message is *updated* (not a new message) to show the resolved state —
  `DECISION NEEDED → answered by Alister 2 min ago`. Keeps the channel clean.
- **Rate-limit aware.** Tier 3 limit (50 req/min for `chat.postMessage`) is far beyond
  any supervisor tick frequency; tier 1 limit for `views.publish` (100/min) similarly.
  Simple exponential backoff on 429; fail-open to `file` channel if Slack is
  unreachable.

### `scripts/slack_listener.py` (new — replaces `scripts/tg_listener.py`)

Runs as a daemon thread in the supervisor, exactly as `TelegramListener` does today —
the architecture does not change, only the transport.

**Transport choice: socket mode, not a public webhook.** Socket Mode keeps the Slack
WebSocket connection entirely on clawsrv's Tailscale side — no public URL required for
the per-project supervisor. (The Tower service, which is always-on on clawsrv and
*does* have a Tailscale-accessible URL, handles the slash commands and interactions
endpoints. The per-project supervisor only needs the outbound socket connection.)

Processes: slash commands forwarded from Tower → the **same shared command-validation
module** P2 specced → the existing action handlers. The slack_listener's queue is the
same `queue.Queue` already drained by `_drain_tg_queue` (renamed `_drain_command_queue`
in the shared-validation refactor).

### `scripts/commands.py` (refactor — the shared command-validation module from P2)

The rename and extraction called for in P2. `tg_commands.py`'s validation logic moves
here; `slack_listener.py` and Tower's command handler both import it. `tg_commands.py`
becomes a thin shim (its tests stay green). This is the structural fix for the
duplication that has caused three real drift incidents.

### `autopilot.json` additions (add-only, ships disabled)

```jsonc
"slack": {
  "enabled": false,
  "project_channel": "",
  "ops_channel": "",
  "thread_tracking": true
},
"notify_channels": ["console", "file"]
// Adding "slack" to notify_channels is the enable step, same as telegram was
```

---

## 6. Tower-side additions

Tower gains:
- `POST /slack/commands` — Slack slash command endpoint, verifies signing secret,
  routes by channel-to-project mapping, enqueues to the project inbox
- `POST /slack/interactions` — Block Kit interactive component endpoint (button
  clicks, modal submissions), same routing + enqueue
- `GET /slack/events` — Event subscriptions (app_home_opened → push updated Home view)
- `slack_router.py` — the routing table (channel_id → project_id), kept in Tower's
  SQLite alongside the project registry; onboarding registers the mapping

---

## 7. Increments (decompose-ready, fits into the TOWER phasing table)

| # | Where | Deliverable | Depends on |
|---|---|---|---|
| P1b-1 | pack | `slack_notify.py` + thread tracking + autopilot.json keys + tests | — |
| P1b-2 | pack | `slack_listener.py` (socket mode) + `commands.py` refactor; `tg_listener.py` becomes a thin shim | P1b-1 |
| P1b-3 | tower | `/slack/commands` + `/slack/interactions` + `/slack/events` + channel router | P1b-2, T1 |
| P1b-4 | tower | App Home tab renderer (live status without opening Tower) | P1b-3 |

P1b-1 ships independently of everything else — Slack notifications with thread tracking
work the moment the token is set, before any command handling or Tower integration.

## 8. Env vars (never in tracked files)

```
DEVTEAM_SLACK_TOKEN          xoxb-...  (Bot User OAuth Token — one per workspace)
DEVTEAM_SLACK_SIGNING_SECRET <hex>     (for verifying requests from Slack to Tower)
DEVTEAM_SLACK_APP_TOKEN      xapp-...  (for Socket Mode in the per-project supervisor)
```

`DEVTEAM_SLACK_APP_TOKEN` is the one new one vs. Telegram's two. The app token is
workspace-level, so all projects on the same workspace share one token — not per-project.

## 9. What Telegram becomes

`tg_listener.py` and `send_telegram` in `notify.py` are preserved as-is, demoted to
fallback. `notify_channels: ["slack"]` is the new default once P1b-1 lands. Projects
that set both `slack` and `telegram` in `notify_channels` get redundant delivery on P1
(stop-the-line) specifically — two independent paths for the one event class where
missing a notification has the worst consequence. Everything else is Slack-only.

No code is deleted. Telegram tests stay green. The feature simply stops being the
primary surface.

## 10. Live verification required (same discipline as S5B and ATLAS enable)

Before enabling on a real project, a human must:
1. Create the Slack app from the manifest
2. Invite the bot to both channels
3. Set all three env vars in the supervisor's shell / PM2 config
4. Run `python scripts/slack_notify.py --test` (a smoke-test subcommand that posts one
   message to each channel and confirms delivery)
5. Set `slack.enabled: true` and add `"slack"` to `notify_channels`

The `slack.enabled: false` default in the pack template is asserted by
`TestPackTemplateShipsSafeDefaults` — same test class that caught the ATLAS default
leak. No new test needed; the class already covers new ask-don't-auto-flip keys.
