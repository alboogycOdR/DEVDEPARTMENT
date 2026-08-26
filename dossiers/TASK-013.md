# Dossier — TASK-013 · commands.py extraction (the wave's foundation)

**Brief:** Every command path — Telegram today, the Slack listener and Tower inbox tomorrow — must validate through one module. You extract tg_commands.py's command-validation logic into `scripts/commands.py` and turn tg_commands into a thin re-exporting shim. Three real drift incidents came from this duplication (SLACK §5); H1 makes the single-path rule a hard constraint. TASK-016 and TASK-017 both import your module, so you gate half the wave.

**Spec:** SLACK §5 "scripts/commands.py" (the governing paragraph), TOWER H1 + §1 P2. Read both in full.

**Intended approach:**
- Move the validation vocabulary and per-command arg checking (approve/rework/answer/stop/resume/wave/dispatch/status/usage/mute/digest) into commands.py with a clean surface the other consumers can call without Telegram concepts leaking through (e.g. `validate(command, args) -> (ok, normalized|reason)`).
- tg_commands.py keeps its module path and re-exports everything it exported before. **ATLAS-surfaced coupling: control.py and maintenance.py import tg_commands directly** for git_pull/git_commit_and_push/git_commit_and_push_detailed — those helpers are NOT command-validation and stay physically in tg_commands; only validation moves.
- **Do not touch tg_listener.py.** SLACK §7's row says "tg_listener.py becomes a thin shim" — that's a wording slip; §5 names tg_COMMANDS as the shim and §9 says tg_listener is "preserved as-is". ORCH resolution recorded in your task block; §5/§9 govern.
- Zero behaviour change is the whole point: tests/test_tg_commands.py must pass **untouched** — it is your strongest regression net and it is out of your territory on purpose.

**Territory note:** scripts/** is builder-protected; your two script paths are per-task grants in hooks/lib.js, removed at done. TASK-014 (CX) and TASK-015 (S5) run CONCURRENTLY — stay strictly inside your three files.

## Work Log
