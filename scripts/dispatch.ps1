<#
.SYNOPSIS
    Dispatch a builder (Grok Build or Codex AI) against PLAN.md, headlessly.
.DESCRIPTION
    Validates PLAN.md, ensures the builder's worktree exists, then launches the
    builder CLI with a condensed protocol prompt. Adjust $GrokCmd / $CodexCmd to
    match your installed CLI binary names and headless flags.
.EXAMPLE
    .\scripts\dispatch.ps1 -Builder grok
    .\scripts\dispatch.ps1 -Builder codex -DryRun
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet("grok", "codex")][string]$Builder,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$RepoName = Split-Path $RepoRoot -Leaf

# --- 1. Gate on protocol-legal plan -----------------------------------------
Write-Host "[dispatch] Validating PLAN.md..." -ForegroundColor Cyan
python "$RepoRoot\scripts\validate_plan.py" "$RepoRoot\PLAN.md"
if ($LASTEXITCODE -ne 0) {
    Write-Error "[dispatch] PLAN.md is protocol-illegal. Fix violations before dispatching builders."
    exit 1
}

# --- 2. Builder config -------------------------------------------------------
# EDIT THESE to match your installed CLIs (binary name + headless prompt flag).
$Config = @{
    grok = @{
        Id       = "GB"
        Worktree = Join-Path (Split-Path $RepoRoot -Parent) "wt-$RepoName-grok"
        Cmd      = "grok"
        # Headless agentic flags. NOT `-p` (single-turn -- prints once, no tool use).
        Args     = @("--always-approve", "--permission-mode", "bypassPermissions")
        Briefing = "briefings\GROK_BUILD_BRIEFING.md"
        Suffix   = "gb"
    }
    codex = @{
        Id       = "CX"
        Worktree = Join-Path (Split-Path $RepoRoot -Parent) "wt-$RepoName-codex"
        Cmd      = "codex"
        # `-s danger-full-access` required: PLAN.md is in the main repo root, outside
        # the worktree, so read-only / workspace-write sandboxes block claiming tasks.
        Args     = @("exec", "--dangerously-bypass-approvals-and-sandbox", "--model", "gpt-5.5")
        Briefing = "briefings\CODEX_BRIEFING.md"
        Suffix   = "cx"
    }
}
$B = $Config[$Builder]

# --- 3. Ensure worktree ------------------------------------------------------
if (-not (Test-Path $B.Worktree)) {
    Write-Host "[dispatch] Creating worktree at $($B.Worktree)..." -ForegroundColor Cyan
    # --detach: main is already checked out in the primary worktree; git forbids a
    # named branch in two worktrees. Builder creates its own task branch after.
    git worktree add --detach $B.Worktree main
    if ($LASTEXITCODE -ne 0) { Write-Error "[dispatch] git worktree add failed."; exit 1 }
}

# --- 4. Condensed headless prompt ---------------------------------------------
$RepoPlan = Join-Path $RepoRoot "PLAN.md"
$Prompt = @"
You are $($B.Id), a builder in a multi-agent dev team. Working directory: $($B.Worktree) (your isolated git worktree; the coordination PLAN.md currently lives at $RepoPlan on branch main -- but ALL your PLAN.md commits happen on your own task branch, never directly on main).
Procedure: (1) Read AGENTS.md and briefings/$(Split-Path $B.Briefing -Leaf) in the repo, then PLAN.md, fresh from disk. (2) RESUME CHECK FIRST -- scan PLAN.md for any task with Assigned_To: $($B.Id) and Status: in_progress or claimed. If found, resume it immediately: re-read its Owned_Paths files and the last Progress_Note to find the exact stopping point, then continue on the existing branch (do not re-claim or re-branch). Only if NO in_progress/claimed task exists: claim the highest-priority pending task Assigned_To: $($B.Id) whose dependencies are done. (3) BRANCH FIRST, THEN COMMIT: create/switch to task/TASK-NNN-$($B.Suffix) in your worktree; run 'git branch --show-current' and confirm it prints task/TASK-NNN-$($B.Suffix), not main; only then make one atomic commit ON THAT BRANCH setting Status: claimed, Branch: task/TASK-NNN-$($B.Suffix), Started_At. Implement strictly against the task's Spec_References, touching ONLY files under its Owned_Paths. Every later PLAN.md commit (in_progress, needs_review) must also land on this branch, never main -- re-check 'git branch --show-current' before each one. (4) Test everything; append Test_Evidence. (5) Append-only Progress_Notes with UTC timestamps and [$($B.Id)] tags -- if context is running low, write a detailed stopping-point note and commit before stopping. (6) Finish at needs_review (never done), or blocked with a vocabulary reason. Conventional Commits ending [TASK-NNN]. Never write to specs/, docs/, REVIEW.md, scripts/, .claude/, other task blocks, or main.
"@

if ($DryRun) {
    Write-Host "[dispatch] DRY RUN -- would execute:" -ForegroundColor Yellow
    Write-Host "  cd $($B.Worktree)"
    Write-Host "  $($B.Cmd) $($B.Args -join ' ') `"<prompt>`""
    Write-Host "`n--- Prompt ---`n$Prompt"
    exit 0
}

# --- 5. Launch -----------------------------------------------------------------
Write-Host "[dispatch] Launching $Builder ($($B.Id)) in $($B.Worktree)..." -ForegroundColor Green
Push-Location $B.Worktree
try {
    & $B.Cmd @($B.Args + $Prompt)
    $exit = $LASTEXITCODE
} finally {
    Pop-Location
}

# --- 6. Post-session validation -------------------------------------------------
Write-Host "[dispatch] Builder session ended (exit $exit). Re-validating PLAN.md..." -ForegroundColor Cyan
python "$RepoRoot\scripts\validate_plan.py" "$RepoRoot\PLAN.md"
if ($LASTEXITCODE -ne 0) {
    Write-Warning "[dispatch] PLAN.md is now protocol-illegal -- builder violated the protocol. Review 'git log -p -- PLAN.md' before proceeding."
    exit 1
}
Write-Host "[dispatch] Done. Run /status in Claude Code for the health scan." -ForegroundColor Green
