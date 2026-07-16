---
description: Review needs_review tasks — verify, verdict, merge or rework
---

You are ORCH executing **Phase 4 — Review & Integration**. Review standard per CLAUDE.md; you are the quality gate and the only unit that can mark work done.

For each task with `Status: needs_review` (or the specific task in $ARGUMENTS):

1. **Territory audit:** `git diff main...task/TASK-NNN-xx --stat`. Any file outside `Owned_Paths` → automatic verdict `rework`, no exceptions; record which paths violated.
2. **PLAN.md discipline audit:** `git log -p -- PLAN.md` for this unit's edits — frontmatter touches, other-block edits, or deleted lines are protocol violations (verdict rework + note in REVIEW.md).
3. **Spec verification:** open every `Spec_References` document; check each acceptance criterion against the spec text itself. Builder summaries are claims, not evidence.
4. **Independent test run:** execute the tests yourself in the branch/worktree. Compare with `Test_Evidence`. Discrepancy → rework.
5. **Code quality:** error handling, input validation, logging, dead code, security smells (hardcoded credentials — a known past failure class), production-readiness.
6. **Verdict:**
   - `approved` → merge: `git merge --no-ff task/TASK-NNN-xx -m "merge: TASK-NNN <title> [ORCH]"`; set `Status: done`; delete the branch; check which `Depends_On` chains this unlocks and note them in `orchestrator_notes`.
   - `rework` → write precise, actionable findings into the task's `Review_Findings`; set `Status: in_progress`; the builder fixes on the same branch.
7. **Log:** append to REVIEW.md: `| TASK-NNN | <unit> | <verdict> | <findings summary> | first-pass: yes/no | <UTC timestamp> |`. Update the per-unit tallies at the top of REVIEW.md — this evidence feeds protocol §8 assignment heuristics.
8. Run `python scripts/validate_plan.py`, commit (`chore(review): TASK-NNN <verdict> [ORCH]`), and report verdicts + newly unlocked work to Alister.

$ARGUMENTS
