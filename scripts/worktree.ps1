<#
.SYNOPSIS
    Manage per-builder git worktrees.
.EXAMPLE
    .\scripts\worktree.ps1 -Action create           # both worktrees
    .\scripts\worktree.ps1 -Action create -Builder grok
    .\scripts\worktree.ps1 -Action status
    .\scripts\worktree.ps1 -Action remove -Builder codex
#>
param(
    [Parameter(Mandatory = $true)][ValidateSet("create", "remove", "status")][string]$Action,
    [ValidateSet("grok", "codex", "all")][string]$Builder = "all"
)
$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
$Parent = Split-Path $RepoRoot -Parent
$ProjectName = Split-Path $RepoRoot -Leaf
Set-Location $RepoRoot

$Targets = @()
if ($Builder -in @("grok", "all"))  { $Targets += @{ Name = "grok";  Path = Join-Path $Parent "wt-grok-$ProjectName" } }
if ($Builder -in @("codex", "all")) { $Targets += @{ Name = "codex"; Path = Join-Path $Parent "wt-codex-$ProjectName" } }

# Integration branch from autopilot.json git.base_branch, matching dispatch.ps1.
# This script used to hardcode "main". Any project whose integration branch is
# NOT main (set via autopilot.json git.base_branch) would have `-Action create`
# hand a builder a tree with no PLAN.md and none of the plan's work in it. It
# went unnoticed because dispatch.ps1 creates worktrees itself from base_branch,
# so this path is only reached when the script is run directly. Fail-safe default
# stays "main" for projects that genuinely use it; an unreadable or malformed
# config must not silently invent a branch name.
$BaseBranch = "main"
try {
    $GitCfg = Get-Content (Join-Path $RepoRoot "autopilot.json") -Raw -ErrorAction Stop | ConvertFrom-Json
    if ($GitCfg.git -and $GitCfg.git.base_branch) { $BaseBranch = $GitCfg.git.base_branch }
} catch {
    Write-Warning "[worktree] Could not read autopilot.json git.base_branch - falling back to '$BaseBranch'."
}

switch ($Action) {
    "create" {
        foreach ($t in $Targets) {
            if (Test-Path $t.Path) { Write-Host "[worktree] $($t.Name): already exists at $($t.Path)"; continue }
            git worktree add $t.Path $BaseBranch
            Write-Host "[worktree] $($t.Name): created at $($t.Path)" -ForegroundColor Green
        }
    }
    "remove" {
        foreach ($t in $Targets) {
            if (-not (Test-Path $t.Path)) { Write-Host "[worktree] $($t.Name): not present"; continue }
            git worktree remove $t.Path --force
            Write-Host "[worktree] $($t.Name): removed" -ForegroundColor Yellow
        }
        git worktree prune
    }
    "status" {
        git worktree list
        Write-Host "`n[worktree] Task branches:" -ForegroundColor Cyan
        git branch --list "task/*"
    }
}
