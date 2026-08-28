# DEVDEPARTMENT — Tower Deployment & Operations Spec (clawsrv)

**Status:** decompose-ready. Companion to `DEVDEPARTMENT_TOWER_SPEC.md` (the
service) and `DEVDEPARTMENT_SLACK_SPEC.md` (the channel). Those two specs
define *what* Tower is; this one defines *how it runs* on clawsrv and how a
project gets connected to it. It exists because the parent specs name the
hosting decision ("clawsrv, PM2, Tailscale-only" — TOWER spec, Decisions
table) but never specify the deployment procedure, secrets handling, or
operational lifecycle — the gap identified 2026-08-26 when the pack-side wave
(TASK-013–018) closed and end-to-end review found no deployable Tower half.

## 0. Hard constraints (inherit the parent specs' rules)

- **H4 (Tailscale-only, tokened)** binds everything here: Tower binds to the
  tailnet interface **only** — never `0.0.0.0`, never a public IP. Every
  project authenticates with a bearer token. No exceptions, including "just
  for testing".
- **Secrets never in tracked files** — same convention as the pack
  (`DEVTEAM_*` env vars). On clawsrv they live in the PM2 ecosystem file's
  `env` block **only if** that file is untracked, or (preferred) in an
  untracked `/opt/tower/.env` the server loads at boot. The ecosystem file
  that IS committed contains no secret values.
- **H5 fail-open posture is symmetric**: Tower being down must never affect a
  project (already proven pack-side); a *project* being down must never
  affect Tower — a dead project is a ⚫ row with a last-seen timestamp,
  nothing more.
- Deployment is **one PM2 process, one port, one origin** (TOWER spec §2):
  FastAPI serves both the API and the static frontend build. No nginx, no
  second process, until a trigger in §6 fires.

## 1. Server layout (clawsrv)

```
/opt/tower/
├── repo/                  # the tower git repo (clone of the fork)
│   ├── server.py          # FastAPI backend
│   ├── tower.db           # SQLite (see §4 backup)
│   ├── dist/              # npm run build output (static frontend)
│   └── ...
├── .env                   # untracked: DEVTEAM_SLACK_TOKEN, DEVTEAM_SLACK_SIGNING_SECRET, TOWER_TOKEN_PEPPER
├── ecosystem.config.cjs   # PM2 config (committed in repo, symlinked or copied here)
└── backups/               # SQLite snapshots (see §4)
```

- Python venv at `/opt/tower/venv` (server deps: fastapi, uvicorn, sse-starlette
  or equivalent — pinned in `requirements.txt` in the tower repo).
- Node is needed at **build time only** (`npm ci && npm run build`); PM2 runs
  the Python process, not a Node one.

## 2. PM2 + Tailscale

**Ecosystem file** (committed, secret-free):

```js
module.exports = {
  apps: [{
    name: "tower",
    cwd: "/opt/tower/repo",
    script: "/opt/tower/venv/bin/uvicorn",
    args: "server:app --host <tailnet-ip> --port 8100",
    interpreter: "none",
    env_file: "/opt/tower/.env",
    max_restarts: 10,
    restart_delay: 5000,
    autorestart: true
  }]
}
```

- `--host` is the machine's **Tailscale IP** (e.g. `100.x.y.z`), resolved at
  deploy time — binding to the tailnet interface is what enforces H4 at the
  socket level. `tailscale ip -4` gives it; the deploy script writes it in.
- `script` **must be absolute**. PM2 resolves a relative `script` against
  `cwd` — not against the ecosystem file's own directory (`lib/Common.js`
  `prepareAppConf`: `app.pm_exec_path = path.resolve(cwd, app.script)`, where
  `cwd` is `app.cwd` whenever it is set). With `cwd: "/opt/tower/repo"`, a
  relative `"venv/bin/uvicorn"` resolves to `/opt/tower/repo/venv/bin/uvicorn`,
  which nothing ever creates — the venv lives at `/opt/tower/venv` per §1 — so
  `pm2 startOrRestart` aborts with `Script not found` and the §7 criterion
  `pm2 ls shows tower online` can never pass. (Spec correction 2026-08-28,
  v1.1: this block previously showed the relative form. Verified against pm2's
  own resolver, not assumed.)
- `pm2 save` + `pm2 startup` so Tower survives a server reboot.
- Optional hardening (deferred, §6): a `tailscale serve` HTTPS front so the
  Slack request URLs (§5) get a valid cert without exposing anything publicly.

