<#
.SYNOPSIS
    Dispatch a builder (grok|codex) headlessly against PLAN.md. Windows mirror of dispatch.sh.
.DESCRIPTION
    Targets Windows PowerShell 5.1 (Windows default). Do NOT add "#requires -Version 7".
    Behaviour is 1:1 with scripts/dispatch.sh: validate plan -> ensure detached worktree ->
    resume-first builder prompt -> launch -> re-validate.
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

# Resolve a Python launcher that exists on this box (Windows usually has `python` or `py`).
function Get-PythonCmd {
    foreach ($c in @("python", "python3", "py")) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { return $c }
    }
    throw "No Python interpreter found on PATH (tried python, python3, py)."
}
$Py = Get-PythonCmd

# --- Builder config (mirror of dispatch.sh case block; amendments 55ce44a + 779746f) ---
switch ($Builder) {
    "grok" {
        $Id = "GB"; $Suffix = "gb"
        $Wt = Join-Path $ParentDir "wt-grok"
        $Cmd = "grok"
        $CmdArgs = @("--always-approve", "--permission-mode", "bypassPermissions")
        $Briefing = "briefings/GROK_BUILD_BRIEFING.md"
    }
    "codex" {
        $Id = "CX"; $Suffix = "cx"
        $Wt = Join-Path $ParentDir "wt-codex"
        $Cmd = "codex"
        $CmdArgs = @("exec", "--model", "gpt-5.6-sol", "--reasoning-effort", "medium", "-s", "danger-full-access")
        $Briefing = "briefings/CODEX_BRIEFING.md"
    }
}

# --- 1. Validate plan -------------------------------------------------------------
Write-Host "[dispatch] Validating PLAN.md..." -ForegroundColor Cyan
& $Py "scripts\validate_plan.py" "PLAN.md"
if ($LASTEXITCODE -ne 0) {
    Write-Error "[dispatch] PLAN.md illegal - fix before dispatching."
    exit 1
}

# --- 2. Ensure detached worktree ---------------------------------------------------
if (-not (Test-Path $Wt)) {
    Write-Host "[dispatch] Creating worktree at $Wt..." -ForegroundColor Cyan
    git worktree add --detach $Wt main
    if ($LASTEXITCODE -ne 0) { Write-Error "[dispatch] git worktree add failed."; exit 1 }
}

# --- 3. Resume-first prompt (verbatim mirror of dispatch.sh; protocol section 10a) ---
$PlanPath = Join-Path $RepoRoot "PLAN.md"
$Prompt = @"
You are $Id, a builder in a multi-agent dev team. Working directory: $Wt (your isolated git worktree; coordination PLAN.md lives at $PlanPath on main).
Procedure: (1) Read AGENTS.md and $Briefing, then PLAN.md, fresh from disk. If dossiers/TASK-NNN.md exists for your task, read it in full before acting and append a Work Log entry each session - never ask for re-explanation of anything in the dossier. (2) RESUME CHECK FIRST - scan PLAN.md for any task with Assigned_To: $Id and Status: in_progress or claimed. If found, resume that task immediately: re-read its Owned_Paths files and the last Progress_Note to find the exact stopping point, then continue on the existing branch (do not re-claim or re-branch). Only if NO in_progress/claimed task exists: claim the highest-priority pending task Assigned_To: $Id whose dependencies are done - one atomic edit+commit setting Status: claimed, Branch: task/TASK-NNN-$Suffix, Started_At. (3) Create (or switch to) the task branch in your worktree and implement strictly against the task's Spec_References, touching ONLY files under its Owned_Paths. (4) Test everything; append Test_Evidence. (5) Append-only Progress_Notes with UTC timestamps and [$Id] tags - if your context is approaching its limit, write a detailed stopping-point note (what is done, what file, exact next step) and commit before stopping. (6) Finish at needs_review (never done), or blocked with a vocabulary reason. Conventional Commits ending [TASK-NNN]. Never write to specs/, docs/, REVIEW.md, scripts/, .claude/, other task blocks, or main.
"@

# --- 3b. Wave C: inject project instincts (fail-open) -------------------------------
# --unit rather than --paths: this script doesn't pre-resolve which task $Id will end
# up claiming (that happens inside the builder's own resume-first/claim logic above),
# so instincts.py predicts the same task via the same priority rule.
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

# --- 4. Launch --------------------------------------------------------------------
Write-Host "[dispatch] Launching $Builder ($Id) in $Wt..." -ForegroundColor Green
Push-Location $Wt
try {
    & $Cmd @($CmdArgs + @($Prompt))
} catch {
    Write-Warning "[dispatch] Builder process error: $($_.Exception.Message)"
} finally {
    Pop-Location
}

# --- 5. Re-validate ----------------------------------------------------------------
Write-Host "[dispatch] Session ended. Re-validating PLAN.md..." -ForegroundColor Cyan
& $Py "scripts\validate_plan.py" "PLAN.md"
if ($LASTEXITCODE -ne 0) {
    Write-Warning "[dispatch] PLAN.md now protocol-illegal - builder violated protocol. Inspect: git log -p -- PLAN.md"
    exit 1
}
Write-Host "[dispatch] Done. Run /devteam-status in Claude Code for the health scan." -ForegroundColor Green
