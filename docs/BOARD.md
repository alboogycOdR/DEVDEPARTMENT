# Mission Control Board (v4)

Live Kanban projected from PLAN.md. Design parity with Starbird's Dispatch
(ISC) — warm paper, mono ink, scanlines, zero radius — adapted to the
DEVDEPARTMENT protocol: stations BACKLOG → CLAIMED → BUILD → REVIEW → BLOCKED →
DONE, animated unit avatars (ORCH crown · GB chisel · CX brackets, pulsing while
running with a blinking beacon), an in-flight tracker with per-task phase
segments + T+ clocks + heartbeat freshness, and a **portfolio view** showing
every project from every machine on one page.

## Multi-machine setup (MacBook + Windows, two projects, one page)
1. Create ONE private `boards` repo on GitHub; clone it on both machines.
2. In each project's autopilot.json:
   "board": { "mode": "central-path", "central_path": "~/git/boards",
              "project_name": "orb-terminal", "host_label": "windows-msi" }
   (the Mac project sets its own project_name + host_label, e.g. "macbook-pro")
3. Each supervisor tick publishes <project>.json and merges itself into
   projects.json, commits and pushes. Enable GitHub Pages (or `tailscale serve`
   the clone) → open the URL → the portfolio lists both projects; tap through
   to either live board. Add to home screen for the app feel.

Modes: `local` (write ./board only) · `central-path` (shared repo, recommended)
· `gh-pages` (this repo's gh-pages branch via a worktree).
Publishing is throttled (min_interval_seconds) and fail-open — a dead board
never blocks a wave. The page auto-refreshes every 45 s and shows a STALE
banner if the last publish is >15 min old.

Manual publish any time: `python(3) scripts/board_publisher.py`.

## Dossiers (borrowed from Dispatch)
`dossiers/TASK-NNN.md` — per-task brief + plan + append-only work log across
units; created at /devteam-decompose, read-first on claim/resume. Kills context
re-briefing between ORCH, GB and CX. See dossiers/README.md.
