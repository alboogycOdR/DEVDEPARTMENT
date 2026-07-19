#!/usr/bin/env bash
# dispatch.sh — launch a builder (grok|codex) headlessly against PLAN.md.
# Usage: scripts/dispatch.sh grok [--dry-run]
#
# Wave I (I1): mode-aware. control.mode=legacy (default) behaves exactly as
# before — builder scans PLAN.md, claims its own task, edits PLAN.md itself.
# control.mode=strict flips to claim-at-dispatch (this script claims/resumes
# the task before launch via scripts/control.py) plus stdout capture and
# devteam-control fence extraction after the session ends. No hybrid: the
# mode gate below is a hard branch, never a partial mix of both behaviors.
set -euo pipefail

BUILDER="${1:?Usage: dispatch.sh <grok|codex> [--dry-run]}"
DRY="${2:-}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Worktree paths are namespaced by this project's own folder name (not just
# "wt-grok"/"wt-codex") because they're created as SIBLINGS of the project
# root, not siblings of DEVDEPARTMENT itself. Two DEVDEPARTMENT-onboarded
# projects sharing a parent directory (a common layout) would otherwise
# compute the exact same worktree path and silently collide — dispatch
# would treat a stale worktree from a DIFFERENT project as its own and
# hand a builder the wrong repo's checkout.
PROJECT_NAME="$(basename "$REPO_ROOT")"

case "$BUILDER" in
  grok)  ID="GB"; WT="$(dirname "$REPO_ROOT")/wt-grok-${PROJECT_NAME}";  CMD=(grok --always-approve --permission-mode bypassPermissions); BRIEFING="briefings/GROK_BUILD_BRIEFING.md"; SUFFIX="gb" ;;
  # --reasoning-effort is not a valid `codex exec` CLI flag (confirmed against
  # codex-cli 0.144.5 -- it errors "unexpected argument"); model_reasoning_effort
  # is already authoritative via .codex/config.toml, per that file's own comment.
  codex) ID="CX"; WT="$(dirname "$REPO_ROOT")/wt-codex-${PROJECT_NAME}"; CMD=(codex exec --model gpt-5.6-sol -s danger-full-access); BRIEFING="briefings/CODEX_BRIEFING.md"; SUFFIX="cx" ;;
  *) echo "Unknown builder: $BUILDER" >&2; exit 1 ;;
esac

echo "[dispatch] Validating PLAN.md..."
python3 scripts/validate_plan.py PLAN.md || { echo "[dispatch] PLAN.md illegal — fix before dispatching." >&2; exit 1; }

# Warn (not block) if an OLD-style unnamespaced worktree sits at the legacy
# path — a leftover from before this fix, or from a pre-fix dispatch of
# this exact project. It's orphaned now, not reused, so it's safe to leave,
# but flag it so it doesn't sit there silently confusing a future `ls`.
LEGACY_WT=""
case "$BUILDER" in grok) LEGACY_WT="$(dirname "$REPO_ROOT")/wt-grok" ;; codex) LEGACY_WT="$(dirname "$REPO_ROOT")/wt-codex" ;; esac
if [[ -d "$LEGACY_WT" && "$LEGACY_WT" != "$WT" ]]; then
  echo "[dispatch] NOTE: found an old-style unnamespaced worktree at $LEGACY_WT (pre-dates per-project namespacing)." >&2
  echo "[dispatch] It is NOT being used by this dispatch. If it belongs to this project, remove it with:" >&2
  echo "[dispatch]   git worktree remove \"$LEGACY_WT\" --force   (run from $REPO_ROOT)" >&2
fi

if [[ -d "$WT" ]]; then
  # Reuse only if it's actually a registered worktree of THIS repo — not
  # just "a directory happens to be sitting there." Catches the case where
  # a foreign/stale directory occupies the expected path for any reason.
  REGISTERED_WORKTREES="$(git -C "$REPO_ROOT" worktree list --porcelain | awk '/^worktree /{ $1=""; sub(/^ /,""); print }')"
  if ! grep -qxF "$WT" <<< "$REGISTERED_WORKTREES"; then
    echo "[dispatch] ERROR: $WT exists but is not a registered worktree of this repo ($REPO_ROOT)." >&2
    echo "[dispatch] This usually means a stale or foreign directory occupies the expected worktree path." >&2
    echo "[dispatch] Inspect it manually, then either remove it or let git reclaim it, and re-run dispatch:" >&2
    echo "[dispatch]   git worktree list   (from $REPO_ROOT, to see what git actually knows about)" >&2
    exit 1
  fi
