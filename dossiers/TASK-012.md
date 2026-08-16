# Dossier — TASK-012 · Dispatch-failure ceiling, `--once` reaping, honest message

**Brief:** When a dispatch fails to launch, the supervisor notices — but never converges. There is no ceiling, so it re-dispatches and re-notifies indefinitely; `--once` never reaps at all, so a single-tick run reports nothing; and the notice blames an unreachable builder CLI when the real cause was a stale directory on this very machine.

**Read `specs/L2_DISPATCH_RESILIENCE.md` §0 before anything else.** It records what already works, because an earlier ORCH reading of this incident wrongly concluded the supervisor was blind to dispatch failures. It is not: `launch_shell_bg()` returns a `Popen`, `execute()` tracks it in `inflight`, and `reap_inflight()` polls each at the start of the next tick, routing nonzero exits to `_notify_if_builder_unreachable()` as P2. **Do not rebuild that machinery.** Your three gaps are B1/B2/B3 in §3.

**Intended approach:**
- **B1 ceiling:** follow the pattern already in `RuntimeState` — `rework_counts`, `stale_resets`, `conflict_counts` are all `dict[str, int]` persisted across ticks with a config ceiling (`max_rework` is the closest analogue, including how `decide()` freezes a task at the limit). Key yours by **unit**, not task. At the ceiling: stop dispatching that unit and emit exactly one P2 — not one per tick. Any successful dispatch zeroes it.
- **B2 `--once`:** the reap call sits at the top of the loop body, so a single tick exits before a second one exists. Reap before returning, giving in-flight dispatches a brief settle window — a dispatch either fails fast or detaches and returns 0, so a few seconds is enough; do not block on the builder's whole session. Route outcomes through the same path a loop tick uses rather than a parallel one.
- **B3 message:** keep the T1 Watchtower explanation (a genuinely unreachable CLI on another host is still a real case) but stop asserting it as *the* cause. Include the exit code, the actual command, both candidate causes, and where to look — the dispatch transcript under `.devteam/launch/`.

**Testing note (graded, §6):** this wave is about failure handling, so the evidence must show real failures. A dispatch command forced to exit nonzero is the honest fixture — prove the ceiling engages on the second consecutive failure, that a success resets it, and that `--once` actually reports. `tests/test_supervisor.py` has established patterns for driving `decide()` as a pure function; prefer those over end-to-end launching.

**Territory note:** `scripts/**` and `autopilot.json` are builder-protected; your three paths are deliberate per-task grants, removed at done. Your `autopilot.json` change is **one key** (`max_dispatch_failures`, default 2) — anything else in that file is out of bounds; it holds the builder registry and review_cmd. TASK-011 (GB) runs CONCURRENTLY in the dispatch scripts.

## Work Log
