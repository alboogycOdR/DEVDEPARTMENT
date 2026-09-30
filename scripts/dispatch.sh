#!/usr/bin/env bash
# dispatch.sh — launch a builder (grok|codex) headlessly against PLAN.md.
# Usage: scripts/dispatch.sh grok [--dry-run]
#
# NOTE (2026-08-02): the Windows mirror scripts/dispatch.ps1 now launches each builder
# in its OWN detached console window by default, because running the builder as a child
# of the dispatching process meant an ORCH harness reaping its background job killed the
# builder with it -- four sessions were lost that way, twice mid-write. This POSIX script
# is deliberately NOT changed to match: it is invoked from a real terminal by a human or
# by the autopilot, neither of which reaps its children mid-run, so blocking in-process is
# correct here and keeps stdout capture simple. If that ever stops being true, the fix is
# setsid/nohup into a per-launch log, mirroring the .ps1's .devteam/launch/ layout.
# Wave I (I1): mode-aware. control.mode=legacy (default) behaves exactly as
# before — builder scans PLAN.md, claims its own task, edits PLAN.md itself.
# control.mode=strict flips to claim-at-dispatch (this script claims/resumes
# the task before launch via scripts/control.py) plus stdout capture and
# devteam-control fence extraction after the session ends. No hybrid: the
# mode gate below is a hard branch, never a partial mix of both behaviors.
set -euo pipefail

BUILDER="${1:?Usage: dispatch.sh <grok|codex|claude> [--dry-run]}"
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

# Integration branch from autopilot.json (git.base_branch, fail-safe default
# "main") — resolved once because create, and the fresh-claim branch (bceb8eb2),
# both need it. Creation previously nested this only inside `if [[ ! -d "$WT" ]]`,
# so a reused worktree had no BASE_BRANCH for `git branch task/<id>-<suffix>`.
BASE_BRANCH="$(python3 -c "
import json
try:
    print(json.load(open('autopilot.json')).get('git',{}).get('base_branch') or 'main')
except Exception:
    print('main')
" 2>/dev/null || echo main)"
BASE_BRANCH="${BASE_BRANCH//$'\r'/}"

# v4.7: builder identity comes from the registry (autopilot.json's builders
# key, dual-shape — see scripts/builder_registry.py). argv may be a unit ID
# (GB/CX/S5/S5B/...) or, as a compatibility shim, a legacy CLI-family name
# (grok/codex/claude -> first ACTIVE unit on that cli). FAIL-CLOSED on
# anything unresolvable: unlike control.mode (safe universal fallback =
# legacy behavior), a wrong guess here hands a builder the wrong
# worktree/CLI — there is no safe default, so we refuse instead.
REG_KV="$(python3 scripts/builder_registry.py resolve "$BUILDER" --repo "$REPO_ROOT")" || {
  echo "[dispatch] ERROR: cannot resolve builder '$BUILDER' from the registry — refusing to dispatch." >&2
  exit 1
}
# Windows python emits CRLF; a trailing CR would end up inside every parsed value (e.g. 'grok<CR>').
# Port of oikonomos d3f5fc08.
REG_KV="${REG_KV//$'\r'/}"
ID="";      CLI="";        MODEL="";      WORKTREE_SUFFIX=""
SUFFIX="";  BRIEFING="";   AUTO_LOADS_CONTEXT="false"
AUTH_MODE="default";       AUTH_VALUE=""
IDENTITY="preamble";       AGENT_NAME="devteam-builder"
CODEX_SANDBOX="danger-full-access"
while IFS='=' read -r k v; do
  case "$k" in
    UNIT) ID="$v" ;; CLI) CLI="$v" ;; MODEL) MODEL="$v" ;;
    WORKTREE_SUFFIX) WORKTREE_SUFFIX="$v" ;; BRANCH_SUFFIX) SUFFIX="$v" ;;
    BRIEFING) BRIEFING="$v" ;; AUTO_LOADS_CONTEXT) AUTO_LOADS_CONTEXT="$v" ;;
    AUTH_MODE) AUTH_MODE="$v" ;; AUTH_VALUE) AUTH_VALUE="$v" ;;
    IDENTITY) IDENTITY="$v" ;; AGENT_NAME) AGENT_NAME="$v" ;;
    CODEX_SANDBOX) CODEX_SANDBOX="$v" ;;
  esac
