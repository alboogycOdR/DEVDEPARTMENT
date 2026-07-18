# LEARNING.md — the DEVDEPARTMENT continuous learning loop (Wave C, v4.3)

## Purpose

Rework patterns captured in REVIEW.md must stop repeating. The learning loop
distills review evidence into per-project **instincts** injected into every
dispatch prompt whose territory they cover, and **proposes** (never silently
applies) improvements to the system's own constitution.

```
REVIEW.md findings
      │  (≥ min_new_findings since last run, or distill_every_n_reviews)
      ▼
distiller.py ──► deterministic lifecycle pass (code-owned math)
      │              bump +0.1 on matching rework · decay −0.15 after
      │              5 consecutive clean first-passes · probation < 0.3
      │              · retire < 0.15 (from probation only)
      ├──► headless sonnet-5 call (structured output only)
      │        ├── INSTINCTS.md blocks  → auto-applied (data, git-reviewable)
      │        └── ## PROPOSED AMENDMENT → .devteam/pending_amendments/AMEND-NNN.md
      │                                     + P2 Telegram: /approve · /rework
      ▼
INSTINCTS.md ──► dispatch.sh / dispatch.ps1 inject top 5 matching instincts
                 as "## PROJECT INSTINCTS — treat as acceptance criteria"

retro.py (weekly) ──► RETRO-<isoweek>.md  (descriptive only, mutates nothing)
```

## The constitutional gate (the rule, stated explicitly)

**The distiller may write exactly one file: `INSTINCTS.md`.** Instinct entries
are data — low blast radius, git-reviewable, auto-applied.

**AGENTS.md, CLAUDE.md, and `briefings/*.md` are the constitution.** If the
distiller concludes the root cause of a failure pattern is a gap in the
constitution (including a new "Common Rationalizations" row), it writes a
`## PROPOSED AMENDMENT` section, which is stored as
`.devteam/pending_amendments/AMEND-NNN.md` with `**Status:** pending` and
raised as a P2 Telegram escalation. Nothing outside `pending_amendments/` is
touched — the test suite asserts AGENTS.md/CLAUDE.md/briefings are
byte-identical across every distiller run.

The gate has **two locks**:
1. The distiller never writes protocol files.
2. `/approve AMEND-NNN` only flips the proposal to `**Status:** approved` —
   the actual constitutional edit is applied by ORCH in a supervised session,
   committed with the `[TG]`-referenced audit trail. The Telegram bot never
   edits the constitution either.

`/rework AMEND-NNN <reason>` marks the proposal `rejected` and records the
reason (sanitized as inert data, same discipline as `/answer`).

## INSTINCTS.md schema (round-trip exact)

```markdown
### INST-007
**Rule:** Firestore rules changes must include emulator-run evidence before needs_review.
**Territory:** functions/**, firestore.rules
**Confidence:** 0.9
**Source:** TASK-005 rework, TASK-012 rework
**Status:** active
```

`Status ∈ active | probation | retired`. IDs sequential `INST-NNN`, never
reused (the ID scanner includes retired blocks). New instincts seed at
confidence **0.6** regardless of what the model claims. Territory matching is
a pass-through to `validate_plan.globs_intersect` — there is exactly one glob
implementation in this system.

## Confidence lifecycle (code-owned, not model-owned)

| Event | Effect |
|---|---|
| New rework finding matching territory | +0.1 (cap 1.0), task ID appended to Source, clean streak reset |
| 5 consecutive clean first-passes in territory since last bump | −0.15, streak reset |
| Confidence < 0.3 (active) | → probation (still injected, flagged `[PROBATION]`) |
| Confidence < 0.15 (probation) | → retired (never injected, kept for history) |

The lifecycle pass runs *before* the model call each distillation, so the
math is deterministic and testable; the model only drafts rules and proposes
status transitions, which the code validates against the thresholds (a
premature retirement proposal is rejected).

## Dispatch injection

`dispatch.sh`/`dispatch.ps1` compose one generic resume-first prompt per unit
and let the builder's own session decide (via its resume-first/claim logic)
which task it ends up working — they don't know a specific task's
`Owned_Paths` ahead of launch. So the real integration point is:

```
scripts/instincts.py inject --unit GB --repo <path> --limit 5
```

which predicts the same task the builder's own resume-first rule would pick
(an in_progress/claimed task for that unit first, else the highest-priority
pending task with dependencies done) and resolves *that* task's
`Owned_Paths` itself before matching instincts. `--paths "<Owned_Paths>"` is
also available directly for any caller that already knows the target task
(this is what the test suite exercises, since it doesn't need a live PLAN.md
claim to test territory matching). Either way it prints the
`## PROJECT INSTINCTS — treat as acceptance criteria` section (highest
confidence first, active + probation only, retired never) or nothing at all.
Fail-open throughout: a corrupted INSTINCTS.md, a missing PLAN.md, or nothing
matching all yield empty output and exit 0 — a broken instinct store can
never block a dispatch.

## Weekly retro

`retro.py` drafts `RETRO-<isoweek>.md` via the shared `scheduling.py`
weekly-marker helper: cycle times (Started_At → Updated_At on done tasks),
territory churn, instinct effectiveness (first-pass rate in instinct-matched
territories vs. project average), escalation counts, team_stats snapshot, and
all pending AMEND proposals with their reply commands. Purely descriptive —
the retro never mutates INSTINCTS.md or any protocol file.

## Failure behavior (all fail-open)

| Failure | Behavior |
|---|---|
| Model call fails / times out | Logged to AUTOPILOT_LOG.md, run skipped, tick unaffected |
| Malformed model output | INSTINCTS.md left byte-identical (temp-write → parse-validate → atomic rename) |
| < min_new_findings new findings | Clean skip — no noise distillation |
| Corrupted INSTINCTS.md | Loads as empty; injection prints nothing; dispatch proceeds |
| notify/team_stats import failure | Degraded silently; distillation still completes |
