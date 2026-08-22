# TOWER — Cross-Project Mission Control. Build Specification

**Status:** SPEC ONLY — nothing here is built.
**Baseline:** pack @ `4071a83`. Suites green at time of writing.
**Decisions locked with Alister (2026-08-16):** **Slack** is the primary interrupt and command channel (see `specs/DEVDEPARTMENT_SLACK_SPEC.md`); Tower is the *pull* surface (where you go to look). Telegram is demoted to a redundant fallback — kept, never primary. Tower runs on **clawsrv** (PM2, Tailscale-only). Tower is **its own repo** (`tower`), deployed once; the DEVDEPARTMENT pack gains exactly **two integration points** and nothing else. Workshop view confirmed as T2.5. Flutter companion app confirmed as T5, built *by* DEVDEPARTMENT as its own onboarded project once Tower's API is stable.

**What TOWER answers, in one line:** *"Across every project I run — what is executing, what is waiting on me, and where is it stalling?"* — currently answerable only by opening N sessions one at a time.

---

## 0. Hard constraints (violations are automatic rework)

**H1 — Tower is never a second writer.** No Tower code path may modify PLAN.md, task branches, worktrees, or any project state directly. All mutations flow: Tower `/act` → command file in the project's `.devteam/inbox/` → consumed by the supervisor on its next tick, through the **same handler path `commands.py` (the shared validator, refactored in P1b-2) exposes**. Everything this framework's reliability rests on (single-writer discipline, plan_guard, the review gate) assumes mutations flow through the supervisor; a dashboard that writes directly would be a bypass dressed as a feature. Corollary, stated for honesty: **actions have tick latency** (≤ `interval_seconds`). That is the correct trade and the UI must present it truthfully — a clicked action shows as `queued` until a subsequent snapshot confirms the state change, never optimistically as done.

