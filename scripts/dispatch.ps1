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
    [switch]$DryRun
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
if (-not $Id -or -not $Cli -or -not $Reg["WORKTREE_SUFFIX"] -or -not $Suffix -or -not $Briefing) {
    Write-Error "[dispatch] Registry resolution for '$Builder' returned an incomplete entry - refusing to dispatch."
    exit 1
}
$Wt = Join-Path $ParentDir ("wt-" + $Reg["WORKTREE_SUFFIX"] + "-" + $ProjectName)

# CLI-invocation table — keyed by CLI FAMILY, not unit (S5/S5B share the
# claude row). These quirks are properties of the CLI binaries, not project
# config, so they stay here:
switch ($Cli) {
    "grok" {
        # Bare `grok <prompt>` starts the INTERACTIVE TUI with a trust-dialog
        # that hangs headless dispatches (confirmed live, multi-hour hang);
        # -p switches to single-turn non-interactive mode. -p must be LAST:
        # $Prompt is appended right after this array at the call site.
        $Cmd = "grok"
        $CmdArgs = @("--always-approve", "--permission-mode", "bypassPermissions", "-p")
    }
    "codex" {
        # Routed through cmd /c: npm's codex.ps1 shim spuriously pipes $input
        # (ExpectingInput) under splatted-array invocation from a script,
        # blocking on stdin ("stdin is not a terminal") -- reproduced live.
        # cmd /c invokes codex.cmd, which has no such pipeline semantics.
        # --reasoning-effort is NOT a valid codex exec flag (codex-cli 0.144.5);
        # model_reasoning_effort is authoritative via .codex/config.toml.
        $Cmd = "cmd"
        $CmdArgs = @("/c", "codex", "exec")
        if ($Model) { $CmdArgs += @("--model", $Model) }
        $CmdArgs += @("-s", "danger-full-access")
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

if (Test-Path $Wt) {
    # Reuse only if it's actually a registered worktree of THIS repo -- not
    # just "a directory happens to be sitting there."
    $registeredRaw = git -C $RepoRoot worktree list --porcelain | Where-Object { $_ -like "worktree *" } | ForEach-Object { $_.Substring(9) }
    $registeredNorm = $registeredRaw | ForEach-Object { Normalize-WtPath $_ }
    $wtNorm = Normalize-WtPath $Wt
    if ($registeredNorm -notcontains $wtNorm) {
        Write-Error "[dispatch] $Wt exists but is not a registered worktree of this repo ($RepoRoot)."
        Write-Error "[dispatch] This usually means a stale or foreign directory occupies the expected worktree path."
        Write-Error "[dispatch] Inspect it manually, then either remove it or let git reclaim it, and re-run dispatch:"
        Write-Error "[dispatch]   git worktree list   (from $RepoRoot, to see what git actually knows about)"
        exit 1
    }
} else {
    Write-Host "[dispatch] Creating worktree at $Wt..." -ForegroundColor Cyan
    git worktree add --detach $Wt main
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
if ($AutoLoadsContext) {
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
    try {
        if ($AuthDir) { $env:CLAUDE_CONFIG_DIR = $AuthDir }
        & $Cmd @($CmdArgs + @($Prompt)) 2>&1 | Tee-Object -FilePath $LogPath
    } catch {
        Write-Warning "[dispatch] Builder process error: $($_.Exception.Message)"
    } finally {
        if ($null -eq $PrevConfigDir) { Remove-Item Env:\CLAUDE_CONFIG_DIR -ErrorAction SilentlyContinue }
        else { $env:CLAUDE_CONFIG_DIR = $PrevConfigDir }
        Pop-Location
    }

    Write-Host "[dispatch] Session ended. Extracting devteam-control block..." -ForegroundColor Cyan
    $ExtractOut = (& $Py "scripts\control.py" "extract" "--log" $LogPath "--task" $TaskId "--unit" $Id "--repo" $RepoRoot | Out-String).Trim()
    Write-Host "[dispatch] $ExtractOut"
    if ($ExtractOut -like "UNREPORTED:*") {
        Write-Warning "[dispatch] NOTE: no CONTROL block found - PLAN.md state will not change until the next supervisor tick's fallback handling. Log: $LogPath"
    }
    Write-Host "[dispatch] control.mode=strict: PLAN.md is applied by the supervisor's next tick, not here. Run /devteam-status once it has ticked." -ForegroundColor Green
    exit 0
} else {
    Push-Location $Wt
    $PrevConfigDir = $env:CLAUDE_CONFIG_DIR
    try {
        if ($AuthDir) { $env:CLAUDE_CONFIG_DIR = $AuthDir }
        & $Cmd @($CmdArgs + @($Prompt))
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
