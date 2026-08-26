# TOWER — Cross-Project Mission Control. Build Specification

**Status:** Pack-side integration points (P1/P2/P1b) are BUILT and merged
(TASK-013–018, 2026-08-26, all first-pass, 931/0 + 36/0). The Tower service
itself (T1–T5) is still SPEC ONLY — nothing in the Tower service is built yet.
**Baseline:** pack @ `5cb23a9` (spec baseline); pack-side wave landed on
master through commit `8d9915d`.
**Last major update:** 2026-08-26 — the grok-workspace fork source (see
"Starting point" below) was confirmed **permanently deleted** by Alister
(worktree cleanup, believed temporary at the time). Exhaustive search (this
machine, GitHub, clawsrv reachability) found no copy. **Decision (Alister,
2026-08-26): T1 rebuilds the frontend shell from this spec's §2 component
inventory instead of forking** — the inventory below was written in enough
detail (every kept/replaced/removed file, the design tokens, the exact stack)
that this is a clean, well-specified scaffold task, not a guess. Section 2's
"Why fork, not build from scratch" and "What gets replaced / what gets kept"
subsections are retained below as the AUTHORITATIVE COMPONENT SPEC for the
from-scratch build — read them as "build this," not "replace this in the
fork." All five hard constraints and all five phases unchanged.

---

## Decisions locked with Alister

| Decision | Answer |
|---|---|
| Interrupt channel | **Slack** (primary) — see `specs/DEVDEPARTMENT_SLACK_SPEC.md`. Telegram is a redundant P1-only fallback. |
| Pull surface | **Tower** — the board you go to look at |
| Hosting | **clawsrv**, PM2, Tailscale-only |
| Repo shape | Tower is its **own repo**, deployed once. The pack gains exactly two integration points and nothing else. |
| Workshop view | Confirmed as **T2.5**. MetroCity 2.0 sprites confirmed as the character set (see assets in session history). |
| Flutter app | Confirmed as **T5**, built *by* DEVDEPARTMENT as an onboarded project once Tower's API is stable. |
| **Starting point** | ~~Fork the grok-workspace Mission Control~~ **SUPERSEDED 2026-08-26**: source permanently deleted, no copy found anywhere (machine, GitHub, clawsrv). **New decision: build the shell from scratch, to this spec's §2 component inventory exactly** — same design tokens, same stack (Vite + TanStack Router + React 19 + Tailwind v4 + Zustand), same file/component shape, no functional difference in the end state. |

**What TOWER answers, in one line:** *"Across every project I run — what is
executing, what is waiting on me, and where is it stalling?"* — currently
answerable only by opening N Claude Code sessions one at a time.

---

## 0. Hard constraints (violations are automatic rework)

**H1 — Tower is never a second writer.** No Tower code path may modify
PLAN.md, task branches, worktrees, or any project state directly. All
mutations flow: Tower `/act` → command file in the project's
`.devteam/inbox/` → consumed by the supervisor on its next tick, through the
**same handler path `commands.py` (the shared validator, refactored in P1b-2)
exposes**. Corollary, stated for honesty: **actions have tick latency**
(≤ `interval_seconds`). That is the correct trade; the UI must show
`queued → delivered → done` honestly, never optimistically.

**H2 — The board renders only true state.** Every pixel derives from a field
in an ingested snapshot. A project whose machine is asleep shows
**"last seen 2h ago"** with its room lights off — never a stale board
pretending to be live. No animation, badge, or metric may exist without a
backing datum; if a cute idea has no real signal, the cute idea is cut.
(The grok-workspace reference earns its animated desks because agent status
is real data from the store — same discipline applies here.)

**H3 — Push, not pull.** Projects push snapshots to Tower at each supervisor
tick. Tower never reaches into project machines — no SSH, no filesystem
access, no credentials. Trust flows one way: projects trust Tower with data;
Tower is trusted with nothing beyond serving it and writing inbox files via
the projects' own queue-pull.

**H4 — Tailscale-only, tokened.** Tower binds to the tailnet interface only,
never a public one. Every `/ingest` and `/act` call carries a per-project
bearer token stored in the project's environment — never in a tracked file,
same convention as `DEVTEAM_TG_TOKEN`. The Flutter app (T5) reaches Tower
over Tailscale on the phone. Inbox delivery: the supervisor tick, immediately
after pushing its snapshot, GETs `/queue/<project>` and materialises pending
commands into `.devteam/inbox/` locally, then consumes them. One HTTP
round-trip per tick, both directions, always initiated by the project.

**H5 — The pack stays lean.** DEVDEPARTMENT gains exactly two integration
points: (1) snapshot push + queue pull, (2) inbox consumer. Both fail-open:
Tower unreachable → one warning line, tick proceeds normally, Slack/console
channels unaffected. A project never depends on Tower to function — Tower is
a window, not a load-bearing wall.

