# DEPLOY_CLAWSRV — Always-On Supervisor Deployment (Wave B, v4.2)

Runs the autopilot supervisor always-on on Alister's Ubuntu 24.04 VPS
(`clawsrv`, PM2, Tailscale) instead of requiring a laptop to stay open and
awake. Two topologies; **T1 is what this wave builds and this guide sets up
end-to-end. T2 is documented as a future path only — do not attempt it from
this guide alone.**

## Topologies

### T1 — Watchtower (build this)

clawsrv runs the supervisor in a **monitoring profile**: the Telegram
listener, board publisher, maintenance scheduler, and escalation watchdog all
run continuously on clawsrv, reading and writing the project repo over git.

**Dispatch and review commands still execute wherever the builder CLIs
(`grok`, `codex`, `claude`) are authenticated** — today that's almost
certainly your laptop, not clawsrv. This is deliberate: T1 doesn't require
installing/authenticating three separate agentic CLIs on a headless VPS, it
just moves the *always-on watching* part off your laptop.

Consequence: when the supervisor on clawsrv decides a DISPATCH is due, it
still tries to run `dispatch_cmd` — which shells out to `grok`/`codex` — on
clawsrv itself. If those CLIs aren't installed/authenticated there (the T1
default), that shell-out fails. **This is expected in T1 and is handled
gracefully**: Wave B added exit-code checking to the DISPATCH and
REDISPATCH_STALE execute() branches specifically for this — a nonzero exit
fires a **P2 escalation** ("builder CLI may be unreachable from this host")
instead of failing silently or crashing the loop. You'll get a Telegram
notification telling you to either dispatch manually from the laptop, or
consider T2.

In practice, most people running T1 will do one of:
- Keep dispatching manually from the laptop (`bash scripts/dispatch.sh grok`)
  while clawsrv handles monitoring/escalation/Telegram/maintenance — the
  laptop only needs to be open when there's active dispatch work, not 24/7.
- Set `dispatch_cmd` in `autopilot.json` to something that reaches the laptop
  remotely (e.g. an SSH command back to the laptop, or a webhook) — outside
  the scope of this wave, but the P2 escalation above is exactly the signal
  you'd wire that automation off of.

### T2 — Full brain (design only — do NOT build yet)

Builder CLIs (`grok`, `codex`, `claude`) installed and authenticated
**directly on clawsrv**, so dispatch/review run headlessly on the same host
as the monitoring profile — no laptop dependency at all, ever.

