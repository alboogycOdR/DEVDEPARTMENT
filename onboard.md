# DEVDEPARTMENT v4.5 — Unified Onboarding Prompt
# Run this in Claude Code from the TARGET PROJECT ROOT.
# The DEVDEPARTMENT pack folder must be reachable (default: ../DEVDEPARTMENT/).
# Idempotent — safe to re-run when DEVDEPARTMENT updates.
#
# Installs in one pass: core protocol + blackboard, devteam-* commands,
# the autopilot layer (dispatch/review/self-maintenance), ECC waves
# (write-time hooks, Codex config, harness audit), Mission Control board,
# two-way Telegram, the continuous learning loop, and Wave I (CONTROL-block
# single-writer blackboard + usage-window meters).

---

You are ORCH (Claude Code), onboarding this project into the DEVDEPARTMENT v4.5 multi-agent workflow system. Working directory: the **project root**. The pack is at `../DEVDEPARTMENT/` (ask once if the path differs; do not guess twice). Execute all steps in order; read every existing file before writing to it; report copied / skipped / merged / conflicts at the end.

## STEP 0 — Preconditions

Verify and report versions of: `git`, Python 3.10+ (`python3` on macOS/Linux; `python` or `py` on Windows), `node` (18+). **Windows note:** all commands below written as `python3` should be run as `python` on Windows; all `bash scripts/*.sh` invocations have PowerShell 5.1 mirrors — use `powershell -ExecutionPolicy Bypass -File scripts\<name>.ps1` instead. Never add `#requires -Version 7` to any script. Node is a hard requirement for the hooks layer; if missing, complete the rest and flag hooks as NOT INSTALLED in the final report. `claude`/`codex` CLIs are NOT hard requirements for onboarding itself, but are needed for: `control.mode=strict` (builders must be able to emit the `devteam-control` fence — see Step 1's control.mode note) and live usage-window data (`scripts/usage_probe.py` — see `docs/USAGE.md`). Report whether they're on PATH; a missing CLI just means those two features stay at their fail-open defaults (legacy mode, `—` usage) until installed.

Confirm the pack contains: `PLAN.md`, `AGENTS.md`, `CLAUDE.md`, `REVIEW.md`, `briefings/`, `docs/`, `scripts/`, `hooks/`, `.codex/`, `.claude/commands/` (5 devteam-* files), `tests/`, `specs/`. Stop and ask if anything is missing.

## STEP 1 — Copy infrastructure (add-only; never overwrite existing files)

Copy from the pack into the project root, skipping any file that already exists (merge folders file-by-file):

- `briefings/`, `docs/`, `scripts/`, `tests/`, `hooks/`, `board/`, `dossiers/`, `specs/` (never overwrite specs the human already placed)
- `.claude/commands/` — **hidden directory; list its 5 files explicitly** (devteam-decompose, devteam-dispatch, devteam-status, devteam-review, devteam-autopilot) and confirm each landed
- `.codex/config.toml` — if the project already has one, merge add-only (bring in missing keys: model, model_reasoning_effort, sandbox_mode, approval_policy, profiles, shell_environment_policy); on any conflicting key keep the existing value and flag it
- `PLAN.md`, `REVIEW.md` — only if absent
- `autopilot.json` — only if absent (copy the pack's template). The template ships `control.mode: "legacy"` — builders still write PLAN.md themselves, which is the safe default for a project onboarding for the first time. **Ask the human whether they want `control.mode: "strict"` instead** (the CONTROL-block single-writer blackboard — builders never touch PLAN.md; the dispatcher claims tasks and the supervisor applies builder-reported state via a fenced `devteam-control` block) before flipping it; see `docs/CONTROL.md`. Do not flip it unasked — it changes what `dispatch.sh`/`.ps1` do and what briefings/GROK_BUILD_BRIEFING.md + CODEX_BRIEFING.md tell the builder to do.
- `INSTINCTS.md` — only if absent; create empty via `python3 -c "import sys; sys.path.insert(0,'scripts'); import instincts; instincts.save_atomic('.', [])"` (Wave C learning loop's instinct store, created empty per project — never copied from the pack)

Usage-window meters (`scripts/usage_probe.py`) work out of the box (fail-open — renders `—` until real data exists) but the exact fields it parses out of `claude`/`codex`'s stream output have only been verified against the reference implementation's source, not a live installed CLI (see `docs/USAGE.md`'s verification commands). Mention this in the final report as a "not yet live-verified" item rather than silently asserting it works.

On macOS/Linux: `chmod +x scripts/*.sh scripts/*.py`. On Windows: skip chmod; nothing needed.

## STEP 2 — Read existing project files (CRITICAL, before any merge)

Read in full, if present: `./CLAUDE.md`, `./AGENTS.md`, `./README.md`. Preserve ALL existing content — conventions, structure notes, protected paths, tooling rules. Extract project name, stack, and key architecture notes for Step 3.

## STEP 3 — Detect project type and structure

```bash
ls pubspec.yaml 2>/dev/null && echo FLUTTER
ls package.json 2>/dev/null && echo NODE/JS
ls requirements.txt pyproject.toml setup.py 2>/dev/null | head -1 && echo PYTHON
ls *.mq5 *.mqh 2>/dev/null | head -1 && echo MQL5
find . -maxdepth 2 -type d | grep -vE '(\.git|node_modules|\.dart_tool|build|__pycache__|\.gradle)' | sort
```

Determine: primary language/framework, source root, test root, platform dirs, build output dirs. These populate the territory map in Step 4.

## STEP 4 — Merge CLAUDE.md

If CLAUDE.md exists AND does not already contain a `## Multi-Agent Orchestration — DEVDEPARTMENT` section: append the entire orchestration section from the pack's CLAUDE.md (everything from its `## Role` heading onward — role, coordination files, phase commands incl. the /devteam-decompose-not-/plan warning, **the full ORCH model discipline table**, protected paths, validation, git conventions) at the very end after a `---` separator, prefixed with:

```markdown
## Multi-Agent Orchestration — DEVDEPARTMENT (ORCH)
> Auto-appended by DEVDEPARTMENT v4.5 onboarding. Re-run onboard.md to refresh.
```

Then add the project territory map with the REAL paths from Step 3:

```markdown
### Builder territory mapping for THIS project
- Source root: [detected]
- Test root: [detected]
- Platform dirs: [detected]
- Owned_Paths must be drawn from these real directories — never a placeholder src/**.
```

If the section already exists: skip (report "already onboarded"). If CLAUDE.md is absent: copy the pack's CLAUDE.md and prepend a short project context block (name, stack, roots).

## STEP 5 — Merge AGENTS.md

Same pattern. Marker section: `## Multi-Agent Coordination Rules — DEVDEPARTMENT`. If AGENTS.md exists without it, append the pack AGENTS.md's ten commandments + Progress_Notes format + commit format under that marker after `---`. If absent, copy the pack's AGENTS.md verbatim. Never touch existing content above the marker. README.md is never modified.

## STEP 6 — Wire the hooks into .claude/settings.json (MOST SENSITIVE STEP)

Read `hooks/hooks.json` and the existing `.claude/settings.json` (if any).

- Idempotency: if `territory-firewall.js` already appears in the existing hooks config → skip this step, report "hooks already wired".
- No settings file → create `.claude/settings.json` with ONLY the `hooks` object (drop `$comment`).
- Existing file → deep-merge: APPEND our entries into any existing PreToolUse / SessionStart / PreCompact / SessionEnd arrays; never remove or replace existing hook entries; never touch other keys (permissions, model, MCP, etc.).

Show the resulting settings.json diff and WAIT for the human's confirmation on this step only. Never register these hooks anywhere else (duplicate hook loading is the #1 ECC-ecosystem failure).

## STEP 7 — Validate everything

Run and report exact output + exit codes:

macOS/Linux:
```bash
python3 scripts/validate_plan.py PLAN.md
python3 -m pytest tests/ -q          # expect 479 passed (validator 18 + supervisor 17 + board 10 + tg_commands 86 + tg_listener 18 + supervisor_telegram 21 + notify 6 + scheduling 14 + budget 23 + maintenance 49 + supervisor_maintenance 18 + instincts 30 + distiller 15 + retro 10 + supervisor_learning 24 + control 52 + supervisor_control 17 + usage 43 + dispatch_worktree 8)
node hooks/run-tests.js              # expect 28 passed
bash scripts/harness-audit.sh --no-shield
python3 scripts/supervisor.py --once --dry-run
```
Windows (PowerShell 5.1):
```powershell
python scripts\validate_plan.py PLAN.md
python -m pytest tests\ -q
node hooks\run-tests.js
powershell -ExecutionPolicy Bypass -File scripts\harness-audit.ps1 -NoShield
python scripts\supervisor.py --once --dry-run
```

If network allows, also run the full gate: `bash scripts/harness-audit.sh` (AgentShield via npx; exit ≥2 = critical findings — report all).

## STEP 8 — Live firewall smoke test

macOS/Linux:
```bash
echo '{"tool_input":{"file_path":"specs/x.md","content":"x"}}' | DEVTEAM_UNIT=GB node hooks/territory-firewall.js; echo "exit=$?"     # expect exit=2, protected-path block
echo '{"tool_input":{"file_path":"specs/x.md","content":"x"}}' | DEVTEAM_UNIT=ORCH node hooks/territory-firewall.js; echo "exit=$?"   # expect exit=0
```
Windows (PowerShell 5.1):
```powershell
$env:DEVTEAM_UNIT = "GB";   '{"tool_input":{"file_path":"specs/x.md","content":"x"}}' | node hooks\territory-firewall.js; "exit=$LASTEXITCODE"   # expect exit=2
$env:DEVTEAM_UNIT = "ORCH"; '{"tool_input":{"file_path":"specs/x.md","content":"x"}}' | node hooks\territory-firewall.js; "exit=$LASTEXITCODE"   # expect exit=0
Remove-Item Env:DEVTEAM_UNIT
```
Report both results.

## STEP 9 — Git staging (no auto-commit)

Stage only what this onboarding created/changed:

```bash
git add briefings/ docs/ scripts/ tests/ hooks/ board/ dossiers/ deploy/ specs/ .claude/ .codex/ PLAN.md REVIEW.md AGENTS.md CLAUDE.md autopilot.json INSTINCTS.md
git status
```

Show the status and WAIT for confirmation. Suggested message:
`chore: onboard DEVDEPARTMENT v4.5 (core + autopilot + ECC waves + learning loop + Wave I) [ORCH]`

## STEP 10 — Final report

```
DEVDEPARTMENT v4.5 ONBOARDING COMPLETE
=======================================
Project / stack / source root / test root:  [...]
Copied (new) / Skipped (existed) / Merged (appended) / Conflicts:  [...]
Hooks wired into .claude/settings.json:  yes / already / SKIPPED (no node)
Tests:  pytest [n], hooks [n], validator [OK/FAIL], audit [PASS/FAIL], supervisor dry-run [actions]
Firewall smoke test:  GB blocked=?, ORCH allowed=?
control.mode:  legacy (default) / strict (confirmed with human)
Usage-window meters:  live-verified against installed claude/codex CLIs? yes/no/not attempted — see docs/USAGE.md

Operating guide:
  /devteam-decompose            → turn specs/ into the task plan   (model: claude-sonnet-5)
  /devteam-dispatch             → worktrees + launch GB/CX          (claude-sonnet-4-6)
  /devteam-status               → health scan                       (claude-sonnet-4-6)
  /devteam-review               → review, verdict, merge            (claude-sonnet-5)
  /devteam-autopilot            → one autonomous wave, digest at end
  python3 scripts/supervisor.py --loop --interval 300   → continuous L2 autopilot
                                  (Windows: python scripts\supervisor.py ... — dispatch
                                   auto-routes to dispatch.ps1 via platform detection)
  touch STOP                    → halt the loop
  export DEVTEAM_TG_TOKEN/DEVTEAM_TG_CHAT + "telegram" in autopilot.json → phone alerts
                                  (once wired: /status /approve /rework /usage /mute /stop from Telegram)

Next: drop spec documents into specs/, run /devteam-decompose, confirm the git commit.
Needs attention: [anything flagged above]
```
