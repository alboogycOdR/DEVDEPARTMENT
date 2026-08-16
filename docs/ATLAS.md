# ATLAS — Persistent Project Map & Memory

ATLAS turns a repository's structure and history into a queryable,
freshness-verified, cross-CLI artifact, so a builder session no longer has
to rebuild its orientation from raw file reads every time. It is read-only
infrastructure: nothing in ATLAS makes decisions or edits code on a
builder's behalf.

Spec of record: `specs/DEVDEPARTMENT_ATLAS_SPEC.md`. This document is the
living reference for what actually shipped; if the two disagree, the spec
explains intent and this file explains the built system.

## Design rules (R1–R4)

These are verbatim from the spec (§0) — every increment's review checks
them explicitly, and nothing in ATLAS may violate them.

> **R1 — Cards are claims; code is truth.** ATLAS output tells a builder
> *where to look*, never substitutes for looking. Any file a builder
> intends to **edit** must still be read live from disk in that session.
> Every generated summary carries the source hash it was built from, and
> every query response marks entries `FRESH` or `STALE (source changed
> since card generated)`. A stale card is served *with its warning*, never
> silently. This is the same epistemics as `Test_Evidence`: unverifiable
> claims are treated as claims.

> **R2 — Derived artifact, never merged.** `.devteam/atlas.db` (and any
> cache beside it) is machine-local, rebuildable from scratch,
> `project_owned` in `sync-manifest.json`, and in `.gitignore` from the
> first commit of increment 1.

> **R3 — Cross-CLI by construction.** GB (Grok) and CX (Codex) cannot see
> an MCP server. Every ATLAS capability must be consumable as (a) a CLI
> invocation and (b) plain text injected into a dispatch prompt. No
> Claude-only integration path may be the only path.

> **R4 — Zero-LLM layer stands alone.** Layer 0 (cartography) and Layer 2
> (episodic indexing) must be fully useful with no model call ever made —
> deterministic, seconds to rebuild, no API dependency. LLM-generated
> content (Layer 1 cards) is an enhancement gated behind explicit
> invocation, priced at the mechanical tier, and the system degrades
> gracefully to Layer 0 when cards are absent or the model is unreachable.

## Architecture

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

Layers, and the module that owns each:

- **Layer 0 — Scanner + core queries** (`scripts/atlas_core.py`, façade in
  `scripts/atlas.py`): builds `files`/`symbols`/`edges`, and answers
  `query`/`where`/`impact`/`status`. Pure stdlib, no model calls, seconds to
  rebuild (R4).
- **Layer 2 — Episodic indexer** (`scripts/atlas_episodes.py`): parses
  `dossiers/`, `REVIEW.md`, `INSTINCTS.md`, `RETRO-*.md` into the `episodes`
  table, reusing the repo's existing parsers rather than duplicating their
  grammars. Pure parsing, zero LLM (R4).
- **Layer 1 — Cards** (`scripts/atlas_cards.py`, this task): optional,
  explicitly-invoked, hash-pinned LLM summaries — described in full below.
- **Layer 3 — Context pack** (`scripts/atlas_pack.py`, a later increment):
  composes a token-budgeted context pack for a dispatch prompt from Layers
  0–2.

The façade (`scripts/atlas.py`) registers Layer 0's commands directly and
then attempts an optional import of each extension module
(`atlas_episodes`, `atlas_cards`, `atlas_pack`); a missing module means its
subcommand is simply absent, with a one-line hint, so increments land in
any order without editing each other's files.

## CLI contract

Exact strings — other scripts and dispatch prompts consume these verbatim
(spec §3):

```
atlas.py scan [--full] [--repo PATH]           # exit 0; prints files scanned/changed/removed
atlas.py query "<fts terms>" [--limit N]       # ranked file/symbol/episode hits, file:line
atlas.py where <symbol>                        # definition site(s) + direct callers/importers
atlas.py impact <path> [--hops N=1]            # reverse-dependency closure of a file
atlas.py cards --generate [--only <glob>] [--model M=claude-sonnet-4-6] [--max N]
atlas.py cards --stale                         # list cards whose source_hash no longer matches
atlas.py episodes [--reindex]
atlas.py pack --task TASK-NNN --budget N=3000 [--format prompt|json]
atlas.py status                                # db size, freshness %, last scan, card coverage
```

Output is plain UTF-8 text with forward-slash paths only. Exit 0 on success
including empty results; exit 1 on real errors; **never** exit 2 (that code
means "veto" elsewhere in this pack).

`query` ranks file and episode hits with FTS5 `MATCH` + `bm25` (not
substring `LIKE`). Multi-word and punctuation-bearing terms are quoted so
they cannot raise an FTS5 syntax error. Output shape, `FRESH`/`STALE`
annotation, and exit codes are unchanged.

