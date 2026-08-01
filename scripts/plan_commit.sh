#!/usr/bin/env bash
# plan_commit.sh — the ONLY supported way for a builder to record a PLAN.md
# coordination change (claim, status transition, Progress_Note, needs_review).
#
#   scripts/plan_commit.sh "chore(plan): claim TASK-007 [S5]"
#
# WHY THIS EXISTS
# ---------------
# The previous instruction was:
#     git add PLAN.md && git commit -m "..." && git push . HEAD:<base>
# run from the builder's worktree. That is correct exactly once — on claim,
# before any code has been committed — and silently wrong every time after.
# By the time a builder reaches `needs_review` its HEAD sits on top of its own
# code commits, so `push . HEAD:<base>` pushes the entire chain and lands
# unreviewed code directly on the integration branch, bypassing the merge gate.
#
# Observed three times across two different builder CLIs before being fixed;
# one builder escaped only by inventing a `git commit-tree` workaround. It is a
# tooling bug, not a discipline problem: the failing command looks identical to
# the working one.
#
# WHAT THIS DOES INSTEAD
# ----------------------
# PLAN.md lives in the main checkout, which has the integration branch checked
# out. So commit it there, with an explicit pathspec:
#     git -C <repo-root> commit -m "<msg>" -- PLAN.md
# No push. No HEAD. No rebase dance. The pathspec form bypasses the index
# entirely, so it commits ONLY PLAN.md's working-tree content and cannot pick
# up code, staged or otherwise. Carrying code onto the integration branch is
# impossible by construction rather than by instruction.

set -euo pipefail

MSG="${1:-}"
if [ -z "$MSG" ]; then
  echo "usage: scripts/plan_commit.sh \"chore(plan): <what> [UNIT]\"" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
PLAN="$REPO_ROOT/PLAN.md"

if [ ! -f "$PLAN" ]; then
  echo "[plan_commit] no PLAN.md at $PLAN" >&2
  exit 1
fi

# Integration branch from autopilot.json (pack default: main). Fail-safe, never
# an invented branch — mirrors dispatch.sh/.ps1's resolution exactly.
# Read via stdin, not a path argument: under Git Bash on Windows this script
# sees POSIX paths (/c/...) while python3 may be native Windows Python, which
# cannot open them. Redirection is resolved by the shell, so stdin always works.
BASE_BRANCH="main"
if [ -f "$REPO_ROOT/autopilot.json" ]; then
  BASE_BRANCH="$(python3 -c "
import json,sys
try:
    print(json.load(sys.stdin).get('git',{}).get('base_branch') or 'main')
except Exception:
    print('main')
" < "$REPO_ROOT/autopilot.json" 2>/dev/null || echo main)"
  [ -z "$BASE_BRANCH" ] && BASE_BRANCH="main"
fi

# The main checkout must actually be on the integration branch — if someone has
# moved it, committing here would land the coordination state somewhere nobody
# is reading. Refuse rather than write to the wrong ref.
CURRENT="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")"
if [ "$CURRENT" != "$BASE_BRANCH" ]; then
  echo "[plan_commit] main checkout ($REPO_ROOT) is on '$CURRENT', expected '$BASE_BRANCH'." >&2
  echo "[plan_commit] Refusing to commit — coordination state must land on the integration branch." >&2
  echo "[plan_commit] Tell ORCH; do not work around this." >&2
  exit 1
fi

if git -C "$REPO_ROOT" diff --quiet -- PLAN.md; then
  echo "[plan_commit] PLAN.md has no uncommitted changes — nothing to record."
  exit 0
fi

# Retry around index.lock: two builders can legitimately commit coordination
# state seconds apart, and that collision is transient, not an error.
for attempt in 1 2 3 4 5; do
  if git -C "$REPO_ROOT" commit -q -m "$MSG" -- PLAN.md 2>/dev/null; then
    echo "[plan_commit] recorded on $BASE_BRANCH: $(git -C "$REPO_ROOT" rev-parse --short HEAD)  $MSG"
    exit 0
  fi
  if [ "$attempt" -lt 5 ]; then
    sleep 2
  fi
done

echo "[plan_commit] failed after 5 attempts (index.lock contention, or nothing to commit)." >&2
echo "[plan_commit] Re-run once; if it persists, report it rather than committing by hand." >&2
exit 1
