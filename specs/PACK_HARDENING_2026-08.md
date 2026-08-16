# Pack Hardening — field defects from 24h of live multi-project use

**Status:** SPEC — decompose target for the first L2 (supervised-loop) wave.
**Origin:** oikonomos field report `2026-08-16-devdepartment-findings.md` (items 8, 9), plus ORCH review findings from the ATLAS build (TASK-002, TASK-003, TASK-005) and the TASK-006 protocol violation.
**Baseline:** pack @ `master` after the A6 wave and the finding-1/11/12 fixes. Suites: 697 Python / 36 Node green.

Every item below was **confirmed in source or measured live** — none are speculative. Items already fixed upstream (oikonomos items 1–7, 10–12) are deliberately out of scope; this spec covers only what remains open.

---

## 0. Design rules (apply to every increment)

**H1 — Semantics before symptoms.** Each fix must correct the underlying rule, not the observed instance. `is_ignored` must implement gitignore's actual matching semantics, not special-case `node_modules`.

**H2 — Fail closed on identity questions.** Anything answering "which repo / which checkout / which index am I touching?" resolves explicitly and refuses when it cannot. Two incidents (TASK-006's main-checkout write, the home-repo capture) came from an implicit answer.

**H3 — A field lesson is not fixed until a test would have caught it.** Every increment lands regression tests that fail against the current code.

**H4 — No behavioural change without a visible signal.** Where a fix changes what a builder sees, the change must be observable (a status line, a report note), not silent.

---

## 1. Increment H-A — `is_ignored()` gitignore semantics (`atlas_core.py`)

**Confirmed defect.** Current implementation:

```python
directory = pattern.endswith("/")
pattern = pattern.strip("/")
if directory and (rel == pattern or rel.startswith(pattern + "/")):
    return True
```

A directory pattern is tested **only against the path root**. Real gitignore semantics: a pattern containing no internal `/` matches at **any depth**. So in a pnpm/monorepo layout, `node_modules/` excludes only the top-level directory while every `packages/*/node_modules/` is indexed in full. oikonomos worked around this with glob-form entries in `atlas.exclude`; that is a per-project patch for a core bug.

**Required semantics** (the subset git defines that ATLAS needs):
- Pattern with **no internal slash** (`node_modules/`, `*.log`, `build/`) matches at any depth — as a path segment for directory patterns, as a basename for file patterns.
- Pattern with a **leading slash** (`/dist/`) anchors to the repo root only.
- Pattern with an **internal slash** (`docs/build/`) anchors to the repo root (git's rule).
- Trailing slash means **directory-only**: it must match a directory segment, never a file of the same name.
- Existing behaviour for `.git/`, `.devteam/`, `atlas.exclude` globs, and basename `fnmatch` matching must be preserved — this is a widening, not a redesign.

**Acceptance:** parametrized tests covering each rule above, including the exact monorepo case (`packages/a/node_modules/x.js` excluded by `node_modules/`) and the anchoring distinction (`/dist/` must NOT exclude `packages/a/dist/`, while `dist/` must). A full scan of this repo must not change its file count except by intentionally-excluded paths, and the scan-time regression must be reported in the task's evidence.

## 2. Increment H-B — ATLAS index must resolve to the main checkout (`atlas_core.py`)

**Confirmed defect.** `db_path(repo) = repo / ".devteam" / "atlas.db"`, and `.devteam/` is gitignored — so **every worktree has its own index**, and nothing maintains it. Only the main checkout's copy is ever scanned. R3 and all three briefings actively tell builders to run `atlas.py query/where/impact` mid-session, i.e. from inside a worktree, where the index is absent or arbitrarily stale (oikonomos measured ~3 hours). The root cause was invisible enough to cost three diagnostic rounds.

**Required:** `db_path()` resolves the **main checkout** from anywhere, including a linked worktree, via `git rev-parse --git-common-dir` (its parent is the main checkout root). Fail-open per R4: when git is unavailable or the path cannot be resolved, fall back to today's `repo/.devteam/atlas.db` rather than erroring. Document the behaviour in `docs/ATLAS.md` — the tool is built for multi-worktree dispatch and must say what its index does across worktrees.

**Acceptance:** a real linked worktree in a temp repo; `db_path()` from inside it returns the main checkout's path; `atlas.py query` run from the worktree returns the main index's results; no-git and non-worktree cases fall back without raising; `docs/ATLAS.md` gains the explanation.

## 3. Increment H-C — FTS5 ranking and episode-index convergence (`atlas_core.py`, `atlas_episodes.py`)

Two ORCH review findings deferred as fast-follows:

**C1 (TASK-002 finding):** `query` uses SQL `LIKE '%term%'` against the FTS5 tables rather than FTS5 `MATCH`/bm25 — correct and instant at ~100 files, but it bypasses the index and yields alphabetical rather than relevance ordering. It compounds now that A2 fills `episodes`. Move the ranked paths to `MATCH` with bm25 ordering, preserving current output format, FRESH/STALE annotation, exit codes, and graceful behaviour on empty tables. Multi-word and punctuation-bearing queries must not raise FTS5 syntax errors — quote/escape user terms.

**C2 (TASK-003 finding):** the incremental indexer never converges to `sources changed: 0`. A source that is present but parses to zero episodes (today `INSTINCTS.md`) inserts no rows, so `_existing_source_hashes` — which reads FROM `episodes` — never records its key, and every subsequent run re-counts it as changed and rebuilds. Record the indexed hash for **every source scanned**, including zero-episode ones.

**Acceptance:** C1 — ranked results are relevance-ordered, a multi-word query and a query containing FTS5 punctuation both succeed, and the §3 contract is unchanged. C2 — a second consecutive `episodes` run reports `sources changed: 0` on this repo, and a test pins the zero-episode source case.

## 4. Increment H-D — repo line-ending policy (`.gitattributes`)

**Confirmed nuisance.** Every `PLAN.md` write in a session emits `LF will be replaced by CRLF`, and `git status` noise has repeatedly obscured real changes during review. The pack is developed on Windows and consumed on both platforms; `sync_from_pack.py` deliberately writes byte-exact and must stay that way.

**Required:** a `.gitattributes` that normalises text files in the repository to LF while leaving the working tree alone where it matters, explicitly marks known-binary paths, and — critically — does **not** alter the bytes `sync_from_pack.py` compares, since its whole conflict model is hash equality. State the interaction with sync explicitly in the file's comments.

**Acceptance:** the CRLF warning no longer appears for `PLAN.md` edits; `python -m pytest tests/test_sync_from_pack.py` stays green (byte-exactness preserved); `git diff --stat` on a fresh clone shows no mass renormalisation; the reasoning is recorded in the file itself.

## 5. Increment H-E — review standard: full-suite rule (`CLAUDE.md`)

**Field lesson, generic.** oikonomos ran two independent review passes each scoped to its own task's package plus lint/typecheck/build; master stayed red for ~2h because the failing assertion lived in a third package neither review touched. In a monorepo a filtered test run cannot see a cross-package regression, and the pack's own review standard does not currently say so.

**Required:** amend the pack's `CLAUDE.md` review standard step 3 to require the **full** suite, never a filtered subset, with a one-line statement of why. Keep it short — `CLAUDE.md` is a hot file paid for on every turn; the rationale belongs in `docs/MODEL_DISCIPLINE.md` or `docs/COORDINATION_PROTOCOL.md` if it needs more than a sentence.

**Acceptance:** the rule is present and ≤2 sentences in `CLAUDE.md`; any longer rationale lives in `docs/`.

---

## 6. Increments and territories (disjoint by design)

| # | Deliverable | Territory | Depends on |
|---|---|---|---|
| **H-A** | gitignore semantics | `scripts/atlas_core.py`, `tests/test_atlas_core.py` | — |
| **H-B** | main-checkout index resolution | `scripts/atlas_core.py`, `tests/test_atlas_core.py`, `docs/ATLAS.md` | H-A (same file — sequence, never concurrent) |
| **H-C** | FTS5 ranking + episode convergence | `scripts/atlas_episodes.py`, `tests/test_atlas_episodes.py` (C2); C1 folds into H-B's territory task | H-A |
| **H-D** | line-ending policy | `.gitattributes` | — |
| **H-E** | review standard amendment | `CLAUDE.md` | — |

H-A and H-B share `atlas_core.py` and therefore **must not be concurrent** — they are one sequenced chain, or one task. H-D and H-E are independent of everything and of each other, so they can run alongside the chain: that is deliberate, because this wave's second purpose is to exercise **concurrent multi-worktree dispatch**, which is where the sqlite contention and per-worktree index problems live.

## 7. Exit criteria

Full Python and Node suites green. No diff outside each task's `Owned_Paths`. A full ATLAS scan of this repo after H-A reports a file count consistent with the corrected exclusion rules and states the delta. `episodes` run twice reports `sources changed: 0` on the second. `atlas.py query` from inside a linked worktree returns main-checkout results. `PLAN.md` edits produce no CRLF warning. All five acceptance blocks above satisfied.

## 8. Open question for Alister

**Q1 — H-D scope.** A `.gitattributes` that renormalises existing tracked files will produce one large whitespace-only commit. Preference: (a) normalise everything now in a single dedicated commit, or (b) `* text=auto` going forward only, leaving existing files as-is until they are next touched? Spec default if unanswered: **(b)**, as the lower-risk option for a pack that other projects sync from byte-exactly.
