# Dossier — TASK-003 · ATLAS A2 — episodic indexer

**Brief:** Build `scripts/atlas_episodes.py`, populating the `episodes` table (created by TASK-002) from dossiers/, REVIEW.md, INSTINCTS.md, RETRO-*.md. Pure parsing, zero LLM (R4). You expose `register(subparsers)`; the TASK-002 façade gives you the `episodes [--reindex]` subcommand — you never edit atlas.py or atlas_core.py, and core `query` already surfaces your rows.

**Spec:** specs/DEVDEPARTMENT_ATLAS_SPEC.md — read in full; load-bearing: §1 (Episodic indexer bullet + episodes schema row), §3 (CLI contract), §6 row A2, R4.

**Intended approach:**
- Reuse `scripts/validate_plan.py`'s `parse_tasks` for dossier/task-shaped content and the existing REVIEW.md verdict-row grammar (`| TASK | unit | verdict | findings | first-pass | ts |`). The spec forbids writing a second parser for a format that already has one.
- episodes columns: kind (dossier/review/instinct/retro), ref (task_id / INST-id / path), ts, unit, indexed_hash, body_fts.
- Incremental: hash each source; skip unchanged on plain `episodes`; `--reindex` rebuilds the table.
- Forward-slash paths, UTF-8, exit 0/1 never 2.
- Verify end-to-end: after indexing, `atlas.py query "<term from a review finding>"` returns the episode hit (§6 A2 acceptance).

**Territory note:** scripts/** is builder-protected; scripts/atlas_episodes.py is a deliberate per-task grant. Concurrent with TASK-004 (S5) — stay strictly inside your two files.

## Work Log
