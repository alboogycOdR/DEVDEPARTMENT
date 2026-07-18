#!/usr/bin/env bash
# dispatch.sh — launch a builder (grok|codex) headlessly against PLAN.md.
# Usage: scripts/dispatch.sh grok [--dry-run]
set -euo pipefail

BUILDER="${1:?Usage: dispatch.sh <grok|codex> [--dry-run]}"
DRY="${2:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

case "$BUILDER" in
  grok)  ID="GB"; WT="$(dirname "$REPO_ROOT")/wt-grok";  CMD=(grok --always-approve --permission-mode bypassPermissions); BRIEFING="briefings/GROK_BUILD_BRIEFING.md"; SUFFIX="gb" ;;
  codex) ID="CX"; WT="$(dirname "$REPO_ROOT")/wt-codex"; CMD=(codex exec --model gpt-5.6-sol --reasoning-effort medium -s danger-full-access); BRIEFING="briefings/CODEX_BRIEFING.md"; SUFFIX="cx" ;;
  *) echo "Unknown builder: $BUILDER" >&2; exit 1 ;;
esac

echo "[dispatch] Validating PLAN.md..."
python3 scripts/validate_plan.py PLAN.md || { echo "[dispatch] PLAN.md illegal — fix before dispatching." >&2; exit 1; }

if [[ ! -d "$WT" ]]; then
  echo "[dispatch] Creating worktree at $WT..."
  git worktree add --detach "$WT" main
fi

PROMPT="You are $ID, a builder in a multi-agent dev team. Working directory: $WT (your isolated git worktree; coordination PLAN.md lives at $REPO_ROOT/PLAN.md on main).
Procedure: (1) Read AGENTS.md and $BRIEFING, then PLAN.md, fresh from disk. If dossiers/TASK-NNN.md exists for your task, read it in full before acting and append a Work Log entry each session — never ask for re-explanation of anything in the dossier. (2) RESUME CHECK FIRST — scan PLAN.md for any task with Assigned_To: $ID and Status: in_progress or claimed. If found, resume that task immediately: re-read its Owned_Paths files and the last Progress_Note to find the exact stopping point, then continue on the existing branch (do not re-claim or re-branch). Only if NO in_progress/claimed task exists: claim the highest-priority pending task Assigned_To: $ID whose dependencies are done — one atomic edit+commit setting Status: claimed, Branch: task/TASK-NNN-$SUFFIX, Started_At. (3) Create (or switch to) the task branch in your worktree and implement strictly against the task's Spec_References, touching ONLY files under its Owned_Paths. (4) Test everything; append Test_Evidence. (5) Append-only Progress_Notes with UTC timestamps and [$ID] tags — if your context is approaching its limit, write a detailed stopping-point note (what is done, what file, exact next step) and commit before stopping. (6) Finish at needs_review (never done), or blocked with a vocabulary reason. Conventional Commits ending [TASK-NNN]. Never write to specs/, docs/, REVIEW.md, scripts/, .claude/, other task blocks, or main."

if [[ "$DRY" == "--dry-run" ]]; then
  echo "[dispatch] DRY RUN — would run: (cd $WT && ${CMD[*]} \"<prompt>\")"
  printf -- '--- Prompt ---\n%s\n' "$PROMPT"
  exit 0
fi

echo "[dispatch] Launching $BUILDER ($ID) in $WT..."
( cd "$WT" && "${CMD[@]}" "$PROMPT" ) || true

echo "[dispatch] Session ended. Re-validating PLAN.md..."
python3 scripts/validate_plan.py PLAN.md || {
  echo "[dispatch] WARNING: PLAN.md now protocol-illegal — builder violated protocol. Inspect: git log -p -- PLAN.md" >&2
  exit 1
}
echo "[dispatch] Done. Run /status in Claude Code for the health scan."
