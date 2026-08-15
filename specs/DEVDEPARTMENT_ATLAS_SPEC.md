# ATLAS — Persistent Project Map & Memory. Build Specification

> **Changelog:** v1.2 (2026-08-15, ORCH) — Added increment **A6** (§9): status staleness in judgeable units + opt-in cards made visible, from oikonomos field defects (2026-08-15). §5 amendment recorded: dispatch.sh/.ps1 now run an incremental `scan` immediately before `pack` (non-fatal, ORCH commit 5af80c7) — the nightly audit was the only other scan caller, so intra-day dispatches served stale maps while pack's own db-open refreshed atlas.db's mtime and hid it. Cards policy decision (Alister, 2026-08-15): generation stays **opt-in** (Q1 deliberate-spend preserved); the defect was discoverability, fixed by A6's status messaging + onboarding ask-step — `cards_auto_refresh` default remains false.
> **Changelog:** v1.1 (2026-08-13, ORCH) — §7 A1 exit criterion corrected per TASK-002 OWNERSHIP_CONFLICT block: `impact scripts/builder_registry.py` must list the registry's *actual* Python importers (`budget.py`, `validate_plan.py`) — not "dispatch/validate/supervisor". supervisor.py has no builder_registry reference, and dispatch.sh/.ps1 consume it via subprocess (Tier B files carry no import edges per §2), so neither may be *required* of the import graph — an implementation is free to surface additional textual-reference hits, but the criterion binds only on true importers. The original sentence described consumers from memory, not from the tree.

**Status:** SPEC ONLY — nothing in this document is built yet.
**Baseline:** pack @ `caa5722` (v4.8 + live fixes + marker fix). All suites green (588 Python / 36 Node).
**Ships as:** v4.9 (increments 1–2) and v5.0 (increment 3), or per actual session boundaries.
**Origin:** BACKLOG.md item 5's trigger, pulled deliberately: builder orientation cost (each session re-reading the codebase to rediscover structure) is the single largest recurring token spend in the system, and session-to-session memory currently lives only in hand-written handovers and dossiers. ATLAS makes the project's structure and history a **queryable, freshness-verified, cross-CLI artifact** instead of something every session rebuilds from raw file reads.

**This spec is itself the bootstrap test:** DEVDEPARTMENT builds ATLAS for DEVDEPARTMENT (and then every onboarded project) using its own decompose → dispatch → review pipeline. Read this file in full before writing any code, exactly as the protocol requires of builders.

---

## 0. Design rules (hard constraints — violations are automatic rework)

These four rules are the spec's spine. Every increment's review checks them explicitly.

**R1 — Cards are claims; code is truth.** ATLAS output tells a builder *where to look*, never substitutes for looking. Any file a builder intends to **edit** must still be read live from disk in that session. Every generated summary carries the source hash it was built from, and every query response marks entries `FRESH` or `STALE (source changed since card generated)`. A stale card is served *with its warning*, never silently. This is the same epistemics as `Test_Evidence`: unverifiable claims are treated as claims.

**R2 — Derived artifact, never merged.** `.devteam/atlas.db` (and any cache beside it) is machine-local, rebuildable from scratch, `project_owned` in `sync-manifest.json`, and in `.gitignore` from the first commit of increment 1. The `graphify-out/`/`__pycache__` flood of `aaa6a59` on rwc-admin-portal is the failure mode this rule exists to prevent — it goes in the onboarding `.gitignore` block too.

**R3 — Cross-CLI by construction.** GB (Grok) and CX (Codex) cannot see an MCP server. Every ATLAS capability must be consumable as (a) a CLI invocation and (b) plain text injected into a dispatch prompt. No Claude-only integration path may be the only path. (A Claude-side skill wrapper is permitted *in addition*, never *instead*.)

**R4 — Zero-LLM layer stands alone.** Layer 0 (cartography) and Layer 2 (episodic indexing) must be fully useful with no model call ever made — deterministic, seconds to rebuild, no API dependency. LLM-generated content (Layer 1 cards) is an enhancement gated behind explicit invocation, priced at the mechanical tier, and the system degrades gracefully to Layer 0 when cards are absent or the model is unreachable.

---

## 1. Architecture

