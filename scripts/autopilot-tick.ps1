# DEVDEPARTMENT autopilot -- one supervisor tick, intended to be run on a
# schedule (Task Scheduler / cron). The supervisor holds no state of its own
# (state lives in .devteam/ and PLAN.md), so a single tick per invocation is
# exactly equivalent to `supervisor.py --loop` and, unlike a long-lived loop,
# survives reboots and crashes with no recovery logic: the next scheduled tick
# simply resumes. The scheduler's "do not start a new instance if one is still
# running" setting means a long tick (a review can take many minutes) is never
# overlapped.
#
# Port of 7baeedf3 (file as of that SHA; originated in 2484988).
# Location-independent: repo root comes from this script's own path. Project-
# specific names, roster, and compose-log paths are stripped.
#
# SAFETY RAILS:
#   * Create a file named STOP in the repo root to halt every tick immediately.
#   * Autopilot merges reviewed work to the integration branch. It NEVER deploys:
#     applying migrations, rebuilding and restarting services stay a deliberate,
#     separate step.
$ErrorActionPreference = 'Continue'
# Location-independent (2026-09-25): resolve the repo root from this script's own path, so the same file
# works on any machine without editing.
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $RepoRoot

# Optional per-machine Node 22 folder: set DEVTEAM_NODE22_DIR (a folder containing node.exe) if Node 22 is not already first on PATH.
if ($env:DEVTEAM_NODE22_DIR -and (Test-Path (Join-Path $env:DEVTEAM_NODE22_DIR 'node.exe'))) { $env:Path = $env:DEVTEAM_NODE22_DIR + ';' + $env:Path }

$LogDir = Join-Path $RepoRoot '.devteam\logs'
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
$Log = Join-Path $LogDir 'autopilot-tick.log'

# Keep the log bounded (rotate at ~2 MB, keep one previous generation).
if ((Test-Path $Log) -and ((Get-Item $Log).Length -gt 2MB)) { Move-Item -Path $Log -Destination ($Log + '.1') -Force }

Add-Content -Path $Log -Value ("=== tick {0} ===" -f (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ'))
& python scripts/supervisor.py --once 2>&1 | ForEach-Object { Add-Content -Path $Log -Value ([string]$_) }
Add-Content -Path $Log -Value ("=== end (exit {0}) ===" -f $LASTEXITCODE)
