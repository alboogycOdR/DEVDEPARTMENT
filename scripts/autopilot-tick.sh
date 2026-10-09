#!/usr/bin/env bash
# DEVDEPARTMENT autopilot -- one supervisor tick, intended to be run on a
# schedule (cron / launchd). macOS/Linux mirror of autopilot-tick.ps1. The
# supervisor holds no state of its own (state lives in .devteam/ and PLAN.md),
# so a single tick per invocation is exactly equivalent to `supervisor.py
# --loop` and, unlike a long-lived loop, survives reboots and crashes with no
# recovery logic: the next scheduled tick simply resumes.
#
# Task Scheduler's "do not start a new instance if one is still running" has no
# cron equivalent, so this script holds its own lock (.devteam/autopilot-tick.lock,
# mkdir-based: macOS ships no flock). A long tick (a review can take many
# minutes) is therefore never overlapped; a tick that finds a live lock exits 0.
#
# Schedule examples (every 5 minutes, matching interval_seconds=300):
#   cron:    */5 * * * * /bin/bash /path/to/repo/scripts/autopilot-tick.sh
#   launchd: a LaunchAgent with ProgramArguments [/bin/bash, <this script>]
#            and StartInterval 300.
#
# SAFETY RAILS:
#   * Create a file named STOP in the repo root to halt every tick immediately.
#   * Autopilot merges reviewed work to the integration branch. It NEVER deploys:
#     applying migrations, rebuilding and restarting services stay a deliberate,
#     separate step.
set -uo pipefail

# Location-independent: resolve the repo root from this script's own path.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || exit 1

# cron/launchd start jobs with a bare PATH (/usr/bin:/bin), which finds none of
# the builder CLIs (claude, codex, grok) or node. Prepend the usual install dirs.
PATH="$HOME/.local/bin:$HOME/.grok/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
# Optional per-machine Node 22 folder: set DEVTEAM_NODE22_DIR (a folder containing node) if Node 22 is not already first on PATH.
if [[ -n "${DEVTEAM_NODE22_DIR:-}" && -x "$DEVTEAM_NODE22_DIR/node" ]]; then PATH="$DEVTEAM_NODE22_DIR:$PATH"; fi
export PATH

LOG_DIR="$REPO_ROOT/.devteam/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/autopilot-tick.log"

LOCK="$REPO_ROOT/.devteam/autopilot-tick.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  HOLDER="$(cat "$LOCK/pid" 2>/dev/null || true)"
  if [[ -n "$HOLDER" ]] && kill -0 "$HOLDER" 2>/dev/null; then
    exit 0  # previous tick still running
  fi
  # Stale lock from a tick that was killed hard: take it over.
  rm -rf "$LOCK"
  mkdir "$LOCK" 2>/dev/null || exit 0
fi
echo $$ > "$LOCK/pid"
trap 'rm -rf "$LOCK"' EXIT

# Keep the log bounded (rotate at ~2 MB, keep one previous generation).
if [[ -f "$LOG" ]] && (( $(wc -c < "$LOG") > 2097152 )); then mv -f "$LOG" "$LOG.1"; fi

echo "=== tick $(date -u +%Y-%m-%dT%H:%M:%SZ) ===" >> "$LOG"
python3 scripts/supervisor.py --once >> "$LOG" 2>&1
echo "=== end (exit $?) ===" >> "$LOG"
