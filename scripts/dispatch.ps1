<#
.SYNOPSIS
    Dispatch a builder (grok|codex) headlessly against PLAN.md. Windows mirror of dispatch.sh.
.DESCRIPTION
    Targets Windows PowerShell 5.1 (Windows default). Do NOT add "#requires -Version 7".
    Behaviour is 1:1 with scripts/dispatch.sh: validate plan -> ensure detached worktree ->
    resume-first builder prompt -> launch -> re-validate.

    Wave I (I1): mode-aware. control.mode=legacy (default) behaves exactly as before -
    builder scans PLAN.md, claims its own task, edits PLAN.md itself. control.mode=strict
    flips to claim-at-dispatch (this script claims/resumes via scripts\control.py) plus
    stdout capture and devteam-control fence extraction after the session ends. No hybrid.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\dispatch.ps1 -Builder grok
    powershell -ExecutionPolicy Bypass -File scripts\dispatch.ps1 -Builder codex -DryRun
#>
param(
    [Parameter(Mandatory = $true)][string]$Builder,  # unit ID (GB/CX/S5/S5B/...) or legacy cli name (grok/codex/claude)
    [switch]$DryRun,
    # Launch the builder in its own detached console window (DEFAULT in legacy mode).
    # Rationale: `& $Cmd ...` runs the builder as a CHILD of whoever ran this script.
    # When ORCH dispatches from a harness background job, the builder is therefore a
    # grandchild of that job -- and when the harness reaps the job, the builder dies
    # with it, mid-write, however healthy it was. Four sessions were lost that way on
    # 2026-08-02. A detached window is outside that process tree and survives.
    [switch]$InProcess   # opt back in to the old blocking, same-console behaviour
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$ParentDir = Split-Path $RepoRoot -Parent
Set-Location $RepoRoot

function Get-PythonCmd {
    foreach ($c in @("python", "python3", "py")) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { return $c }
    }
    throw "No Python interpreter found on PATH (tried python, python3, py)."
}
$Py = Get-PythonCmd

# Worktree paths are namespaced by this project's own folder name (not just
# "wt-grok"/"wt-codex") because they're created as SIBLINGS of the project
# root, not siblings of DEVDEPARTMENT itself. Two DEVDEPARTMENT-onboarded
# projects sharing a parent directory (a common layout) would otherwise
# compute the exact same worktree path and silently collide.
$ProjectName = Split-Path $RepoRoot -Leaf

# v4.7: builder identity from the registry (autopilot.json's builders key,
# dual-shape — scripts/builder_registry.py). $Builder may be a unit ID or a
# legacy cli name (grok/codex/claude -> first ACTIVE unit on that cli).
# FAIL-CLOSED on anything unresolvable: no safe default exists for a wrong
# worktree/CLI guess.
$RegOut = & $Py "scripts\builder_registry.py" resolve $Builder --repo $RepoRoot 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Error "[dispatch] Cannot resolve builder '$Builder' from the registry - refusing to dispatch. ($RegOut)"
    exit 1
}
$Reg = @{}
foreach ($line in $RegOut) {
    $i = "$line".IndexOf("=")
    if ($i -gt 0) { $Reg["$line".Substring(0, $i)] = "$line".Substring($i + 1) }
}
$Id = $Reg["UNIT"]; $Cli = $Reg["CLI"]; $Model = $Reg["MODEL"]
$Suffix = $Reg["BRANCH_SUFFIX"]; $Briefing = $Reg["BRIEFING"]
$AutoLoadsContext = ($Reg["AUTO_LOADS_CONTEXT"] -eq "true")
$AuthMode = $Reg["AUTH_MODE"]; $AuthValue = $Reg["AUTH_VALUE"]
$Identity = $Reg["IDENTITY"]; if (-not $Identity) { $Identity = "preamble" }
$AgentName = $Reg["AGENT_NAME"]; if (-not $AgentName) { $AgentName = "devteam-builder" }
if (-not $Id -or -not $Cli -or -not $Reg["WORKTREE_SUFFIX"] -or -not $Suffix -or -not $Briefing) {
    Write-Error "[dispatch] Registry resolution for '$Builder' returned an incomplete entry - refusing to dispatch."
    exit 1
}
$Wt = Join-Path $ParentDir ("wt-" + $Reg["WORKTREE_SUFFIX"] + "-" + $ProjectName)

