<#
.SYNOPSIS
    DEVDEPARTMENT harness security & integrity gate. Windows mirror of harness-audit.sh.
.DESCRIPTION
    Targets Windows PowerShell 5.1. Three layers, fails on any breach:
      1. AgentShield scan of the agent-config surface (via npx; skippable with -NoShield)
      2. Protocol validator on PLAN.md
      3. Internal test suites (pytest + node hook tests)
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File scripts\harness-audit.ps1
    powershell -ExecutionPolicy Bypass -File scripts\harness-audit.ps1 -NoShield
#>
param(
    [switch]$NoShield
)

$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$Fail = $false

function Get-PythonCmd {
    foreach ($c in @("python", "python3", "py")) {
        if (Get-Command $c -ErrorAction SilentlyContinue) { return $c }
    }
    return $null
}
$Py = Get-PythonCmd

function Write-Section([string]$Title) { Write-Host "`n=== $Title ===" -ForegroundColor Cyan }

# --- 1. AgentShield ----------------------------------------------------------------
Write-Section "1/3 AgentShield harness scan"
if ($NoShield) {
    Write-Host "SKIPPED (-NoShield). Run the full gate before any release."
} elseif (Get-Command npx -ErrorAction SilentlyContinue) {
    & npx --yes ecc-agentshield scan
    $rc = $LASTEXITCODE
    if ($rc -ge 2) {
        Write-Host "FAIL: AgentShield reported critical findings (exit $rc). Fix before proceeding." -ForegroundColor Red
        $Fail = $true
    } elseif ($rc -ne 0) {
        Write-Host "WARN: AgentShield exited $rc (non-critical findings or tool error). Review output above." -ForegroundColor Yellow
    } else {
        Write-Host "OK: AgentShield clean." -ForegroundColor Green
    }
} else {
    Write-Host "WARN: npx not found - AgentShield skipped. Install Node.js to enable the harness scan." -ForegroundColor Yellow
}

# --- 2. Protocol validator -----------------------------------------------------------
Write-Section "2/3 PLAN.md protocol validator"
if (-not $Py) {
    Write-Host "FAIL: No Python interpreter found on PATH (tried python, python3, py)." -ForegroundColor Red
    $Fail = $true
} elseif (Test-Path "PLAN.md") {
    & $Py "scripts\validate_plan.py" "PLAN.md"
    if ($LASTEXITCODE -ne 0) { Write-Host "FAIL: PLAN.md protocol-illegal." -ForegroundColor Red; $Fail = $true }
} else {
    Write-Host "No PLAN.md at repo root - skipped."
}

# --- 3. Internal test suites -----------------------------------------------------------
Write-Section "3/3 Internal test suites"
if ($Py -and (Test-Path "tests")) {
    & $Py -m pytest tests\ -q
    if ($LASTEXITCODE -ne 0) { Write-Host "FAIL: python test suite red." -ForegroundColor Red; $Fail = $true }
}
if (Test-Path "hooks\run-tests.js") {
    if (Get-Command node -ErrorAction SilentlyContinue) {
        & node "hooks\run-tests.js"
        if ($LASTEXITCODE -ne 0) { Write-Host "FAIL: hook test suite red." -ForegroundColor Red; $Fail = $true }
    } else {
        Write-Host "WARN: node not found - hook tests skipped (hooks layer requires Node.js)." -ForegroundColor Yellow
    }
}

if ($Fail) {
    Write-Host "`n=== HARNESS AUDIT: FAIL ===" -ForegroundColor Red
    exit 1
} else {
    Write-Host "`n=== HARNESS AUDIT: PASS ===" -ForegroundColor Green
    exit 0
}
