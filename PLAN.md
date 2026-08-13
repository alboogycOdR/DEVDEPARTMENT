---
plan_version: 1.5
last_updated: 2026-08-13T16:07:16Z
overall_status: in_progress
orchestrator_notes: "Plan v1.5 — TASK-002 (ATLAS A1) APPROVED & merged to master (--no-ff) after opus-4-8 /devteam-review. First-pass approval. Independent verification: territory clean (5/5 files in Owned_Paths, no PLAN.md edits); 618 Python + 30 core + 36 Node all green (re-run by ORCH, matches CX Test_Evidence); §7 A1 exit criteria all pass on this repo — full scan 1.41s (<10s), `where decide`→supervisor.py:224+callers, `impact builder_registry.py` lists budget.py AND validate_plan.py, one-file touch re-parses exactly 1, exit 0 empty / 1 error / never 2. Non-blocking findings (see REVIEW.md): (1) query uses LIKE substring vs FTS5 MATCH/bm25 ranking — correct+fast at current scale, revisit for large-repo latency; (2) no explicit ls/find filesystem-check evidence in commit history (first-occurrence note, all files new). Branch task/TASK-002-cx deleted. UNLOCKED by TASK-002 done: TASK-003 (GB, A2) and TASK-004 (S5, A3) are now dispatchable in parallel (disjoint territories: atlas_episodes.py vs atlas_cards.py+docs/ATLAS.md). NOTE S5 waves still need PROTECTED_EXCEPTIONS grants in hooks/lib.js before TASK-004 dispatch. TASK-005 (CX, A4) still waits on 002+003+004; TASK-006 (S5, A5) on 002+005. Next ORCH action: dispatch Wave 2 (003+004)."
---

# Project Plan

Coordination blackboard for ORCH (Claude Code), GB (Grok Build), CX (Codex AI), S5 (Sonnet 5 headless).
Rules: `AGENTS.md` (summary) and `docs/COORDINATION_PROTOCOL.md` (authoritative).
Status lifecycle: `pending → claimed → in_progress → needs_review → done`, `blocked` from claimed/in_progress. Builders never set `done`.

## Work Items

