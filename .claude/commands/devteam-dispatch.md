---
description: Create worktrees and launch/queue builders against the plan
---

You are ORCH executing **Phase 2 — Delegation & Execution**.

1. Run `python scripts/validate_plan.py`. Non-zero exit → stop and fix; never dispatch against an illegal plan.
2. Identify eligible tasks: `Status: pending`, `Assigned_To` in {GB, CX}, all `Depends_On` done. Re-verify pairwise territory disjointness across (eligible ∪ currently active) tasks per assignee pairing — if a would-be-concurrent pair intersects, re-sequence with Depends_On before dispatching.
3. Ensure worktrees exist: `powershell -File scripts/worktree.ps1 -Action create` (or `git worktree add ../wt-grok main` / `../wt-codex main` directly).
4. Launch builders:
   - If the CLIs are on PATH and headless flags in `scripts/dispatch.ps1` are configured: run `powershell -File scripts/dispatch.ps1 -Builder grok` and `-Builder codex` (they self-validate before and after).
   - Otherwise: print the exact launch command + condensed prompt for Alister to run in each builder's own terminal (use `-DryRun` output).
5. Note the dispatch in `orchestrator_notes` (who was launched, against which tasks, when) and commit: `chore(plan): dispatch wave <n> [ORCH]`.
6. Tell Alister what was dispatched and when to run `/status`.

$ARGUMENTS