# CLI-invocation table — keyed by CLI FAMILY, not unit (S5/S5B share the
# claude row). These quirks are properties of the CLI binaries, not project
# config, so they stay here:
$PromptViaStdin = $false
switch ($Cli) {
    "grok" {
        # Bare `grok <prompt>` starts the INTERACTIVE TUI with a trust-dialog
        # that hangs headless dispatches (confirmed live, multi-hour hang);
        # -p switches to single-turn non-interactive mode. -p must be LAST:
        # $Prompt is appended right after this array at the call site.
        $Cmd = "grok"
        $CmdArgs = @("--always-approve", "--permission-mode", "bypassPermissions")
        # Registry model pin (fix 2026-08-15). The codex and claude branches both
        # honour $Model; this one did not, so autopilot.json's `model` field for GB
        # was a DEAD KNOB -- settable, resolved into $Model by builder_registry.py,
        # and then silently ignored. A config field that looks authoritative and is
        # not is worse than an absent one: GB's model was in fact coming from the
        # user-level ~/.grok/config.toml ([models] default), outside the repo, out of
        # version control, and invisible to anyone reading the registry. Left null,
        # behaviour is unchanged (the guard below is skipped) and GB still floats on
        # the CLI default -- but the pin now works when set.
        if ($Model) { $CmdArgs += @("--model", $Model) }
        # -p must stay LAST: $Prompt is appended immediately after this array at the
        # call site, and -p/--single takes it as its value.
        $CmdArgs += "-p"
    }
    "codex" {
        # Routed through cmd /c: npm's codex.ps1 shim spuriously pipes $input
        # (ExpectingInput) under splatted-array invocation from a script,
        # blocking on stdin ("stdin is not a terminal") -- reproduced live.
        # cmd /c invokes codex.cmd, which has no such pipeline semantics.
        # --reasoning-effort is NOT a valid codex exec flag (codex-cli 0.144.5);
        # model_reasoning_effort is authoritative via .codex/config.toml.
        # PROMPT VIA STDIN (fix 2026-08-14, oikonomos live failure): cmd.exe
        # treats embedded newlines in an argument as command breaks and drops
        # into "More?" continuation reading stdin — the multiline prompt never
        # reached codex ("Builder process error: Reading additional input from
        # stdin...", zero-work session). Same principle as the legacy-mode
        # launcher's prompt file: a file has no quoting. `codex exec -` reads
        # the prompt from stdin; the strict-mode launch path below redirects
        # the prompt file into it inside a single-line cmd string.
        $Cmd = "cmd"
        $CmdArgs = @("/c", "codex", "exec")
        if ($Model) { $CmdArgs += @("--model", $Model) }
        $CmdArgs += @("-s", "danger-full-access")
        $PromptViaStdin = $true
    }
    "claude" {
        # claude.exe is a native binary (not an npm .ps1 shim) -- no cmd /c
        # workaround needed; prompt is a trailing positional argument.
        $Cmd = "claude"
        $CmdArgs = @("-p")
        if ($Model) { $CmdArgs += @("--model", $Model) }
        $CmdArgs += @("--dangerously-skip-permissions")
    }
    default {
        Write-Error "[dispatch] Unknown CLI family '$Cli' for unit $Id - refusing to dispatch."
        exit 1
    }
}

Write-Host "[dispatch] Validating PLAN.md..." -ForegroundColor Cyan
& $Py "scripts\validate_plan.py" "PLAN.md"
if ($LASTEXITCODE -ne 0) {
    Write-Error "[dispatch] PLAN.md illegal - fix before dispatching."
    exit 1
}

