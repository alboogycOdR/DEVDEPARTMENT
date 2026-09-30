# TASK-036 — Wave E E-G — bookkeeping push policy (every | batch | merge_only)

## Brief
`git.push_policy`: every | batch | merge_only. Default `batch` for NEW projects only; existing projects keep `every` unless the owner opts in (ask-don't-auto-flip). batch: plan-only commits (chore(plan), CONTROL applications, status scans) pushed at most every `git.push_batch_minutes`=30, always on merge or park. merge_only: only on merge or park. Status scans that change nothing produce no commit. Implement the policy once in push_policy.py; plan_commit, control and the supervisor's merge/park paths call it.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §9 (E-G)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/plan_commit.sh, scripts/plan_commit.ps1, scripts/control.py, tests/test_control.py, tests/test_plan_commit.py, scripts/push_policy.py (new), tests/test_push_policy.py (new), scripts/supervisor.py
- Protected-path grants: scripts/plan_commit.sh, scripts/plan_commit.ps1, scripts/control.py, scripts/push_policy.py, scripts/supervisor.py
- Depends_On: TASK-034, TASK-031, TASK-037

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log

- 2026-09-30 [CX]: The TASK-044 split owns the new-project `batch` default; this task keeps a missing policy key at `every`. Added `push_policy.py` with a durable `.devteam/push_policy.json` batch window and a cross-process lock. `plan_commit.sh`/`.ps1`, CONTROL commits, Telegram answer/rework commits, and the supervisor's merge, park, and scheduled tick paths now use the same policy. The scheduled tick flushes at the batch boundary even when no new commit arrives.
- 2026-09-30 [CX]: Regression fixtures use a bare Git remote and an advanced clock across separate Python processes. Twenty commits in ten minutes caused zero pushes; the boundary tick caused one. Merge and park flushes, missing-key `every`, a no-change PLAN commit, and the shell `plan_commit` batch path are covered. Focused suite: 87 passed. Full Python suite: 1195 passed in 543.37s. Node suite: 47 passed, 0 failed.
- 2026-09-30 [CX]: New framework files `scripts/push_policy.py` and `tests/test_push_policy.py` need `sync-manifest.json` registration in ORCH's merge commit. That manifest is outside TASK-036 Owned_Paths, and the pack tests explicitly defer registration of new task files until integration.
- 2026-09-30 [CX]: ORCH rework at 15:00:56Z widened Owned_Paths to include `sync-manifest.json`. Corrected the missing-key behavior at the `plan_commit` call site: it stays local-only when no policy is configured, while CONTROL and supervisor retain the `every` default. Benign scheduler results return success; shell and PowerShell warnings now include the actual error text. Registered both new files in the manifest and added explicit absent-key and `every` push regressions. Previous test counts predate this rework; new full-suite evidence follows in PLAN.md.
- 2026-09-30 [CX]: Rework code committed as `82decda`; focused suite 153 passed in 111.93s, Node 47 passed. Full Python suite 1197 passed, 1 failed in 579.98s. The failure is `tests/test_dispatch_worktree.py::TestClaimVerifiedAfterLegacyLaunch::test_claim_flip_within_window_does_not_log_claim_unverified`; isolation repeats it (1 failed in 2.49s). Its fixture copies only `dispatch.sh`, `dispatch.ps1`, `validate_plan.py`, `instincts.py`, and `builder_registry.py`, none changed on this task branch relative to master. A fixed one-second writer delay races dispatch's preflight, leaving PLAN.md dirty when dispatch checks it. Repair needs `tests/test_dispatch_worktree.py`, outside Owned_Paths, so TASK-036 is blocked for ORCH re-carve.
- 2026-09-30 [CX]: ORCH added `tests/test_dispatch_worktree.py` to Owned_Paths for the race repair. The target test now waits for a marker written by the fake builder after dispatch preflight, commits the claim, and releases the builder with a second marker. The isolated test passes; full-suite verification is next.
- 2026-09-30 [CX]: Verification after the race repair: `python -m pytest -q` passed 1198 tests in 524.57s; `node hooks/run-tests.js` passed 47 tests, 0 failed. All branch changes are within TASK-036 Owned_Paths. Submitting commits `5a10368`, `82decda`, `8143d80`, and `4e7087d` for ORCH review.