```
.devteam/atlas.db          (SQLite, one file, FTS5 enabled — R2: gitignored)
├── files       path, lang, content_hash, loc, mtime, indexed_at
├── symbols     file_id, name, kind(func/class/const/…), line_start, line_end, signature
├── edges       src_file_id, dst_file_id, kind(import/include/call?), detail
├── cards       file_id, source_hash, generated_at, model, purpose, invariants,
│               gotchas, entry_points, tokens_estimate          (Layer 1)
├── episodes    kind(dossier/review/instinct/retro), ref(task_id/INST-id/path),
│               ts, unit, indexed_hash, body_fts                (Layer 2)
└── meta        schema_version, last_full_scan, pack_version
```

- **Scanner** (`scripts/atlas.py scan`): walks the repo honoring `.gitignore` + an `atlas.exclude` config list; language detection by extension; symbol/import extraction per language (see §2); content-hash every file; incremental by default (only re-parse files whose hash changed), `--full` to rebuild.
- **Query** (`scripts/atlas.py query|where|impact|find`): read-only, milliseconds, no LLM.
- **Cards** (`scripts/atlas.py cards --generate`): per-file/module summaries via one headless `claude-sonnet-4-6` call per changed file, hash-pinned (R1). Never auto-runs inside `scan`.
- **Pack** (`scripts/atlas.py pack --task TASK-NNN --budget N`): the product — a token-budgeted context pack for a dispatch prompt (§4).
- **Episodic indexer** (`scripts/atlas.py episodes`): parses dossiers/, REVIEW.md, INSTINCTS.md, RETRO-*.md into the `episodes` table with FTS. Pure parsing, zero LLM (R4). Reuses `validate_plan.parse_tasks` and the existing REVIEW.md row grammar — never write a second parser for a format that already has one.

## 2. Language coverage (increment 1 scope — explicit, so nobody gold-plates)

Tier A (symbols + imports): **Python** (`ast` stdlib), **JS/TS** (regex-based import/export/function extraction — no Node dependency in the Python scanner; document the known limits), **Dart** (regex: `import`, `class`, top-level functions).
Tier B (files + FTS only, no symbol extraction): everything else, including **MQL5** (`.mq5/.mqh` — record `#include` edges via regex; MQL5 symbol grammar is a stated non-goal for v1), Markdown, JSON/YAML, PowerShell/Bash.
A file in Tier B is still fully searchable and fully hash-tracked — Tier A only adds the symbol/edge tables. Adding a language later = one extractor function + tests; the schema does not change.

## 3. CLI contract (exact — these strings are the API other scripts consume)

```
atlas.py scan [--full] [--repo PATH]           # exit 0; prints files scanned/changed/removed
atlas.py query "<fts terms>" [--limit N]       # ranked file/symbol/episode hits, file:line
atlas.py where <symbol>                        # definition site(s) + direct callers/importers
atlas.py impact <path> [--hops N=1]            # reverse-dependency closure of a file
atlas.py cards --generate [--only <glob>] [--model M=claude-sonnet-4-6]
atlas.py cards --stale                         # list cards whose source_hash no longer matches
atlas.py episodes [--reindex]
atlas.py pack --task TASK-NNN --budget N=3000 [--format prompt|json]
atlas.py status                                # db size, freshness %, last scan, card coverage
```
Output is plain UTF-8 text with forward-slash paths only (the preflight_paths TASK-024 lesson is now a standing rule). Exit 0 on success including empty results; exit 1 on real errors; **never** exit 2 (that exit code means "veto" elsewhere in this pack — don't overload it).

## 4. The context pack (Layer 3 — where the tokens are saved)

`pack --task TASK-NNN` composes, in order, within `--budget`:
1. **Territory core:** the task's `Owned_Paths` resolved against the live tree (reusing `preflight_paths`' classification), with per-file: symbol outline + card (if `FRESH`) or first-N-lines head (if no card/stale — R4 degradation).
2. **One-hop neighborhood:** `impact` closure of the territory — files that import/are imported by it — as *pointers + cards only*, never bodies. This is what stops "just an import" territory violations before they happen: the builder can see what depends on its files without reading them.
3. **Episodic hits:** top-K `episodes` matches on the task Title/Description terms — prior reworks, instincts, review findings touching this territory ("TASK-102 rework: same module, missing emulator evidence").
4. **Freshness footer:** scan timestamp, stale-card count in this pack, and the R1 sentence verbatim: *"This pack is a map, not the ground: read live any file you edit."*
Hard cap enforcement: sections truncate lowest-priority-first (3 → 2 → card bodies in 1); the pack **always** states what was truncated.

