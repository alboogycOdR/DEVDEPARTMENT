# ORCH Model Discipline — Current State & Open Decision

## Current table (from `CLAUDE.md`)

| Operation | Model | Rationale |
|---|---|---|
| `/devteam-review` — full territory diff + spec verification + test run | `claude-sonnet-5` | High-stakes judgment; rework verdict must be correct |
| Scope triage — unblocking, re-carving territories, dependency re-sequencing | `claude-sonnet-5` | Architectural reasoning; a wrong call cascades across tasks |
| Architectural decisions — task decomposition, Owned_Paths design | `claude-sonnet-5` | Planning errors are expensive to unwind mid-wave |
| `/devteam-status` — sync scan, health report, PLAN.md read | `claude-sonnet-4-6` | Pattern-matching over structured state; no deep judgment needed |
| PLAN.md updates — frontmatter, orchestrator_notes, status writes | `claude-sonnet-4-6` | Mechanical structured writes |
| `/devteam-dispatch` — validate + launch builders | `claude-sonnet-4-6` | Script execution; decision already made at planning time |
| AUTOPILOT_LOG.md and REVIEW.md append operations | `claude-sonnet-4-6` | Logging; no reasoning required |

**Switching mechanism:** manual — model selector in UI, or `--model` flag on a headless session. Revert to session default after the high-stakes op completes.

## The open question

ORCH's high-stakes rows run on `claude-sonnet-5` — the **same model** as the **S5** builder unit (dispatched headless via `dispatch.ps1`/`.sh -Builder claude`). The only separation between "orchestrator" and "builder" today is *interactive vs. headless dispatch*, not model tier. No row currently assigns ORCH a heavier model (e.g. `claude-opus-4-8`) for judgment calls.

**Decision needed:** keep ORCH and S5 on the same model tier (rely on role/context separation only), or move ORCH's high-stakes rows to a heavier model to give it a real capability edge over the builder it reviews and orchestrates?