done <<< "$REG_KV"
[[ -n "$ID" && -n "$CLI" && -n "$WORKTREE_SUFFIX" && -n "$SUFFIX" && -n "$BRIEFING" ]] || {
  echo "[dispatch] ERROR: registry resolution for '$BUILDER' returned an incomplete entry — refusing to dispatch." >&2
  exit 1
}
WT="$(dirname "$REPO_ROOT")/wt-${WORKTREE_SUFFIX}-${PROJECT_NAME}"

# CLI-invocation table — keyed by CLI FAMILY, not unit (S5 and S5B share the
# claude row verbatim). These quirks are properties of the CLI binaries, not
# project configuration, so they stay here rather than in the registry:
case "$CLI" in
  # Bare `grok <prompt>` starts the INTERACTIVE TUI, which shows a "Do you
  # trust this directory?" dialog on every freshly (re)created worktree --
  # confirmed live: a headless dispatch just hung on this for hours, because
  # --always-approve/--permission-mode bypassPermissions cover tool-call
  # approval but not this separate trust gate. -p/--single switches to
  # single-turn non-interactive mode, which does not show it -- confirmed
  # with a live scratch-directory test. -p must be the LAST flag here: its
  # value is the next argv entry, and $PROMPT is appended at the call site.
  # Registry model pin (2026-08-15, parity with dispatch.ps1): codex/claude honour
  # $MODEL; grok didn't, making autopilot.json's model field a dead knob for GB.
  # -p must stay LAST: its value is the next argv entry ($PROMPT at the call site).
  grok)  CMD=(grok --always-approve --permission-mode bypassPermissions ${MODEL:+--model "$MODEL"} -p) ;;
  # --reasoning-effort is not a valid `codex exec` CLI flag (confirmed against
  # codex-cli 0.144.5); model_reasoning_effort is authoritative via
  # .codex/config.toml, per that file's own comment.
  codex) CMD=(codex exec ${MODEL:+--model "$MODEL"} -s "$CODEX_SANDBOX") ;;
  # claude: -p takes the prompt as a trailing positional argument.
  claude) CMD=(claude -p ${MODEL:+--model "$MODEL"} --dangerously-skip-permissions) ;;
  *) echo "[dispatch] ERROR: unknown CLI family '$CLI' for unit $ID — refusing to dispatch." >&2; exit 1 ;;
esac

echo "[dispatch] Validating PLAN.md..."
python3 scripts/validate_plan.py PLAN.md || { echo "[dispatch] PLAN.md illegal — fix before dispatching." >&2; exit 1; }

# Verify the current registry-derived CLI argv can write to a disposable
# owned-path fixture before the first real launch. The fixture dispatch sets
# DEVTEAM_PREFLIGHT_ACTIVE so it does not recursively launch another preflight.
if [[ "${DEVTEAM_PREFLIGHT_ACTIVE:-0}" != "1" && -f "$REPO_ROOT/tests/test_harness_smoke.py" ]]; then
  echo "[dispatch] Running registry fixture preflight for $ID..."
  DEVTEAM_PREFLIGHT_ACTIVE=1 python3 tests/test_harness_smoke.py --preflight --repo "$REPO_ROOT" --units "$ID" || {
    echo "[dispatch] ERROR: registry fixture preflight failed; refusing to launch $ID." >&2
    exit 1
  }
fi

# E-F.4: the main checkout's PLAN.md must be exactly what's committed — a
# dirty main-checkout PLAN.md means some earlier write never landed (the
# oikonomos SYNC_MISMATCH class), and dispatching against it risks reading
# state nobody else can see. This refuses, it never stashes/discards.
if ! git -C "$REPO_ROOT" diff --quiet -- PLAN.md || ! git -C "$REPO_ROOT" diff --quiet --cached -- PLAN.md; then
  echo "[dispatch] ERROR: PLAN.md has uncommitted changes in the main checkout ($REPO_ROOT)." >&2
  echo "[dispatch] Refusing to dispatch — commit or discard them first; do not work around this." >&2
  exit 1
fi