---

## 1. The two pack-side integration points (unchanged — DEVDEPARTMENT repo)

### P1 — Snapshot push + queue pull (`scripts/tower_sync.py`)

Config in `autopilot.json` (add-only, ships disabled):
```jsonc
"tower": { "enabled": false, "url": "", "project_id": "",
           "_token_env": "DEVTEAM_TOWER_TOKEN" }
```

Snapshot schema v1:
```jsonc
{
  "schema": 1, "project_id": "orb-jun-26", "ts": "2026-08-22T09:00:00Z",
  "pack_version": "v4.9",
  "supervisor": {"mode": "loop|once|idle", "autonomy_level": 2,
                 "tick": 41, "stop_file": false},
  "wave": {"total": 16, "done": 11, "started_at": "...", "prev_wave_minutes": 214},
  "tasks": [ {"id": "TASK-117", "title": "...", "status": "in_progress",
               "assignee": "GB", "branch": "...", "started_at": "...",
               "updated_at": "...", "rework_count": 1, "blocked_reason": null,
               "heartbeat_age_min": 7} ],
  "builders": [ {"unit": "GB", "state": "active|idle|stale",
                  "task": "TASK-117", "heartbeat_age_min": 7} ],
  "review_queue": [ {"id": "TASK-114", "age_min": 95} ],
  "usage": { "claude": {"pct_5h": 62, "pct_7d": 41}, "codex": {"pct": 30} },
  "recent_events": [ {"ts": "...", "kind": "DISPATCH|REVIEW|MERGE|BLOCKED|DIGEST",
                       "text": "..."} ]
}
```
Everything above is already computed inside the supervisor — P1 is assembly
and transport, not new analysis.

### P2 — Inbox consumer (in `supervisor.py`, before `decide()`)

```jsonc
{ "id": "uuid", "issued_at": "...", "source": "tower|app", "actor": "alister",
  "command": "approve|rework|answer|stop|resume|wave|dispatch",
  "args": {"task_id": "TASK-114", "text": "optional free text"} }
```
Validates through `commands.py` (the shared module from P1b-2). Malformed →
`inbox/rejected/`; unknown → rejected, never guessed.

---

## 2. The Tower service — component spec (originally fork-and-extend; source lost 2026-08-26, see status header — build to this inventory from scratch instead)

### Why this design (originally: why fork, not build from scratch)

The grok-workspace Mission Control (built by GB for Alister, discovered
2026-08-22, **since permanently deleted — no copy recoverable**) was a
**production-quality personal dashboard** that already implemented the
visual and structural shell Tower needs. It is gone, but the description
below of what it had is detailed enough to serve as the build spec directly —
read every "keep verbatim" below as "build this component to this
description," not as an instruction to copy a file that no longer exists:

- **Design system:** Orbitron + Share Tech Mono, full custom token set
  (`--color-cyan: #00f5ff`, `--shadow-glow`, atmospheric orb background,
  cyan grid overlay) — exactly the aesthetic Tower needs, already built and
  tested on a real screen
- **Virtual office page:** `AgentsPage.tsx` with positioned desks, bounce
  animations, day/night cycle, stars, water cooler, wandering idle behaviour,
  agent-brief side panel — the workshop floor visual is this component with
  builder units substituted for fictional agents and MetroCity sprites
  substituted for CSS circles
- **Widget strip:** `WeatherWidget`, `AgentStatusWidget`, `UsageWidget`,
  `EmailWidget`, `CalendarWidget` — the T1 home-row panel shape, already
  componentised in `Panel` + `FetchState`
- **Kanban board:** `KanbanBoard.tsx` with drag-and-drop, due-date
  detection, overdue counting — extends directly to PLAN.md's five-column
  status machine
- **TanStack Router + Vite + Tailwind v4** — modern, deployable to Vercel
  or to clawsrv via PM2 with zero stack changes
- **Zustand store** as the state layer — clean, persist-capable, replaces
  with a live SSE feed by swapping one file

Building from scratch would reproduce all of this at lower quality and higher
cost. Fork it instead.

### What gets replaced / what gets kept

**Build-from-scratch reading key** (the source no longer exists — build
straight to the end state each row describes, there is no intermediate
"first copy, then edit" step): **Keep verbatim** → build this component
fresh, exactly as described (design tokens, structure, behaviour) — nothing
to diff against, just build it right the first time. **Keep, adapt** /
**Extend** / **Replace \*** → build directly in the described end-state
form; there is no "original" version to start from and modify. **Remove** →
simply don't build it; it never needs to exist in the new repo.

