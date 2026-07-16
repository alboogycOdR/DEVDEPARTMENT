# DEVDEPARTMENT Onboarding Prompt
# Run this in Claude Code from the TARGET PROJECT ROOT.
# Usage: /project:../DEVDEPARTMENT/onboard  (or paste as a task)
#
# This script is idempotent — safe to re-run when DEVDEPARTMENT updates.

---

You are ORCH (Claude Code), onboarding a new project into the DEVDEPARTMENT multi-agent workflow system. Your working directory is the **project root**. The DEVDEPARTMENT folder is at `../DEVDEPARTMENT/` relative to here (adjust the path if the human has placed it elsewhere — ask once if uncertain).

Execute the following steps in order. At the end, report what was done, what was skipped (already existed), and what needs the human's attention.

---

## STEP 1 — Locate DEVDEPARTMENT

Confirm `../DEVDEPARTMENT/` exists and contains at minimum: `briefings/`, `docs/`, `scripts/`, `PLAN.md`, `REVIEW.md`, `AGENTS.md` (template), `CLAUDE.md` (template).

If the path is wrong, stop and ask the human for the correct DEVDEPARTMENT path. Do not proceed with a missing or incomplete DEVDEPARTMENT.

---

## STEP 2 — Copy infrastructure (non-conflicting folders)

Copy the following from DEVDEPARTMENT into the project root, **skipping any that already exist**. If a folder already exists, merge contents (do not overwrite existing files — only add missing ones):

- `.claude/commands/` → `./.claude/commands/`  **← hidden dir; must be listed explicitly**
  (creates `.claude/` if absent; copies devteam-decompose.md, devteam-dispatch.md, devteam-status.md, devteam-review.md)
- `briefings/`        → `./briefings/`
- `docs/`             → `./docs/`
- `scripts/`          → `./scripts/`
- `tests/`            → `./tests/`
- `specs/`            → `./specs/`   (copy folder structure; do NOT overwrite any spec files the human has already placed here)
- `PLAN.md`           → `./PLAN.md`  (only if PLAN.md does not already exist in the project root)
- `REVIEW.md`         → `./REVIEW.md` (only if it does not already exist)

> **Why `.claude/commands/` must be listed explicitly:** shell globs and
> generic "copy all folders" instructions silently skip hidden directories
> (those prefixed with `.`). The slash commands (`/devteam-decompose`, `/devteam-dispatch`,
> `/devteam-status`, `/devteam-review`) live in `.claude/commands/` and are the primary
> interface ORCH uses to drive the workflow. If they are missing, none of
> the phase commands work — yet no error is thrown at onboarding time, making
> the gap invisible until the first `/devteam-decompose` invocation fails.

Report: list of folders/files copied, list skipped.

---

## STEP 3 — Read existing project files (CRITICAL before touching anything)

Before writing a single byte to CLAUDE.md or AGENTS.md, read their current contents in full.

- Read `./CLAUDE.md` if it exists. Extract and preserve all existing content — project conventions, directory structure, protected paths, tooling notes, Git rules, everything.
- Read `./AGENTS.md` if it exists. Same — extract and preserve all existing content.
- Read `./README.md` if it exists. Extract the project name, tech stack, and any key architecture notes. You will use these to write an accurate project context block.

If neither CLAUDE.md nor AGENTS.md exist, you will create them fresh from the DEVDEPARTMENT templates.

---

## STEP 4 — Detect project type and structure

Run the following to understand the project:

```bash
# Detect language/framework
ls pubspec.yaml 2>/dev/null && echo "FLUTTER"
ls package.json 2>/dev/null && echo "NODE/JS"
ls requirements.txt pyproject.toml setup.py 2>/dev/null | head -1 && echo "PYTHON"
ls *.sln *.csproj 2>/dev/null | head -1 && echo "DOTNET"
ls pom.xml build.gradle 2>/dev/null | head -1 && echo "JVM"
ls Cargo.toml 2>/dev/null && echo "RUST"

# Top-level structure
find . -maxdepth 2 -type d | grep -v -E '(\.git|node_modules|\.dart_tool|build|\.pub-cache|__pycache__|\.gradle)' | sort
```