## Cross-worktree index

ATLAS is built for multi-worktree dispatch: builders run
`atlas.py query` / `where` / `impact` from inside a linked worktree, but
`.devteam/` is gitignored, so a per-worktree database would be empty or
hours stale while only the main checkout is ever scanned.

`db_path()` therefore resolves the **main checkout** from anywhere via
`git rev-parse --git-common-dir` (that path's parent is the main root) and
opens `<main>/.devteam/atlas.db`. A query issued in a worktree reads the
same index `scan` wrote in the main tree.

When git is unavailable, the directory is not a git checkout, or
`--git-common-dir` cannot be resolved, the function fails open (R4) to
`repo/.devteam/atlas.db` and does not raise. Identity is explicit: one
shared index on the main checkout, or the local fallback — never an
implicit "whichever `.devteam` happens to sit next to cwd".

## Cards — Layer 1 (`scripts/atlas_cards.py`)

### What a card is

A card is a per-file, machine-generated summary with five fields:
`purpose`, `invariants`, `gotchas`, `entry_points`, `tokens_estimate`. It is
produced by exactly **one headless `claude-sonnet-4-6` call per changed
file** and stored pinned to that file's `content_hash` at generation time
(`cards.source_hash`). List-valued fields (`invariants`, `gotchas`,
`entry_points`) are stored as JSON-encoded arrays; consumers should
`json.loads()` them.

### Generation

`atlas.py cards --generate [--only <glob>] [--model M=claude-sonnet-4-6]
[--max N]`:

1. Selects every file whose current `content_hash` differs from its card's
   `source_hash` — including files with no card yet. `--only <glob>`
   restricts the candidate set (matched against the full relative path and
   the bare filename). `--max N` caps how many files are processed in one
   run (spec §8 Q1 — first full generation on a large project is one
   deliberate, bounded call).
2. For each candidate, reads the file, builds a structured prompt asking
   for a single JSON object with the five card fields, and shells out one
   headless model call (`claude -p --model <M> --dangerously-skip-permissions`
   by default; overridable for tests/config — see below).
3. Parses the response, requiring `purpose`, `invariants`, `gotchas`, and
   `entry_points` to be present; `tokens_estimate` falls back to a
   deterministic `len(text)//4` estimate if the model omits it or returns a
   non-positive value.
4. Writes the card as a **single atomic `INSERT ... ON CONFLICT ... DO
   UPDATE`, committed immediately** — so a killed run, or a failure on file
   N, never leaves a partial card and never blocks the files before or
   after it.

**Cards generation is never triggered by `atlas.py scan`.** Scanning is
free and automatic; card generation is a priced, explicit action every
time (R4).

### Failure handling (R1/R4 in practice)

If the model is unreachable, exits non-zero, or returns output that isn't a
JSON object with the required keys, that file's card write is skipped
entirely — the database is left exactly as it was for that file — a clear
one-line error is printed to stderr, and every other candidate file still
gets its normal chance to succeed. `cards --generate`'s process exit code
is `1` if any file failed and `0` if every attempted file succeeded
(including zero candidates, which is success).

### Testing without a live model

Tests never call a live model (spec §6 A3). The headless command is
resolved through `_cards_cmd()`, which checks, in order: an explicit
`cmd_override` argument to `generate_cards()`, then
`autopilot.json → atlas.cards_cmd` (a literal argv list — the same
override pattern `scripts/distiller.py` uses for `learning.distill_cmd`),
and only then falls back to the real `claude -p ...` invocation. Tests
substitute a small real (but fake) executable that echoes canned JSON on
stdout, so the full subprocess path is exercised without ever reaching a
network or a real model.

### Staleness

`atlas.py cards --stale` lists every file whose card's `source_hash` no
longer equals the file's current `content_hash`, one per line, with both
hash prefixes shown. This module never renders `FRESH`/`STALE` in `query`
results — that annotation is computed entirely by
`scripts/atlas_core.py::query`/`where`/`impact` by comparing
`cards.source_hash` to `files.content_hash`; `atlas_cards.py` only ever
*produces* the pin those functions read, and never edits Layer 0's files.

## Card lifecycle

```
file changes on disk
        │  (next atlas.py scan)
        ▼
files.content_hash updated  ──────────────►  no card yet → query shows plain hit
        │                                     stale card  → query shows STALE + warning
        │ atlas.py cards --generate (explicit, priced)
        ▼
cards.source_hash := files.content_hash (at generation time)
        │
        ▼
query/where/impact show FRESH ... until the file changes again
```

A card regenerates **only** when its pinned `source_hash` no longer matches
the file's `content_hash` — an unrelated file changing, or a file being
re-scanned with no content change, never triggers a re-generation call.

## R4 degradation, end to end

- No cards table populated at all → every `query`/`where`/`impact` hit is
  shown with no freshness annotation (Layer 0 alone is fully useful).
- A card exists and its hash matches → `FRESH`.
- A card exists and its hash no longer matches (file edited since, or the
  hash was doctored) → `STALE (source changed since card generated)`,
  **always shown**, never silently dropped.
- The model is unreachable when `cards --generate` runs → the run reports
  the failure per file, the database is untouched for those files, and
  every other ATLAS capability (scan, query, where, impact, episodes,
  status) keeps working exactly as if cards had never been attempted.

## Integration (A5)

ATLAS ships **disabled** (`autopilot.json` → `"atlas": {"enabled": false, ...}`);
every integration point below fails open and is a no-op until a human flips
it on during onboarding (`onboard.md`'s ask-step, same "ask, don't
auto-flip" pattern as `control.mode` and the builder roster).

### `dispatch.sh` / `dispatch.ps1`

After instinct injection, if `.devteam/atlas.db` exists **and**
`autopilot.json → atlas.enabled` is `true`, dispatch resolves the task it's
about to launch (already known in `control.mode=strict`; predicted with the
same resume-first priority rule `instincts.py` uses in legacy mode) and
runs:

```
python3 scripts/atlas.py pack --task <TASK-ID> --budget <atlas.budget_tokens>
```

On success the output is appended to the dispatch prompt as a
`## PROJECT MAP (ATLAS) — a map, not the ground` section. On **any** error
— pack crashes, times out, returns empty, or a task can't be confidently
resolved — dispatch proceeds without the section and prints one warning
line to stderr. This is the identical posture to the instincts injection
immediately above it in both scripts: fail-open, one line, never a hard
stop. With `atlas.enabled: false` (the shipped default) or no
`.devteam/atlas.db` yet, this block is skipped entirely and dispatch
prompts are byte-identical to a build with no ATLAS integration at all
(§7 A5 exit criterion).

### `maintenance.py` — nightly audit

A new `_step_atlas` runs after `_step_backup`, gated on `atlas.enabled`:

1. `atlas.py scan --repo .`
2. `atlas.py episodes --reindex --repo .`
3. if `atlas.cards_auto_refresh` is true: `atlas.py cards --generate --max <atlas.max_cards_per_night>` (default 30, bounding per-night LLM spend)

Unlike every other audit step, an ordinary ATLAS failure (model
unreachable, a transient parse error) is logged in the step's detail and
the audit still passes — ATLAS is a convenience layer, not something that
should page a human at 2am. The one exception is **database corruption**
(scan's output matches a known SQLite-corruption marker): that fails the
step, because the prescribed remedy — delete `.devteam/atlas.db` and
re-run `atlas.py scan --full --repo .` — is destructive enough that it
should go through a filed task rather than run unattended inside a nightly
job.

### `autopilot.json`

```json
"atlas": {
  "enabled": false,
  "budget_tokens": 3000,
  "cards_auto_refresh": false,
  "max_cards_per_night": 30,
  "exclude": []
}
```

Ships disabled. `budget_tokens` bounds `pack`'s output; `max_cards_per_night`
bounds nightly `cards --generate` spend when `cards_auto_refresh` is on.

### `onboard.md`

New ask-step, same "ask, don't auto-flip" pattern as `control.mode` and the
roster: ask whether to enable ATLAS for the project; if yes, flip
`atlas.enabled: true` and run an initial `atlas.py scan --full --repo .`.
The R2 `.gitignore` block (`.devteam/atlas.db`, `.devteam/atlas.db-*`) is
added regardless of the answer, so a later enable doesn't need a second
onboarding pass.

### Briefings (`briefings/*.md`, all three units)

Each gains a short "ATLAS — the project map (if present)" section:
what the `## PROJECT MAP (ATLAS)` prompt section is, R1 verbatim ("This
pack is a map, not the ground: read live any file you edit."), and
`atlas.py query/where/impact` documented as plain CLIs any builder — GB,
CX, or S5 — can shell out to mid-session, no MCP server required (R3).

### `.claude/commands/devteam-decompose.md`

One added instruction: when carving `Owned_Paths`, consult
`atlas.py impact <path>` on candidate territories before finalizing them,
and record any surprising coupling it surfaces in the task's Description.
Prose-only; ATLAS is an aid to the carve, never a gate on it.

### `board_publisher.py`

Optional, cosmetic `"atlas"` board key: `{"files", "card_coverage_pct",
"stale_cards"}`, read directly from `.devteam/atlas.db` if it exists (`{}`
otherwise, matching the `learning`/`usage` keys' "absent subsystem → empty
dict" convention). Never part of exit criteria.