# Warn (not block) if an OLD-style unnamespaced worktree sits at the legacy
# path — a leftover from before this fix, or from a pre-fix dispatch of
# this exact project. It's orphaned now, not reused, so it's safe to leave,
# but flag it so it doesn't sit there silently confusing a future `ls`.
LEGACY_WT="$(dirname "$REPO_ROOT")/wt-${WORKTREE_SUFFIX}"
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
  # Git for Windows prints C:/... while this shell's $WT is in MSYS form (/tmp/...); compare like with like.
  # Port of oikonomos d3f5fc08.
  if command -v cygpath >/dev/null 2>&1; then
    REGISTERED_WORKTREES="$(while IFS= read -r _wt; do cygpath -u "$_wt"; done <<< "$REGISTERED_WORKTREES")"
  fi
  if ! grep -qxF "$WT" <<< "$REGISTERED_WORKTREES"; then
    # Empty husk (Windows leftover after worktree remove while a handle is
    # held): nobody's work is here. Reclaim and fall through to create.
    # "Empty" includes dotfiles — a dir holding only .foo is still refused.
    # rmdir (not rm -rf) so a non-empty dir can never be deleted by this path.
    if [[ -z "$(ls -A "$WT" 2>/dev/null)" ]] && rmdir "$WT" 2>/dev/null; then
      echo "[dispatch] Reclaimed empty unregistered directory at $WT (leftover husk, not a git worktree) - proceeding to create it."
    else
      echo "[dispatch] ERROR: $WT exists but is not a registered worktree of this repo ($REPO_ROOT)." >&2
      echo "[dispatch] This usually means a stale or foreign directory occupies the expected worktree path." >&2
      echo "[dispatch] Inspect it manually, then either remove it or let git reclaim it, and re-run dispatch:" >&2
      echo "[dispatch]   git worktree list   (from $REPO_ROOT, to see what git actually knows about)" >&2
      exit 1
    fi
  fi
fi

if [[ ! -d "$WT" ]]; then
  echo "[dispatch] Creating worktree at $WT..."
  git worktree add --detach "$WT" "$BASE_BRANCH"
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
CONTROL_MODE="${CONTROL_MODE//$'\r'/}"

TASK_ID=""
RESUME_OR_CLAIM=""
if [[ "$CONTROL_MODE" == "strict" ]]; then
  CLAIM_ARGS=(claim --unit "$ID" --repo "$REPO_ROOT")
  if [[ "$DRY" == "--dry-run" ]]; then
    CLAIM_ARGS+=(--dry-run)
  fi
  CLAIM_OUT="$(python3 scripts/control.py "${CLAIM_ARGS[@]}")"
  CLAIM_OUT="${CLAIM_OUT//$'\r'/}"
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

# E-F.4/E-F.7 carry-over from TASK-024 review: the pinned-base pre-create
# below only fires when dispatch itself claims (strict mode, RESUME_OR_CLAIM
# == "claimed"). In LEGACY mode the builder claims its own task from inside
# its session, so dispatch never sees a task id here -- but it CAN still
# guarantee the worktree's HEAD is the base tip before launch, unless this
# unit has a resumable (claimed/in_progress) task, in which case leaving the
# worktree exactly where its own branch is is the whole point of resuming.
if [[ "$CONTROL_MODE" == "legacy" && -d "$WT" ]]; then
  if python3 -c "
import sys; sys.path.insert(0, 'scripts')
from validate_plan import has_resumable_task
sys.exit(0 if has_resumable_task('.', '$ID') else 1)
" 2>/dev/null; then
    echo "[dispatch] $ID has a resumable task — leaving worktree on its current branch."
  else
    git -C "$WT" checkout --detach "$BASE_BRANCH" >/dev/null 2>&1 \
      && echo "[dispatch] worktree reset to $BASE_BRANCH tip (fresh-claim pinned base, legacy mode)." \
      || echo "[dispatch] WARNING: could not reset worktree to $BASE_BRANCH tip; a fresh claim may branch from a stale base." >&2
  fi
fi

# Port of oikonomos bceb8eb2: a FRESH claim must start from the integration
# branch. The builder otherwise creates task/<id>-<suffix> from whatever its
# worktree is on (the previous task's branch): a stale base plus foreign
# commits. Create the branch here, from the base tip, when it does not exist
# yet. Resuming keeps the existing branch.
# Pack adaptation: worktree creation is not gated by --dry-run, so neither is
# this pre-create (tests exercise it under --dry-run on a tmp fixture repo).
if [[ "$RESUME_OR_CLAIM" == "claimed" && -n "$TASK_ID" && -d "$WT" ]]; then
  TASK_BRANCH="task/${TASK_ID}-${SUFFIX}"
  if ! git -C "$WT" rev-parse --verify --quiet "refs/heads/$TASK_BRANCH" >/dev/null; then
    if git -C "$WT" branch "$TASK_BRANCH" "$BASE_BRANCH" >/dev/null 2>&1; then
      echo "[dispatch] created $TASK_BRANCH from $BASE_BRANCH tip (fresh base)."
    else
      echo "[dispatch] WARNING: could not pre-create $TASK_BRANCH from $BASE_BRANCH; the builder will create it from its worktree HEAD." >&2
    fi
  fi
