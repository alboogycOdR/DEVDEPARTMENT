# REVIEW.md — Review Log (ORCH-only writes)

## Per-unit performance tallies

| Unit | Reviews | First-pass approvals | Rework | Common rework causes |
|---|---|---|---|---|
| GB | 1 | 1 | 0 | — |
| CX | 1 | 1 | 0 | — |

Evidence here refines assignment heuristics (protocol §8) after ~10 reviews.

## Verdicts

| Task | Unit | Verdict | Findings | First-pass | Timestamp |
|---|---|---|---|---|---|
| TASK-002 | CX | approved | Territory clean (5/5 in Owned_Paths, no PLAN.md edits). 618 Python + 30 core + 36 Node green, re-run by ORCH — matches Test_Evidence. §7 A1 exit criteria all pass on repo: full scan 1.41s (<10s), `where decide`→supervisor.py:224+callers, `impact builder_registry.py` lists budget.py+validate_plan.py, one-touch re-parses exactly 1, exit codes 0/1 never 2. Non-blocking: (1) query uses LIKE not FTS5 MATCH/bm25 (fast now, revisit at scale); (2) no ls/find check evidence in history (first-occurrence, all files net-new). | yes | 2026-08-13T16:07:16Z |
| TASK-003 | GB | approved | Territory clean: own commit 289499a = exactly the 2 Owned_Paths; merge 6f61a81 carried only ORCH's master fix f938fa0 + PLAN.md forward, no GB out-of-territory edits; c8b9872 filesystem-check evidence present; no GB PLAN.md/frontmatter edits. Spec verified §1/§3/§6 A2/R4 — all 6 ACs map to spec text; parser reuse confirmed (parse_tasks/ROW_RE/parse_instincts, no duplicate). ORCH re-ran in worktree: 18 episode + 636 full Python + 36 Node green (matches Test_Evidence); real-repo smoke episode hits TASK-002:1 + TASK-003:1, forward-slash, exit codes correct. Non-blocking: incremental never converges to changed:0 — a present source that parses to zero episodes (INSTINCTS.md today) is never recorded in the episodes table, so it re-counts as changed and rebuilds episodes_fts every run (cosmetic/perf, no data impact, no AC violated); idempotency of a zero-yield source is also untested. Fast-follow. | yes | 2026-08-13T16:32:48Z |
