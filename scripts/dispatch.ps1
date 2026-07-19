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
    [Parameter(Mandatory = $true)][ValidateSet("grok", "codex")][string]$Builder,
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

switch ($Builder) {
    "grok" {
        $Id = "GB"; $Suffix = "gb"
        $Wt = Join-Path $ParentDir "wt-grok-$ProjectName"
        $Cmd = "grok"
        $CmdArgs = @("--always-approve", "--permission-mode", "bypassPermissions")
        $Briefing = "briefings/GROK_BUILD_BRIEFING.md"
    }
    "codex" {
        $Id = "CX"; $Suffix = "cx"
        $Wt = Join-Path $ParentDir "wt-codex-$ProjectName"
        # --reasoning-effort is not a valid `codex exec` CLI flag (confirmed against
        # codex-cli 0.144.5 -- it errors "unexpected argument"); model_reasoning_effort
        # is already authoritative via .codex/config.toml, per that file's own comment.
        #
        # Routed through cmd /c rather than invoking codex(.ps1) directly: npm's
        # generated .ps1 shim (AppData\Roaming\npm\codex.ps1) checks
        # $MyInvocation.ExpectingInput and pipes $input into the underlying node
        # process when true. That check spuriously fires when this splatted-array
        # invocation pattern (`& $Cmd @($CmdArgs + @($Prompt))`) is used from inside
        # a script -- reproduced live: codex then blocks on stdin and fails with
        # "Error: stdin is not a terminal" in any non-interactive/background
        # invocation, even though the exact same args work fine called literally.
        # cmd /c invokes codex.cmd instead, which has no such pipeline semantics.
        $Cmd = "cmd"
        $CmdArgs = @("/c", "codex", "exec", "--model", "gpt-5.6-sol", "-s", "danger-full-access")
        $Briefing = "briefings/CODEX_BRIEFING.md"
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
$LegacyName = if ($Builder -eq "grok") { "wt-grok" } else { "wt-codex" }
$LegacyWt = Join-Path $ParentDir $LegacyName
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
if ($ControlMode -eq "strict") {
    $Prompt = @"
You are $Id, a builder in a multi-agent dev team. Working directory: $Wt (your isolated git worktree; coordination PLAN.md lives at $PlanPath on main - control.mode=strict: you never write PLAN.md yourself).
Your task is $TaskId ($ResumeOrClaim by the dispatcher before this session started - do not re-claim or re-branch).
Procedure: (1) Read AGENTS.md and $Briefing, then PLAN.md, fresh from disk, for $TaskId's Spec_References/Owned_Paths/Acceptance_Criteria. Read dossiers/$TaskId.md in full if it exists (your prior work log) before acting. (2) If resuming: continue on the existing branch task/$TaskId-$Suffix at the exact stopping point recorded in the dossier. If newly claimed: create branch task/$TaskId-$Suffix in your worktree. (3) Implement strictly against Spec_References, touching ONLY files under Owned_Paths plus your own dossier (dossiers/$TaskId.md - append a Work Log entry at minimum every ~30 minutes of work and at every stopping point; it is your heartbeat, since you never touch PLAN.md for this). (4) Test everything. (5) Emit a devteam-control block as the LAST thing you print, fenced exactly like this:
${Fence}devteam-control
{"control_version": 1, "task": "$TaskId", "unit": "$Id", "status": "needs_review", "progress_note": "...", "artifacts": ["path/a.dart"], "test_evidence": "...", "blocked_reason": null, "next_step": null}
${Fence}
status must be exactly one of in_progress (mid-session checkpoint - dossier note + next_step, nothing else changes) / needs_review (requires non-empty test_evidence) / blocked (blocked_reason must start with SPEC_AMBIGUITY, MISSING_DEPENDENCY, OWNERSHIP_CONFLICT, SYNC_MISMATCH, TOOLING_FAILURE, or OTHER:). Never done/pending/claimed - those are the supervisor's alone. Conventional Commits ending [$TaskId] for your code commits (never for PLAN.md - you don't touch it). Never write to specs/, docs/, REVIEW.md, scripts/, .claude/, PLAN.md, other dossiers, or main.
"@
} else {
    $Prompt = @"
You are $Id, a builder in a multi-agent dev team. Working directory: $Wt (your isolated git worktree; coordination PLAN.md lives at $PlanPath on main).
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

if ($DryRun) {
    Write-Host "[dispatch] DRY RUN - would run: (cd $Wt ; $Cmd $($CmdArgs -join ' ') `"<prompt>`")" -ForegroundColor Yellow
    Write-Host "--- Prompt ---"
    Write-Host $Prompt
    exit 0
}

Write-Host "[dispatch] Launching $Builder ($Id) in $Wt..." -ForegroundColor Green

if ($ControlMode -eq "strict") {
    $DevteamDir = Join-Path $RepoRoot ".devteam\runs"
    if (-not (Test-Path $DevteamDir)) { New-Item -ItemType Directory -Path $DevteamDir -Force | Out-Null }
    $RunTs = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH-mm-ssZ")
    $LogPath = Join-Path $DevteamDir "$TaskId-$RunTs.log"

    Push-Location $Wt
    try {
        & $Cmd @($CmdArgs + @($Prompt)) 2>&1 | Tee-Object -FilePath $LogPath
    } catch {
        Write-Warning "[dispatch] Builder process error: $($_.Exception.Message)"
    } finally {
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
    try {
        & $Cmd @($CmdArgs + @($Prompt))
    } catch {
        Write-Warning "[dispatch] Builder process error: $($_.Exception.Message)"
    } finally {
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
