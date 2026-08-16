# Dossier — TASK-009 · Episodic indexer convergence

**Brief:** `atlas.py episodes` never settles. Run it twice and the second run still reports sources as changed, because a source that parses to ZERO episodes writes no rows — and `_existing_source_hashes` reads the hash keys back out of the `episodes` table, so a zero-episode source can never be recorded as seen. Today that's `INSTINCTS.md`; on any project it's every empty or unparseable source. Found as a review finding on the task that built this file.

**Spec:** `specs/PACK_HARDENING_2026-08.md` §3 C2, plus §0 H3 (the regression test must fail against current code).

**Intended approach:**
- Track indexed source hashes for every source SCANNED, not merely every source that produced rows. Either widen the existing storage to record zero-episode sources, or keep the hash bookkeeping in its own place rather than deriving it from `episodes` — your call, but state which you chose and why in the Work Log.
- `--reindex` must still wipe and rebuild; don't let the convergence fix accidentally make a full rebuild a no-op.
- The §3 CLI contract is fixed: output shape (`episodes indexed: N; sources scanned: N; sources changed: N`), forward-slash paths, exit 0 on success including empty results, 1 on real errors, never 2.

**Verification:** the acceptance criterion is empirical and cheap — run `python scripts/atlas.py episodes` twice against this repo and show both outputs in Test_Evidence. The second must read `sources changed: 0`.

**Territory note:** `scripts/**` is builder-protected; `scripts/atlas_episodes.py` is a deliberate per-task grant, removed at done. Do NOT touch `atlas_core.py` — TASK-008 owns it and is running CONCURRENTLY with you in its own worktree. If you believe a fix requires a core change, that is an `OWNERSHIP_CONFLICT` block, not a quick edit.

## Work Log