**Verification (deploy-time, non-negotiable):** from a non-tailnet network,
`curl http://<public-ip>:8100/health` must **fail to connect**; from a tailnet
device, `curl http://<tailnet-ip>:8100/health` must return 200. Both checks
recorded in the deploy log. A Tower reachable off-tailnet is an automatic
rollback, not a finding to triage later.

## 3. Project registration & token lifecycle

The `projects` table (TOWER spec §2: `id, token_hash, registered_at,
last_seen`) is the source of truth. Procedure to connect a project:

1. On clawsrv: `python server.py register-project <project_id>` (a CLI
   subcommand Tower's backend must provide) → generates a random token,
   stores **only its hash** (salted with `TOWER_TOKEN_PEPPER` from `.env`),
   prints the token **once**.
2. On the project machine: set `DEVTEAM_TOWER_TOKEN=<token>` in the
   supervisor's environment (shell profile / PM2 env — never a tracked file),
   and set `tower.enabled: true`, `tower.url: "http://<tailnet-ip>:8100"`,
   `tower.project_id: "<project_id>"` in that project's `autopilot.json`.
3. Next supervisor tick pushes the first snapshot; Tower's row appears.
   Registration is complete only when the dashboard row goes 🟢 — not when
   the config is written.

Token rotation: `register-project --rotate <project_id>` invalidates the old
hash and prints a new token once. There is no token recovery — rotation is
the only remedy for a lost token. Revocation: `--revoke` clears the hash;
subsequent pushes from that project get 401 and (H5) the project logs one
warning per tick and continues unharmed.

## 4. Data durability

- `tower.db` is the only state. Nightly cron on clawsrv:
  `sqlite3 /opt/tower/repo/tower.db ".backup /opt/tower/backups/tower-$(date +%F).db"`,
  keep 14 days, delete older. Losing tower.db loses dashboard history and
  the command audit trail but **cannot** lose project work — PLAN.md in each
  repo remains the coordination truth (TOWER spec H1/H2 discipline: Tower
  renders state, it never owns it). This asymmetry is the reason a simple
  nightly backup is sufficient and anything fancier is over-engineering.
- Snapshot ring buffer per project (TOWER spec §2) caps table growth; the
  `commands` audit trail is append-only and small (one row per human action).

## 5. Slack request-URL cutover (P1b-3 prerequisite)

The pack shipped Socket Mode as the **pre-Tower** command path (TASK-016's
recorded resolution: Socket Mode and request URLs are mutually exclusive per
Slack app). When Tower's `/slack/*` endpoints (SLACK spec §6) deploy:

1. Slack app config: disable Socket Mode, set the three request URLs to the
   Tower host (the `https://<tower-tailnet-host>/slack/...` values from the
   manifest in SLACK spec §1 — this requires the §2 HTTPS option or a
   tailnet-valid cert; **decompose must resolve which** before P1b-3 ships).
2. Per-project supervisors keep `slack_listener.py` available as the
   no-Tower fallback (already its designed role) — flipping a project back
   to Socket Mode is a Slack-app toggle plus restarting that supervisor,
   nothing else.
3. The cutover is per-workspace (one Slack app), so it happens **once**, and
   only after T1 + the `/slack/*` endpoints pass the SLACK §10 checklist
   re-run against Tower instead of the local listener.

**Pre-Tower interim (documented so testing is possible today):** create the
Slack app with the SLACK §1 manifest **minus** the three `request_url`
settings blocks, enable Socket Mode, and generate an app-level token
(`connections:write`) for `DEVTEAM_SLACK_APP_TOKEN`. The full manifest
becomes correct at cutover.

## 6. Deferred (each with a trigger, per BACKLOG discipline)

| Item | Trigger |
|---|---|
| `tailscale serve` HTTPS front | P1b-3 needs valid-cert request URLs (likely immediately at that phase) |
| Separate nginx / static CDN | Frontend asset load ever measurably slows the API |
| Postgres over SQLite | A second Tower operator or >20 projects pushing |
| Off-server backup of tower.db | The audit trail becomes compliance-relevant |
| Deploy automation (CI → clawsrv) | Third manual deploy in a month |

## 7. Exit criteria for "deployed"

- `pm2 ls` shows `tower` online; survives `pm2 restart tower` and a full
  server reboot.
- Health check passes from tailnet, **fails from public internet** (both
  logged).
- At least one real project registered per §3 and showing 🟢 with live data.
- Nightly backup cron installed and one backup file verified restorable
  (`sqlite3 <backup> "select count(*) from snapshots"`).
- Kill Tower (`pm2 stop tower`) mid-wave on the test project: supervisor
  logs exactly one `[tower]` warning per tick and continues — the H5 proof
  from TOWER spec §5, now demonstrated against the *real* deployment, not a
  stub.