### TASK-002
**Title:** ATLAS A1 — scanner, schema, core query CLI (façade + extension hooks)
**Status:** done
**Assigned_To:** CX
**Priority:** critical
**Spec_References:** specs/DEVDEPARTMENT_ATLAS_SPEC.md §0–§3, §6 (A1), §7 (A1 exit criteria)
**Owned_Paths:** scripts/atlas.py, scripts/atlas_core.py, tests/test_atlas_core.py, .gitignore, sync-manifest.json
**Depends_On:** —
**Description:** Build ATLAS Layer 0: `scripts/atlas.py` CLI façade + `scripts/atlas_core.py` implementation. Pure Python stdlib (`sqlite3`, `ast`, `re`). Creates `.devteam/atlas.db` with the FULL §1 schema (files, symbols, edges, cards, episodes, meta — all six tables, FTS5), even though cards/episodes stay empty until TASK-003/004. ORCH-mandated architecture for territorial isolation of later increments: (1) `atlas.py` is a thin argparse façade that registers `scan/query/where/impact/status` from `atlas_core` and then attempts optional imports of `atlas_episodes`, `atlas_cards`, `atlas_pack`, each exposing `register(subparsers)`; a missing module means its subcommand is absent with a graceful one-line message — later tasks never edit A1's files. (2) All READ paths ship complete in core: `query` searches files/symbols AND the episodes FTS table (empty → zero hits, no error), and every file hit is annotated FRESH/STALE by comparing `cards.source_hash` to `files.content_hash` (no card → no annotation, degrade silently per R4). Tier A symbol/import extraction: Python (`ast`), JS/TS (regex, documented limits), Dart (regex). Tier B (files + FTS + hash only) for everything else, including MQL5 `#include` edges via regex. Scanner honors `.gitignore` + an `atlas.exclude` config list, incremental by hash, `--full` rebuilds. Output: plain UTF-8, forward-slash paths only; exit 0 success (including empty results), 1 real error, never 2. R2 lands here: `.gitignore` entry for `.devteam/atlas.db` (+ any cache beside it) in the first commit; `sync-manifest.json` gains `scripts/atlas.py`, `scripts/atlas_core.py`, `scripts/atlas_episodes.py`, `scripts/atlas_cards.py`, `scripts/atlas_pack.py`, `tests/test_atlas_*.py`, `docs/ATLAS.md` → framework_owned, and a note that `.devteam/atlas.db` sits under project_owned's `.devteam/` umbrella.
**Acceptance_Criteria:**
- [x] `atlas.py scan [--full] [--repo PATH]` builds `.devteam/atlas.db` (SQLite, FTS5) with all six §1 tables; prints files scanned/changed/removed; incremental by default (only re-parses files whose content hash changed)
- [x] Scanner honors `.gitignore` plus an `atlas.exclude` config list; language detection by extension (§1 Scanner)
- [x] Tier A extraction for Python (`ast` stdlib), JS/TS (regex-based import/export/function, no Node dependency, limits documented), Dart (regex: import, class, top-level functions); Tier B files remain fully searchable and hash-tracked; MQL5 `#include` edges recorded via regex (§2)
- [x] `query`/`where`/`impact`/`status` match the §3 CLI contract strings exactly; read-only, no LLM (§1, R4)
- [x] `query` returns ranked file/symbol/episode hits as `file:line`; file hits carry FRESH/STALE derived from `cards.source_hash` vs `files.content_hash`; empty cards/episodes tables degrade gracefully (R1, R4)
- [x] Façade registers later subcommands via optional import + `register(subparsers)` hook; absent module → subcommand absent with a graceful message (enables A2/A3/A4 disjoint territories, §6 note)
- [x] All output forward-slash paths, UTF-8; exit 0 on success including empty results, 1 on real errors, never 2 (§3)
- [x] `.gitignore` and `sync-manifest.json` entries land in this task (R2, §5)
- [x] ≥25 tests in tests/test_atlas_core.py incl. incremental-rescan correctness, gitignore honoring, hash stability, forward-slash output (§6 A1); full Python + Node suites stay green
- [x] §7 A1 exit criteria on this repo (as amended by spec changelog v1.1): full scan < 10s; `where decide` finds supervisor.py's function with callers; `impact scripts/builder_registry.py` lists its actual Python importers (scripts/budget.py, scripts/validate_plan.py — shell consumers are Tier B, no import edges); touching one file re-parses exactly one file
**Branch:** task/TASK-002-cx
**Started_At:** 2026-08-13T15:31:35Z
**Progress_Notes:**
- [2026-08-13T15:32:30Z] [CX] Claimed TASK-002 and created task/TASK-002-cx. Beginning A1 implementation: schema, incremental scanner, core read-only queries, and façade.
- [2026-08-13T15:39:30Z] [CX] Layer 0 implementation committed as 6a5c9e6. Focused suite: 29 passed in 1.00s; real-repo full scan, `where decide`, and `impact scripts/builder_registry.py` verified. Full Python suite was started but exceeded the 125s command window without a result; next: obtain full-suite result, then record acceptance evidence and submit for review.
- [2026-08-13T17:03:00Z] [CX] Full Python suite completed: 617 passed in 92.43s. A1 is blocked on its real-repo impact exit criterion: `scripts/supervisor.py` contains no `builder_registry` reference, so `impact scripts/builder_registry.py` correctly lists dispatch/validate but not supervisor. Adding that dependency requires an out-of-territory edit to `scripts/supervisor.py`; awaiting ORCH's specification/acceptance decision.
- [2026-08-13T15:55:00Z] [ORCH] UNBLOCKED — correct block, the spec was wrong about the tree. Spec amended (changelog v1.1): the impact criterion binds only on true Python importers; supervisor is not one and is NOT required; do not touch supervisor.py. Note your own evidence shows `impact scripts/builder_registry.py` omits `scripts/budget.py`, which genuinely imports it (function-scoped `import builder_registry as _br`, budget.py:95) — that is a detection gap inside your territory (atlas_core edge extraction must see function-scoped imports); fix it, then re-verify the amended criterion (budget.py + validate_plan.py both listed). Also: 'no Node test suite' is wrong — it runs via `node hooks/run-tests.js` (no package.json needed); run it for the full-suites-green criterion. Timestamps must be UTC (your 17:03:00Z entries appear to be local time). Then self-check criteria and go to needs_review.
- [2026-08-13T15:53:16Z] [CX] Fresh full scan confirms the AST scanner indexes `budget.py`'s function-scoped import; added regression coverage and committed c8f4c4c. Python suite is green; awaiting resolution of the unrelated Node hook-test failure before review submission.
- [2026-08-13T16:00:00Z] [ORCH] UNBLOCKED — correct block again. Root cause was ORCH machinery, not your branch: run-tests.js inherited the DEVTEAM_UNIT=CX your dispatch exports, so the unset-var test could only fail inside builder sessions. Fixed on master (88d7965, harness scrubs the var); verified 36/36 with and without DEVTEAM_UNIT exported. Rerun `node hooks/run-tests.js` from your worktree (the fix lives on master in the main checkout — run it there: `node C:/CLAUDECODE_kingdom.work/DEVDEPARTMENT/hooks/run-tests.js`), record evidence, self-check all criteria, and submit needs_review. Nothing else outstanding.
- [2026-08-13T15:59:12Z] [CX] Re-ran the shared-main Node hook suite (36 passed, 0 failed), fresh core tests, and amended real-repo exit checks. All acceptance criteria self-checked; submitting for ORCH review.
**Artifacts:**
- [2026-08-13T15:39:30Z] [CX] scripts/atlas.py, scripts/atlas_core.py, tests/test_atlas_core.py, .gitignore, sync-manifest.json (commit 6a5c9e6)
**Test_Evidence:**
- [2026-08-13T15:39:30Z] [CX] `python -m pytest tests/test_atlas_core.py -q` → 29 passed in 1.00s. `python scripts/atlas.py scan --full --repo .` → 103 files scanned; smoke checks for `where decide`, `impact scripts/builder_registry.py`, and `status` passed. `python -m pytest -q` exceeded the 125s command window with no completed result; pending rerun/confirmation.
- [2026-08-13T17:03:00Z] [CX] `python -m pytest -q` → 617 passed in 92.43s. `python scripts/atlas.py scan --full --repo .` → 103 scanned, then incremental scan → 103 scanned / 0 changed. `where decide` returned `scripts/supervisor.py:224` plus callers. `impact scripts/builder_registry.py` returned `PLAN.md`, README/docs/dossier/spec references, `scripts/dispatch.ps1`, `scripts/dispatch.sh`, and `scripts/validate_plan.py`; it cannot return supervisor because no source edge exists. No package.json or Node test suite is present in this repository.
- [2026-08-13T15:53:16Z] [CX] `python -m pytest tests/test_atlas_core.py -q` → 30 passed in 1.12s. `python scripts/atlas.py scan --full --repo .` → 103 scanned; `impact scripts/builder_registry.py` lists `scripts/budget.py` and `scripts/validate_plan.py`. `python -m pytest -q` → 618 passed in 101.46s. `node hooks/run-tests.js` → 35 passed, 1 failed: `unset DEVTEAM_UNIT still means ORCH (interactive sessions unaffected)` expected 0, got 2.
- [2026-08-13T15:59:12Z] [CX] `node C:/CLAUDECODE_kingdom.work/DEVDEPARTMENT/hooks/run-tests.js` → 36 passed, 0 failed. `python -m pytest tests/test_atlas_core.py -q` → 30 passed in 1.75s. Fresh scan: full 103 scanned / 103 changed, incremental 103 scanned / 0 changed; `where decide` returned `scripts/supervisor.py:224` and callers; `impact scripts/builder_registry.py` lists both `scripts/budget.py` and `scripts/validate_plan.py`. A combined fresh full-suite rerun exceeded the 125s command window before buffered output; prior completed evidence remains `python -m pytest -q` → 618 passed in 101.46s.
**Review_Findings:**
- [2026-08-13T16:07:16Z] [ORCH] APPROVED (first-pass), merged to master. Non-blocking notes for future increments (no rework required): (1) `query`/`query`-episodes search uses SQL `LIKE '%term%'` against the FTS5 virtual tables rather than FTS5 `MATCH`/bm25 — functionally correct, forward-slash + file:line + FRESH/STALE all verified, and instant at 103 files, but it bypasses the FTS index and gives alphabetical rather than relevance ranking; revisit if query latency matters on large repos (A2 populates episodes, so this compounds there). (2) No explicit `ls`/`find` filesystem-check evidence in commit history per c8b9872 — first-occurrence note only; all five Owned_Paths were net-new files so there was nothing to clobber.
**Blocked_Reason:** —
**Updated_By:** ORCH
**Updated_At:** 2026-08-13T16:07:16Z