else
  echo "[dispatch] Creating worktree at $WT..."
  git worktree add --detach "$WT" main
fi

# Wave I: control.mode from autopilot.json. Fail-safe default: legacy —
# an unreadable/missing/malformed config must never silently switch a live
# dispatch into strict behavior it isn't prepared for.
CONTROL_MODE="$(python3 -c "
import json, sys
try:
    cfg = json.load(open('autopilot.json'))
    m = (cfg.get('control') or {}).get('mode')
    print('strict' if m == 'strict' else 'legacy')
except Exception:
    print('legacy')
" 2>/dev/null || echo legacy)"

TASK_ID=""
RESUME_OR_CLAIM=""
if [[ "$CONTROL_MODE" == "strict" ]]; then
  CLAIM_ARGS=(claim --unit "$ID" --repo "$REPO_ROOT")
  if [[ "$DRY" == "--dry-run" ]]; then
    CLAIM_ARGS+=(--dry-run)
  fi
  CLAIM_OUT="$(python3 scripts/control.py "${CLAIM_ARGS[@]}")"
  case "$CLAIM_OUT" in
    RESUME:*)  TASK_ID="${CLAIM_OUT#RESUME:}";  RESUME_OR_CLAIM="resuming" ;;
    CLAIMED:*) TASK_ID="${CLAIM_OUT#CLAIMED:}"; RESUME_OR_CLAIM="claimed" ;;
    NONE:*)
      echo "[dispatch] Nothing to dispatch for $ID (${CLAIM_OUT#NONE:}) — skipping launch."
      exit 0
      ;;
    *)
      echo "[dispatch] WARNING: unexpected claim output '$CLAIM_OUT' — treating as nothing to dispatch." >&2
      exit 0
      ;;
  esac
  echo "[dispatch] $RESUME_OR_CLAIM $TASK_ID for $ID (control.mode=strict$( [[ "$DRY" == "--dry-run" ]] && echo ", DRY RUN — no write performed" ))."
fi

if [[ "$CONTROL_MODE" == "strict" ]]; then
  PROMPT="You are $ID, a builder in a multi-agent dev team. Working directory: $WT (your isolated git worktree; coordination PLAN.md lives at $REPO_ROOT/PLAN.md on main — control.mode=strict: you never write PLAN.md yourself).