fi

# S5 runs the literal `claude` CLI, which auto-loads CLAUDE.md as ambient
# project context regardless of what this prompt tells it to read -- and
# CLAUDE.md's own orchestration section says "You are ORCH". Without an
# explicit override, S5 would start this session confused about its own
# identity. GB/CX don't have this problem (grok/codex don't auto-load
# CLAUDE.md), so this prefix is S5-only.
PEERS="$(python3 -c "
import sys; sys.path.insert(0, 'scripts')
import builder_registry as br
try:
    ids = [u for u in br.active_units('$REPO_ROOT') if u != '$ID']
    print(' and '.join([', '.join(ids[:-1]), ids[-1]]) if len(ids) > 1 else (ids[0] if ids else 'the other builders'))
except Exception:
    print('the other builders')
" 2>/dev/null || echo "the other builders")"
PEERS="${PEERS//$'\r'/}"
# Identity mechanism (see docs/BUILDER_REGISTRY.md "Builder identity").
#
# identity=agent  -- the unit launches with `--agent <name>`, so its role
#   comes from a real agent definition (.claude/agents/devteam-builder.md)
#   and NO override text is prepended. This is the structural fix for a real
#   observed failure: the preamble below opens with "IMPORTANT IDENTITY
#   OVERRIDE ... Ignore CLAUDE.md's ORCH role assignment entirely", which has
#   the exact shape of a prompt-injection attempt. A safety-trained model
#   treating it with suspicion is behaving CORRECTLY, not malfunctioning --
#   so the fix is to stop needing the override, not to word it more forcefully.
#
# identity=preamble (default) -- today's behavior, byte-identical. Remains the
#   default until `--agent` is live-verified on the target machine, exactly as
#   control.mode and S5B activation are gated.
IDENTITY_OVERRIDE=""
if [[ "$AUTO_LOADS_CONTEXT" == "true" && "$IDENTITY" == "agent" ]]; then
  CMD+=(--agent "$AGENT_NAME")
  echo "[dispatch] $ID identity via --agent $AGENT_NAME (no override preamble)."
elif [[ "$AUTO_LOADS_CONTEXT" == "true" ]]; then
  IDENTITY_OVERRIDE="IMPORTANT IDENTITY OVERRIDE: your project context auto-loaded CLAUDE.md, which contains a \"## Multi-Agent Orchestration\" section describing an ORCH role and saying \"You are ORCH\". That does NOT apply to this session. You are $ID — a builder unit, exactly parallel to $PEERS, implemented via Claude Code. Ignore CLAUDE.md's ORCH role assignment entirely for this session: you have none of ORCH's exclusive powers here — no merging task branches, no review verdicts, no editing PLAN.md frontmatter, no editing any task block but your own claimed one. Those remain the separate, interactive ORCH session's job. Follow the builder procedure below exactly as GB/CX would.

"
fi

if [[ "$CONTROL_MODE" == "strict" ]]; then
  PROMPT="${IDENTITY_OVERRIDE}You are $ID, a builder in a multi-agent dev team. Working directory: $WT (your isolated git worktree; coordination PLAN.md lives at $REPO_ROOT/PLAN.md on main — control.mode=strict: you never write PLAN.md yourself).
