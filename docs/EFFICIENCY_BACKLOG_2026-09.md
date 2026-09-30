# Efficiency backlog — review, testing and wave throughput

**Status:** BACKLOG — documented on owner request 2026-09-30, not scheduled. Nothing here is
started. Revisit after Wave E closes (TASK-036, 041–046).

**Origin:** owner question during the Wave E autopilot run: the full Python suite takes ~8 minutes
and is run at least twice per task (builder handoff + ORCH review), more on rework.

## Measurements (master, 2026-09-30, `python -m pytest -q --durations=60`)

- 1,191 tests, 470 s (7m50s) serial; typical uncontended run 310–345 s.
- **60 tests (5%) account for ~330 s (~70%)**; the other 1,131 take ~2 minutes together.
- Slow tests are the ones that start real git repos, worktrees and dispatcher processes:

| File | Time in tests ≥ 1 s |
|---|---|
| `tests/test_dispatch_worktree.py` | 98 s (27 tests) |
| `tests/test_supervisor.py` | 83 s (7 tests, 11–13 s each — looks like a fixed wait) |
| `tests/test_plan_commit.py` | 41 s (12 tests) |
| `tests/test_supervisor_ledgers.py` | 36 s (3 tests) |
| `tests/test_harness_smoke.py` | 29 s (4 tests) |
| `tests/test_supervisor_once_inbox.py` | 19 s |
| `tests/test_notify_needs_review.py` | 13 s |

- The suite runs on **one core of 8**; `pytest-xdist` is not installed.

## Where time was actually lost (29–30 Sept, TASK-034/037/038/040/036)

- Full-suite runs: ≥ 2 per task, ~6 on TASK-037 (~45 min of waiting on tests alone).
- Ownership blocks: TASK-037 stopped three times on files outside its territory; four stops in
  two days were `sync-manifest.json` registration alone.
- Builder sessions ending before handoff: 3 on TASK-038, 1 on TASK-037, including ~18 h overnight
  with nothing monitoring (ORCH's scheduled check does not survive the session ending).
- One CX session at a time: ready tasks with disjoint territories wait in a queue.
- ORCH slips: master left red twice after a merge (new files not in the manifest).

## Proposals

| # | Proposal | Expected gain | Cost / risk | Kind |
|---|---|---|---|---|
| 1 | Run tests in parallel (`pytest-xdist`, e.g. `-n 6`) | ~8 min → ~1.5–2 min | One trial run to find tests sharing state (fixture repos, `.devteam/`, ports) | Framework task |
| 2 | Fix the 11–13 s `test_supervisor.py` tests (suspected fixed wait) | ~1 min | Small | Framework task |
| 3 | Fast lane: mark the ~60 slow tests `slow`; builders run the fast set while working, full suite once at handoff | Builder inner loop ~2 min | Needs a marker convention + briefing change | Framework task |
| 4 | Stop running the full suite twice: builder pushes the task branch, CI runs it on Windows + Ubuntu, ORCH reads the CI result for that exact SHA | ~8 min off every review | Needs TASK-046 (CI green) first; pushes task branches to GitHub; **changes CLAUDE.md "re-run the tests yourself" — owner decision** | Process change |
| 5 | Archive done tasks out of PLAN.md (`scripts/plan_archive.py`) — 350 KB vs 150 KB cap, 38/45 done, every builder session reads it all | Tokens + startup time per builder session | Run between builder sessions only | ORCH chore |
| 6 | Parallel CX sessions for disjoint territories (one worktree per task) | Removes queueing of ready tasks | Dispatcher change; Codex quota use rises | Framework task |
| 7 | Remove the manifest block: sync check discovers test files by pattern instead of a hand-kept list (or every test-adding task owns `sync-manifest.json` by rule) | Eliminates a recurring stop | Touches `sync_from_pack.py` + its tests | Framework task |
| 8 | Stall detection independent of the ORCH session: `scripts/autopilot-tick.ps1` as a Windows scheduled task | No more silent overnight gaps | Owner setup step on the machine | Setup |
| 9 | Event-based progress updates (handoff, verdict, block, stall) instead of a 5-minute tick | Fewer no-change model calls | None | Reporting |

## What to keep

The independent check before merge. On 30 Sept it caught two defects that had fully green
suites: the WSL-bash launch failure in TASK-038's live smoke, and TASK-036's `plan_commit`
starting to push the integration branch unasked (257 unpushed commits).

## Suggested order when picked up

1. Item 5 (ORCH chore) and item 9 (reporting) — no code.
2. Decompose 1, 2, 3, 6, 7 as a small wave (`/devteam-decompose`); 1 and 2 first.
3. Item 8 — owner sets up the scheduled task.
4. Item 4 — only after TASK-046 and an explicit owner decision on the review rule.

## Open owner question (related)

Should `master` be pushed to GitHub at all, and on what policy? As of 2026-09-30 it is 257
commits ahead of `origin/master` (last push 2026-09-15) and nothing pushes it automatically.