# Warn (not block) about an old-style unnamespaced worktree left over from
# before this fix -- it is NOT reused, just flagged so it doesn't sit there
# silently confusing a future look at the folder.
$LegacyWt = Join-Path $ParentDir ("wt-" + $Reg["WORKTREE_SUFFIX"])
if ((Test-Path $LegacyWt) -and ($LegacyWt -ne $Wt)) {
    Write-Warning "[dispatch] Found an old-style unnamespaced worktree at $LegacyWt (pre-dates per-project namespacing)."
    Write-Warning "[dispatch] It is NOT being used by this dispatch. If it belongs to this project, remove it with:"
    Write-Warning "[dispatch]   git worktree remove `"$LegacyWt`" --force   (run from $RepoRoot)"
}

function Normalize-WtPath([string]$p) {
    return ($p -replace '\\', '/').TrimEnd('/').ToLowerInvariant()
}

# Integration branch from autopilot.json (git.base_branch, fail-safe default
# "main") — resolved ONCE here because both the create and refresh paths below
# need it; creation previously hardcoded "main" and failed on master-based repos.
$BaseBranch = "main"
try {
    $GitCfg = Get-Content "$RepoRoot\autopilot.json" -Raw | ConvertFrom-Json
    if ($GitCfg.git -and $GitCfg.git.base_branch) { $BaseBranch = $GitCfg.git.base_branch }
} catch { $BaseBranch = "main" }

if (Test-Path $Wt) {
    # Reuse only if it's actually a registered worktree of THIS repo -- not
    # just "a directory happens to be sitting there."
    $registeredRaw = git -C $RepoRoot worktree list --porcelain | Where-Object { $_ -like "worktree *" } | ForEach-Object { $_.Substring(9) }
    $registeredNorm = $registeredRaw | ForEach-Object { Normalize-WtPath $_ }
    $wtNorm = Normalize-WtPath $Wt
    if ($registeredNorm -notcontains $wtNorm) {
        # Empty husk (Windows leftover after worktree remove while a handle is
        # held): nobody's work is here. Reclaim and fall through to create.
        # Get-ChildItem -Force includes hidden/dotfiles; a dir holding only
        # .foo is NON-empty and is refused. Directory.Delete refuses a
        # non-empty dir, so this path can never wipe someone's work.
        $huskEntries = @(Get-ChildItem -LiteralPath $Wt -Force -ErrorAction SilentlyContinue)
        $reclaimed = $false
        if ($huskEntries.Count -eq 0) {
            try {
                [System.IO.Directory]::Delete($Wt)
                $reclaimed = $true
            } catch {
                $reclaimed = $false
            }
        }
        if ($reclaimed) {
            Write-Host "[dispatch] Reclaimed empty unregistered directory at $Wt (leftover husk, not a git worktree) - proceeding to create it."
        } else {
            Write-Error "[dispatch] $Wt exists but is not a registered worktree of this repo ($RepoRoot)."
            Write-Error "[dispatch] This usually means a stale or foreign directory occupies the expected worktree path."
            Write-Error "[dispatch] Inspect it manually, then either remove it or let git reclaim it, and re-run dispatch:"
            Write-Error "[dispatch]   git worktree list   (from $RepoRoot, to see what git actually knows about)"
            exit 1
        }
    }
}

if (Test-Path $Wt) {
    # Refresh a REUSED worktree to the integration tip.
    #
    # Without this, dispatch only ever set the worktree's commit at creation
    # time, so every later dispatch handed the builder a stale tree -- and,
    # critically, a stale PLAN.md. That bit on 2026-08-02: wt-s5 sat on the
    # TASK-027 merge, its local PLAN.md still read "TASK-027: needs_review",
    # and S5 correctly concluded there was nothing claimable and exited. The
    # bug was latent for the whole plan because until then every builder's
    # decision depended on CODE (which its task branch supplied) rather than
    # on PLAN.md state.
    #
    # Two hard guards. Only refresh when the worktree is DETACHED -- a
    # worktree sitting on task/TASK-NNN-xx holds a live branch and may hold
    # uncommitted work, and resetting it is precisely how a session's output
    # gets destroyed. And only when it is CLEAN, so an interrupted builder's
    # in-flight files survive to its next session (that has already saved one
    # builder's work this project).
    $wtBranch = (git -C $Wt rev-parse --abbrev-ref HEAD 2>$null)
    $wtDirty  = @(git -C $Wt status --porcelain 2>$null | Where-Object { $_ -and $_ -notmatch '\.serena' })
    if ($wtBranch -eq "HEAD" -and $wtDirty.Count -eq 0) {
        $before = (git -C $Wt rev-parse --short HEAD 2>$null)
        git -C $Wt checkout --detach $BaseBranch --quiet 2>$null
        $after = (git -C $Wt rev-parse --short HEAD 2>$null)
        if ($before -ne $after) {
            Write-Host "[dispatch] Refreshed worktree to $BaseBranch tip ($before -> $after) - PLAN.md is current." -ForegroundColor Cyan
        }
    } elseif ($wtBranch -ne "HEAD") {
        Write-Host "[dispatch] Worktree is on '$wtBranch' - NOT refreshing (resume path; its branch and any in-flight work stay put)." -ForegroundColor Yellow
    } else {
        Write-Host "[dispatch] Worktree is detached but DIRTY - NOT refreshing, so uncommitted work survives. Files: $($wtDirty -join '; ')" -ForegroundColor Yellow
    }
} else {
    Write-Host "[dispatch] Creating worktree at $Wt..." -ForegroundColor Cyan
    git worktree add --detach $Wt $BaseBranch
    if ($LASTEXITCODE -ne 0) { Write-Error "[dispatch] git worktree add failed."; exit 1 }
}

# Wave I: control.mode from autopilot.json. Fail-safe default: legacy.
$ControlMode = "legacy"
try {
    $CfgJson = Get-Content "autopilot.json" -Raw | ConvertFrom-Json
    if ($CfgJson.control -and $CfgJson.control.mode -eq "strict") {
        $ControlMode = "strict"
    }
} catch {
    $ControlMode = "legacy"
}

# Wave I: claim-at-dispatch in strict mode.
$TaskId = ""
$ResumeOrClaim = ""
if ($ControlMode -eq "strict") {
    $ClaimArgs = @("scripts\control.py", "claim", "--unit", $Id, "--repo", $RepoRoot)
    if ($DryRun) { $ClaimArgs += "--dry-run" }
    $ClaimOut = (& $Py @ClaimArgs | Out-String).Trim()

    if ($ClaimOut -like "RESUME:*") {
        $TaskId = $ClaimOut.Substring(7)
        $ResumeOrClaim = "resuming"
    } elseif ($ClaimOut -like "CLAIMED:*") {
        $TaskId = $ClaimOut.Substring(8)
        $ResumeOrClaim = "claimed"
    } elseif ($ClaimOut -like "NONE:*") {
        Write-Host "[dispatch] Nothing to dispatch for $Id ($($ClaimOut.Substring(5))) - skipping launch." -ForegroundColor Yellow
        exit 0
    } else {
        Write-Warning "[dispatch] unexpected claim output '$ClaimOut' - treating as nothing to dispatch."
        exit 0
    }
    $DryNote = ""
    if ($DryRun) { $DryNote = ", DRY RUN - no write performed" }
    Write-Host "[dispatch] $ResumeOrClaim $TaskId for $Id (control.mode=strict$DryNote)." -ForegroundColor Cyan
}

$PlanPath = Join-Path $RepoRoot "PLAN.md"
$Fence = '```'

# S5 runs the literal `claude` CLI, which auto-loads CLAUDE.md as ambient
# project context regardless of what this prompt tells it to read -- and
# CLAUDE.md's own orchestration section says "You are ORCH". Without an
# explicit override, S5 would start this session confused about its own
# identity. GB/CX don't have this problem (grok/codex don't auto-load
# CLAUDE.md), so this prefix is S5-only.
$IdentityOverride = ""
# Identity mechanism (see docs/BUILDER_REGISTRY.md "Builder identity").
# identity=agent  -> role comes from .claude/agents/<name>.md via --agent, and
#   NO override preamble is prepended. Structural fix: the preamble opens with
#   "IMPORTANT IDENTITY OVERRIDE ... Ignore CLAUDE.md's ORCH role assignment",
#   which has the shape of a prompt-injection attempt -- a safety-trained model
#   treating it with suspicion is behaving correctly, so the fix is to stop
#   needing it rather than to word it harder.
# identity=preamble (default) -> today's behavior, byte-identical.
$Peers = & $Py -c "
import sys; sys.path.insert(0, 'scripts')
import builder_registry as br
try:
    ids = [u for u in br.active_units(r'$RepoRoot') if u != '$Id']
    print(' and '.join([', '.join(ids[:-1]), ids[-1]]) if len(ids) > 1 else (ids[0] if ids else 'the other builders'))
except Exception:
    print('the other builders')
" 2>$null
if (-not $Peers) { $Peers = "the other builders" }
if ($AutoLoadsContext -and $Identity -eq "agent") {
    $CmdArgs += @("--agent", $AgentName)
    Write-Host "[dispatch] $Id identity via --agent $AgentName (no override preamble)." -ForegroundColor Cyan
} elseif ($AutoLoadsContext) {
    $IdentityOverride = "IMPORTANT IDENTITY OVERRIDE: your project context auto-loaded CLAUDE.md, which contains a `"## Multi-Agent Orchestration`" section describing an ORCH role and saying `"You are ORCH`". That does NOT apply to this session. You are $Id -- a builder unit, exactly parallel to $Peers, implemented via Claude Code. Ignore CLAUDE.md's ORCH role assignment entirely for this session: you have none of ORCH's exclusive powers here -- no merging task branches, no review verdicts, no editing PLAN.md frontmatter, no editing any task block but your own claimed one. Those remain the separate, interactive ORCH session's job. Follow the builder procedure below exactly as GB/CX would.`n`n"
}

if ($ControlMode -eq "strict") {
    $Prompt = $IdentityOverride + @"
You are $Id, a builder in a multi-agent dev team. Working directory: $Wt (your isolated git worktree; coordination PLAN.md lives at $PlanPath on main - control.mode=strict: you never write PLAN.md yourself).
Your task is $TaskId ($ResumeOrClaim by the dispatcher before this session started - do not re-claim or re-branch).
Procedure: (1) Read AGENTS.md and $Briefing, then PLAN.md, fresh from disk, for $TaskId's Spec_References/Owned_Paths/Acceptance_Criteria. Read dossiers/$TaskId.md in full if it exists (your prior work log) before acting. (2) If resuming: continue on the existing branch task/$TaskId-$Suffix at the exact stopping point recorded in the dossier. If newly claimed: create branch task/$TaskId-$Suffix in your worktree. (3) Implement strictly against Spec_References, touching ONLY files under Owned_Paths plus your own dossier (dossiers/$TaskId.md - append a Work Log entry at minimum every ~30 minutes of work and at every stopping point; it is your heartbeat, since you never touch PLAN.md for this). (4) Test everything. (5) Emit a devteam-control block as the LAST thing you print, fenced exactly like this:
${Fence}devteam-control
{"control_version": 1, "task": "$TaskId", "unit": "$Id", "status": "needs_review", "progress_note": "...", "artifacts": ["path/a.dart"], "test_evidence": "...", "blocked_reason": null, "next_step": null}
${Fence}
status must be exactly one of in_progress (mid-session checkpoint - dossier note + next_step, nothing else changes) / needs_review (requires non-empty test_evidence) / blocked (blocked_reason must start with SPEC_AMBIGUITY, MISSING_DEPENDENCY, OWNERSHIP_CONFLICT, SYNC_MISMATCH, TOOLING_FAILURE, or OTHER:). Never done/pending/claimed - those are the supervisor's alone. Conventional Commits ending [$TaskId] for your code commits (never for PLAN.md - you don't touch it). Never write to specs/, docs/, REVIEW.md, scripts/, .claude/, PLAN.md, other dossiers, or main.
"@
} else {
    $Prompt = $IdentityOverride + @"
You are $Id, a builder in a multi-agent dev team. Working directory: $Wt - your isolated git worktree on your own task branch. ALL CODE changes are made and committed there; never put code on main (merging is ORCHs, after review). PLAN.md is the deliberate exception: it is the shared coordination blackboard living at $PlanPath on main. READ and EDIT it at that path so you both see and publish current state. To record ANY PLAN.md change (claim, status transition, Progress_Note, needs_review) run: scripts/plan_commit.sh 'chore(plan): <what> [$Id]'  (PowerShell: powershell -ExecutionPolicy Bypass -File scripts\plan_commit.ps1 'chore(plan): <what> [$Id]'). That script commits PLAN.md alone, directly onto main, and cannot carry code. DO NOT run 'git push . HEAD:main' - that is the old procedure and it is a trap: it works on claim, but by needs_review your HEAD sits on top of your code commits and that push lands them all on the integration branch unreviewed, bypassing review. A PLAN.md commit left on your task branch is invisible to ORCH and the other builders until merge, which defeats the whole point of a blackboard. Code to your branch; PLAN.md to main; never the reverse.
Procedure: (1) Read AGENTS.md and $Briefing, then PLAN.md, fresh from disk. If dossiers/TASK-NNN.md exists for your task, read it in full before acting and append a Work Log entry each session - never ask for re-explanation of anything in the dossier. (2) RESUME CHECK FIRST - scan PLAN.md for any task with Assigned_To: $Id and Status: in_progress or claimed. If found, resume that task immediately: re-read its Owned_Paths files and the last Progress_Note to find the exact stopping point, then continue on the existing branch (do not re-claim or re-branch). Only if NO in_progress/claimed task exists: claim the highest-priority pending task Assigned_To: $Id whose dependencies are done - one atomic edit+commit setting Status: claimed, Branch: task/TASK-NNN-$Suffix, Started_At. (3) Create (or switch to) the task branch in your worktree and implement strictly against the task's Spec_References, touching ONLY files under its Owned_Paths. (4) Test everything; append Test_Evidence. (5) Append-only Progress_Notes with UTC timestamps and [$Id] tags - if your context is approaching its limit, write a detailed stopping-point note (what is done, what file, exact next step) and commit before stopping. (6) Finish at needs_review (never done), or blocked with a vocabulary reason. Conventional Commits ending [TASK-NNN]. Never write to specs/, docs/, REVIEW.md, scripts/, .claude/, other task blocks, or main.
"@
}

