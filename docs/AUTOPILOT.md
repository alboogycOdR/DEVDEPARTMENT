# AUTOPILOT — Autonomous Orchestration Layer (v1.0.0)

Extends the Coordination Protocol. Nothing here weakens the protocol; autopilot
is simply ORCH executing the existing phases in a loop **without waiting for a
human between waves**, plus a hard escalation contract that defines exactly when
the human (Alister) is pulled back in.

## The autonomy ladder

| Level | Name | Human involvement |
|---|---|---|
| L0 | Manual | Human runs /plan, /dispatch, /status, /review by hand (where you started) |
| L1 | Assisted | Human runs /autopilot per wave; ORCH chains dispatch→monitor→review→merge for one wave, then stops |
| L2 | Supervised loop | `supervisor.py --loop` runs continuously; ORCH auto-reviews and auto-merges **approved** work; human sees only escalations + a digest |
| L3 | Self-extending | L2 + ORCH may auto-create follow-up tasks from review findings and re-plan territories within an approved scope envelope |

Start at **L1** for one wave to build trust, then move to **L2**. L3 is opt-in
per project via `autopilot.scope_envelope` in the config.

## The core loop (one "tick")

Model assignments per tick action (CLAUDE.md model discipline; rationale in docs/MODEL_DISCIPLINE.md):
- **REVIEW, REVIEW_TG, TRIAGE_UNBLOCK** → `claude-opus-4-8` (judgment calls; wrong verdict cascades).
  Taken from `autopilot.json` → `review_cmd` / `judgment_model`, never hardcoded. Specifically NOT
  `claude-sonnet-5`: that is the S5 builder's own model, and a checker must not share the maker's
  blind spots.
- **DISPATCH, status reads, PLAN.md writes, log appends** → `claude-sonnet-4-6` (mechanical)
- **Distiller** (Wave C) → `claude-sonnet-5` via `learning.model` — deliberate; it is not a gate.

```
tick:
  1. validate_plan.py          → illegal plan?            ESCALATE (P1)
  2. scan tasks
     needs_review exists?      → launch ORCH review session (headless claude -p /devteam-review
                                  --model claude-opus-4-8, per review_cmd)
                                  verdict approved → merge happens inside review
                                  verdict rework   → count rework cycles
  3. blocked exists?           → classify:
       SPEC_AMBIGUITY          → ESCALATE (P2, batched) — only a human answers spec questions
       MISSING_DEPENDENCY      → re-sequence if ORCH can; else ESCALATE (P2)
       OWNERSHIP_CONFLICT      → ORCH re-carves territories, unblocks; 2nd occurrence on same task → ESCALATE (P2)
       TOOLING_FAILURE         → retry once; then ESCALATE (P2)
  4. eligible pending exists AND builder idle?  → dispatch that builder
  5. stale heartbeat (> stale_minutes on active task)?    → redispatch that builder; its resume-first rule
                                  (protocol §10a) continues the existing branch from the last Progress_Note;
                                  3rd stale on same task → ESCALATE (P2)
  6. all tasks done?           → ESCALATE (P0-GOOD: wave complete digest) and stop
  sleep(interval); repeat
```

## Escalation contract — the ONLY reasons the human is contacted

| Priority | Condition | Channel behaviour |
|---|---|---|
| **P1 — stop the line** | Protocol-illegal PLAN.md; out-of-territory diff detected; merge conflict on main; validator or git corruption; same task fails review ≥ `max_rework` (default 2) times | Immediate notification, loop pauses |
| **P2 — decision needed** | SPEC_AMBIGUITY; unresolvable dependency; repeated OWNERSHIP_CONFLICT; builder session died twice; task exceeds `max_task_hours` | Immediate notification, loop continues on other lanes |
| **P0 — digest** | Wave complete; or every `digest_hours` (default 4) a one-paragraph summary | Batched, never interrupts |

Everything else — claims, progress notes, passing reviews, merges, redispatches —
is logged to `AUTOPILOT_LOG.md` and **not** sent to the human. Silence means health.

## Rework-loop discipline

Auto-review may bounce a task to rework at most `max_rework` times. On breach,
the task freezes (`blocked`, `OTHER: MAX_REWORK — human review required`) and a
P1 escalation fires with the accumulated Review_Findings. This is the guardrail
against two AIs ping-ponging a task forever.

## Learning assignment (closes the loop on protocol §8)

`scripts/team_stats.py` parses REVIEW.md and emits per-unit metrics:
first-pass approval rate, mean rework count, rework causes by category, and
per-territory success (e.g. GB on `functions/**` vs CX on `lib/features/**`).
The /plan and /dispatch commands read its output; after ~10 reviews the
orchestrator assigns by evidence, not by static heuristics. Stats are also
included in every P0 digest so you can watch the team specialise.

## Why the supervisor survives context limits (synergy with protocol §10)

Protocol §10's design principle — *PLAN.md + git history = complete recoverable
state* — is exactly what makes L2 possible. The supervisor daemon holds **no
context window at all**: every tick re-reads PLAN.md cold and re-derives its
decisions. When ORCH or a builder exhausts context mid-wave, the supervisor
doesn't notice or care: builders resume via their resume-first rule on the next
dispatch, and each headless ORCH review session (`claude -p "/devteam-review"`)
is a fresh context that recovers state from CLAUDE.md → PLAN.md → REVIEW.md per
§10b. Context exhaustion becomes a routine, self-healing event instead of a
failure.

## Notifications

`scripts/notify.py` — pluggable channels: `console` (default), `file`
(AUTOPILOT_LOG.md), `telegram` (reads `DEVTEAM_TG_TOKEN` / `DEVTEAM_TG_CHAT`
from environment variables — **never hardcode credentials**, per your own
security-audit finding on the CRT EA). Telegram is ideal here: P1/P2 pings
reach your phone while the team works overnight.

## Safety rails (non-negotiable, enforced in code)

1. Autopilot never edits specs, never widens a territory mid-flight, never
   raises its own autonomy level.
2. Auto-merge happens **only** through the /devteam-review flow with its
   territory diff + independent test run — the same gate as manual mode.
3. A `STOP` file in the repo root halts the loop at the next tick (create it
   from anywhere: `echo. > STOP`). Deleting it resumes.
4. Every autopilot action is a git commit with `[AUTOPILOT]` suffix — the whole
   run is replayable and auditable after the fact.
5. `--max-ticks` and `--budget-minutes` caps for bounded overnight runs.
