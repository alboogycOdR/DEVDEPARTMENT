---
description: Decompose specs/ into PLAN.md tasks with disjoint territories
---

You are ORCH executing **Phase 1 — Planning**. Follow CLAUDE.md and docs/COORDINATION_PROTOCOL.md.

> **Model discipline:** run this command on `claude-fable-5`, reasoning effort **medium minimum** (high for complex or many-task waves). This is the highest-leverage judgment in the system — every builder faithfully executes whatever this step produces, so a decomposition error is the most expensive mistake in the pipeline. Effort is a depth knob, not a discount knob: territory carving and dependency sequencing are exactly where shallow reasoning misses interaction effects. Switch before proceeding; revert to your session default after.

1. Read every document in `specs/` end to end. Read the current `PLAN.md`.
2. If the example tasks (TASK-000/001 marked EXAMPLE) are still present, delete them.
3. Decompose the specs into tasks sized for one builder session (~1–4 h agent work). For each task write the full schema block: Title, Status: pending, Assigned_To (per protocol §8 heuristics — or TBD only for backlog), Priority, Spec_References, **Owned_Paths**, Depends_On, Description, Acceptance_Criteria (each criterion traceable to a spec sentence), and initialise the remaining fields to `—` with Updated_By: ORCH and a real UTC timestamp.
4. **Territorial design is the core of this step.** Draft the intended file layout first, then carve territories so that any two tasks that could plausibly be active at the same time own disjoint paths. Shared/cross-cutting files get single-owner integration tasks, sequenced with Depends_On so they are never concurrent with tasks touching adjacent territory.
5. Balance the initial assignment so GB and CX each have at least one immediately eligible task (all dependencies satisfied) — the units are waiting.
6. Bump `plan_version`, set `overall_status`, update `last_updated` and `orchestrator_notes` (what the first dispatch wave should be).
7. Run `python scripts/validate_plan.py`. Fix every violation. Do not finish this command with a failing validator.
8. Commit: `docs(plan): plan v<X.Y> — <N> tasks from <M> specs [ORCH]`.
9. Report to Alister: task table (ID, title, assignee, priority, territory), the dependency graph, the first dispatch wave, and any spec ambiguities you resolved or need answered.

Additionally (v4): for every task created, write `dossiers/TASK-NNN.md` containing the brief (2-4 sentences), the relevant spec excerpts/pointers, the intended approach, and an empty `## Work Log` section. The dossier is the context hand-off that kills re-briefing between units.

$ARGUMENTS
