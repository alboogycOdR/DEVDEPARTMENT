<#
.SYNOPSIS
    Record a PLAN.md coordination change. The ONLY supported way for a builder
    to claim, transition status, add a Progress_Note, or hand off needs_review.
.DESCRIPTION
    Windows mirror of scripts/plan_commit.sh. Targets Windows PowerShell 5.1 --
    do NOT add "#requires -Version 7".

    WHY THIS EXISTS
    The previous instruction was, from the builder's worktree:
        git add PLAN.md && git commit -m "..." && git push . HEAD:<base>
    That is correct exactly once -- on claim, before any code is committed -- and
    silently wrong every time after. By `needs_review` the builder's HEAD sits
    on top of its own code commits, so `push . HEAD:<base>` pushes the whole
    chain and lands unreviewed code on the integration branch, bypassing the
    merge gate. Observed three times across two builder CLIs; one builder
    escaped only by inventing a `git commit-tree` workaround. Tooling bug, not
    discipline: the failing command is indistinguishable from the working one.

    WHAT THIS DOES INSTEAD
    PLAN.md lives in the main checkout, which has the integration branch
    checked out. Commit it there with an explicit pathspec:
        git -C <repo-root> commit -m "<msg>" -- PLAN.md
    No push, no HEAD, no rebase. The pathspec form bypasses the index and
    commits only PLAN.md's working-tree content, so carrying code onto the
    integration branch is impossible by construction, not by instruction.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\plan_commit.ps1 "chore(plan): claim TASK-007 [S5]"
#>
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Message
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Plan = Join-Path $RepoRoot "PLAN.md"

if (-not (Test-Path $Plan)) {
    Write-Error "[plan_commit] no PLAN.md at $Plan"
    exit 1
}

# Integration branch from autopilot.json (pack default: main). Fail-safe --
# never an invented branch. Mirrors dispatch.ps1's resolution exactly.
$BaseBranch = "main"
$CfgPath = Join-Path $RepoRoot "autopilot.json"
if (Test-Path $CfgPath) {
    try {
        $Cfg = Get-Content $CfgPath -Raw | ConvertFrom-Json
        if ($Cfg.git -and $Cfg.git.base_branch) { $BaseBranch = $Cfg.git.base_branch }
    } catch { $BaseBranch = "main" }
}

# The main checkout must actually be on the integration branch. If someone has
# moved it, committing here would put coordination state on a ref nobody reads.
$Current = (git -C $RepoRoot rev-parse --abbrev-ref HEAD 2>$null)
if ($Current -ne $BaseBranch) {
    Write-Error "[plan_commit] main checkout ($RepoRoot) is on '$Current', expected '$BaseBranch'. Refusing to commit -- coordination state must land on the integration branch. Tell ORCH; do not work around this."
    exit 1
}

git -C $RepoRoot diff --quiet -- PLAN.md
if ($LASTEXITCODE -eq 0) {
    Write-Host "[plan_commit] PLAN.md has no uncommitted changes -- nothing to record."
    exit 0
}

# Retry around index.lock: two builders committing coordination state seconds
# apart is legitimate, and the collision is transient rather than an error.
for ($attempt = 1; $attempt -le 5; $attempt++) {
    git -C $RepoRoot commit -q -m $Message -- PLAN.md 2>$null
    if ($LASTEXITCODE -eq 0) {
        $sha = (git -C $RepoRoot rev-parse --short HEAD)
        Write-Host "[plan_commit] recorded on ${BaseBranch}: $sha  $Message" -ForegroundColor Green
        exit 0
    }
    if ($attempt -lt 5) { Start-Sleep -Seconds 2 }
}

Write-Error "[plan_commit] failed after 5 attempts (index.lock contention, or nothing to commit). Re-run once; if it persists, report it rather than committing by hand."
exit 1