### TASK-003
**Title:** ATLAS A2 — episodic indexer (dossiers/REVIEW/INSTINCTS/RETRO → FTS)
**Status:** pending
**Assigned_To:** GB
**Priority:** high
**Spec_References:** specs/DEVDEPARTMENT_ATLAS_SPEC.md §1 (Episodic indexer), §3, §6 (A2), R4
**Owned_Paths:** scripts/atlas_episodes.py, tests/test_atlas_episodes.py
**Depends_On:** TASK-002
**Description:** Build `scripts/atlas_episodes.py`: parses `dossiers/`, `REVIEW.md`, `INSTINCTS.md`, `RETRO-*.md` into the `episodes` table (kind, ref, ts, unit, indexed_hash, body_fts) created by TASK-002's schema. Pure parsing, zero LLM (R4). Reuses `validate_plan.parse_tasks` and the existing REVIEW.md row grammar — never write a second parser for a format that already has one (§1). Exposes `register(subparsers)` consumed by the TASK-002 façade to provide `atlas.py episodes [--reindex]` (§3). Do NOT edit `scripts/atlas.py`/`atlas_core.py` — core `query` already reads the episodes table; your job is only to populate it. Incremental via `indexed_hash`; `--reindex` rebuilds.
**Acceptance_Criteria:**
- [ ] `atlas.py episodes [--reindex]` works via the façade's registration hook, contract string per §3, without any edit to TASK-002's files
- [ ] Parses dossiers/, REVIEW.md, INSTINCTS.md, RETRO-*.md into the episodes table with FTS (§1)
- [ ] Reuses `validate_plan.parse_tasks` and the existing REVIEW.md row grammar — no duplicate parser (§1)
- [ ] Zero model calls anywhere in the module (R4)
- [ ] Episode hits appear in `atlas.py query` results after indexing (§6 A2)
- [ ] tests/test_atlas_episodes.py green; full suites green; forward-slash output; exit codes per §3
**Branch:** —
**Started_At:** —
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** ORCH
**Updated_At:** 2026-08-13T14:31:00Z

