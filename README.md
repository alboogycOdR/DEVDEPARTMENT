# DEVDEPARTMENT v4.2 — Multi-Agent Development Workflow Framework

One pack, three layers, one onboarding prompt. Basileia Technologies.

| Layer | What it gives you |
|---|---|
| **Core protocol** | ORCH (Claude Code) + GB (Grok Build) + CX (Codex AI) coordinating through a git-versioned PLAN.md blackboard; strict status state machine; territorial isolation (Owned_Paths + worktrees + validator); /devteam-decompose /devteam-dispatch /devteam-status /devteam-review; Protocol §10 session continuity (resume-first, checkpoints); ORCH model discipline table |
| **Autopilot** | /devteam-autopilot (L1 one-wave autonomy) and scripts/supervisor.py (L2 continuous loop): auto-review, auto-merge, self-healing redispatch, P0/P1/P2 escalation contract, STOP kill switch, team_stats learning assignment, **two-way Telegram** (v4.1: /status /board /answer /approve /rework /stop /resume /wave /digest /mute), **nightly self-audit + dispatch budget ceiling** (v4.2: files TASK-MAINT-\* on failure, T1 Watchtower unreachable-builder P2 escalation) |
| **ECC waves** | Write-time enforcement hooks (territory firewall, secret scan, §10 lifecycle automation), .codex/config.toml for CX, harness-audit.sh release gate (AgentShield + validator + all test suites) |
| **Deployment** | PM2 process definition + clawsrv (Ubuntu 24.04, Tailscale) T1 "Watchtower" always-on deployment guide (v4.2) — supervisor runs unattended, dispatch/review still execute wherever builder CLIs are authenticated |

## Install into any new project

1. Keep this DEVDEPARTMENT/ folder next to your projects (not inside one).
2. Open Claude Code in the project root.
3. Paste the contents of `onboard.md` (or reference it) and run it.
4. Confirm the two human-gated steps (settings.json diff, git commit).
5. Drop specs into `specs/`, run `/devteam-decompose`, then `/devteam-dispatch`.
6. (Optional) Set `DEVTEAM_TG_TOKEN` / `DEVTEAM_TG_CHAT` and add `"telegram"`
   to `autopilot.json → notify_channels` for two-way command & control from
   your phone — see `docs/TELEGRAM.md`.
7. (Optional) Deploy the supervisor always-on to a VPS — see
   `docs/DEPLOY_CLAWSRV.md`.

## Layout

```
PLAN.md  AGENTS.md  CLAUDE.md  REVIEW.md  autopilot.json  onboard.md
briefings/   GROK_BUILD_BRIEFING.md · CODEX_BRIEFING.md   (filesystem-check mandate, resume-first)
docs/        COORDINATION_PROTOCOL.md · AUTOPILOT.md · HOOKS.md · BOARD.md · TELEGRAM.md · DEPLOY_CLAWSRV.md
             LEARNING.md · CONTROL.md · USAGE.md · MODEL_DISCIPLINE.md
scripts/     board_publisher.py · validate_plan.py · dispatch.sh + dispatch.ps1 · worktree.ps1 · harness-audit.sh + harness-audit.ps1 · supervisor.py (platform-aware) · notify.py · team_stats.py · tg_listener.py · tg_commands.py · maintenance.py · budget.py · scheduling.py
deploy/      ecosystem.config.js (PM2 process definition for clawsrv)
hooks/       territory-firewall.js · secret-scan.js · session-start.js · pre-compact.js · session-end.js · lib.js · hooks.json · run-tests.js
.claude/commands/  devteam-decompose · devteam-dispatch · devteam-status · devteam-review · devteam-autopilot
.codex/      config.toml (CX: gpt-5.6-sol, medium effort, sandbox profiles, DEVTEAM_UNIT=CX)
tests/       test_validate_plan.py · test_supervisor.py · test_board_publisher.py · test_tg_commands.py · test_tg_listener.py · test_supervisor_telegram.py · test_notify.py · test_scheduling.py · test_budget.py · test_maintenance.py · test_supervisor_maintenance.py
board/       index.html (Mission Control frontend)
dossiers/    per-task context dossiers (v4)
specs/       drop per-project spec documents here
```

## Test totals (run them all via `bash scripts/harness-audit.sh --no-shield`)

- Python: 277 (validator 18 + supervisor 17 + board_publisher 8 + tg_commands 85 + tg_listener 18 + supervisor_telegram 21 + notify 6 + scheduling 14 + budget 23 + maintenance 49 + supervisor_maintenance 18)
- Node hooks: 21
- Plus PLAN.md protocol validation and (online) AgentShield config scan.

