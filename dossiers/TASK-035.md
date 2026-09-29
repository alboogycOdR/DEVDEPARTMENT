# TASK-035 — Wave E E-F2 — verified claim, pinned base, dirty-PLAN refusal, strict Owned_Paths grammar, strict-by-default onboarding

## Brief
(3) Legacy mode: after launch, dispatch polls main-checkout PLAN.md up to `dispatch.claim_verify_seconds`=120 for the unit's claim flip; none → log CLAIM_UNVERIFIED, hold the builder's first commit for next tick's reconciliation (strict mode: dispatch claims itself). (4) Extend TASK-024's base-tip port: branch created from <base> tip in the worktree on every fresh claim, both scripts; refuse if PLAN.md has uncommitted changes in the main checkout. (5) validate_plan: Owned_Paths is a comma-separated list of globs only; reject prose, parentheses (other than the single permitted ` (new)` suffix) and TBD. (7) Onboarding writes control.mode strict only for projects whose active units are all verified CONTROL emitters; existing projects are OFFERED strict in the upgrade checklist, never flipped (ask-don't-auto-flip).

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §8 (E-F.3, E-F.4, E-F.5, E-F.7)

## Territory
- Owned_Paths: scripts/dispatch.sh, scripts/dispatch.ps1, tests/test_dispatch_worktree.py, scripts/validate_plan.py, tests/test_validate_plan.py, scripts/sync_from_pack.py, tests/test_sync_from_pack.py, dossiers/TASK-035.md
- Depends_On: TASK-027, TASK-032, TASK-026 (all done)

## Implementation notes

**E-F.5 (Owned_Paths grammar):** `check_owned_paths_grammar()` in validate_plan.py
rejects any token containing `(`/`)` other than a trailing ` (new)`, rejects the
literal `TBD`, and rejects anything that isn't a bare path/glob character set.
Wired into `validate()`'s per-task loop. Verified against the LIVE PLAN.md
(304KB, hundreds of Owned_Paths entries) with zero false positives before
writing tests.

**E-F.4 (dirty-PLAN refusal):** both scripts now check
`git diff --quiet -- PLAN.md` (worktree AND index) in the main checkout
before doing anything else, right after the existing `validate_plan.py`
call. Refuses with a clear message; never stashes/discards.

**E-F.4 (pinned base, legacy mode):** new `has_resumable_task(repo, unit)` in
validate_plan.py (sibling of the existing `predict_dispatch_task`) — true iff
this unit has exactly one claimed/in_progress task. When false (this launch
can only be a fresh claim) and the worktree is on a branch (not detached),
both scripts reset it to the base tip before launch. This closes the gap the
TASK-024 review carried over: the existing pinned-base pre-create only ever
fired for STRICT mode (dispatch claims and knows the task id up front);
legacy mode never had an equivalent because dispatch doesn't know the task id
in advance there. `has_resumable_task` sidesteps that — it doesn't need to
know WHICH task, only whether one is already claimed.

Note: dispatch.ps1 already had a *different*, pre-existing mechanism (the
"Refresh a REUSED worktree to the integration tip" block) that resets a
DETACHED+clean worktree to the base tip. That block explicitly skips a
worktree that's on a branch ("resume path... stay put") — which is exactly
the gap this task closes; the two mechanisms are complementary, not
duplicates.

**E-F.3 (verified claim, legacy mode):** dispatch can't claim for the builder
in legacy mode, so it verifies AFTER launch instead: polls PLAN.md for up to
`dispatch.claim_verify_seconds` (default 120) while the session runs, logging
`CLAIM_UNVERIFIED` (not fatal) if no flip appears before the window elapses
or the session ends first.
- `dispatch.sh`'s launch is synchronous by design (documented at file top:
  blocking is correct for a real-terminal/autopilot invocation). Backgrounded
  the launch (`&`) so the poll loop and the launch run concurrently, then
  `wait`s for it — the overall call still blocks until the session ends,
  same as before.
- `dispatch.ps1`'s DEFAULT mode (detached window) already returns
  immediately by design (2026-08-02 fix, documented at file top) — adding a
  120s blocking poll there would be a real regression against that fix, so
  legacy-mode verification only applies to `-InProcess` (the blocking
  mirror of .sh's model). Used a background `Start-Job` rather than
  `Start-Process`: `Start-Process`'s own resolution can't execute an
  npm-installed `.cmd` shim directly (no shell), while a `Start-Job`
  script block uses the exact same `&` invocation the rest of the file
  already relies on for that.

**E-F.7 (strict-by-default offer):** `strict_mode_offer()` in
sync_from_pack.py returns a one-line checklist entry when every active unit's
`cli` is in `_VERIFIED_CONTROL_EMITTER_CLIS` (currently `{"codex"}` — per the
spec text, `claude` is verified only once F3's schema output lands, which
hasn't happened yet in this repo; `grok` is unmentioned). Read-only: it never
writes `autopilot.json`. Wired into `run_sync()`'s return path and
`render_report()`'s new "Upgrade checklist" section. `merge_add_only_keys()`
already never touches an existing key, so `control.mode` on an existing
project was already untouched by sync before this task — the gap was only
that nothing OFFERED the upgrade.

## Work Log
- [2026-09-29T02:30:00Z] [CX] Implemented all four sub-items. Focused suite:
  tests/test_dispatch_worktree.py (38), tests/test_validate_plan.py (34),
  tests/test_sync_from_pack.py (63) — 135 passed. Full suite after merging
  master: `python -m pytest -q` → 1123 passed in 205.10s; `node
  hooks/run-tests.js` → 47 passed, 0 failed.
