---
description: Prepare generate-lane tasks — classify, budget, and stage one long OMLCP generation onto the task branch
---

You are ORCH executing **Phase 1b — Generation staging** (Wave O, `specs/OMLCP_GENERATION_LANE_2026-10.md`).
Guide: `docs/OMLCP.md`. Read it the first time you run this command in a session, not every time.

> **Model discipline:** run on the mechanical model; classification and staging are scripted. The judgment
> already happened at `/devteam-decompose` when you wrote the manifest. The *generator* model is
> `autopilot.json → omlcp.model` and must differ from the judgment model — `omlcp` refuses otherwise.

For each task ID in `$ARGUMENTS` (or, if empty, every `pending` task with `**Lane:** generate` whose
`Depends_On` are all `done`):

1. `python scripts/validate_plan.py` — non-zero exit → stop and fix.
2. `python scripts/omlcp.py classify TASK-NNN`. If it prints `ITERATE`, do **not** force it: fix the plan
   (manifest, `(new)` markers, territory, wording) or set `**Lane:** iterate` and dispatch normally.
3. `python scripts/omlcp.py packet TASK-NNN`. Input above ~30k tokens → cite narrower spec sections or drop
   context files, then rebuild. Estimated output above one segment is fine up to three; above that, split.
4. `python scripts/omlcp.py stage TASK-NNN` (assignee from `Assigned_To`; `--unit` to override). It
   generates in `../wt-omlcp-<project>-<task>` on `task/TASK-NNN-<suffix>`, commits the generation as one
   commit, and removes the worktree. Generation can take 10–30 minutes per segment: run it as a background
   job / subagent and take back only the final summary lines.
   - **COMPLETE** → note in `orchestrator_notes`: `TASK-NNN generation staged at <sha> (<N> segments, verify <PASS|k findings>)`.
   - **INCOMPLETE** → re-run the same command once (it resumes). Still incomplete → read the stop reason;
     fix the packet and re-run with `--fresh`, or fall back: set `**Lane:** iterate` and dispatch normally.
     Remove a leftover staging worktree before dispatch (`git worktree remove <path>`).
5. Commit PLAN.md notes: `chore(plan): stage OMLCP generation for TASK-NNN [ORCH]`.
6. Run `/devteam-dispatch` as usual. The builder claims, switches to the existing branch, verifies, repairs,
   logs repair cost and hands off. Review it with the normal `/devteam-review` — generated code gets no
   discount on the review standard.
7. Report to Alister: per task — lane, packet budget, segments, verify result, cost from `omlcp report`.
