# Dossier — TASK-011 · Dispatch reclaims an empty, unregistered worktree directory

**Brief:** This one stopped a live supervised-loop tick. Dispatch refuses to reuse a directory sitting at the expected worktree path unless git recognises it as a worktree of this repo — correct, because a foreign directory may hold someone's work. But on Windows a removed worktree routinely leaves an **empty** directory behind (a process still holds a handle), and the guard cannot tell that husk from real work. Result: that builder can never be dispatched again until a human deletes the folder. Three occurrences so far; the third blocked GB on tick 1 of the first L2 run.

**Spec:** `specs/L2_DISPATCH_RESILIENCE.md` §2 (R-A). Read §1 L1 — prevent the trigger rather than improve the report — and §6, which is graded: this wave is about failure handling, so evidence must show the failure conditions actually happening.

**Intended approach:**
- The guard already exists in both scripts (the "exists but is not a registered worktree" branch). Add the empty check inside it — don't restructure the surrounding logic.
- "Empty" means **no entries at all**, including dotfiles. A directory holding only `.foo` is NOT empty and must still be refused: it plausibly contains something a human cares about.
- Reclaim = remove the directory, log one line naming what was reclaimed and why, fall through to the normal `git worktree add` path.
- Removal can still fail if the lock is genuinely held. That path must produce today's clean refusal, not a crash and not a half-removed directory.
- `dispatch.sh` and `dispatch.ps1` must agree. The bug is Windows-visible but the contract is platform-neutral, and the two scripts drifting is its own recurring defect class in this pack.

**Testing note:** `tests/test_dispatch_worktree.py` already exists — extend it. Cover all three states (empty → reclaimed, file → refused, dotfile-only → refused) and assert on the refusal message so a future refactor can't silently drop the guidance.

**Territory note:** `scripts/**` is builder-protected; your two dispatch scripts are deliberate per-task grants in `hooks/lib.js`, removed when this task is done. TASK-012 (CX) runs CONCURRENTLY in `scripts/supervisor.py` — adjacent subject, disjoint files. If you believe a fix needs supervisor changes, that is an `OWNERSHIP_CONFLICT` block, not a quick edit.

## Work Log
