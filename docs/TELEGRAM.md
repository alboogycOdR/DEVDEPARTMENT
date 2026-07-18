# TELEGRAM — Two-Way Command & Control (Wave A-remainder, v4.1)

Completes Pillar 2 of the v3 design: every escalation the supervisor can
raise becomes answerable from your phone, with a full audit trail and no way
to bypass the protocol's authority model. Outbound alerts already existed
(`notify.py`'s telegram channel); this wave adds the inbound half — a
long-polling listener that turns `/answer`, `/approve`, `/rework`, `/stop`
and friends into real PLAN.md edits and supervisor actions.

## Setup

1. **Create a bot.** Message [@BotFather](https://t.me/BotFather) on
   Telegram, `/newbot`, follow the prompts. You'll get a token that looks
   like `123456789:AAF...`.
2. **Find your chat ID.** Message your new bot anything, then visit
   `https://api.telegram.org/bot<TOKEN>/getUpdates` in a browser — your
   `chat.id` is in the JSON response.
3. **Set environment variables** (never in a tracked file — this is the
   same convention `notify.py` already uses for the outbound channel):
   ```bash
   export DEVTEAM_TG_TOKEN="123456789:AAF..."
   export DEVTEAM_TG_CHAT="987654321"
   ```
   On the PM2 deployment (Wave B), put these in the PM2 env file, never in
   `ecosystem.config.js` itself.
4. **Enable the channel** in `autopilot.json`:
   ```jsonc
   "notify_channels": ["console", "file", "telegram"]
   ```
   The listener thread only starts if `"telegram"` is in `notify_channels`
   AND both env vars are set. If the channel is enabled but the env vars are
   missing, the supervisor logs a warning and runs normally without the
   listener — this is a deliberate degrade, not a crash.
5. **(Optional) Multiple operators.** Add chat IDs to
   `autopilot.json → telegram.chat_allowlist`. `DEVTEAM_TG_CHAT` is always
   additionally allowed even if you forget to include it in the list, so you
   can never lock yourself out by editing this array.
6. **(Optional) `/board` URL.** Set `autopilot.json → board.url` to wherever
   your Mission Control board is actually served (e.g. a Tailscale-served
   local URL, or a gh-pages URL) so `/board` has something to reply with.
7. **Run it.** `python scripts/supervisor.py --loop` (or via PM2, Wave B).
   You should see `[supervisor] Telegram listener started ...` in the log.

## Command reference

| Command | Args | Effect |
|---|---|---|
| `/status` | none | Compact task-board summary (burndown %, per-column task IDs, STOP status) |
| `/board` | none | Replies with the URL from `autopilot.json → board.url` |
| `/answer` | `TASK-NNN <free text>` | Appends a `[TG-DECISION]` Progress_Note. If the task was `blocked`, flips it to `pending` and clears `Blocked_Reason`. Commits `[TG]`. |
| `/approve` | `TASK-NNN` | Runs a headless ORCH review session scoped to exactly that task (`/devteam-review TASK-NNN` on `claude-sonnet-5`, per the model discipline table) |
| `/rework` | `TASK-NNN <reason>` | Appends a `[TG-REWORK]` Review_Finding; flips Status to `in_progress`. Commits `[TG]`. |
| `/stop` | none | Creates the `STOP` file in repo root — the same kill switch `/devteam-autopilot` and the supervisor already respect. Works even if PLAN.md is corrupted or every other subsystem is broken. |
| `/resume` | none | Deletes `STOP` if present |
| `/wave` | none | Wakes the tick loop immediately instead of waiting out the rest of its sleep interval |
| `/digest` | none | Sends a P0 digest on demand (burndown, open escalations, team stats) — same channel as the automatic wave-complete digest |
| `/mute` | `<duration, e.g. 2h, 30m>` | Suppresses P0/P2 notifications until expiry. **P1 can never be muted** — it's a safety rail, not a preference. |
| anything else | — | Replies with a one-line help/usage message. Nothing is executed. |

Every reply is sent back to the chat that issued the command, so you get
instant confirmation on your phone that the command landed (or, honestly,
that it failed and why).

## Security model

1. **Token source.** `DEVTEAM_TG_TOKEN` env var only. Never read from any
   tracked file, matching `notify.py`'s existing convention.
2. **Allowlist.** Only `DEVTEAM_TG_CHAT` (plus `chat_allowlist` for multiple
   operators) may issue commands. Anyone else is **silently dropped** — no
   reply is sent, so the bot's existence is never confirmed to a stranger.
   The attempt is still logged (`tg_listener` log, not PLAN.md) so you can
   see if someone's poking at your bot.
3. **Free text containment.** The only place raw user text ever lands is
   the argument of `/answer` and `/rework`, and it is written into PLAN.md
   strictly as inert data — a single Markdown bullet line. It is never
   `eval`'d, never shelled out to, never opened as a path. Embedded newlines
   are collapsed to spaces specifically so a message can never forge a fake
   `### TASK-NNN` header or `**Field:**` line by landing at the start of a
   line inside PLAN.md. See `tg_commands._sanitize_free_text()` for exactly
   how this is enforced, and `tests/test_tg_commands.py::TestFreeTextInjectionSafety`
   for the adversarial test cases (shell metacharacters, path traversal,
   forged headers/fields, control characters, oversized payloads).
4. **Full audit trail.** Every accepted command produces THREE independent
   provenance markers:
   - an `AUTOPILOT_LOG.md` line: `TG_COMMAND unit=TG cmd=/answer task=TASK-016`
   - a git commit message tag `[TG]`, symmetrical with `[ORCH]`/`[GB]`/`[CX]`
   - a `[TG-DECISION]` / `[TG-REWORK]` prefix on the PLAN.md bullet itself

   **Design note on `Updated_By`.** The `Updated_By` field on a
   Telegram-edited task is set to `ORCH`, not `TG`. `validate_plan.py`'s
   `VALID_UNITS` vocabulary (`ORCH`/`GB`/`CX`) is a load-bearing protocol
   invariant this wave does not touch — Telegram is a remote *channel* for
   human/ORCH-level decisions, not a fourth autonomous unit that writes
   code. Nothing about who-really-typed-this is lost; it's just carried by
   the three markers above instead of by `Updated_By`.
5. **Micro-transactions.** Every `/answer`/`/rework` edit is: `git pull` →
   parse → locate the single target task's block → edit ONLY that block's
   `Progress_Notes`/`Status`/`Review_Findings`/`Blocked_Reason` fields →
   `git commit` → `git push`. Every other byte of PLAN.md — every other
   task's block — is preserved verbatim. This is the same territorial
   isolation discipline builders already follow, applied to a single remote
   writer instead of two local ones.
6. **`/stop` is unbreakable.** It never reads or parses PLAN.md — it just
   writes the `STOP` file. It works even if PLAN.md is corrupted, git is
   broken, or every other subsystem has failed. Each queued command is
   handled in its own try/except in the drain loop specifically so a bad
   `/answer` can never block a `/stop` queued right behind it.

## Architecture

```
Telegram servers
      │  long-poll getUpdates (25s timeout)
      ▼
tg_listener.TelegramListener (daemon thread, started from supervisor.py)
      │  allowlist check → parse_command() → validated {cmd,args,chat_id}
      ▼
queue.Queue  ───────────────────────────────────────────────┐
      │                                                      │
      ▼ (main thread, once per tick, BEFORE decide())        │
supervisor.drain_tg_queue()                                  │
      │  /stop /resume /wave /mute /digest /status /board    │
      │  → handled directly, replies sent                    │
      │  /answer /rework → git pull → edit ONE task block     │
      │                     → git commit+push [TG] → reply    │
      │  /approve → Action("REVIEW_TG", task_id=...)  ────────┘
      ▼
supervisor.execute()  (REVIEW_TG scoped to claude-sonnet-5, same as REVIEW)
```

The listener thread never touches PLAN.md or git — mutation happens only on
the main thread, inside `drain_tg_queue()`, so there is exactly one writer to
the repo at any moment. This mirrors the single-writer discipline the
territory firewall enforces for GB/CX, just applied across threads instead
of processes.

The offset (`last processed update_id + 1`) is persisted to
`.devteam/tg_offset.txt` after every update, so a supervisor restart never
replays an old `/stop` or `/approve`.

## Testing

```bash
python -m pytest tests/test_tg_commands.py tests/test_tg_listener.py \
                  tests/test_supervisor_telegram.py tests/test_notify.py -q
```

All network and git operations in these suites are either injected (the
listener's `fetch` parameter) or run against a real throwaway `git init`
temp repo — nothing hits the real Telegram API or a real remote.

## Troubleshooting

- **Listener doesn't start.** Check `[supervisor]` stdout at startup — it
  logs exactly why (channel not enabled, or an env var missing).
- **Commands silently ignored.** Almost always the allowlist. Double check
  `DEVTEAM_TG_CHAT` matches the `chat.id` from `getUpdates`, not your
  username.
- **`/answer` replies with a git warning.** The edit still landed in
  PLAN.md locally — only the commit/push failed (no git repo, no remote
  configured, or a genuine push conflict). Check the repo on the host
  running the supervisor.
- **Bot never replies to anyone, ever.** That's correct behavior for chats
  not on the allowlist — silence is the security feature, not a bug.
