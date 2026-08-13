# Dossier — TASK-002 · ATLAS A1 — scanner, schema, core query CLI

**Brief:** Build ATLAS Layer 0 from scratch: `scripts/atlas.py` (thin argparse façade) + `scripts/atlas_core.py` (scanner, full six-table SQLite/FTS5 schema, `scan/query/where/impact/status`). This is the root of the whole ATLAS diamond — every later increment plugs into the extension hooks you build here, so the façade's `register(subparsers)` optional-import design and the complete read paths (episodes FTS in `query`, FRESH/STALE annotation from the cards table) are non-negotiable parts of the deliverable, not gold-plating.

**Spec:** specs/DEVDEPARTMENT_ATLAS_SPEC.md — read it in full. Load-bearing sections: §0 (R1–R4 hard constraints), §1 (schema + component list), §2 (Tier A/B language scope — do not exceed it), §3 (exact CLI strings; forward-slash paths; exit 0/1, never 2), §6 row A1, §7 A1 exit criteria.

**Intended approach:**
- Pure Python stdlib only: `sqlite3` (FTS5), `ast` for Python symbols, `re` for JS/TS/Dart/MQL5. No pip installs.
- Create ALL six tables (files, symbols, edges, cards, episodes, meta) even though cards/episodes stay empty until TASK-003/004 — later tasks populate, never migrate.
- `query` reads episodes FTS and annotates file hits FRESH/STALE (`cards.source_hash` vs `files.content_hash`) from day one; empty tables degrade silently (R4). This is what lets TASK-003/004 ship without touching your files.
- Façade: after registering core subcommands, `for mod in (atlas_episodes, atlas_cards, atlas_pack): try import; mod.register(subparsers); except ImportError: pass` (with a graceful "not installed" stub subcommand or absent command — your choice, message required).
- Incremental scan: hash every file, re-parse only changed hashes; `--full` drops and rebuilds. Honor `.gitignore` (use `git ls-files`/check-ignore or a parser — document choice) + `atlas.exclude` list.
- R2 same-commit rule: `.gitignore` entry for `.devteam/atlas.db` and `sync-manifest.json` framework_owned entries land in your first commit.
- ≥25 tests; §7 exit criteria run against DEVDEPARTMENT itself (`where decide`, `impact scripts/builder_registry.py`, one-file rescan).

**Territory note:** scripts/** is normally builder-protected; your Owned_Paths are a deliberate ORCH grant for this task only. Touch nothing in scripts/ except atlas.py / atlas_core.py.

## Work Log
