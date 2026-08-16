# Dossier — TASK-008 · atlas_core correctness (gitignore, worktree index, FTS5)

**Brief:** Three confirmed defects in `scripts/atlas_core.py`, bundled because they share one file. All three were found in live use, not synthetic testing: the gitignore bug polluted a real monorepo's index, the worktree bug cost three rounds of misdiagnosis on another project, and the FTS5 one is a deferred review finding from the task that built this file.

**Spec:** `specs/PACK_HARDENING_2026-08.md` §1 (H-A), §2 (H-B), §3 C1. Read §0 first — H1 (fix the semantics, not the symptom) and H3 (a lesson isn't fixed until a test would have caught it) are graded at review.

**Intended approach:**
- **H-A, `is_ignored()`**: the current code does `rel == pattern or rel.startswith(pattern + "/")`, which is root-anchored. Git's actual rule: a pattern with no internal `/` matches at ANY depth. Restructure around three cases — leading-slash (root-anchored), internal-slash (root-anchored), no-slash (any depth, as a path SEGMENT for directory patterns and as a basename for file patterns). Trailing slash = directory-only. The existing parametrized cases in `tests/test_atlas_core.py` are your regression net — they must keep passing unchanged.
- **H-B, `db_path()`**: `git rev-parse --git-common-dir` from any linked worktree returns the main checkout's `.git`; its parent is the main root. Wrap in try/except and fall back to today's `repo/.devteam/atlas.db` on any failure — R4 says the tool degrades, never errors, when git is absent. Test with a REAL linked worktree (`git worktree add`) in a temp repo, not a simulated one.
- **C1, FTS5**: `query` currently does `LIKE '%term%'`. Move to `MATCH` with `ORDER BY bm25(...)`. The trap is user input: FTS5 treats `-`, `"`, `*`, `:` as syntax, so a query like `foo-bar` or `a:b` raises `sqlite3.OperationalError`. Quote terms defensively. Output format, FRESH/STALE annotation and exit codes are contract (§3) — do not change them.

**Verification that matters:** run a full scan of this repo before and after H-A and state the file-count delta in Test_Evidence — a large unexplained drop means the widening went too far.

**Territory note:** `scripts/**` and `docs/**` are builder-protected; your three paths are deliberate per-task grants in `hooks/lib.js`, removed when this task is done. TASK-009 and TASK-010 run CONCURRENTLY with you in their own worktrees — stay strictly inside your files.

## Work Log