Your task is $TASK_ID ($RESUME_OR_CLAIM by the dispatcher before this session started — do not re-claim or re-branch).
Procedure: (1) Read AGENTS.md and $BRIEFING, then PLAN.md, fresh from disk, for $TASK_ID's Spec_References/Owned_Paths/Acceptance_Criteria. Read dossiers/$TASK_ID.md in full if it exists (your prior work log) before acting. (2) If resuming: continue on the existing branch task/$TASK_ID-$SUFFIX at the exact stopping point recorded in the dossier. If newly claimed: create branch task/$TASK_ID-$SUFFIX in your worktree. (3) Implement strictly against Spec_References, touching ONLY files under Owned_Paths plus your own dossier (dossiers/$TASK_ID.md — append a Work Log entry at minimum every ~30 minutes of work and at every stopping point; it is your heartbeat, since you never touch PLAN.md for this). (4) Test everything - IN THE FOREGROUND, blocking, within this turn. You are one-shot; backgrounding a suite and waiting to be notified kills the session with the handoff undone (five recorded incidents). (5) Emit a devteam-control block as the LAST thing you print, fenced exactly like this:
\`\`\`devteam-control
{\"control_version\": 1, \"task\": \"$TASK_ID\", \"unit\": \"$ID\", \"status\": \"needs_review\", \"progress_note\": \"...\", \"artifacts\": [\"path/a.dart\"], \"test_evidence\": \"...\", \"blocked_reason\": null, \"next_step\": null}
\`\`\`
status must be exactly one of in_progress (mid-session checkpoint — dossier note + next_step, nothing else changes) / needs_review (requires non-empty test_evidence) / blocked (blocked_reason must start with SPEC_AMBIGUITY, MISSING_DEPENDENCY, OWNERSHIP_CONFLICT, SYNC_MISMATCH, TOOLING_FAILURE, or OTHER:). Never done/pending/claimed — those are the supervisor's alone. Conventional Commits ending [$TASK_ID] for your code commits (never for PLAN.md — you don't touch it). Never write to specs/, docs/, REVIEW.md, scripts/, .claude/, PLAN.md, other dossiers, or main."
else
  PROMPT="${IDENTITY_OVERRIDE}You are $ID, a builder in a multi-agent dev team. Working directory: $WT — your isolated git worktree on your own task branch. ALL CODE changes are made and committed there; never put code on main (merging is ORCH's, after review). PLAN.md is the deliberate exception: it is the shared coordination blackboard living at $REPO_ROOT/PLAN.md on main. READ and EDIT it at that path so you both see and publish current state. To record ANY PLAN.md change (claim, status transition, Progress_Note, needs_review) run: scripts/plan_commit.sh 'chore(plan): <what> [$ID]' - it commits PLAN.md alone directly onto main and cannot carry code. DO NOT run 'git push . HEAD:main': that is the old procedure and a known trap - it works on claim, but by needs_review your HEAD sits on your code commits and that push lands them all on the integration branch unreviewed. A PLAN.md commit left on your task branch is invisible to ORCH and the other builders until merge, which defeats the whole point of a blackboard. Code to your branch; PLAN.md to main; never the reverse.
Procedure: (1) Read AGENTS.md and $BRIEFING, then PLAN.md, fresh from disk. If dossiers/TASK-NNN.md exists for your task, read it in full before acting and append a Work Log entry each session — never ask for re-explanation of anything in the dossier. (2) RESUME CHECK FIRST — scan PLAN.md for any task with Assigned_To: $ID and Status: in_progress or claimed. If found, resume that task immediately: re-read its Owned_Paths files and the last Progress_Note to find the exact stopping point, then continue on the existing branch (do not re-claim or re-branch). Only if NO in_progress/claimed task exists: claim the highest-priority pending task Assigned_To: $ID whose dependencies are done — one atomic edit+commit setting Status: claimed, Branch: task/TASK-NNN-$SUFFIX, Started_At. (3) Create (or switch to) the task branch in your worktree and implement strictly against the task's Spec_References, touching ONLY files under its Owned_Paths. (4) Test everything; append Test_Evidence. RUN EVERY VERIFICATION COMMAND IN THE FOREGROUND AND WAIT FOR IT IN THIS TURN - you are a one-shot -p invocation, so there is no later turn and NO notification can ever reach you. Never background a suite and say you will resume when notified: five builder sessions have died exactly there, work committed but the handoff lost. A long blocking wait is correct and safe. (5) Append-only Progress_Notes with UTC timestamps and [$ID] tags — if your context is approaching its limit, write a detailed stopping-point note (what is done, what file, exact next step) and commit before stopping. (6) Finish at needs_review (never done), or blocked with a vocabulary reason. Conventional Commits ending [TASK-NNN]. Never write to specs/, docs/, REVIEW.md, scripts/, .claude/, other task blocks, or main."
fi

# Wave C: inject project instincts (fail-open — empty output when no match,
# broken/missing store, or import failure). --unit rather than --paths:
# this script doesn't pre-resolve which task $ID will end up claiming (that
# happens inside the builder's own resume-first/claim logic above), so
# instincts.py predicts the same task via the same priority rule and
# resolves its Owned_Paths itself.
INSTINCTS_SECTION="$(python3 scripts/instincts.py inject \
  --unit "$ID" --repo "$REPO_ROOT" --limit 5 2>/dev/null || true)"
INSTINCTS_SECTION="${INSTINCTS_SECTION//$'\r'/}"
if [[ -n "$INSTINCTS_SECTION" ]]; then
  PROMPT="${PROMPT}

${INSTINCTS_SECTION}"
fi

# ATLAS A5 (§5): append a token-budgeted project-map section, same posture
# as the instincts injection just above -- fail-open, one warning line on
# any error, dispatch proceeds without the section either way. Gated on
# BOTH the db existing (nothing to pack before A1 has ever scanned) and
# autopilot.json -> atlas.enabled (ships false; onboarding asks). In
# control.mode=strict TASK_ID is already resolved by the claim above; in
# legacy mode nobody pre-resolves which task the unit will end up on (same
# gap the instincts injection above has), so this predicts it with the
# identical resume-first priority rule, reusing instincts.py's own private
# helpers (_deps_done, _PRIORITY_ORDER) rather than re-deriving the logic
# a third time -- if it can't confidently predict a task, it skips the
# section rather than guess.
#
# PYTHONIOENCODING is forced to utf-8 for every python3 call in this block
# (scoped to the export/unset pair around it, never leaked past it): Python
# on Windows defaults a piped/redirected child's stdout to the OS ANSI
# codepage, not UTF-8, and atlas.py's own em-dashes/arrows then crash with
# UnicodeEncodeError before a single byte reaches us -- silently defeating
# the fail-open path's actual purpose (which is to show the section when
# atlas genuinely succeeds, not to paper over an avoidable encoding crash
# on the platform this was built and tested on).
if [[ -f "$REPO_ROOT/.devteam/atlas.db" ]]; then
  export PYTHONIOENCODING=utf-8
  ATLAS_ENABLED="$(python3 -c "
import json
try:
    print(bool((json.load(open('autopilot.json')).get('atlas') or {}).get('enabled', False)))
except Exception:
    print('False')
" 2>/dev/null || echo False)"
  ATLAS_ENABLED="${ATLAS_ENABLED//$'\r'/}"
  if [[ "$ATLAS_ENABLED" == "True" ]]; then
    ATLAS_TASK_ID="$TASK_ID"
    if [[ -z "$ATLAS_TASK_ID" ]]; then
      ATLAS_TASK_ID="$(python3 -c "
import sys; sys.path.insert(0, 'scripts')
import instincts
from pathlib import Path
from validate_plan import parse_tasks, Report
try:
    text = Path('PLAN.md').read_text(encoding='utf-8')
    rep = Report()
    all_tasks = parse_tasks(text, rep)
    by_id = {t.task_id: t for t in all_tasks}
    mine = [t for t in all_tasks if t.get('Assigned_To') == '$ID']
    resuming = [t for t in mine if t.get('Status') in ('in_progress', 'claimed')]
    if resuming:
        target = resuming[0]
    else:
        pending = [t for t in mine if t.get('Status') == 'pending' and instincts._deps_done(t, by_id)]
        pending.sort(key=lambda t: (instincts._PRIORITY_ORDER.get(t.get('Priority'), 4), t.task_id))
        target = pending[0] if pending else None
    print(target.task_id if target else '')
except Exception:
    print('')
" 2>/dev/null || echo "")"
      ATLAS_TASK_ID="${ATLAS_TASK_ID//$'\r'/}"
    fi
    if [[ -n "$ATLAS_TASK_ID" ]]; then
      ATLAS_BUDGET="$(python3 -c "
import json
try:
    print(int((json.load(open('autopilot.json')).get('atlas') or {}).get('budget_tokens', 3000)))
except Exception:
    print(3000)
" 2>/dev/null || echo 3000)"
      ATLAS_BUDGET="${ATLAS_BUDGET//$'\r'/}"
      # Refresh the index BEFORE composing the pack (oikonomos defect,
      # 2026-08-15): the nightly audit is the only other scan caller, so
      # without this every post-merge dispatch ships a stale map — and
      # pack's db-open updates atlas.db's mtime, hiding the staleness from
      # casual inspection. Incremental scan ~seconds; non-fatal on failure.
      # RETRY ONCE (parity with dispatch.ps1): concurrent dispatch is the
      # NORMAL mode — two builders launched seconds apart both scan the same
      # sqlite db, and a cold scan's long write transaction can lose the
      # lock. One retry after a short pause clears it; a second failure
      # degrades to the pre-fix behaviour (stale index) with a visible
      # warning, which is the property that matters.
      set +e
      ATLAS_SCAN_OK=0
      for _attempt in 1 2; do
        if python3 scripts/atlas.py scan --repo "$REPO_ROOT" >/dev/null 2>&1; then ATLAS_SCAN_OK=1; break; fi
        [[ $_attempt -eq 1 ]] && sleep 3
      done
      [[ $ATLAS_SCAN_OK -eq 1 ]] \
        || echo "[dispatch] WARNING: atlas scan failed twice (concurrent dispatch can contend on .devteam/atlas.db) — packing against the existing, possibly stale index." >&2
      ATLAS_SECTION="$(python3 scripts/atlas.py pack --task "$ATLAS_TASK_ID" --budget "$ATLAS_BUDGET" 2>/dev/null)"
      ATLAS_RC=$?
      ATLAS_SECTION="${ATLAS_SECTION//$'\r'/}"
      set -e
      if [[ $ATLAS_RC -eq 0 && -n "$ATLAS_SECTION" ]]; then
        PROMPT="${PROMPT}

## PROJECT MAP (ATLAS) — a map, not the ground
${ATLAS_SECTION}"
      else
        echo "[dispatch] WARNING: atlas pack failed for $ATLAS_TASK_ID (exit $ATLAS_RC) — dispatching without the ATLAS section." >&2
      fi
    fi
  fi
  unset PYTHONIOENCODING
fi

# v4.7: per-unit auth, resolved BEFORE the dry-run branch so previews are
# accurate about it. config_dir mode sets CLAUDE_CONFIG_DIR for the launch
# only (scoped inside the launch subshell via env(1) — it must not leak
# into this script's own environment or any post-launch step).
AUTH_ENV=()
if [[ "$AUTH_MODE" == "config_dir" ]]; then
  AUTH_DIR="${AUTH_VALUE/#\~/$HOME}"
  AUTH_ENV=(env "CLAUDE_CONFIG_DIR=$AUTH_DIR")
  echo "[dispatch] Unit $ID authenticates via CLAUDE_CONFIG_DIR=$AUTH_DIR (scoped to this launch)."
fi

if [[ "$DRY" == "--dry-run" ]]; then
  echo "[dispatch] DRY RUN — would run: (cd $WT && ${AUTH_ENV[*]:+${AUTH_ENV[*]} }${CMD[*]} \"<prompt>\")"
  printf -- '--- Prompt ---\n%s\n' "$PROMPT"
  exit 0
fi

# hooks/lib.js's unit() defaults to 'ORCH' (unrestricted) when DEVTEAM_UNIT
# is unset -- harmless for grok/codex (neither reads .claude/settings.json,
# so territory-firewall.js never fires for them at all), but load-bearing
# for S5: it runs the literal `claude` CLI, which DOES load the hooks, so
# without this the firewall would silently treat S5 as unrestricted ORCH
# instead of enforcing its Owned_Paths. Set for every builder regardless,
# for consistency and to future-proof if GB/CX ever gain hook support.
#
# E-A.2/E-A.3: DEVTEAM_TASK is the session's task when we know exactly one
# (strict-mode claim, or a unique legacy resume/pending candidate). More than
# one claimed/in_progress task stays unset — pinning the first PLAN block is
# the H11 bug. DEVTEAM_DELEGATED=1 marks every dispatched process so the
# firewall can refuse autopilot.json writes.
if [[ -z "${TASK_ID}" ]]; then
  TASK_ID="$(python3 -c "
import sys
sys.path.insert(0, 'scripts')
from validate_plan import predict_dispatch_task
print(predict_dispatch_task('.', '$ID') or '')
" 2>/dev/null || true)"
  TASK_ID="${TASK_ID//$'\r'/}"
fi
export DEVTEAM_UNIT="$ID"
export DEVTEAM_TASK="$TASK_ID"
export DEVTEAM_DELEGATED=1
if [[ -n "$TASK_ID" ]]; then
  echo "[dispatch] session env DEVTEAM_UNIT=$ID DEVTEAM_TASK=$TASK_ID DEVTEAM_DELEGATED=1"
else
  echo "[dispatch] session env DEVTEAM_UNIT=$ID DEVTEAM_TASK unset (ambiguous or none) DEVTEAM_DELEGATED=1"
fi

echo "[dispatch] Launching $BUILDER ($ID) in $WT..."
if [[ "$CONTROL_MODE" == "strict" ]]; then
  mkdir -p "$REPO_ROOT/.devteam/runs"
  RUN_TS="$(date -u +%Y-%m-%dT%H-%M-%SZ)"
  LOG_PATH="$REPO_ROOT/.devteam/runs/${TASK_ID}-${RUN_TS}.log"
  # Capture full stdout to the run log while still showing it live (tee),
  # so the CONTROL fence can be extracted from the log afterward regardless
  # of what the terminal happened to scroll past.
  set +e
  ( cd "$WT" && "${AUTH_ENV[@]}" "${CMD[@]}" "$PROMPT" ) 2>&1 | tee "$LOG_PATH"
  LAUNCH_STATUS=${PIPESTATUS[0]}
  set -e

  echo "[dispatch] Session ended. Extracting devteam-control block..."
  EXTRACT_OUT="$(python3 scripts/control.py extract \
    --log "$LOG_PATH" --task "$TASK_ID" --unit "$ID" --repo "$REPO_ROOT")"
  EXTRACT_OUT="${EXTRACT_OUT//$'\r'/}"
  echo "[dispatch] $EXTRACT_OUT"
  case "$EXTRACT_OUT" in
    UNREPORTED:*)
      echo "[dispatch] NOTE: no CONTROL block found — PLAN.md state will not change until the next supervisor tick's fallback handling. Log: $LOG_PATH" >&2
      ;;
  esac
  echo "[dispatch] control.mode=strict: PLAN.md is applied by the supervisor's next tick, not here. Run /devteam-status once it has ticked."
  if [[ "$LAUNCH_STATUS" -ne 0 ]]; then
    echo "[dispatch] ERROR: builder CLI exited $LAUNCH_STATUS; dispatch did not succeed." >&2
    exit "$LAUNCH_STATUS"
  fi
  exit 0
else
  ( cd "$WT" && "${AUTH_ENV[@]}" "${CMD[@]}" "$PROMPT" ) &
  LAUNCH_PID=$!

  # E-F.3: verified claim before launch is strict mode's job (it claims
  # itself); legacy mode instead verifies AFTER launch, polling the main
  # checkout's PLAN.md for this unit's claim flip while the session runs.
  # None within the window -> CLAIM_UNVERIFIED, logged (not fatal): the
  # builder's own commit is not rejected, it is left for the next
  # /devteam-status or supervisor tick's reconciliation to notice and
  # investigate, exactly as an in-flight, not-yet-verified claim should be.
  CLAIM_VERIFY_SECONDS="$(python3 -c "
import json
try:
    print(int((json.load(open('autopilot.json')).get('dispatch') or {}).get('claim_verify_seconds', 120)))
except Exception:
    print(120)
" 2>/dev/null || echo 120)"
  CLAIM_VERIFY_SECONDS="${CLAIM_VERIFY_SECONDS//$'\r'/}"
  CLAIM_SEEN=0
  ELAPSED=0
  while [[ "$ELAPSED" -lt "$CLAIM_VERIFY_SECONDS" ]]; do
    if ! kill -0 "$LAUNCH_PID" 2>/dev/null; then
      break  # session already ended -- nothing left to poll for
    fi
    if python3 -c "
import sys; sys.path.insert(0, 'scripts')
from validate_plan import has_resumable_task
sys.exit(0 if has_resumable_task('.', '$ID') else 1)
" 2>/dev/null; then
      CLAIM_SEEN=1
      break
    fi
    sleep 2
    ELAPSED=$((ELAPSED + 2))
  done
  if [[ "$CLAIM_SEEN" -eq 0 ]] && kill -0 "$LAUNCH_PID" 2>/dev/null; then
    echo "[dispatch] CLAIM_UNVERIFIED: no PLAN.md claim/in_progress flip observed for $ID within ${CLAIM_VERIFY_SECONDS}s. Not fatal -- the builder's first commit will be picked up and reconciled on the next check." >&2
  fi
  set +e
  wait "$LAUNCH_PID"
  LAUNCH_STATUS=$?
  set -e

  echo "[dispatch] Session ended. Re-validating PLAN.md..."
  python3 scripts/validate_plan.py PLAN.md || {
    echo "[dispatch] WARNING: PLAN.md now protocol-illegal — builder violated protocol. Inspect: git log -p -- PLAN.md" >&2
    exit 1
  }
  if [[ "$LAUNCH_STATUS" -ne 0 ]]; then
    echo "[dispatch] ERROR: builder CLI exited $LAUNCH_STATUS; dispatch did not succeed." >&2
    exit "$LAUNCH_STATUS"
  fi
  echo "[dispatch] Done. Run /status in Claude Code for the health scan."
fi