### TASK-004
**Title:** ATLAS A3 — cards: hash-pinned LLM summaries, staleness, docs
**Status:** pending
**Assigned_To:** S5
**Priority:** high
**Spec_References:** specs/DEVDEPARTMENT_ATLAS_SPEC.md §1 (Cards), §3, §6 (A3), §7 (A3 exit criteria), §8 Q1, R1, R4
**Owned_Paths:** scripts/atlas_cards.py, tests/test_atlas_cards.py, docs/ATLAS.md
**Depends_On:** TASK-002
**Description:** Build `scripts/atlas_cards.py`: Layer 1 card generation. One headless `claude-sonnet-4-6` call per changed file, structured output (purpose, invariants, gotchas, entry_points, tokens_estimate), atomic write into the `cards` table created by TASK-002, pinned to the file's `source_hash` (R1). Never auto-runs inside `scan` (§1). CLI via `register(subparsers)`: `cards --generate [--only <glob>] [--model M=claude-sonnet-4-6]` plus `--max N` interactive cap (spec §8 Q1 default: add `--max`, cheap) and `cards --stale` (§3). Do NOT edit TASK-002's files — core query already derives FRESH/STALE from `cards.source_hash`; your job is generation, pinning, and `--stale` listing. Tests use a fake model transcript (a stubbed subprocess/transcript fixture), never live calls; live verification is an exit-criteria step on Alister's machine (§6 A3). Also author `docs/ATLAS.md`: architecture, R1–R4 verbatim, CLI contract, card lifecycle, degradation behavior.
**Acceptance_Criteria:**
- [ ] `cards --generate` issues one headless claude-sonnet-4-6 call per changed file, structured output, atomic DB write, `source_hash`-pinned (§1, R1)
- [ ] Generation never triggered by `scan` (§1); `--only <glob>`, `--model` override, and `--max N` cap supported (§3, §8 Q1)
- [ ] `cards --stale` lists cards whose source_hash no longer matches (§3)
- [ ] §7 A3 exit criteria: a card regenerates only when its source hash changes; a doctored hash flips every query path to STALE-with-warning (verified end-to-end through core query)
- [ ] Tests use a fake model transcript — zero live model calls in the suite (§6 A3); suites green
- [ ] `docs/ATLAS.md` exists: architecture, R1–R4, CLI contract, card lifecycle, R4 degradation
- [ ] No edits outside Owned_Paths (in particular none to atlas.py/atlas_core.py)
**Branch:** —
**Started_At:** —
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** ORCH
**Updated_At:** 2026-08-13T14:31:00Z