**This wave does not implement T2.** It's documented here purely as the
"future path" the spec calls for. Before attempting it yourself, you'd need
to verify (and this list is exactly what's still unverified as of v4.2):

1. Can `grok --always-approve --permission-mode bypassPermissions` and
   `codex exec --model gpt-5.6-sol --reasoning-effort medium -s
   danger-full-access` actually authenticate and run **headless, with no
   interactive terminal**, on a Linux VPS? (Today's `dispatch.sh` assumes an
   interactive-capable shell; some CLI auth flows require a browser-based
   OAuth step that a headless server can't complete on its own.)
2. Does `claude -p ... --dangerously-skip-permissions` on clawsrv have the
   same effective permissions/model access as on the laptop, including
   `claude-opus-4-8` for review/triage and `claude-sonnet-5` for the S5
   builder? (Model discipline table, CLAUDE.md.)
3. Storage/CPU headroom on clawsrv for three concurrent agentic CLI
   sessions plus the supervisor's own monitoring profile — not evaluated.
4. Secrets management for THREE sets of CLI credentials living on a
   server instead of a laptop — needs its own hardening pass (this wave's
   security model, `docs/TELEGRAM.md` § Security, only covers the Telegram
   token/chat allowlist).

When you're ready to pursue T2, treat items 1–4 as their own spec.

## Prerequisites

- clawsrv: Ubuntu 24.04, Node.js + npm (for PM2), Python 3.10+, git, Tailscale
  already configured (per your existing BASILEIA infrastructure).
- The project repo cloned on clawsrv, with a remote clawsrv can push to (so
  Telegram-driven and maintenance-driven PLAN.md commits actually land
  somewhere the laptop can pull from).
- PM2 installed globally: `npm install -g pm2`

## Setup

1. **Clone the repo on clawsrv** (if not already):
   ```bash
   git clone <your-remote> /path/to/project
   cd /path/to/project
   ```

2. **Set the project name in the PM2 config.** Edit
   `deploy/ecosystem.config.js` and replace `<PROJECT_PLACEHOLDER>` with your
   actual project name (e.g. `orb-terminal`) in the `name:` field. This
   matters if you ever run the supervisor for more than one
   DEVDEPARTMENT-managed project on the same clawsrv — each needs a unique
   PM2 process name.

3. **Set credentials via PM2, never in a tracked file** (same convention as
   everywhere else in this codebase — see `docs/TELEGRAM.md` § Security):
   ```bash
   pm2 set devteam-<project>:DEVTEAM_TG_TOKEN "123456789:AAF..."
   pm2 set devteam-<project>:DEVTEAM_TG_CHAT "987654321"
   ```
   Or export them in your shell before `pm2 start` if you prefer an env file
   sourced outside PM2's own config store — either way, `deploy/ecosystem.
   config.js` itself must never contain a literal token.

4. **Review `autopilot.json`** on clawsrv, especially:
   - `notify_channels` — include `"telegram"` if you want two-way command &
     control from clawsrv (see `docs/TELEGRAM.md`).
   - `maintenance.hour_utc` — when the nightly self-audit runs. Default `2`
     (02:00 UTC). Convert to your local time when picking a value — e.g.
     SAST (UTC+2) 04:00 local = `hour_utc: 2`.
   - `board.mode` — `"local"` writes `board/` on clawsrv itself; pair with
     the Tailscale-serve pattern below to view it from your phone/laptop.
   - `dispatch_cmd` — leave the T1 defaults unless you've built your own
     remote-dispatch bridge (see T1 note above).

5. **Start it:**
   ```bash
   pm2 start deploy/ecosystem.config.js
   pm2 save          # persist the process list across reboots
   pm2 startup       # follow the printed instructions to enable boot-start
   ```

6. **Verify:**
   ```bash
   pm2 status                        # devteam-<project> should show "online"
   pm2 logs devteam-<project>        # tail stdout/stderr
   ```
   You should see the same `[supervisor] Autopilot L2 ...` startup line you'd
   see running it manually, plus (if Telegram is enabled)
   `[supervisor] Telegram listener started ...`.

7. **Survive a restart** (exit criterion for this wave):
   ```bash
   pm2 restart devteam-<project>
   pm2 status   # should return to "online" within a few seconds
   ```
   `autorestart: true` + `max_restarts: 10` + `min_uptime: 30s` in
   `ecosystem.config.js` means PM2 will also auto-recover from a crash, not
   just a manual restart — with a floor against a fast-crash loop burning
   through all 10 restarts in seconds.

## Exposing the Mission Control board over Tailscale

If `board.mode` is `"local"`, the board JSON + `index.html` land in
`board/` on clawsrv. Serve that directory and expose it via `tailscale
serve`:

```bash
# from the project root on clawsrv, in a separate PM2 process (or any
# static file server you prefer):
pm2 start "python3 -m http.server 8080 --directory board" --name devteam-board-<project>

# expose it on your tailnet at https://<clawsrv-hostname>/board
tailscale serve --bg --set-path /board http://127.0.0.1:8080
```

Then set `autopilot.json → board.url` to that Tailscale URL so `/board` (via
Telegram) replies with something you can actually open from your phone.

## Log access

- `pm2 logs devteam-<project>` — live tail, same output as running it
  in a terminal manually.
- `pm2 logs devteam-<project> --lines 200 --nostream` — last 200 lines,
  non-streaming (good for a quick check without tailing forever).
- `AUTOPILOT_LOG.md` in the repo root — the append-only structured audit
  trail (every tick decision, every `[TG]`/`[MAINT]` command, every mute)
  independent of PM2's own log rotation.

## What this wave adds to the supervisor for clawsrv specifically

- **Nightly self-audit** (`scripts/maintenance.py`) — runs once per
  configured UTC hour, files a `TASK-MAINT-<date>` block on any failure,
  never needs a human watching for it to fire.
- **Dispatch budget ceiling** (`scripts/budget.py`) — caps dispatches per
  hour and supports `quiet_hours`, so an unattended overnight loop can't run
  away and burn through builder-CLI usage while nobody's watching.
- **Unreachable-builder P2 escalation** — see the T1 section above; this is
  specifically what makes T1 safe to run on a host that doesn't have the
  builder CLIs installed.

## Troubleshooting

- **Process keeps restarting.** `pm2 logs devteam-<project> --err` — almost
  always a missing `PLAN.md` (wrong `cwd`) or a Python import error (wrong
  `python3` on PATH — check `which python3` matches what PM2's using).
- **Board never updates.** Check `board.enabled` in `autopilot.json` and
  that the `board/` directory is writable by the user PM2 runs as.
  `publish_throttled()` also enforces `board.min_interval_seconds` — don't
  expect it to update faster than that even when working correctly.
- **Nightly audit never seems to run.** `python3 scripts/maintenance.py
  --repo . --check-only` prints exactly why (hour not reached yet, or
  already ran today) without touching anything.
- **Dispatch always fails with a P2 escalation.** Expected in a pure T1
  setup where the builder CLIs aren't on clawsrv — see the T1 section. This
  is the system correctly telling you dispatch needs to happen from wherever
  `grok`/`codex` are actually authenticated.
