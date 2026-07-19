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

switch ($Action) {
    "create" {
        foreach ($t in $Targets) {
            if (Test-Path $t.Path) { Write-Host "[worktree] $($t.Name): already exists at $($t.Path)"; continue }
            git worktree add $t.Path main
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