### TASK-005
**Title:** ATLAS A4 — context-pack composer with budget + truncation reporting
**Status:** pending
**Assigned_To:** CX
**Priority:** high
**Spec_References:** specs/DEVDEPARTMENT_ATLAS_SPEC.md §3, §4, §6 (A4), §7 (A4 exit criteria), R1, R4
**Owned_Paths:** scripts/atlas_pack.py, tests/test_atlas_pack.py
**Depends_On:** TASK-002, TASK-003, TASK-004
**Description:** Build `scripts/atlas_pack.py` (registered via the façade hook): `pack --task TASK-NNN --budget N=3000 [--format prompt|json]`. Composes, in order, within budget: (1) territory core — the task's Owned_Paths resolved against the live tree reusing `preflight_paths`' classification, per-file symbol outline + card if FRESH else first-N-lines head; (2) one-hop neighborhood — `impact` closure as pointers + cards only, never bodies; (3) episodic hits — top-K episodes matches on the task's Title/Description terms; (4) freshness footer — scan timestamp, stale-card count, and the R1 sentence verbatim: "This pack is a map, not the ground: read live any file you edit." Hard cap: truncate lowest-priority-first (3 → 2 → card bodies in 1); the pack always states what was truncated. Reads PLAN.md via `validate_plan.parse_tasks` — no second parser. Must degrade to A1-only content when cards are absent and say so (§6 A4) — test both paths. No edits to any other atlas module.
**Acceptance_Criteria:**
- [ ] `pack --task TASK-NNN --budget N=3000 [--format prompt|json]` per §3 contract, via registration hook, no edits to other atlas files
- [ ] Sections compose in §4 order: territory core (outline + FRESH card or first-N-lines head), one-hop neighborhood as pointers+cards never bodies, episodic top-K on Title/Description terms, freshness footer with the R1 sentence verbatim
- [ ] Budget is a hard cap; truncation is lowest-priority-first (3 → 2 → card bodies in 1) and the pack always states what was truncated (§4)
- [ ] With cards absent, degrades to A1-only content and says so — both degraded and full paths tested (§6 A4)
- [ ] §7 A4 exit criteria: on a synthetic PLAN.md task the pack stays under budget and contains territory outline + neighborhood pointers + R1 footer
- [ ] tests/test_atlas_pack.py green; full suites green
**Branch:** —
**Started_At:** —
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** ORCH
**Updated_At:** 2026-08-13T14:31:00Z

