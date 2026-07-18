#!/usr/bin/env bash
# harness-audit.sh — DEVDEPARTMENT harness security & integrity gate (Wave 2).
#
# Runs three layers and fails on the first breach:
#   1. AgentShield scan of the agent-harness config surface (CLAUDE.md,
#      settings/hooks, MCP configs, skills, agent definitions). Exit code 2 on
#      critical findings gates the run. Requires network for `npx` on first use.
#   2. Protocol validator on PLAN.md.
#   3. Internal test suites (validator + supervisor + hooks).
#
# Usage:
#   bash scripts/harness-audit.sh            # full gate
#   bash scripts/harness-audit.sh --no-shield  # offline mode: skip AgentShield
#
# Run this before every DEVDEPARTMENT release/upgrade and after any change to
# hooks/, .claude/, .codex/, autopilot.json, or the scripts/ directory.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
FAIL=0
SKIP_SHIELD=0
[[ "${1:-}" == "--no-shield" ]] && SKIP_SHIELD=1

section() { printf '\n=== %s ===\n' "$1"; }

# --- 1. AgentShield -----------------------------------------------------------
section "1/3 AgentShield harness scan"
if [[ "$SKIP_SHIELD" -eq 1 ]]; then
  echo "SKIPPED (--no-shield). Run the full gate before any release."
elif command -v npx >/dev/null 2>&1; then
  npx --yes ecc-agentshield scan
  rc=$?
  if [[ $rc -ge 2 ]]; then
    echo "FAIL: AgentShield reported critical findings (exit $rc). Fix before proceeding." >&2
    FAIL=1
  elif [[ $rc -ne 0 ]]; then
    echo "WARN: AgentShield exited $rc (non-critical findings or tool error). Review output above."
  else
    echo "OK: AgentShield clean."
  fi
else
  echo "WARN: npx not found — AgentShield skipped. Install Node.js to enable the harness scan."
fi

# --- 2. Protocol validator ------------------------------------------------------
section "2/3 PLAN.md protocol validator"
if [[ -f PLAN.md ]]; then
  python3 scripts/validate_plan.py PLAN.md || { echo "FAIL: PLAN.md protocol-illegal." >&2; FAIL=1; }
else
  echo "No PLAN.md at repo root — skipped."
fi

# --- 3. Internal test suites ------------------------------------------------------
section "3/3 Internal test suites"
if [[ -d tests ]]; then
  python3 -m pytest tests/ -q || { echo "FAIL: python test suite red." >&2; FAIL=1; }
fi
if [[ -f hooks/run-tests.js ]]; then
  node hooks/run-tests.js || { echo "FAIL: hook test suite red." >&2; FAIL=1; }
fi

printf '\n=== HARNESS AUDIT: %s ===\n' "$([[ $FAIL -eq 0 ]] && echo PASS || echo FAIL)"
exit $FAIL