Your task is $TASK_ID ($RESUME_OR_CLAIM by the dispatcher before this session started — do not re-claim or re-branch).
Procedure: (1) Read AGENTS.md and $BRIEFING, then PLAN.md, fresh from disk, for $TASK_ID's Spec_References/Owned_Paths/Acceptance_Criteria. Read dossiers/$TASK_ID.md in full if it exists (your prior work log) before acting. (2) If resuming: continue on the existing branch task/$TASK_ID-$SUFFIX at the exact stopping point recorded in the dossier. If newly claimed: create branch task/$TASK_ID-$SUFFIX in your worktree. (3) Implement strictly against Spec_References, touching ONLY files under Owned_Paths plus your own dossier (dossiers/$TASK_ID.md — append a Work Log entry at minimum every ~30 minutes of work and at every stopping point; it is your heartbeat, since you never touch PLAN.md for this). (4) Test everything. (5) Emit a devteam-control block as the LAST thing you print, fenced exactly like this:
\`\`\`devteam-control
{\"control_version\": 1, \"task\": \"$TASK_ID\", \"unit\": \"$ID\", \"status\": \"needs_review\", \"progress_note\": \"...\", \"artifacts\": [\"path/a.dart\"], \"test_evidence\": \"...\", \"blocked_reason\": null, \"next_step\": null}
\`\`\`
status must be exactly one of in_progress (mid-session checkpoint — dossier note + next_step, nothing else changes) / needs_review (requires non-empty test_evidence) / blocked (blocked_reason must start with SPEC_AMBIGUITY, MISSING_DEPENDENCY, OWNERSHIP_CONFLICT, SYNC_MISMATCH, TOOLING_FAILURE, or OTHER:). Never done/pending/claimed — those are the supervisor's alone. Conventional Commits ending [$TASK_ID] for your code commits (never for PLAN.md — you don't touch it). Never write to specs/, docs/, REVIEW.md, scripts/, .claude/, PLAN.md, other dossiers, or main."
else
  PROMPT="You are $ID, a builder in a multi-agent dev team. Working directory: $WT (your isolated git worktree; coordination PLAN.md lives at $REPO_ROOT/PLAN.md on main).
Procedure: (1) Read AGENTS.md and $BRIEFING, then PLAN.md, fresh from disk. If dossiers/TASK-NNN.md exists for your task, read it in full before acting and append a Work Log entry each session — never ask for re-explanation of anything in the dossier. (2) RESUME CHECK FIRST — scan PLAN.md for any task with Assigned_To: $ID and Status: in_progress or claimed. If found, resume that task immediately: re-read its Owned_Paths files and the last Progress_Note to find the exact stopping point, then continue on the existing branch (do not re-claim or re-branch). Only if NO in_progress/claimed task exists: claim the highest-priority pending task Assigned_To: $ID whose dependencies are done — one atomic edit+commit setting Status: claimed, Branch: task/TASK-NNN-$SUFFIX, Started_At. (3) Create (or switch to) the task branch in your worktree and implement strictly against the task's Spec_References, touching ONLY files under its Owned_Paths. (4) Test everything; append Test_Evidence. (5) Append-only Progress_Notes with UTC timestamps and [$ID] tags — if your context is approaching its limit, write a detailed stopping-point note (what is done, what file, exact next step) and commit before stopping. (6) Finish at needs_review (never done), or blocked with a vocabulary reason. Conventional Commits ending [TASK-NNN]. Never write to specs/, docs/, REVIEW.md, scripts/, .claude/, other task blocks, or main."
fi

# Wave C: inject project instincts (fail-open — empty output when no match,
# broken/missing store, or import failure). --unit rather than --paths:
# this script doesn't pre-resolve which task $ID will end up claiming (that
# happens inside the builder's own resume-first/claim logic above), so
# instincts.py predicts the same task via the same priority rule and
# resolves its Owned_Paths itself.
INSTINCTS_SECTION="$(python3 scripts/instincts.py inject \
  --unit "$ID" --repo "$REPO_ROOT" --limit 5 2>/dev/null || true)"
if [[ -n "$INSTINCTS_SECTION" ]]; then
  PROMPT="${PROMPT}

${INSTINCTS_SECTION}"
fi

if [[ "$DRY" == "--dry-run" ]]; then
  echo "[dispatch] DRY RUN — would run: (cd $WT && ${CMD[*]} \"<prompt>\")"
  printf -- '--- Prompt ---\n%s\n' "$PROMPT"
  exit 0
fi

echo "[dispatch] Launching $BUILDER ($ID) in $WT..."
if [[ "$CONTROL_MODE" == "strict" ]]; then
  mkdir -p "$REPO_ROOT/.devteam/runs"
  RUN_TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
  LOG_PATH="$REPO_ROOT/.devteam/runs/${TASK_ID}-${RUN_TS}.log"
  # Capture full stdout to the run log while still showing it live (tee),
  # so the CONTROL fence can be extracted from the log afterward regardless
  # of what the terminal happened to scroll past.
  ( cd "$WT" && "${CMD[@]}" "$PROMPT" ) 2>&1 | tee "$LOG_PATH" || true

  echo "[dispatch] Session ended. Extracting devteam-control block..."
  EXTRACT_OUT="$(python3 scripts/control.py extract \
    --log "$LOG_PATH" --task "$TASK_ID" --unit "$ID" --repo "$REPO_ROOT")"
  echo "[dispatch] $EXTRACT_OUT"
  case "$EXTRACT_OUT" in
    UNREPORTED:*)
      echo "[dispatch] NOTE: no CONTROL block found — PLAN.md state will not change until the next supervisor tick's fallback handling. Log: $LOG_PATH" >&2
      ;;
  esac
  echo "[dispatch] control.mode=strict: PLAN.md is applied by the supervisor's next tick, not here. Run /devteam-status once it has ticked."
  exit 0
else
  ( cd "$WT" && "${CMD[@]}" "$PROMPT" ) || true

  echo "[dispatch] Session ended. Re-validating PLAN.md..."
  python3 scripts/validate_plan.py PLAN.md || {
    echo "[dispatch] WARNING: PLAN.md now protocol-illegal — builder violated protocol. Inspect: git log -p -- PLAN.md" >&2
    exit 1
  }
  echo "[dispatch] Done. Run /status in Claude Code for the health scan."
fi