### TASK-006
**Title:** ATLAS A5 — dispatch/maintenance/autopilot/briefings/onboarding integration
**Status:** pending
**Assigned_To:** S5
**Priority:** medium
**Spec_References:** specs/DEVDEPARTMENT_ATLAS_SPEC.md §5, §6 (A5), §7 (A5 exit criteria), R2, R3
**Owned_Paths:** scripts/dispatch.sh, scripts/dispatch.ps1, scripts/maintenance.py, scripts/board_publisher.py, autopilot.json, briefings/**, onboard.md, .claude/commands/devteam-decompose.md, docs/ATLAS.md
**Depends_On:** TASK-002, TASK-005
**Description:** Wire ATLAS into the pack per §5, every integration fail-open. dispatch.sh/.ps1: after instinct injection, if `.devteam/atlas.db` exists and `autopilot.json → atlas.enabled` is true, run `atlas.py pack --task $TASK_ID --budget atlas.budget_tokens` and append as a `## PROJECT MAP (ATLAS) — a map, not the ground` section; any atlas error → dispatch proceeds without the section plus one warning line (same posture as instincts injection). maintenance.py nightly audit: `atlas.py scan` + `episodes --reindex` + (if `atlas.cards_auto_refresh`) `cards --generate` for changed files capped at `atlas.max_cards_per_night` (default 30); failure = logged audit line, never a TASK-MAINT unless the db is corrupt (remedy: delete + full rescan). autopilot.json gains `"atlas": {"enabled": false, "budget_tokens": 3000, "cards_auto_refresh": false, "max_cards_per_night": 30, "exclude": []}` — ships disabled. onboard.md: ask-step (ask, don't auto-flip, same pattern as control.mode/roster) + the R2 `.gitignore` block. All three briefings: one short section — what the ATLAS prompt section is, R1 verbatim, and `atlas.py query/where/impact` as plain-CLI mid-session tools (R3). .claude/commands/devteam-decompose.md: one added prose instruction — consult `atlas.py impact` on candidate territories when carving Owned_Paths and record surprising couplings in Descriptions. board_publisher.py: optional `"atlas"` key (files, card_coverage_pct, stale_cards) — cosmetic, not exit criteria. Amend docs/ATLAS.md with an Integration section.
**Acceptance_Criteria:**
- [ ] dispatch.sh AND dispatch.ps1 append the ATLAS pack section exactly when db exists and atlas.enabled; fail-open with one warning line on any atlas error (§5)
- [ ] maintenance.py nightly runs scan + episodes --reindex + optional capped card refresh (default 30); failures log only; corrupt-db remedy is delete + full rescan (§5)
- [ ] autopilot.json atlas block present with §5 defaults, enabled=false (§5)
- [ ] onboard.md ask-step added (ask, don't auto-flip) including the R2 .gitignore block (§5, R2)
- [ ] All three briefings gain the ATLAS section: prompt-section meaning, R1 verbatim, query/where/impact as builder-runnable CLIs (§5, R3)
- [ ] .claude/commands/devteam-decompose.md gains the impact-consultation prose instruction (§5)
- [ ] §7 A5 exit criteria: dry-run dispatch on a real task shows the ATLAS section; with atlas.enabled=false (default) dispatch prompts are byte-identical to today; nightly audit runs scan without error
- [ ] docs/ATLAS.md Integration section updated; suites green
**Branch:** —
**Started_At:** —
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** ORCH
**Updated_At:** 2026-08-13T14:31:00Z