## Version history

- v1.0 — core protocol, blackboard, validator, dispatch (renamed devteam-*, §10 continuity)
- v1.1–1.2 — autopilot layer; resume-first stale handling; model discipline (sonnet-5 judgment / sonnet-4-6 mechanical); c8b9872 filesystem checks; gpt-5.6-sol for CX
- v2.0 — ECC waves vendored: write-time hooks, .codex config, harness audit gate; unified onboarding
- v2.1 — full Windows 11 / PowerShell 5.1 parity: dispatch.ps1 + harness-audit.ps1 mirrors (resume-first, gpt-5.6-sol flags), platform-aware supervisor dispatch defaults, dual-platform onboarding steps
- v4.0 — Mission Control: board_publisher + paper-terminal Kanban PWA (portfolio across machines, animated unit avatars, in-flight tracker), per-task dossiers, supervisor board publishing. Python tests: 43.
- v4.1 — Two-way Telegram (Wave A-remainder, completes Pillar 2): `tg_listener.py` long-polling daemon thread + `tg_commands.py` command grammar/PLAN.md micro-transactions; 10-command grammar (`/status /board /answer /approve /rework /stop /resume /wave /digest /mute`); chat allowlist with silent-drop for unknown senders; P2 escalations get an actionable `/answer` reply line (notify.py); offset-persisted long-poll (no replay on restart); `/stop` verified unbreakable even against a corrupted PLAN.md. Python tests: 173.
- v4.2 — Self-maintenance + clawsrv deployment (Wave B, Pillar 3): `maintenance.py`'s six-step nightly self-audit (harness audit, validator, pytest, node hooks, hygiene, backup) idempotent via the new shared `scheduling.py` daily/weekly marker helper — files a `TASK-MAINT-<date>` block on any failure, committed `[MAINT]`; `budget.py` dispatch-ceiling + `quiet_hours` gating before every DISPATCH; T1 "Watchtower" unreachable-builder P2 escalation (dispatch/review CLIs may live on a different host than the monitoring supervisor); `deploy/ecosystem.config.js` PM2 process definition + `docs/DEPLOY_CLAWSRV.md` full T1 setup guide (T2 documented as a future path only); task-ID grammar widened from `TASK-\d+` to `TASK-[A-Z0-9-]+` so self-generated escalation IDs are fully recognized by the validator, board, and Telegram commands. Python tests: 277.
- v4.3 — Wave C: continuous learning loop — `INSTINCTS.md` store + `distiller.py` (deterministic confidence lifecycle in code, model only drafts rule text; new-instinct IDs and seed confidence 0.6 are forced, never trusted from model output), dispatch-time instinct injection (`instincts.py inject --unit`, since dispatch.sh/.ps1 predict — rather than pre-know — which task a builder will claim), weekly retro drafter (`retro.py`, cycle-time + territory-churn + instinct-effectiveness cross-reference), and the `AMEND-NNN` constitutional gate: proposed edits to AGENTS.md/CLAUDE.md/briefings are never auto-applied — `/approve AMEND-NNN` only flips the proposal's own Status field, the actual edit is always applied by ORCH in a supervised session (second lock, beyond the distiller itself never writing those files). Python tests: 358, Node hook tests: 23.
- v4.5 — Wave I: I1 CONTROL-block single-writer blackboard (`scripts/control.py`) — `control.mode=strict` (opt-in; `legacy`, the default, is byte-for-byte v4.4 behavior) moves PLAN.md writes off builders entirely: `dispatch.sh`/`.ps1` claim-at-dispatch (with a real `--dry-run` that predicts without writing), builders report via a fenced `devteam-control` JSON block instead of editing PLAN.md, the supervisor is the sole writer (`Updated_By: SV`, a new `VALID_UNITS` member), the firewall protects PLAN.md and grants only `dossiers/<own-task>.md`, and stale detection reads `max(Updated_At, dossier mtime)`. Plus I2 usage-window meters (`scripts/usage_probe.py`) — live `claude`/`codex` 5h/7d usage on the board top-strip and a new `/usage` command, `budget.py`'s `DEFER_USAGE` composing with the existing hourly-ceiling gate (critical-priority tasks can override), all cache-backed and fail-open end to end since the underlying data only exists as a side effect of a real, tiny, throwaway CLI invocation (see `docs/USAGE.md` for what was actually verified against the reference implementation vs. what still needs live confirmation against the real installed CLIs). Note: Wave H was explicitly skipped per instruction — this ships directly on the v4.3 base. Python tests: 471, Node hook tests: 28.


