---
description: Review needs_review tasks — verify, verdict, merge or rework
---

You are ORCH executing **Phase 4 — Review & Integration**. Review standard per CLAUDE.md; you are the quality gate and the only unit that can mark work done.

> **Reviewer identity:** use the model selected for this ORCH review session. For a headless review, the configured model is the `--model` value in `autopilot.json`'s `review_cmd` (fall back to `judgment_model` only when no model is specified). Record the actual runtime model when exposed; otherwise identify it explicitly as the configured model. Never invent or infer a different model name.

For each task with `Status: needs_review` (or the specific task in $ARGUMENTS):

1. **Territory audit:** `git diff main...task/TASK-NNN-xx --stat`. Any file outside `Owned_Paths` → automatic verdict `rework`, no exceptions; record which paths violated.
2. **Filesystem check audit (c8b9872):** verify in `git log task/TASK-NNN-xx` that the builder ran `ls`/`find` on each `Owned_Paths` entry before writing code. Evidence is a shell command in the commit history or a Progress_Note. If no evidence of the check exists → flag as a protocol gap in `Review_Findings` (not automatic rework on first occurrence, but a repeat absence is a rework trigger).
3. **PLAN.md discipline audit:** `git log -p -- PLAN.md` for this unit's edits — frontmatter touches, other-block edits, or deleted lines are protocol violations (verdict rework + note in REVIEW.md). For GB specifically: any edit to a task block that is not its own claimed task is an automatic rework per the c8b9872 hard prohibition.
4. **Spec verification.** Check each acceptance criterion against the spec text itself — builder summaries are claims, not evidence. If a `Spec_References` document is long, delegate to a subagent (Task tool): "read <spec path>, extract only the passages bearing on these acceptance criteria: <list>, quote them verbatim with section refs." You verify against the extracted passages; the full document never enters this session. Short specs — read directly, delegation costs more than it saves.
5. **Independent test run — delegate it.** Run the suite via a subagent, not inline: "run <test command> in <worktree path>; report pass/fail counts, and for any failure the test name, assertion, and relevant traceback lines — nothing else." A full suite is thousands of tokens of output you'd otherwise hold for the rest of the review, on the most expensive model in the system, to learn one number. Compare the returned summary with `Test_Evidence`; discrepancy → rework. If the subagent's summary is ambiguous about whether a specific test actually ran, ask it again — do not fill the gap by assuming, and do not accept the builder's own claim as a substitute.
6. **Code quality:** error handling, input validation, logging, dead code, security smells (hardcoded credentials — a known past failure class), production-readiness.
7. **Verdict:**
   - `approved` → merge: `git merge --no-ff task/TASK-NNN-xx -m "merge: TASK-NNN <title> [ORCH]"`; set `Status: done`; delete the branch; check which `Depends_On` chains this unlocks and note them in `orchestrator_notes`.
   - `rework` → write precise, actionable findings into the task's `Review_Findings`; set `Status: in_progress`; the builder fixes on the same branch.
8. **Clock-stamp and log:** immediately before recording each verdict, obtain UTC time from the system clock with `date -u +"%Y-%m-%dT%H:%M:%SZ"` (or PowerShell `[DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")`). Use that exact value in the REVIEW.md row; never type, round, reuse, or ask a model to generate the timestamp. Append `| TASK-NNN | <unit> | <verdict> | <findings summary>; reviewer model: <actual/configured model> | first-pass: yes/no | <UTC timestamp> |`. Also name the reviewer model in the task's `Review_Findings`. Update the per-unit tallies at the top of REVIEW.md — this evidence feeds protocol §8 assignment heuristics.
9. Run `python scripts/validate_plan.py`, commit (`chore(review): TASK-NNN <verdict> [ORCH]`), and report verdicts + newly unlocked work to Alister.

$ARGUMENTS
