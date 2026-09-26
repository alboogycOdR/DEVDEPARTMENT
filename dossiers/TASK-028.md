# TASK-028 — Wave E E-B1 — review ledger (head-SHA keyed), backoff, review.lock, markers, atomic state + multi-tick harness

## Brief
Extend TASK-023's port. Build `tests/tick_harness.py` FIRST: a reusable harness that runs N supervisor ticks with a fake clock, and N separate `--once` processes (real subprocesses) against a tmp fixture repo — TASK-029/030/031/043 reuse it. Then: review ledger keyed by task_id + task-branch head SHA; REVIEW only when needs_review and (no entry, or entry ended without verdict and backoff 5 min × 2^n capped at `review.max_backoff_minutes`=120 elapsed); new head SHA resets; at most one REVIEW per tick, oldest first; `.devteam/review.lock` (PID + start, stale after `review.lock_stale_minutes`=90). AUTOPILOT_LOG markers `REVIEW_START task= sha= session=` / `REVIEW_END task= verdict=approved|rework|none duration=`. Atomic RuntimeState save (temp + os.replace); corrupt file renamed `.autopilot_state.corrupt-<ts>.json`, logged, one P2. Dry-run never saves state. Also: the P0 digest prints TASK-025's `behind_pack()` line.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §1 H3/H7, §4 (E-B.1, E-B.2, E-B.6)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/supervisor.py, scripts/status_digest.py, tests/test_supervisor.py, tests/test_supervisor_ledgers.py (new), tests/tick_harness.py (new), tests/test_token_efficiency.py
- Protected-path grants: scripts/supervisor.py, scripts/status_digest.py
- Depends_On: TASK-023, TASK-024, TASK-025

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log