From this output, determine:
- **Primary language / framework**
- **Source root** (e.g. `lib/` for Flutter, `src/` for many others)
- **Test root** (e.g. `test/` for Flutter/Python, `__tests__/` or `spec/` for JS)
- **Build output** (e.g. `build/`, `dist/`, `.dart_tool/`)
- **Key subdirectories** that builders will work in

Store these as variables — you'll use them in Steps 5 and 6.

---

## STEP 5 — Merge CLAUDE.md

### If CLAUDE.md already existed:

Append the following block at the very end of the existing file, separated by a horizontal rule. Do NOT remove or alter any existing content above it.

```markdown

---

## Multi-Agent Orchestration — DEVDEPARTMENT (ORCH)

> Auto-appended by DEVDEPARTMENT onboarding. Do not edit this section manually —
> re-run `DEVDEPARTMENT/onboard.md` to refresh it.

### Role
You are ORCH, the orchestrator in a three-unit development team. Builders are
GB (Grok Build) and CX (Codex AI). You plan, assign, review, and merge. You
do not implement feature code directly.

### Coordination files (always read fresh — never from memory)
- `PLAN.md` — blackboard; all coordination flows through it
- `AGENTS.md` — shared conventions summary
- `docs/COORDINATION_PROTOCOL.md` — authoritative rules
- `REVIEW.md` — review log (ORCH-only writes)
- `briefings/GROK_BUILD_BRIEFING.md` — GB launch brief
- `briefings/CODEX_BRIEFING.md` — CX launch brief

### Slash commands
- `/devteam-decompose` — decompose specs/ into PLAN.md tasks
- `/devteam-dispatch`  — create worktrees + launch builders
- `/devteam-status`    — sync scan and health report
- `/devteam-review`    — review needs_review tasks, verdict, merge or rework

### Builder territory mapping for THIS project
> ORCH: fill in the real paths from your Step 4 detection below.
- **Source root:** [DETECTED — e.g. lib/]
- **Test root:** [DETECTED — e.g. test/]
- **Platform dirs:** [DETECTED — e.g. android/, ios/, functions/]
- **Builder Owned_Paths** must be drawn from these real directories.
  Never use the placeholder `src/**` for this project.

### Protected paths (builders must never touch)
- `specs/**`, `AGENTS.md`, `CLAUDE.md`, `docs/**`, `REVIEW.md`
- `.claude/**`, `scripts/**`, `PLAN.md` frontmatter + other tasks' blocks
- Any path not listed in a task's Owned_Paths

### Validation
Run `python scripts/validate_plan.py` before every dispatch and after every
builder session. Non-zero exit = do not proceed.

### Git conventions
Conventional Commits. Every task-related commit ends `[TASK-NNN]`.
Orchestration commits end `[ORCH]`. Only ORCH merges to main/master.
```

Fill in the `[DETECTED — ...]` placeholders with real values from Step 4.

### If CLAUDE.md did NOT exist:

Create it fresh using the DEVDEPARTMENT template (`../DEVDEPARTMENT/CLAUDE.md`),
then prepend a project context block:

```markdown
# CLAUDE.md — [PROJECT NAME]

**Stack:** [primary language/framework from Step 4]
**Source root:** [detected]
**Test root:** [detected]

[Add any project-specific conventions here before the orchestration section]

---
```

Then append the orchestration section from above.

---

## STEP 6 — Merge AGENTS.md

### If AGENTS.md already existed:

Append the following at the very end, separated by `---`. Do not alter existing content.

```markdown

---

## Multi-Agent Coordination Rules — DEVDEPARTMENT

> Auto-appended by DEVDEPARTMENT onboarding. See `docs/COORDINATION_PROTOCOL.md` for full rules.

**Unit IDs:** ORCH (Claude Code) · GB (Grok Build) · CX (Codex AI)

**The ten commandments:**
1. PLAN.md is the blackboard. Re-read it fresh every session — never from memory.
2. Builders edit only their own task block in PLAN.md, append-only mutable fields only.
3. Only claim tasks Assigned_To your own ID. TBD tasks are untouchable.
4. Owned_Paths is your exclusive territory. Files outside it → blocked (OWNERSHIP_CONFLICT).
5. Work on your task branch (`task/TASK-NNN-gb` or `-cx`) in your worktree. Never commit to main.
6. specs/ is read-only for builders. Ambiguity → blocked (SPEC_AMBIGUITY) with a precise question.
7. Status lifecycle is law: pending→claimed→in_progress→needs_review→done. Builders never set done.
8. No needs_review without Test_Evidence. Untested work is unfinished work.
9. Leave no silent state. End every session: in_progress+note, needs_review+evidence, or blocked+reason.
10. When in doubt, block — don't improvise.

**Progress_Notes format:**
`- [2026-07-12T15:28:00Z] [GB] What was done / what is next.`

**Commit format:** `type(scope): description [TASK-NNN]`

**Project-specific territory map:**
> Builders: your Owned_Paths will be drawn from this project's real directories.
> See CLAUDE.md → "Builder territory mapping for THIS project".
```

### If AGENTS.md did NOT exist:

Create it fresh from `../DEVDEPARTMENT/AGENTS.md` (copy verbatim — it already
contains the full coordination rules).

---

## STEP 7 — Validate the result

```bash
python3 scripts/validate_plan.py ./PLAN.md
```

If this fails: the PLAN.md that was copied may have the example tasks (TASK-000/001)
in an inconsistent state. Fix by ensuring the example tasks have valid schemas,
or strip them (they are marked EXAMPLE and `/devteam-decompose` will delete them on first real run).

Also run the validator self-test to confirm the Python environment is healthy:

```bash
python3 -m pytest tests/test_validate_plan.py -v
```

If `pytest` is not installed: `pip3 install pytest` (or `pip3 install pytest --break-system-packages`
on macOS with a Homebrew-managed Python 3.12+). The validator itself only uses stdlib, so a
missing pytest only blocks the self-test — `validate_plan.py` will still work.

---

## STEP 7b — Smoke-test dispatch (dry run)

Run a dry-run dispatch to confirm the worktree script works before any real builder session:

```bash
bash scripts/dispatch.sh grok --dry-run
```

**Expected output:** PLAN.md validation passes, worktree path is printed, and the
builder prompt is shown. No builder is launched. Verify the printed command line
contains the correct CLI flags (see "Builder CLI flags" below).

**Common failure — `fatal: 'main' is already used by worktree`:**
The script uses `git worktree add --detach "$WT" main` which creates the worktree
at main's current HEAD commit without checking out the branch name. If you see this
error, the copy of `scripts/dispatch.sh` in your project still has the old
`git worktree add "$WT" main` (without `--detach`). Fix:

```bash
sed -i 's/git worktree add "\$WT" main/git worktree add --detach "\$WT" main/' scripts/dispatch.sh
```

> **Why `--detach` is required:** git does not allow a named branch to be checked
> out in two worktrees simultaneously. The primary worktree already has `main`
> checked out. The builder worktree must therefore start in detached-HEAD state
> pointing at main's tip; the builder then immediately creates its own
> `task/TASK-NNN-gb` branch, which is the branch it actually works on.

**Builder CLI flags — verify these in the dry-run output:**

| Builder | Correct flags | What goes wrong with wrong flags |
|---------|--------------|----------------------------------|
| Grok Build | `grok --always-approve --permission-mode bypassPermissions` | `grok -p` / `--single` is **single-turn only** — prints text and exits with no tool use, no file writes, session over after one response. The builder appears to run but does nothing. |
| Codex AI | `codex exec --dangerously-bypass-approvals-and-sandbox` | Default (`codex exec`) uses `read-only` sandbox — all writes blocked. `-s workspace-write` allows writes only inside the worktree dir; PLAN.md is in the main repo root (outside the worktree), so the builder cannot claim tasks. `-s danger-full-access` alone only lifts the **sandbox** axis — it does not touch the separate **approval-policy** axis (`-a/--ask-for-approval`), and Codex additionally keeps a per-directory trust registry (`~/.codex/.codex-global-state.json`) that gates a brand-new worktree path the CLI flags don't override. The result: Codex silently sits waiting on a confirmation prompt in the "headless" terminal — indistinguishable from a hang until a human notices and answers it. **There is no `--yolo` flag in this Codex CLI version** (checked `codex exec --help`) — do not use it. `--dangerously-bypass-approvals-and-sandbox` is the one flag whose help text explicitly promises to "skip all confirmation prompts and execute commands without sandboxing," covering the trust gate too. Verified empirically: ran clean and unattended on the first invocation in a never-before-seen worktree directory. |

> **Why Codex needs `--dangerously-bypass-approvals-and-sandbox`:** DEVDEPARTMENT's design
> keeps PLAN.md at the main repo root while builders work in sibling worktrees
> (`../wt-codex`). Plain `-s danger-full-access` only grants filesystem access — Codex can
> still stop and wait for a human to approve a command or to trust the (new) working
> directory, which is fatal for an unattended dispatch nobody is watching in real time.
> `--dangerously-bypass-approvals-and-sandbox` removes sandboxing AND every
> confirmation/trust prompt in one flag, matching the filesystem + approval access Codex
> needs to write PLAN.md across the worktree boundary with zero human interaction.

---

## STEP 8 — Git hygiene

If the project uses git:

```bash
# Add only the DEVDEPARTMENT-sourced files; don't stage unrelated project changes
git add .claude/commands/ briefings/ docs/ scripts/ tests/ specs/ PLAN.md REVIEW.md AGENTS.md CLAUDE.md
git status
```

Do NOT commit automatically — show the human the staged files and ask for
confirmation before committing. Suggested message:
`chore: integrate DEVDEPARTMENT multi-agent workflow system [ORCH]`

---

## STEP 9 — Report to the human

Print a clean summary:

```
DEVDEPARTMENT ONBOARDING COMPLETE
==================================
Project:        [name]
Stack:          [framework]
Source root:    [path]
Test root:      [path]

Files copied:   [list]
Files skipped:  [list — already existed, not overwritten]
Files merged:   CLAUDE.md, AGENTS.md (sections appended)

Territory map (for PLAN.md Owned_Paths):
  lib/features/**     → feature work
  lib/core/**         → shared utilities
  functions/**        → backend/cloud functions
  test/**             → test suite
  android/ / ios/     → platform-specific (assign carefully — usually one task)
  [adjust for actual project structure]

Next steps:
  1. Drop your spec documents into specs/
  2. Run /devteam-decompose in Claude Code
  3. Run /devteam-dispatch to activate builders
  4. Confirm git commit? [y/n]

Needs your attention:
  [Any spec ambiguities, missing dependencies, or decisions required]
```

---

## Notes for re-runs (idempotency)

- Infrastructure folders: only add missing files, never overwrite existing ones.
- `.claude/commands/`: check each file individually — only copy files that don't already exist.
- PLAN.md: never overwrite if it already contains real tasks (non-example).
- CLAUDE.md / AGENTS.md: check whether the `## Multi-Agent Orchestration — DEVDEPARTMENT` / `## Multi-Agent Coordination Rules — DEVDEPARTMENT` sections already exist before appending — if they do, skip (don't double-append).
- validate_plan.py: always re-run at the end regardless.
- dispatch.sh: always verify the `--detach` flag is present after copying. Run the dry-run smoke test (Step 7b) before reporting onboarding complete.

---

## Changelog

| Version | Change |
|---------|--------|
| Initial | First published onboarding script |
| +1 | **Added `.claude/commands/` to Step 2 copy list.** Hidden directories are silently skipped by generic copy instructions; the slash commands are non-functional without them. |
| +1 | **Fixed `dispatch.sh` source: `git worktree add --detach`.** Without `--detach`, every project hits `fatal: 'main' is already used by worktree` on first dispatch. Also updated Step 8 git-add to include `.claude/commands/`. |
| +1 | **Added Step 7b dispatch dry-run smoke test** so the `--detach` issue is caught at onboarding time rather than at first real builder dispatch. |
| +1 | **Switched `python` → `python3`** for macOS compatibility. |
| +1 | **Renamed all four commands to `devteam-*` prefix** (`/devteam-decompose`, `/devteam-dispatch`, `/devteam-status`, `/devteam-review`). Claude Code has built-in slash commands including `/plan` (plan mode), and potentially `/status` and `/review`. Naming custom DEVDEPARTMENT commands with the same names causes both to fire simultaneously, breaking the workflow. The `devteam-` prefix guarantees no collision with any Claude Code built-in, now or in future releases. |
| +1 | **Fixed Grok Build CLI flags: `grok -p` → `grok --always-approve --permission-mode bypassPermissions`.** `-p`/`--single` is a single-turn flag — it prints one text response and exits immediately with zero tool use. The builder appeared to launch but wrote nothing. The correct headless agentic flags are `--always-approve` (auto-approves tool calls) and `--permission-mode bypassPermissions` (removes permission prompts). Updated in `dispatch.sh` and documented in Step 7b dry-run output checklist. |
| +1 | **Fixed Codex AI CLI flags: `codex exec` → `codex exec -s danger-full-access`.** Default `codex exec` sandbox is `read-only` — all writes blocked. `-s workspace-write` only allows writes inside the working directory (the builder worktree); PLAN.md lives at the main repo root one level above, so the builder could not claim tasks (`patch rejected: writing outside of the project`). `-s danger-full-access` removes the sandbox restriction, matching the filesystem access Codex needs to write PLAN.md across the worktree boundary. Updated in `dispatch.sh` and documented in Step 7b with a table of flag consequences. |
| +1 | **Fixed latent context-window resumption bug.** Builder briefings Step 2 previously said "find the highest-priority *pending* task" — a re-dispatched builder with an `in_progress` task from a prior session would skip it and claim a new task. Now Step 2 says: resume any `in_progress` or `claimed` task first; only claim a new `pending` task if none exists. Same fix applied to the inline dispatch prompt in `dispatch.sh`. Added §10 "Session Continuity" to `docs/COORDINATION_PROTOCOL.md` covering ORCH and builder context-limit procedures. Added Step 11 "Context limit discipline" to both builder briefings. |
| +1 | **Brought `dispatch.ps1` in line with `dispatch.sh` (the earlier flag fixes only touched the `.sh`).** The Windows launcher still shipped the broken pre-fix values: Grok `-p` (single-turn), Codex bare `exec` (read-only sandbox), and `git worktree add` **without** `--detach` (`fatal: 'main' is already used by worktree`). Corrected all three, added the resume-check to its prompt, and made the file **ASCII** — Windows PowerShell 5.1 mis-decodes UTF-8 em-dashes in a no-BOM `.ps1` and throws `The term 'exit' is not recognized` mid-run (PowerShell 7/`pwsh` is unaffected). |
| +1 | **Normalised `dispatch.sh` to LF + added `.gitattributes` (`*.sh text eol=lf`).** With `core.autocrlf=true` on Windows, the script checked out with CRLF and bash rejected the first line: `set -euo pipefail\r` → `: invalid option name: pipefail`. Windows users should prefer `pwsh -File scripts/dispatch.ps1 -Builder <grok|codex>`. |
| +1 | **Corrected Codex launch flag: `-s danger-full-access` → `--dangerously-bypass-approvals-and-sandbox`.** An intermediate revision briefly used `--yolo`, believed to skip approvals + sandbox in one flag — but **`--yolo` does not exist in this Codex CLI** (`codex exec --help` lists no such flag) and was silently accepted as a no-op-ish positional/unknown arg without erroring, so the builder kept its default approval policy and sat waiting on a confirmation prompt in the "headless" terminal until a human noticed and answered it manually. Root cause: `-s/--sandbox` (filesystem access) and `-a/--ask-for-approval` (whether the model must ask before running a command) are two independent axes in Codex — `-s danger-full-access` alone only lifts the first. Codex also keeps a **per-directory trust registry** (`~/.codex/.codex-global-state.json`) that can gate a brand-new worktree path independent of both CLI flags. `--dangerously-bypass-approvals-and-sandbox` is the one documented flag that skips sandboxing, approval prompts, and the trust gate together — verified empirically to run clean and unattended on the very first invocation in a never-before-seen worktree. Updated in `dispatch.ps1` + `dispatch.sh` (project and template) and Step 7b. **Lesson: always confirm a flag exists via `--help` on the exact subcommand before trusting it — a silently-accepted-but-inert flag is worse than a rejected one.** |
| +1 | **Root-caused and fixed builders committing PLAN.md claims directly to `main`.** CODEX_BRIEFING.md / GROK_BUILD_BRIEFING.md Step 3 previously sequenced the claim commit *before* branch creation ("One commit... Create/switch to that branch"), so a builder following the literal instruction order committed the claim while still sitting on `main` — confirmed to have silently occurred on multiple prior tasks (not just the one that surfaced it) before it was caught. Reordered Step 3 in both briefings (template and project, all four files) to: create/switch branch first, run `git branch --show-current` to confirm it is not `main`, only then edit and commit PLAN.md — with the same verification re-run before every subsequent PLAN.md commit that session. |
| +1 | **Added targeted-edit rule for PLAN.md to Commit discipline (Step 6).** A builder that reads the whole PLAN.md into a buffer and writes it back out re-serializes line endings across the entire ~2000-line file, so every commit shows as a full-file rewrite — ORCH cannot merge that normally and must discard the builder's PLAN.md diff wholesale (`git merge -X ours`) even when the real edit was one field. Builders must use their editor's in-place patch/edit capability on their own block only, never a full read-modify-write of the file. |
| +1 | **Namespaced builder worktree paths per project: `wt-grok`/`wt-codex` → `wt-<repo-name>-grok`/`wt-<repo-name>-codex`.** All worktree paths were hardcoded to `$(dirname "$REPO_ROOT")/wt-grok` and `wt-codex` — a fixed name in the *parent* of the repo, not inside it. Two different DEVDEPARTMENT-managed projects checked out as siblings under the same parent directory (the common case — e.g. `~/Projects/project-a` and `~/Projects/project-b`) would silently dispatch builders into the *same* worktree folders, cross-contaminating branches and task state between unrelated projects. Fixed in `dispatch.sh`, `dispatch.ps1`, and `worktree.ps1` (template and project, all six files) by deriving `$REPO_NAME`/`$RepoName` from the repo root's own folder name and interpolating it into the worktree path. Doc references in both builder briefings, `devteam-dispatch.md`, and `COORDINATION_PROTOCOL.md` updated to the `wt-<repo-name>-{grok,codex}` pattern so no stale literal path misleads a reader. **Lesson: never hardcode a path that lives outside the repo you're templating — anything derived from "the parent directory" needs the repo's own identity baked in, or every clone of the template collides with every other.** |