# Wave C: inject project instincts (fail-open).
$InstinctsSection = ""
try {
    $InstinctsSection = & $Py "scripts\instincts.py" "inject" "--unit" $Id "--repo" $RepoRoot "--limit" "5" 2>$null | Out-String
} catch {
    $InstinctsSection = ""
}
if ($InstinctsSection.Trim().Length -gt 0) {
    $Prompt = $Prompt + "`r`n`r`n" + $InstinctsSection.Trim() + "`r`n"
}

# ATLAS A5 (§5): mirrors dispatch.sh's block exactly -- same posture as the
# instincts injection just above (fail-open, one warning line on any error,
# dispatch proceeds without the section either way), gated on both the db
# existing and autopilot.json -> atlas.enabled (ships false). In
# control.mode=strict $TaskId is already resolved by the claim above; in
# legacy mode this predicts it with the same resume-first priority rule,
# reusing instincts.py's own private helpers (_deps_done, _PRIORITY_ORDER)
# instead of re-deriving the logic a third time -- if it can't confidently
# predict a task, it skips the section rather than guess.
#
# PYTHONIOENCODING is forced to utf-8 for every python3 call in this block
# (saved/restored around it, PS 5.1 has no env(1)-style scoping): Python on
# Windows defaults a piped/redirected child's stdout to the OS ANSI
# codepage, not UTF-8, and atlas.py's own em-dashes/arrows then crash with
# UnicodeEncodeError before a single byte reaches us -- silently defeating
# the fail-open path's actual purpose (which is to show the section when
# atlas genuinely succeeds, not to paper over an avoidable encoding crash
# on the platform this was built and tested on).
$AtlasDbPath = Join-Path $RepoRoot ".devteam\atlas.db"
if (Test-Path $AtlasDbPath) {
    $PrevPyIoEncoding = $env:PYTHONIOENCODING
    $env:PYTHONIOENCODING = "utf-8"
    $AtlasEnabled = "False"
    try {
        $AtlasEnabled = (& $Py -c "
import json
try:
    print(bool((json.load(open('autopilot.json')).get('atlas') or {}).get('enabled', False)))
except Exception:
    print('False')
" 2>$null | Out-String).Trim()
    } catch {
        $AtlasEnabled = "False"
    }
    if ($AtlasEnabled -eq "True") {
        $AtlasTaskId = $TaskId
        if (-not $AtlasTaskId) {
            try {
                $AtlasTaskId = (& $Py -c "
import sys; sys.path.insert(0, 'scripts')
import instincts
from pathlib import Path
from validate_plan import parse_tasks, Report
try:
    text = Path('PLAN.md').read_text(encoding='utf-8')
    rep = Report()
    all_tasks = parse_tasks(text, rep)
    by_id = {t.task_id: t for t in all_tasks}
    mine = [t for t in all_tasks if t.get('Assigned_To') == '$Id']
    resuming = [t for t in mine if t.get('Status') in ('in_progress', 'claimed')]
    if resuming:
        target = resuming[0]
    else:
        pending = [t for t in mine if t.get('Status') == 'pending' and instincts._deps_done(t, by_id)]
        pending.sort(key=lambda t: (instincts._PRIORITY_ORDER.get(t.get('Priority'), 4), t.task_id))
        target = pending[0] if pending else None
    print(target.task_id if target else '')
except Exception:
    print('')
" 2>$null | Out-String).Trim()
            } catch {
                $AtlasTaskId = ""
            }
        }
        if ($AtlasTaskId) {
            $AtlasBudget = "3000"
            try {
                $AtlasBudget = (& $Py -c "
import json
try:
    print(int((json.load(open('autopilot.json')).get('atlas') or {}).get('budget_tokens', 3000)))
except Exception:
    print(3000)
" 2>$null | Out-String).Trim()
            } catch {
                $AtlasBudget = "3000"
            }
            $AtlasSection = ""
            $AtlasOk = $false
            # REFRESH BEFORE PACK (fix 2026-08-15). This path called `pack` only,
            # and NOTHING anywhere called `scan` -- so the index was only ever
            # rebuilt when a human ran it by hand. Measured consequence: the last
            # manual scan was 09:14Z, and by 15:09Z the map was 8 merged tasks and
            # 30 files out of date (213 indexed vs 243 real). Every builder
            # dispatched in that window received a PROJECT MAP of a repo that no
            # longer existed -- `atlas query consumeApproval` and `redactPayload`
            # both returned nothing for code that was already merged, and `impact`
            # returned empty for a file that genuinely had importers. `pack` opens
            # the db and updates its mtime, which made the index look fresh on
            # inspection while its contents aged silently; that is why this went
            # unnoticed for six hours.
            #
            # A fresh scan restored all three (impact resolved, symbols resolved),
            # so the features were starving, not broken. Scan is incremental and
            # costs ~10s cold, far less when little changed -- negligible against a
            # builder session, and cheap insurance against handing a builder a map
            # of the wrong repo. Failure is non-fatal: pack still runs against
            # whatever index exists, and pack's own freshness footer reports the
            # scan timestamp, so a degraded map is visible rather than silent.
            # Retry once. Concurrent dispatch is the NORMAL mode for this pack --
            # wave 7 launched two builders 4s apart -- and both scans write the same
            # sqlite .devteam/atlas.db, so a cold scan (one with changes to write,
            # i.e. a long write transaction) can lose the lock and error. Observed
            # live on the first concurrent wave after this scan step was added; a
            # `changed: 0` scan does not reproduce it because the write window is
            # too short to contend. One retry after a short pause clears it, since
            # by then the other dispatch's scan has usually committed and this one
            # finds `changed: 0`. Still non-fatal on a second failure: packing
            # against a stale index is the pre-fix behaviour, and the warning makes
            # it visible rather than silent -- which is the property that matters,
            # since a silently stale index is exactly what went unnoticed for six
            # hours before this step existed.
            $AtlasScanOk = $false
            foreach ($attempt in 1, 2) {
                try {
                    & $Py "scripts\atlas.py" "scan" 2>$null | Out-Null
                    if ($LASTEXITCODE -eq 0) { $AtlasScanOk = $true; break }
                } catch { }
                if ($attempt -eq 1) { Start-Sleep -Seconds 3 }
            }
            if (-not $AtlasScanOk) {
                Write-Warning "[dispatch] atlas scan failed twice (concurrent dispatch can contend on .devteam/atlas.db) - packing against the existing, possibly stale index."
            }
            try {
                $AtlasSection = (& $Py "scripts\atlas.py" "pack" "--task" $AtlasTaskId "--budget" $AtlasBudget 2>$null | Out-String)
                $AtlasOk = ($LASTEXITCODE -eq 0)
            } catch {
                $AtlasOk = $false
            }
            if ($AtlasOk -and $AtlasSection.Trim().Length -gt 0) {
                # ASCII hyphen here (spec / dispatch.sh use an em-dash) is a deliberate
                # PS 5.1 ANSI-codepage-safety choice, not an oversight: a literal em-dash
                # in a UTF-8-without-BOM .ps1 risks mis-decoding on a non-UTF-8 console
                # codepage. Cosmetic only — ATLAS ships disabled by default.
                $Prompt = $Prompt + "`r`n`r`n## PROJECT MAP (ATLAS) - a map, not the ground`r`n" + $AtlasSection.Trim() + "`r`n"
            } else {
                Write-Warning "[dispatch] atlas pack failed for $AtlasTaskId - dispatching without the ATLAS section."
            }
        }
    }
    if ($null -eq $PrevPyIoEncoding) { Remove-Item Env:\PYTHONIOENCODING -ErrorAction SilentlyContinue }
    else { $env:PYTHONIOENCODING = $PrevPyIoEncoding }
}

# v4.7: per-unit auth, resolved BEFORE the dry-run branch so previews are
# accurate about it. config_dir mode sets CLAUDE_CONFIG_DIR for the launch
# only -- saved and restored around each builder invocation, never left set
# for the rest of this script's life (PS 5.1 has no env(1)-style scoping,
# so save/restore in finally is the equivalent).
$AuthDir = $null
if ($AuthMode -eq "config_dir" -and $AuthValue) {
    $AuthDir = $AuthValue -replace '^~', $env:USERPROFILE
    Write-Host "[dispatch] Unit $Id authenticates via CLAUDE_CONFIG_DIR=$AuthDir (scoped to this launch)." -ForegroundColor Cyan
}
$AuthNote = ""
if ($AuthDir) { $AuthNote = "CLAUDE_CONFIG_DIR=$AuthDir " }

# PROMPT QUOTING (fix 2026-08-15, oikonomos live failure — GB/TASK-020 died in 2s).
#
# Windows PowerShell 5.1 does NOT escape embedded double quotes when it builds the
# command line for a native exe. The target's CRT argv parser then re-splits the
# argument at an unbalanced quote run, so ONE prompt arrives as TWO argv entries.
# Observed exactly: grok got `-p <first-half>` plus a positional `<remainder>` and
# refused with "the argument '--single <PROMPT>' cannot be used with '[PROMPT]'",
# the split landing mid-ATLAS-excerpt. Measured with an argv probe on the real
# 3392-char prompt (42 double quotes): ARGC=6 unescaped, ARGC=5 escaped.
#
# This is CONTENT-DEPENDENT, which is why GB succeeded on TASK-001/004/008/010 and
# then failed here: the ATLAS project-map section varies per task, and the index was
# rebuilt at 27faec6 just before this wave. A latent intermittent, not a regression.
#
# Applies to every CLI that takes the prompt as an ARGUMENT — grok AND claude (S5),
# whose prompt is likewise a trailing positional. S5 has not tripped it yet; it is
# the same landmine, so it is fixed here rather than left for a future wave.
# codex is exempt: it already routes the prompt through stdin ($PromptViaStdin),
# because a file has no quoting — the same principle, applied one layer earlier.
#
# Escaping is content-preserving: verified that `{"control_version": 1` arrives with
# real quotes and no literal \" over-escaping, which matters because a mangled
# control-block spec is what produced S5's invalid devteam-control block on TASK-003.
$PromptArg = if ($PromptViaStdin) { $Prompt } else { $Prompt -replace '"', '\"' }

if ($DryRun) {
    Write-Host "[dispatch] DRY RUN - would run: (cd $Wt ; $AuthNote$Cmd $($CmdArgs -join ' ') `"<prompt>`")" -ForegroundColor Yellow
    Write-Host "--- Prompt ---"
    Write-Host $Prompt
    exit 0
}

Write-Host "[dispatch] Launching $Builder ($Id) in $Wt..." -ForegroundColor Green

# hooks/lib.js's unit() defaults to 'ORCH' (unrestricted) when DEVTEAM_UNIT
# is unset -- harmless for grok/codex (neither reads .claude/settings.json,
# so territory-firewall.js never fires for them at all), but load-bearing
# for S5: it runs the literal `claude` CLI, which DOES load the hooks, so
# without this the firewall would silently treat S5 as unrestricted ORCH
# instead of enforcing its Owned_Paths. Set for every builder regardless,
# for consistency and to future-proof if GB/CX ever gain hook support.
$env:DEVTEAM_UNIT = $Id

if ($ControlMode -eq "strict") {
    $DevteamDir = Join-Path $RepoRoot ".devteam\runs"
    if (-not (Test-Path $DevteamDir)) { New-Item -ItemType Directory -Path $DevteamDir -Force | Out-Null }
    $RunTs = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH-mm-ssZ")
    $LogPath = Join-Path $DevteamDir "$TaskId-$RunTs.log"

    Push-Location $Wt
    $PrevConfigDir = $env:CLAUDE_CONFIG_DIR
    $PrevEAP = $ErrorActionPreference
    try {
        if ($AuthDir) { $env:CLAUDE_CONFIG_DIR = $AuthDir }
        # EAP Continue during the builder run: under Stop, the first native
        # stderr line routed through `2>&1 |` throws (PS 5.1 NativeCommandError)
        # and aborts the pipeline before the log is written — codex's version
        # banner on stderr killed a live dispatch this way (2026-08-14). The
        # legacy-mode runner already sets Continue for the same reason.
        $ErrorActionPreference = "Continue"
        if ($PromptViaStdin) {
            # Multiline prompt cannot survive a cmd /c argument (see codex case
            # comment). Write it to a file and redirect into `codex exec -`.
            # Log redirection also happens INSIDE cmd (raw UTF-8, no PS streams,
            # no Tee-Object UTF-16, no stderr-to-pipeline exception surface).
            $PromptFile = Join-Path $DevteamDir "$TaskId-$RunTs.prompt.txt"
            [System.IO.File]::WriteAllText($PromptFile, $Prompt, (New-Object System.Text.UTF8Encoding($false)))
            $NativeArgs = @($CmdArgs | Select-Object -Skip 1)  # drop leading /c
            $CmdLine = ($NativeArgs -join " ") + " - < `"$PromptFile`" > `"$LogPath`" 2>&1"
            & cmd /c $CmdLine
            if (Test-Path $LogPath) { Get-Content $LogPath | Write-Host }
        } else {
            & $Cmd @($CmdArgs + @($PromptArg)) 2>&1 | Tee-Object -FilePath $LogPath
        }
    } catch {
        Write-Warning "[dispatch] Builder process error: $($_.Exception.Message)"
    } finally {
        if ($null -eq $PrevConfigDir) { Remove-Item Env:\CLAUDE_CONFIG_DIR -ErrorAction SilentlyContinue }
        else { $env:CLAUDE_CONFIG_DIR = $PrevConfigDir }
        Pop-Location
        $ErrorActionPreference = $PrevEAP
    }

    # Re-encode the captured log to UTF-8 (no BOM) before extraction: PS 5.1's
    # Tee-Object writes UTF-16 LE, and control.py reads utf-8 — the fence regex
    # can never match interleaved NULs (live failure 2026-08-14, oikonomos:
    # builder emitted a valid CONTROL block, extract said UNREPORTED).
    if (Test-Path $LogPath) {
        $RawLog = Get-Content $LogPath -Raw
        [System.IO.File]::WriteAllText($LogPath, $RawLog, (New-Object System.Text.UTF8Encoding($false)))
    }
    Write-Host "[dispatch] Session ended. Extracting devteam-control block..." -ForegroundColor Cyan
    $ExtractOut = (& $Py "scripts\control.py" "extract" "--log" $LogPath "--task" $TaskId "--unit" $Id "--repo" $RepoRoot | Out-String).Trim()
    Write-Host "[dispatch] $ExtractOut"
    if ($ExtractOut -like "UNREPORTED:*") {
        Write-Warning "[dispatch] NOTE: no CONTROL block found - PLAN.md state will not change until the next supervisor tick's fallback handling. Log: $LogPath"
    }
    Write-Host "[dispatch] control.mode=strict: PLAN.md is applied by the supervisor's next tick, not here. Run /devteam-status once it has ticked." -ForegroundColor Green
    exit 0
} elseif (-not $InProcess) {
    # ---- Detached-window launch (default in legacy mode) --------------------
    # Everything the builder needs goes into a generated runner script and a
    # sibling prompt file. That is deliberate: the prompt is ~2 KB of prose
    # containing quotes, backticks and newlines, and every attempt to pass it
    # through a command line gets mangled by one shell layer or another -- S5
    # once refused a dispatch as prompt injection because embedded quotes
    # truncated its identity override mid-sentence. A file has no quoting.
    $LaunchDir = Join-Path $RepoRoot ".devteam\launch"
    if (-not (Test-Path $LaunchDir)) { New-Item -ItemType Directory -Path $LaunchDir -Force | Out-Null }
    $Stamp      = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
    $PromptPath = Join-Path $LaunchDir "$Id-$Stamp.prompt.txt"
    $RunnerPath = Join-Path $LaunchDir "$Id-$Stamp.run.ps1"
    $LogPath    = Join-Path $LaunchDir "$Id-$Stamp.log"

    # UTF8 without BOM: PowerShell 5.1 reads a BOM'd .ps1 fine, but the CLIs
    # choke on a BOM at the head of the prompt text.
    $Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($PromptPath, $Prompt, $Utf8NoBom)

    $RunnerLines = @(
        '# Auto-generated by scripts/dispatch.ps1 -- one builder session, one window.',
        '# Safe to delete once the session has ended.',
        "`$ErrorActionPreference = 'Continue'",
        "Set-Location -LiteralPath '$Wt'",
        "`$env:DEVTEAM_UNIT = '$Id'"
    )
    if ($AuthDir) { $RunnerLines += "`$env:CLAUDE_CONFIG_DIR = '$AuthDir'" }
    $RunnerLines += @(
        "`$Prompt = [System.IO.File]::ReadAllText('$PromptPath')"
    )
    if (-not $PromptViaStdin) {
        # Same PS 5.1 embedded-quote defect as the in-process path (see the
        # $PromptArg comment above). The file read hands back the RAW prompt, so
        # the escape has to happen here too -- the file solves BOM and newlines,
        # not argv splitting.
        $RunnerLines += "`$Prompt = `$Prompt -replace '`"', '\`"'"
    }
    $RunnerLines += @(
        "Write-Host '[$Id] starting in $Wt' -ForegroundColor Green",
        # Same invocation shape as the in-process path below -- one flat array of
        # args with the prompt appended -- so both paths pass arguments identically.
        "& '$Cmd' (@('$($CmdArgs -join "','")') + @(`$Prompt)) 2>&1 | Tee-Object -FilePath '$LogPath'",
        "Write-Host ''",
        "Write-Host '[$Id] session ended. Run /devteam-status in the ORCH session.' -ForegroundColor Cyan",
        "Write-Host 'This window stays open so the transcript is readable; close it when done.' -ForegroundColor DarkGray"
    )
    [System.IO.File]::WriteAllLines($RunnerPath, $RunnerLines, $Utf8NoBom)

    Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoExit", "-ExecutionPolicy", "Bypass", "-File", $RunnerPath) `
        -WorkingDirectory $Wt | Out-Null

    Write-Host "[dispatch] $Id launched in its OWN window - detached from this process tree." -ForegroundColor Green
    Write-Host "[dispatch]   transcript: $LogPath"
    Write-Host "[dispatch]   runner:     $RunnerPath"
    Write-Host "[dispatch] This script does NOT wait. PLAN.md will change as the builder works;" -ForegroundColor Cyan
    Write-Host "[dispatch] run /devteam-status to follow it. Use -InProcess to block instead." -ForegroundColor Cyan
    exit 0
} else {
    Push-Location $Wt
    $PrevConfigDir = $env:CLAUDE_CONFIG_DIR
    try {
        if ($AuthDir) { $env:CLAUDE_CONFIG_DIR = $AuthDir }
        & $Cmd @($CmdArgs + @($PromptArg))
    } catch {
        Write-Warning "[dispatch] Builder process error: $($_.Exception.Message)"
    } finally {
        if ($null -eq $PrevConfigDir) { Remove-Item Env:\CLAUDE_CONFIG_DIR -ErrorAction SilentlyContinue }
        else { $env:CLAUDE_CONFIG_DIR = $PrevConfigDir }
        Pop-Location
    }

    Write-Host "[dispatch] Session ended. Re-validating PLAN.md..." -ForegroundColor Cyan
    & $Py "scripts\validate_plan.py" "PLAN.md"
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "[dispatch] PLAN.md now protocol-illegal - builder violated protocol. Inspect: git log -p -- PLAN.md"
        exit 1
    }
    Write-Host "[dispatch] Done. Run /devteam-status in Claude Code for the health scan." -ForegroundColor Green
}