**H2 — The board renders only true state** (ATLAS R1's sibling). Every pixel derives from a field in an ingested snapshot. A project whose machine is asleep shows **"last seen 2h ago"** with its room lights off — never a stale board pretending to be live. No animation, badge, or metric may exist without a backing datum; if a cute idea has no real signal behind it, the cute idea is cut.

**H3 — Push, not pull.** Projects push snapshots to Tower at each supervisor tick (plus on notable events). Tower never reaches into project machines — it has no credentials to them, no SSH, no filesystem access. This keeps the trust direction one-way: projects trust Tower with *data*; Tower is trusted with *nothing* except serving it and writing inbox files via the projects' own pushes (see H4 transport note).

**H4 — Tailscale-only, tokened.** Tower binds to the tailnet interface only, never a public one. Every `/ingest` and `/act` call carries a per-project bearer token (issued at project registration, stored in the project's environment — never in a tracked file, same convention as `DEVTEAM_TG_TOKEN`). The Flutter app (T5) reaches Tower over Tailscale on the phone. Transport note for H1/H3 consistency: since Tower cannot write to project machines, the inbox is delivered by the **project pulling its own queue**: the supervisor tick, immediately after pushing its snapshot, GETs `/queue/<project>` and materializes any pending commands into `.devteam/inbox/` locally, then consumes them. One HTTP round-trip per tick, both directions, always initiated by the project.

**H5 — The pack stays lean.** DEVDEPARTMENT gains exactly two integration points: (1) the snapshot push + queue pull step in the supervisor tick, (2) the inbox consumer. Both fail-open: Tower unreachable → one warning line, tick proceeds normally, Slack/console channels unaffected. A project never depends on Tower to function — Tower is a window, not a load-bearing wall.

---

## 1. The two pack-side integration points (DEVDEPARTMENT repo — small, ships first)

### P1 — Snapshot push + queue pull (`scripts/tower_sync.py`, called from the supervisor tick)

Config in `autopilot.json` (add-only keys, ships disabled):
```jsonc
"tower": { "enabled": false, "url": "", "project_id": "", "_token_env": "DEVTEAM_TOWER_TOKEN" }
```

Snapshot schema v1 (JSON, one POST per tick to `/ingest`):
```jsonc
{
  "schema": 1,
  "project_id": "orb-jun-26",
  "ts": "2026-08-16T09:00:00Z",
  "pack_version": "v4.9",
  "supervisor": {"mode": "loop|once|idle", "autonomy_level": 2, "tick": 41, "stop_file": false},
  "wave": {"total": 16, "done": 11, "started_at": "...", "prev_wave_minutes": 214},
  "tasks": [ {"id": "TASK-117", "title": "...", "status": "in_progress", "assignee": "GB",
               "branch": "...", "started_at": "...", "updated_at": "...", "rework_count": 1,
               "blocked_reason": null, "heartbeat_age_min": 7} ],
  "builders": [ {"unit": "GB", "state": "active|idle|stale", "task": "TASK-117",
                  "heartbeat_age_min": 7} ],
  "review_queue": [ {"id": "TASK-114", "age_min": 95} ],
  "usage": { "claude": {"pct_5h": 62, "pct_7d": 41}, "codex": {"pct": 30} },
  "recent_events": [ {"ts": "...", "kind": "DISPATCH|REVIEW|MERGE|BLOCKED|DIGEST", "text": "..."} ]
}
```
Everything above is already computed by `decide()`/`board_publisher.py`/`team_stats.py`/`usage_probe.py` — P1 is assembly and transport, not new analysis. After the POST, GET `/queue/<project_id>`; each returned command is written to `.devteam/inbox/<uuid>.json` and acknowledged (`DELETE /queue/<project_id>/<uuid>`) only after the file is durably written.

### P2 — Inbox consumer (in `supervisor.py`, same tick, before `decide()`)

Command file schema — identical in vocabulary to `commands.py` (the shared module, P1b-2):
```jsonc
{ "id": "uuid", "issued_at": "...", "source": "tower|app", "actor": "alister",
  "command": "approve|rework|answer|stop|resume|wave|dispatch",
  "args": {"task_id": "TASK-114", "text": "optional free text"} }
```
Consumption: parse → validate against **`commands.py`** — the shared command-validation module extracted in P1b-2 from `tg_commands.py`; both Tower and the Slack listener import it, eliminating the duplication that caused three real drift incidents → execute via the existing handler → move file to `.devteam/inbox/done/` with the outcome appended. Malformed file → moved to `inbox/rejected/` with the reason, P2-notified. Unknown command → rejected, never guessed.

**Pack-side tests:** snapshot assembly from fixture state; fail-open on unreachable Tower (tick proceeds, no exception); inbox consumption for every command; malformed/unknown rejection paths; the shared-validation refactor keeps every existing Telegram AND Slack test green.

---

## 2. The Tower service (own repo: `tower` — bootstrapped as a DEVDEPARTMENT-onboarded project)

Stack: **FastAPI + SQLite** (mirrors the pack's own bias — boring, file-backed, zero external services), Jinja/HTMX or a small React app for the board, SSE for live updates. PM2 process on clawsrv beside the existing supervisor deployments; Tailscale serve for the UI.

Endpoints: `POST /ingest` · `GET /queue/{project}` · `DELETE /queue/{project}/{id}` · `POST /act` (UI/app → enqueue) · `GET /board` (home) · `GET /project/{id}` (drill-down) · `GET /api/state` + `/api/events` (SSE; also T5's API) · `GET /health`.

Storage: `snapshots` (latest + ring-buffered history per project), `commands` (queued/delivered/done/rejected — full audit trail of every human action with actor + timestamp), `projects` (id, token hash, registered_at, last_seen).

### T1 — read-only aggregation (ships first, zero risk)
Home page: one row per project — state badge (🟢 wave running / 🟡 **waiting on you** / 🔴 stalled / ⚪ idle / ⚫ last-seen-Xh), builders active, review-queue depth + max age, blocked count, wave progress, usage meters. Sorted **waiting-on-you first** — the board's entire purpose is answering "where am I the bottleneck." Exit criteria: two real projects pushing; kill one supervisor and its row honestly decays to ⚫ with last-seen.

### T2 — project drill-down (Kanban) + history
Columns = the PLAN.md status machine verbatim (`pending → claimed → in_progress → needs_review → done`, `blocked` as a red side lane — no invented workflow states, H2). Cards: task id/title/assignee/time-in-column/rework badge/heartbeat freshness. History panel from accumulated snapshots: wave timeline, this-wave-vs-last duration, `team_stats` first-pass rates per builder.

### T2.5 — the Workshop view (toggle: `[ Board | Workshop ]`)
The animated floor, as agreed: ORCH figurine (crown, rubber stamp) at a raised desk with a review tray; one figurine per registered builder (avatar key optionally in the builder registry entry, so S5B arrives with a face the day it activates). Every animation maps 1:1 to a snapshot field (H2): hammering = `in_progress`+fresh heartbeat · walking a labeled 📦 to the tray = transition to `needs_review` · dust motes on tray packages ∝ review age · ✅ stamp → package to Done shelf · ❌ → trudge back (speed ∝ 1/rework_count) · frozen + ❓ + floor dims = `blocked` (the eye goes straight to it) · 💤 slump = stale heartbeat · lights out = idle/offline. Click figurine → dossier tail panel; click package → task card. Implementation is a pure rendering layer (SVG/CSS or PixiJS) over the *same* `/api/state` — if it breaks, the Kanban is untouched; the fun layer is never load-bearing.

### T3 — actions
Buttons on cards/rows, scoped to what's valid for the state: `approve`/`rework` on needs_review, `answer` on blocked, `stop`/`resume`/`wave` per project, `dispatch` per idle builder. Flow: `POST /act` → `commands` row → project pulls next tick (H1). UI shows `queued → delivered → done/rejected` from the audit trail — **never optimistic**. Free-text commands get the same confirmation UX Telegram's `/answer` has.

### T4 — polish: SSE everywhere, mobile-responsive layout, per-builder analytics page, digest timeline.

### T5 — Flutter companion app (own repo, **built by DEVDEPARTMENT as an onboarded project**)
Thin client of Tower's API only — no direct project contact ever. FCM push for P1/P2 escalations (Tower gains one `notify_push` sender fed by the same `notify.py` event that already fans out to Telegram); Android notification **action buttons** (approve/dismiss from the shade; free-text reply opens the app — iOS gets buttons, text-reply in-app, per platform limits); Tailscale on the phone; token + biometric gate on actions. Telegram remains as a **redundant P1-only fallback** — projects may set both `slack` and `telegram` in `notify_channels` for stop-the-line events specifically. Two independent interrupt paths for the one event class where missing a notification has the worst consequence.

---

## 3. Increments & territories (decompose-ready)

| # | Where | Deliverable | Depends |
|---|---|---|---|
| P1 | pack | `tower_sync.py` + tick wiring + config + tests | — |
| P2 | pack | inbox consumer + shared command-validation refactor + tests | — (parallel with P1) |
| T1 | tower | ingest/store/home | P1 pushing real data |
| T2 | tower | Kanban + history | T1 |
| T2.5 | tower | workshop renderer | T2 (same API) |
| T3 | tower + pack | `/act` + queue + audit UI | P2, T1 |
| T4 | tower | SSE/mobile/analytics | T2 |
| T5 | new repo | Flutter app + Tower FCM sender | T3 stable |

P1+P1b+P2 are one pack wave (P1b disjoint from P1: different scripts; P2 builds on both). T1–T4 are the `tower` repo's own PLAN.md waves — **onboard `tower` with DEVDEPARTMENT on day one and let the framework build its own mission control**; it appears as a row on the board it is constructing. T5 is a separate onboarded Flutter project later.

## 4. Open items deliberately deferred (BACKLOG discipline — each with its trigger)
Multi-user/roles (trigger: a second human operator exists) · public/non-Tailscale access (trigger: never, ideally) · Tower-initiated scheduling (trigger: wanting cross-project wave orchestration — a big step; today the supervisor per project stays sovereign) · iOS full text-reply-from-notification (trigger: Apple relaxing category limits).

## 5. Exit criteria for "TOWER exists"
Both real projects (orb-jun-26, rwc-admin-portal) + the pack repo pushing; home page answers the one-line question at a glance on the phone over Tailscale; an `/approve` clicked in Tower lands as a merged task with the full queued→delivered→done trail visible; killing Tower mid-wave affects **nothing** except the window going dark (H5 proven live).