## 5. Integration points (each an amendment to an existing file)

- **`dispatch.sh` / `.ps1`:** after instinct injection, if `.devteam/atlas.db` exists and `autopilot.json → atlas.enabled` is true, run `atlas.py pack --task $TASK_ID` and append as `## PROJECT MAP (ATLAS) — a map, not the ground` section. Fail-open: any atlas error → dispatch proceeds without the section, one warning line. Same posture as instincts injection.
- **`maintenance.py`:** nightly audit gains step: `atlas.py scan` + `episodes --reindex` + (if `atlas.cards_auto_refresh`) `cards --generate` for changed files, capped at `atlas.max_cards_per_night` (default 30) to bound cost. Failure = logged line in the audit, never a TASK-MAINT by itself unless the db is corrupt (then: delete + full rescan is the remedy the task prescribes).
- **`/devteam-decompose`:** one added instruction: when carving `Owned_Paths`, consult `atlas.py impact` on candidate territories and record surprising couplings in the task's Description. (Prose instruction to fable — no code change.)
- **`board_publisher.py`:** optional `"atlas": {"files":…, "card_coverage_pct":…, "stale_cards":…}` key. Cosmetic; not exit criteria.
- **`autopilot.json`:** `"atlas": {"enabled": false, "budget_tokens": 3000, "cards_auto_refresh": false, "max_cards_per_night": 30, "exclude": []}` — ships **disabled**; onboarding asks, same "ask, don't auto-flip" pattern as control.mode and the roster.
- **`sync-manifest.json`:** `scripts/atlas.py`, its tests, `docs/ATLAS.md` → `framework_owned`; `.devteam/atlas.db` explicitly noted under project_owned's `.devteam/` umbrella. Same-commit rule applies.
- **Briefings (all three):** one short section: what the ATLAS section in the prompt is, the R1 rule verbatim, and `atlas.py query/where/impact` as tools the builder may run mid-session (they're plain CLIs — GB and CX can shell out to them, R3 delivered).

## 6. Increments (decompose these into PLAN.md tasks — territories are disjoint by design)

| # | Deliverable | Territory | Depends on |
|---|---|---|---|
| **A1** | Scanner + schema + `query/where/impact/status` + FTS5. Pure Python stdlib (`sqlite3`, `ast`, `re`). ≥25 tests incl. incremental-rescan correctness, gitignore honoring, hash stability, forward-slash output. `.gitignore` + manifest entries land here (R2). | `scripts/atlas.py`, `tests/test_atlas_core.py`, `.gitignore`, `sync-manifest.json` | — |
| **A2** | Episodic indexer + `episodes` in query results. Parses dossiers/REVIEW.md/INSTINCTS.md via existing grammars. | `scripts/atlas.py` (episodes fns), `tests/test_atlas_episodes.py` | A1 |
| **A3** | Cards: generation (headless sonnet-4-6, one file per call, structured output, atomic write), hash-pinning, `--stale`, FRESH/STALE marking in every query path. Tests use a **fake model transcript**, not live calls; live verification is an exit-criteria step on Alister's machine. | `scripts/atlas.py` (cards fns), `tests/test_atlas_cards.py`, `docs/ATLAS.md` | A1 |
| **A4** | `pack` composer + budget enforcement + truncation reporting. | `scripts/atlas.py` (pack fns), `tests/test_atlas_pack.py` | A1–A3 (degrades to A1-only content when A3 absent — test both) |
| **A5** | Dispatch + maintenance + autopilot.json + briefings + decompose-prose integration; onboarding ask-step; board key. | `scripts/dispatch.sh/.ps1`, `scripts/maintenance.py`, `autopilot.json`, `briefings/*`, `onboard.md`, `.claude/commands/devteam-decompose.md`, `docs/ATLAS.md` | A1, A4 |

A2 and A3 are parallelizable after A1 (disjoint function groups + disjoint test files; if decompose prefers hard file-level disjointness, split atlas.py into `atlas_core.py`/`atlas_cards.py`/`atlas_episodes.py` with `atlas.py` as the CLI façade — builder's choice, stated in the task).

## 7. Exit criteria

**A1:** on the DEVDEPARTMENT repo itself: full scan < 10s; `where decide` finds supervisor.py's function with callers; `impact scripts/builder_registry.py` lists its actual Python importers (`scripts/budget.py`, `scripts/validate_plan.py`; shell consumers are Tier B and carry no import edges — §7 changelog v1.1); rescan after touching one file re-parses exactly one file. **A3:** a card regenerates only when its source hash changes; a doctored hash flips every query path to STALE-with-warning. **A4:** `pack --task` on a synthetic PLAN.md task stays under budget, contains territory outline + neighborhood pointers + the R1 footer; with cards absent, degrades and says so. **A5:** a dry-run dispatch on a real task shows the ATLAS section; `atlas.enabled=false` (default) produces byte-identical dispatch prompts to today; nightly audit runs scan without error. **All:** suites green; no diffs outside the increments' territories; manifest self-check passes.

## 8. Risks & open questions for Alister

- **Q1 — card cost ceiling:** `max_cards_per_night=30` at sonnet-4-6 ≈ trivial spend, but first full generation on a big project (ORB: hundreds of files) is one deliberate `cards --generate` run. Acceptable, or want a `--max N` interactive cap too? (Spec default: add `--max`, cheap.)
- **Q2 — embeddings:** deliberately out of v1 (FTS5 + graph covers the shaped queries). FAISS/Chroma slot in later as an A6 without schema change. Agree to defer?
- **Q3 — MQL5 Tier A:** worth a real symbol extractor for `.mq5` given CRT Systems EA? Non-goal here; would be its own small increment with your MQL5 grammar knowledge as the spec source.

## 9. Increment A6 — status staleness in judgeable units; opt-in cards made visible (v1.2)

**Origin:** oikonomos field report, 2026-08-15. Two discoverability failures: (a) `status` reported a bare `last scan` timestamp the reader had to date-arithmetic against reality — an index 8 merged tasks and 30 files stale looked healthy; (b) `status` reported `cards: 0` as if normal, with no signal that generation is opt-in and had simply never been run.

**A6-1 — scan records its position.** `scan` writes two `meta` keys at the end of every successful run: `last_scan_head` (the repo's HEAD commit hash at scan time; empty string when git is unavailable) and the existing scan timestamp. No schema migration — `meta` is already key/value.

**A6-2 — status reports deltas, not timestamps alone.** `status` output gains, after the existing lines:
- `tracked files: <N> (git) vs <M> indexed — <D> not indexed` where N is `git ls-files` count filtered by the same ignore rules the scanner applies, M the `files` table count. D=0 renders `— in sync`.
- `commits since last scan: <K>` via `git rev-list --count <last_scan_head>..HEAD`; renders `n/a` when git or `last_scan_head` is unavailable (R4 degradation — status must never fail because git is absent).
- When the `cards` table is empty: `cards: 0 (generation is opt-in and has never run — python scripts/atlas.py cards --generate)`. When cards exist, the existing count/stale lines are unchanged.
All existing §3 contract behavior holds: plain UTF-8, forward slashes, exit 0 on success, 1 on real errors, never 2.

**A6-3 — onboarding asks about cards.** The onboard.md ATLAS ask-step gains a second question, asked only when ATLAS is enabled: "Generate summary cards now (one sonnet-4-6 call per file — a deliberate spend, ~N files detected), and/or enable nightly `cards_auto_refresh` (capped at `max_cards_per_night`)?" Default on silence: neither — status's A6-2 hint remains the reminder. Same ask-don't-auto-flip pattern as every other onboarding decision.

**A6 exit criteria:** on this repo: `status` shows git-vs-indexed delta and commits-since-scan that change correctly after a commit without a scan and return to in-sync after a scan; with an empty cards table the opt-in hint renders verbatim; with git renamed away (PATH manipulation in tests), both new lines degrade to `n/a`/graceful text at exit 0. Tests cover: meta head recording, delta computation, the no-git degradation, and the cards hint. Suites green.

## Session handover protocol

Same as prior specs: the build session reads this file in full, runs `/devteam-decompose` against it (fable-5, medium+), validates the plan, and builds increment-by-increment to exit criteria. ATLAS ships disabled-by-default; enabling it on DEVDEPARTMENT itself after A5 — and then syncing to orb-jun-26/rwc-admin-portal — is the real-world acceptance run.
