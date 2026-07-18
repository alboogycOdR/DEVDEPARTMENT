# dossiers/ — Per-Task Context Dossiers (v4, borrowed from Starbird's Dispatch)

One file per task: `dossiers/TASK-NNN.md`. The dossier kills context re-briefing —
it holds the brief, the plan, and an append-only work log across every phase and
every unit. Rules:

- Created by ORCH at /devteam-decompose time (brief + spec pointers + plan sketch).
- **Append-only** for builders: add a `## Work Log` entry per session
  (`### [UTC] [UNIT]` heading, what was done, exact stopping point, next step).
- Read FIRST on claim/resume — never ask the human to re-explain anything in it.
- ORCH appends review outcomes; the dossier travels with the task to done.
- Referenced from the task's PLAN.md block; PLAN.md stays the coordination truth,
  the dossier is the context depth behind it.