| Component | Action | Reason |
|---|---|---|
| `src/styles.css` — design system | **Keep verbatim** | Best-in-class; matches the aesthetic |
| `src/components/ui/panel.tsx` | **Keep verbatim** | The foundational building block |
| `src/components/layout/Atmosphere.tsx` | **Keep verbatim** | The orbs + grid background |
| `src/components/layout/NavBar.tsx` | **Keep, adapt** | Add project switcher |
| `src/components/agents/AgentsPage.tsx` | **Replace agents with builders** | Desk geometry stays; GB/CX/S5/S5B replace grok/scout/ledger/cut/signal; MetroCity sprites replace CSS circles; state from snapshot, not Zustand mock |
| `src/components/dashboard/DashboardPage.tsx` | **Replace widget content** | Shell and Panel layout kept; `AgentStatusWidget` → multi-project status strip; `UsageWidget` reads from snapshot usage; weather stays |
| `src/components/dashboard/KanbanBoard.tsx` | **Extend columns** | 3 → 5 columns matching PLAN.md status machine; data from snapshot tasks, not Zustand mock |
| `src/lib/store.ts` | **Replace with snapshot store** | Zustand kept as the state manager; seed data replaced with live snapshot ingest + SSE subscription |
| `src/lib/types.ts` | **Extend** | Add `BuilderUnit`, `ProjectSnapshot`, `CommandAudit` types alongside existing types |
| Auth system | **Remove** | Tower uses Tailscale + bearer tokens; no email/password needed |
| YouTube / Memory / Schedule pages | **Remove** | Out of Tower's scope |
| `src/routes/agents.tsx` | **Becomes the workshop route** | `/workshop/:projectId` |

### Stack (matches the lost reference implementation exactly)

**Vite + TanStack Router + React 19 + Tailwind v4 + Zustand.** FastAPI is
no longer needed for the UI layer — the fork is already a complete,
deployable frontend. Tower gains a **lightweight backend** (`server.py`,
FastAPI, SQLite) for snapshot ingest, queue management, and SSE event
streaming; the frontend talks only to this backend, never to projects
directly.

```
clawsrv (PM2)
├── tower-ui     (npm run build → serve static via FastAPI /static)
└── tower-server (FastAPI: /ingest, /queue, /act, /api/events SSE, /health)
```

One PM2 process, one port, one origin. The frontend is a static build served
by the same FastAPI process — no separate web server needed.

### Backend endpoints (FastAPI + SQLite)

`POST /ingest` · `GET /queue/{project}` · `DELETE /queue/{project}/{id}` ·
`POST /act` · `GET /api/state` · `GET /api/events` (SSE) · `GET /health`

Storage tables: `snapshots` (latest + ring-buffered history per project),
`commands` (queued/delivered/done/rejected audit trail), `projects`
(id, token_hash, registered_at, last_seen).

---

## 3. Phase breakdown

### Pack phases (DEVDEPARTMENT repo — done first)

| Phase | Deliverable | Territory | Depends |
|---|---|---|---|
| **P1** | `scripts/tower_sync.py` + tick wiring + config + tests | pack only | — |
| **P1b** | Slack primary channel (full spec in `specs/DEVDEPARTMENT_SLACK_SPEC.md`) | pack only | — (parallel) |
| **P2** | Inbox consumer + `commands.py` refactor + tests | pack only | — (parallel) |

P1 + P1b + P2 are one DEVDEPARTMENT wave — disjoint file territories,
dispatchable in parallel.

### Tower phases (own repo — onboarded with DEVDEPARTMENT, built by itself)

#### T1 — Fork + data layer + home dashboard

Fork grok-workspace. Remove auth, YouTube/Memory/Schedule pages. Replace
Zustand seed data with a live SSE subscription to Tower's `/api/events`.
Ship the home dashboard showing one row per project: state badge
(🟢 running / 🟡 **waiting on you** / 🔴 stalled / ⚪ idle / ⚫ last-seen-Xh),
builders active, review-queue depth, blocked count, wave progress, usage
meters. **Sorted waiting-on-you first** — that is the dashboard's entire
purpose. The existing widget strip shell hosts project-status panels instead
of weather/email/calendar; weather stays as a widget because it was already
there and Alister is in Cape Town.

Exit criteria: two real projects pushing; kill one supervisor; its row
honestly decays to ⚫ with last-seen timestamp.

#### T2 — Project drill-down: Kanban + history

Extend `KanbanBoard.tsx` from 3 columns to 5: `pending → claimed →
in_progress → needs_review → done`, with `blocked` as a red side lane. Data
from the snapshot's `tasks` array, not Zustand mock. Cards carry task ID,
title, assignee avatar, time-in-column (derived from `updated_at`), rework
badge, heartbeat-freshness indicator. History panel: wave timeline from
accumulated snapshots, this-wave vs prior-wave duration, `team_stats`
first-pass rates per builder.

#### T2.5 — Workshop floor (toggle: `[ Board | Workshop ]`)

