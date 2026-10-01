# Next-wave priority — Waves F / G / H triage (2026-10-01, ORCH)

**Status:** decision pending (Alister). Project PARKED 2026-10-01 after Wave E closed; resume here.
**Inputs:** `specs/VERIFICATION_GATES_2026-09.md` (Wave G), `specs/CLAUDE_NATIVE_LEVERAGE_2026-09.md` (Wave F),
`specs/TIERED_ROUTING_AND_MEMORY_2026-09.md` (Wave H). None has been decomposed; no task references them.
Ranking criteria: field/session evidence of the problem, prevents-harm vs saves-money, build cost, relevance to
the current roster (effectively CX-only during Wave E).

## Do now (5–6 tasks instead of ~25)

1. **G-D.0 path preflight as a hard gate** — strongest evidence (rwc: 38% of first 29 tasks blocked → 0 of next 11
   once paths were checked). Half exists already (`scripts/preflight_paths.py`, `(new)` grammar). Small.
2. **G-C.1 one test run per SHA, reused by review** — Wave E reviews re-ran ~15-min full suites the builder had just
   run; two ORCH runs were killed by host memory pressure (TASK-043 needed an owner waiver). Medium.
3. **G-A scripted pre-review gate, trimmed** — territory + full suite + baseline-ownership rule (V3), no G-B
   reachability/mutation. Stops broken work before an Opus review. Medium.
4. **F2 session caps only** (`--max-turns` / `--max-budget-usd` / wall clock on every headless session) — KERYX
   14 h runaway agent. Ledger deferred. Small.
5. **Reviewer-model cleanup (F1 subset)** — `autopilot.json` / `review_cmd` pin `claude-opus-4-8` while Wave E
   reviews ran on `claude-opus-5-5`; pick one and update config + docs/MODEL_DISCIPLINE.md. Tiny.

## Second

- **G-D.3 bounded self-grant** of colocated test file / barrel (TASK-041 blocked on exactly this).
- **G-B.3 mutation check** (tests that pass without the fix — KERYX's top rework cause). High value, tricky.

## Defer until evidence demands it

- **G-E capacity failover** — pays off only with several active builders.
- **G-B.1 reachability** — needs per-stack wiring; do it for a field project that suffers unwired components.
- **F3–F8** — all ship disabled by the spec's own N4 rule; opt-in experiments.
- **All of Wave H** — cost/memory optimisation whose own rule T3 says measure first; the F2 ledger it needs does
  not exist; exit criteria require a 50-task field replay; rests on fast-moving Claude Code features.

## The deciding question

Is the pack actively driving field projects (rwc-*, local-business-opportunity-engine*, scripturequest*, tower)?
- **Yes** → items 1–4 pay immediately (the evidence came from them).
- **Mostly self-building** → items 1, 2, 5 only; then point builders at product work and at
  `docs/EFFICIENCY_BACKLOG_2026-09.md` (parked until Wave E closed — now due).

## Open loose ends at park time

- Run one full Python + Node suite on master when memory allows (closes the TASK-043 waiver).
- `control.py:252` writes bare `CAPACITY` (needs `CAPACITY: <detail>`) — fold into the next task owning control.py.
- Branch `task/TASK-045-cx` is merged but still checked out in `wt-codex-DEVDEPARTMENT`; delete when CX moves off it.
- Something appends duplicate `**Updated_At:**` lines in builder task blocks — investigate plan_commit/plan_stamp.
