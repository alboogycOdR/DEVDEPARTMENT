---
description: Run one autonomous wave — dispatch, monitor, auto-review, merge; escalate only real blockers
---

You are ORCH executing **AUTOPILOT L1 — one full autonomous wave** per docs/AUTOPILOT.md. Chain the phases yourself; do not stop to ask the human between routine steps. Interrupt the human ONLY per the escalation contract.

Loop until the wave is complete or a P1 condition fires:

1. **Gate.** `python scripts/validate_plan.py`. Illegal → P1: report violations, identify the offending unit via `git log -p -- PLAN.md`, STOP.
2. **Review lane.** For every `needs_review` task, execute the full /devteam-review procedure (territory diff, PLAN.md discipline audit, spec verification, independent test run, quality read). `approved` → merge --no-ff, Status: done, delete branch, log to REVIEW.md. `rework` → findings into Review_Findings, Status: in_progress. **If the same task returns to rework a 2nd time → P1: freeze it (`blocked`, `OTHER: MAX_REWORK`) and escalate with accumulated findings.**
3. **Blocked lane.** Triage per protocol §7. SPEC_AMBIGUITY → collect the question, continue other lanes, include in escalation batch (P2). OWNERSHIP_CONFLICT → re-carve territories once yourself; repeat occurrence → P2. MISSING_DEPENDENCY → re-sequence.
4. **Dispatch lane.** For each idle builder with an eligible pending task (deps done, priority order): re-verify territory disjointness, then launch via `bash scripts/dispatch.sh grok|codex` (or dispatch.ps1 on Windows). If headless launch isn't possible in this environment, print the launch command, then continue monitoring.
5. **Monitor.** After dispatching, poll: re-read PLAN.md, check for new needs_review/blocked states, check heartbeats (stale > 90 min → simply redispatch that builder; its resume-first rule per protocol §10a continues the existing branch from the last Progress_Note — never reset an in_progress task to pending; third stale on the same task → P2).
6. **Evidence.** After each review batch run `python scripts/team_stats.py` and adjust which builder gets the next critical task if the hint says so.
7. **Wave end.** All tasks done (or only frozen/escalated ones remain) → write the digest.

Every autopilot commit message ends `[AUTOPILOT]`. Log every action as one line in AUTOPILOT_LOG.md.

**Model discipline (per CLAUDE.md table).** Switch models per operation within this wave — do not run everything on the same model:
- **`/devteam-review`, scope triage, territory re-carving, architectural decisions** → `claude-sonnet-5`
- **`/devteam-status`, PLAN.md writes, dispatch, log appends** → `claude-sonnet-4-6`

Switch the model selector before each high-stakes operation; revert after. This is not optional — a rework verdict written at the wrong model level is the most expensive mistake in the wave.

**Context checkpoint discipline (protocol §10b).** If your own context approaches ~80%: write a detailed `orchestrator_notes` checkpoint to PLAN.md frontmatter (wave/phase, active tasks, exact next ORCH action, open decisions), commit `chore(plan): ORCH context checkpoint [AUTOPILOT]`, and tell the human to start a fresh session with "Resume DEVDEPARTMENT orchestration — continue autopilot wave". A fresh session recovers via git log → CLAUDE.md → PLAN.md → REVIEW.md and continues the wave; never push past the limit mid-review or mid-merge.

**Final digest to the human (the ONLY routine output):**
- Wave outcome: N tasks done, first-pass rate per unit, merges performed
- Escalations: each P1/P2 with your recommended decision (never an open question without a recommendation)
- Unlocked next wave: what /plan or /devteam-dispatch should tackle next

$ARGUMENTS