Replace `AgentsPage.tsx`'s CSS circle agents with the DEVDEPARTMENT builder
units, driven by the snapshot's `builders` array. The existing desk geometry,
bounce animation, day/night cycle, and wander behaviour are kept and adapted.
MetroCity 2.0 sprites (composited in session history, `TOWER_workshop_MetroCity.html`)
replace the CSS circles — the sprite pipeline is already built and verified:
32×41px cells, 4 directions × 3 walk frames, loaded from base64-embedded PNGs.

State animations, all H2-compliant (every one maps to a snapshot field):

| What you see | Snapshot field |
|---|---|
| Builder at desk, bounce animation, screen lit, warm lamp pool | `state: "active"` + `heartbeat_age_min` < `stale_minutes` |
| Builder walks package up lane to review tray | `status` flip to `needs_review` (detected by snapshot delta) |
| Package in tray, dust motes grow | `review_queue[n].age_min` — motes proportional to age |
| ORCH stamps ✅, crate flies to Done shelf | `approved` event in `recent_events` |
| ORCH stamps ❌, builder trudges back (slower each rework) | `rework` event; speed = 1/`rework_count` |
| Builder frozen, ❓ bubble, floor dims, spotlight cone, action card appears | `state: "blocked"` |
| Builder slumped, 💤 bubble | `heartbeat_age_min` > `stale_minutes` |
| Lights off, "last seen Xh ago" overlay | `last_seen` > 30 min, no active supervisor |

The blocked state uses the `permission_wait` camera treatment from
agents-in-the-office (confirmed reference): floor darkens to 66% opacity,
a spotlight cone finds the blocked unit, a magenta vignette breathes at the
edges, and the action card slides in with the blocked reason and decision
buttons.

The workshop view is a pure rendering layer over the same SSE stream.
If it breaks, the Kanban is untouched. The fun layer is never load-bearing.

#### T3 — Actions

Buttons scoped to valid states: `approve`/`rework` on needs_review, `answer`
on blocked (opens a modal with the blocked reason pre-populated), `stop`/
`resume`/`wave` per project, `dispatch` per idle builder. Flow: button click
→ `POST /act` → `commands` row → project pulls next tick → `.devteam/inbox/`.
UI renders `queued → delivered → done/rejected` from the audit trail —
**never optimistic**. Answer modal uses the same Panel + input components
already in the fork.

#### T4 — Polish

SSE live updates everywhere (replace manual polling). Mobile-responsive
layout using the existing Tailwind breakpoints. Per-builder analytics page
(first-pass rates, wave contribution, rework history). Digest timeline view
from `recent_events` history.

#### T5 — Flutter companion app (own repo)

Thin client of Tower's `/api/state` and `/act`. FCM push for escalations
(Tower gains a `notify_push` sender). Android notification action buttons
(approve/dismiss from the shade; free-text reply opens the app). iOS gets
buttons; full text-reply is in-app. Tailscale on the phone; biometric gate
on actions. Built *by* DEVDEPARTMENT as a separate onboarded Flutter project
once T3's API is stable — the framework builds its own companion app.

---

## 4. Onboarding the Tower repo

Day one: `git init` a new `tower` repo (originally specced as `git clone
<grok-workspace>` — superseded 2026-08-26, source deleted; scaffold fresh
instead: `npm create vite@latest . -- --template react-ts` or equivalent,
then add TanStack Router + Tailwind v4 + Zustand per §2's stack). Immediately
run `onboard.md` in Claude Code. The repo appears as a row on the board it
is constructing — DEVDEPARTMENT builds Tower's T1–T4 as a real PLAN.md wave.
Project ID: `tower`. This is not a meta-joke; it is the most honest
acceptance test of the framework's pipeline.

---

## 5. Exit criteria for "TOWER exists"

- orb-jun-26, rwc-admin-portal, and the DEVDEPARTMENT repo itself all pushing
- Home dashboard answers the one-line question at a glance on the phone over Tailscale
- An `/approve` clicked in Tower lands as a merged task with the full
  `queued → delivered → done` audit trail visible
- The workshop floor shows the right builder state within one tick of a
  real state change (not mocked)
- Killing Tower mid-wave affects **nothing** — next tick, the supervisor logs
  one warning and continues (H5 proven live)

---

## 6. Deferred items (BACKLOG discipline — each with a trigger)

| Item | Trigger to revisit |
|---|---|
| Multi-user / roles | A second human operator exists |
| Public / non-Tailscale access | Never, ideally |
| Tower-initiated cross-project scheduling | Wanting to orchestrate waves across projects; today each supervisor is sovereign |
| iOS full text-reply from notification shade | Apple relaxing notification category limits |
| Real-time collaborative editing of PLAN.md via Tower | A second ORCH-level operator working concurrently |
