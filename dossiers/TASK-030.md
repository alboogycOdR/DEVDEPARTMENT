# TASK-030 — Wave E E-C — durable park state, git heartbeat, on-disk in-flight tracking

## Brief
(1) `RuntimeState.parked = {kind: P1|WAVE_DONE, reason, since}` replaces halt-and-exit, honoured on every start. Parked tick: drain commands, maintenance, board/Tower, reap; skip decide()/execute(). Unpark on /resume, on the P1 condition clearing, or on a pending task appearing after WAVE_DONE. Exit only on STOP (code 3), --max-ticks, --budget-minutes; ecosystem.config.js `stop_exit_codes: [3]`. (2) Heartbeat = max(Updated_At, last commit time on the task branch, dossier mtime); surface any in_progress task with no source newer than stale_minutes×4 in /devteam-status and the digest, even when redispatch is exhausted. (3) `.devteam/inflight/<unit|review>.json {pid, task_id, cmd, started}` written at launch; reaper checks PID liveness each tick, replacing the in-memory inflight dict. Use tests/tick_harness.py. The /resume-via-file-inbox acceptance line is proven in TASK-031 (E-K) once the inbox path exists; here, prove /resume via the existing command path.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §1 H3/H5, §5 (E-C.1–3)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/supervisor.py, scripts/status_digest.py, tests/test_supervisor.py, tests/test_supervisor_park.py (new), tests/test_stagnation_signal.py, deploy/ecosystem.config.js, .claude/commands/devteam-status.md
- Protected-path grants: scripts/supervisor.py, scripts/status_digest.py, .claude/commands/devteam-status.md, deploy/ecosystem.config.js
- Depends_On: TASK-029

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log
