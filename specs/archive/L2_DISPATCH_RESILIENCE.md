# L2 dispatch resilience — surviving a builder that cannot launch

**Status:** SPEC — target of the second L2 (supervised-loop) wave.
**Origin:** measured during the first live L2 run, 2026-08-16. Tick 1 dispatched three builders; GB's dispatch **exited nonzero and never launched**, because an empty-but-unregistered directory occupied its expected worktree path. The wave completed only because a human was watching the console.
**Baseline:** pack @ `master` after the pack-hardening wave. Suites: 717 Python / 36 Node green.

## 0. What is already correct — do not rebuild it

An earlier reading of this incident concluded "the supervisor cannot see dispatch failures." **That is wrong, and the spec records it so nobody re-implements a working mechanism.** `launch_shell_bg()` returns a `Popen` handle, `execute()` tracks it in `inflight`, and `reap_inflight()` polls every tracked dispatch at the start of each subsequent tick, routing any nonzero exit to `_notify_if_builder_unreachable()` → **P2**. That deferral is deliberate and documented in the function's own docstring.

The real gaps are narrower, and all three were observed rather than theorised.

## 1. Design rules

**L1 — Prevent the trigger before improving the report.** An empty directory at a worktree path is provably nobody's work; refusing it is safety theatre that converts a self-healing condition into a permanent stall.

**L2 — Every repeat-failure class needs a ceiling.** Rework has `max_rework`, staleness escalates on the 3rd reset, `OWNERSHIP_CONFLICT` escalates on the 2nd. Dispatch failure is the only repeat-failure class in the tick model with **no ceiling at all** — it can notify forever without ever converging.

**L3 — A diagnosis must not name the wrong cause.** Same family as the `git commit/push failed` defect: a confident, incorrect message costs more than a vague one.

**L4 — Regression tests must fail against current code.**

---

## 2. Increment R-A — dispatch reclaims an empty, unregistered worktree directory

**Observed, three times.** Removing a worktree leaves its directory behind when a process still holds a handle on it (Windows). The directory is then empty and unregistered, and every future dispatch for that unit fails at the guard that refuses to reuse an unrecognised directory. The guard itself is correct — a *non-empty* foreign directory must never be silently reused — but it does not distinguish "someone's work is here" from "an empty husk is here."

**Required:** in both `dispatch.sh` and `dispatch.ps1`, when the expected worktree path exists but is not a registered worktree of this repo:
- If the directory is **empty** (no entries at all, including dotfiles), remove it and proceed to create the worktree normally, logging one line stating what was reclaimed and why.
- If it is **non-empty**, keep today's hard refusal verbatim, including the existing guidance to inspect it manually.
- Removal failure (still locked) must be a clean refusal with the current message, never a crash or a partial state.

Both scripts must behave identically; this is a behavioural contract, not a Windows patch.

**Acceptance:** an empty unregistered directory at the worktree path is reclaimed and dispatch proceeds; a directory containing even one file is still refused with the existing message; a dotfile-only directory counts as **non-empty** and is refused; the reclaim emits exactly one explanatory log line; `bash -n` and the PS parser accept both scripts.

## 3. Increment R-B — a ceiling on repeated dispatch failure, and reaping in `--once`

Three defects in one area, all in `supervisor.py`:

**B1 — no ceiling.** After `reap_inflight` reports a nonzero exit, nothing records it. The next tick sees the unit idle and its task pending, dispatches again, fails again, and notifies again — indefinitely. Per L2, add a per-unit consecutive-failure counter in `RuntimeState` (mirroring `rework_counts` / `stale_resets` / `conflict_counts`), and once it reaches a configurable ceiling (`autopilot.json` → `max_dispatch_failures`, default **2**) **stop dispatching that unit** and raise a single P2 stating the unit is parked and why. Any successful dispatch for that unit resets its counter to zero.

**B2 — `--once` never reaps.** `reap_inflight` runs at the start of each tick; a single-tick run exits before any second tick exists, so the `Popen` handles are discarded and a failed dispatch is silent. This is exactly how the live incident escaped notice. `--once` must reap before returning — briefly waiting for in-flight dispatch processes to settle (a few seconds is sufficient; a dispatch either fails fast or detaches and returns 0) and then reporting outcomes through the same path a loop tick uses.

**B3 — the message names the wrong cause.** `_notify_if_builder_unreachable` attributes every nonzero exit to "builder CLI may be unreachable from this host." That is one plausible cause; the observed cause was a stale worktree directory, and a reader who trusts the message looks at the wrong machine. Widen it to state the exit code and the actual command, list unreachable-CLI **and** local-dispatch-precondition failure as candidates, and point at the dispatch transcript. Do not remove the T1 Watchtower explanation — it is still a real case.

**Acceptance:** a unit whose dispatch fails twice consecutively is no longer dispatched and produces one P2 naming the ceiling; a successful dispatch resets the counter; `--once` surfaces a failed dispatch (proven with a dispatch command that exits nonzero) rather than exiting silently; the notification text no longer asserts a single cause; existing tick behaviour for healthy dispatches is unchanged.

## 4. Territories

| # | Deliverable | Territory |
|---|---|---|
| **R-A** | worktree reclaim | `scripts/dispatch.sh`, `scripts/dispatch.ps1`, `tests/test_dispatch_worktree.py` |
| **R-B** | failure ceiling, `--once` reap, honest message | `scripts/supervisor.py`, `tests/test_supervisor.py`, `autopilot.json` |

Disjoint, no dependency between them, deliberately concurrent again.

## 5. Exit criteria

Full Python and Node suites green. No diff outside each task's `Owned_Paths`. The originally-observed sequence is demonstrably fixed end to end: an empty unregistered worktree directory no longer blocks a dispatch, and a dispatch that does fail is reported in `--once` and converges to a parked unit under `--loop` instead of notifying forever.

## 6. Note for the reviewer

This wave's whole subject is failure handling, so **the acceptance evidence must show failures actually occurring** — a dispatch command forced to exit nonzero, a directory deliberately left at the worktree path. Tests that only prove the happy path do not satisfy L4 here.
