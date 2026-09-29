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

function Stop-RecordedRunner([string]$WorktreePath) {
    $LaunchDir = Join-Path $RepoRoot ".devteam\launch"
    if (-not (Test-Path -LiteralPath $LaunchDir)) { return }
    $WorktreeFull = [System.IO.Path]::GetFullPath($WorktreePath)
    foreach ($PidFile in (Get-ChildItem -LiteralPath $LaunchDir -Filter "*.pid" -File -ErrorAction SilentlyContinue)) {
      $PidPath = $PidFile.FullName
      try {
        $Record = Get-Content -LiteralPath $PidPath -Raw -ErrorAction Stop | ConvertFrom-Json
        if (-not $Record.worktree -or
            -not [System.IO.Path]::GetFullPath([string]$Record.worktree).Equals($WorktreeFull, [System.StringComparison]::OrdinalIgnoreCase)) {
            continue
        }
        $RunnerPid = 0
        if (-not [int]::TryParse([string]$Record.pid, [ref]$RunnerPid) -or $RunnerPid -le 0 -or -not $Record.runner) {
            Write-Warning "[worktree] Invalid runner PID record at $PidPath; refusing to stop an unverified process."
            continue
        }
        $ExpectedRunner = [System.IO.Path]::GetFullPath([string]$Record.runner)
        $AllowedPrefix = [System.IO.Path]::GetFullPath($LaunchDir).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
        if (-not $ExpectedRunner.StartsWith($AllowedPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
            -not $ExpectedRunner.EndsWith(".run.ps1", [System.StringComparison]::OrdinalIgnoreCase)) {
            Write-Warning "[worktree] Runner path in $PidPath is outside .devteam\launch; refusing to stop PID $RunnerPid."
            continue
        }
        $Process = Get-Process -Id $RunnerPid -ErrorAction SilentlyContinue
        if (-not $Process) {
            Remove-Item -LiteralPath $PidPath -Force
            Write-Host "[worktree] Removed stale runner PID record $PidPath."
            return
        }
        $Info = Get-CimInstance -ClassName Win32_Process -Filter "ProcessId = $RunnerPid" -ErrorAction SilentlyContinue
        $ExpectedTicks = [long]$Record.started_utc_ticks
        if ($Process.ProcessName -notin @("powershell", "pwsh") -or
            [long]$Process.StartTime.ToUniversalTime().Ticks -ne $ExpectedTicks -or
            -not $Info -or $Info.CommandLine.IndexOf($ExpectedRunner, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
            Write-Warning "[worktree] PID $RunnerPid no longer matches the recorded runner at $PidPath; refusing to stop it."
            continue
        }
        Stop-Process -Id $RunnerPid -Force -ErrorAction Stop
        Wait-Process -Id $RunnerPid -Timeout 10 -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $PidPath -Force
        Write-Host "[worktree] Stopped runner PID $RunnerPid for worktree $WorktreePath."
      } catch {
        Write-Warning "[worktree] Could not verify or stop runner from ${PidPath}: $($_.Exception.Message)"
      }
    }
}

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
            Stop-RecordedRunner $t.Path
            git worktree remove $t.Path --force
            if ($LASTEXITCODE -ne 0) {
                $EmptySource = Join-Path ([System.IO.Path]::GetTempPath()) ("devteam-empty-" + [guid]::NewGuid().ToString("N"))
                New-Item -ItemType Directory -Path $EmptySource -Force | Out-Null
                try {
                    $LongTarget = "\\?\" + [System.IO.Path]::GetFullPath($t.Path)
                    robocopy $EmptySource $LongTarget /MIR /R:1 /W:1 /NFL /NDL /NJH /NJS | Out-Null
                    if ($LASTEXITCODE -gt 7) { throw "robocopy cleanup failed with exit code $LASTEXITCODE" }
                } finally {
                    Remove-Item -LiteralPath $EmptySource -Force -ErrorAction SilentlyContinue
                }
                git worktree remove $t.Path --force
            }
            if ($LASTEXITCODE -ne 0) { throw "git worktree remove failed for $($t.Path) (exit $LASTEXITCODE)" }
            $LaunchDir = Join-Path $RepoRoot ".devteam\launch"
            if (Test-Path -LiteralPath $LaunchDir) {
                foreach ($PidFile in (Get-ChildItem -LiteralPath $LaunchDir -Filter "*.pid" -File -ErrorAction SilentlyContinue)) {
                    try {
                        $Record = Get-Content -LiteralPath $PidFile.FullName -Raw | ConvertFrom-Json
                        if ($Record.worktree -and [System.IO.Path]::GetFullPath([string]$Record.worktree).Equals(
                                [System.IO.Path]::GetFullPath($t.Path), [System.StringComparison]::OrdinalIgnoreCase)) {
                            Remove-Item -LiteralPath $PidFile.FullName -Force
                        }
                    } catch { }
                }
            }
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
