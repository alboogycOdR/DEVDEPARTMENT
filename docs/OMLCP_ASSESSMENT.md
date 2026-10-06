# OMLCP assessment — what Output Maxing means for DEVDEPARTMENT

**Status:** ORCH assessment, 2026-10-06. Decision input for Wave O (`specs/OMLCP_GENERATION_LANE_2026-10.md`).
**Source:** J. Viviers, *Output-Maximizing Long-Context Programming: Why Agentic Coding Workflows Scale Poorly
and What to Do Instead*, v1.0.0, 11 March 2026 (Zenodo 18963411; `github.com/JasonViviers/OMLCP-paper`, CC BY 4.0).
**Evidence used:** the paper PDF in full; this repo at `2d4202c` (parked after Wave E);
`docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md`; `docs/EFFICIENCY_BACKLOG_2026-09.md`;
Anthropic pricing and model pages (verified 2026-10-06); `scripts/omlcp.py economics` on this repo.
**Reproduce every number here:** `python scripts/omlcp.py economics --lines 4000` (and `--lines 1500`,
`--lines 10000`, `--prefix-doc-tokens 15000`).

---

## 1. The claim, in one paragraph

Agentic coding tools spend most of their tokens re-reading context and reasoning between small edits; that
shape is a leftover from 4k-token output limits. With 64k–128k output windows, plan the whole artifact first,
then emit it in one or a few long generations, continue at a structural boundary when the ceiling is hit,
and fix residual defects with targeted edits. The paper reports 16× (10k lines) to 44× (30k lines) token
savings from its cost model, and field cases of 26–52× (a 14,431-line app for $0.58) and ~60×.

It is explicit that this fits well-specified work only. Exploratory work, vague requirements and
runtime-diagnosis tasks stay agentic, and its recommended shape is a **hybrid**: generate first, then refine
agentically (paper Fig. 2).

## 2. What holds up and what does not

Read with the paper's own claim tiers (§11.11): Tier 1 = derived from its model, Tier 2 = observed in its
field cases, Tier 3 = forecast.

| # | Paper claim | Verdict | Evidence |
|---|---|---|---|
| A | Iterative workflows have superlinear cost (Tier 1) | **Holds, with a correction.** | Eq. (2) is `0.004L² + 48L`. At 10k lines the *linear reasoning term* (480k) still outweighs the quadratic context term (400k); the quadratic term only dominates past ~12k lines. The paper's narrative leads with rehydration, but its own numbers are mostly per-step reasoning at typical task sizes. |
| B | T ≈ 3 tokens per line of code (§4.2) | **Does not hold for us — ~4× low.** | This pack's Python measures **12.7 tokens/line** (13,457 script lines, chars ÷ 3.5); PowerShell ~15. At T = 12.7 the 10k-line ratio falls from **16× to 6.5×** in the paper's own model (`priced_measured_T`). Per-pass capacity at the paper's observed ~60k practical ceiling (§11.7) is **~4,700 lines**, not ~20,000. |
| C | Rehydration is the dominant *cost* (§2.3, §9) | **Mostly not true in dollars on Claude.** | Opus 5.5 cache reads bill at 0.05× input; Sonnet at 0.1×. With a 90% cache hit, input is **1–2% of the agentic bill** in the paper's flows; reasoning and output tokens (billed at output price) are the cost. The paper prices nothing and ignores caching. Rehydration still matters for **context quality** (lost-in-the-middle, compaction drift — paper §8.2) and for **usage windows**, whose weighting of cached tokens Anthropic does not publish. |
| D | Single-pass generation preserves global coherence (§5) | **Plausible, matches our field evidence.** | Synthesis §0.3–0.4: 58 OWNERSHIP_CONFLICT blocks (oikonomos), 38% of rwc's first 29 tasks blocked on shared/new files, cross-package type breaks merged green, KERYX's v2 wave never wired. That is the "interface misalignment / architectural erosion" failure class the paper names, produced here by carving one design across several builders and sessions. |
| E | 26–52× field savings, $0.58 for 14,431 lines (Tier 2) | **Unverified; treat as anecdote.** | n = 3, author-run, baseline token counts estimated (the paper says Windsurf lacked per-step telemetry, §6.4). At T = 12.7 the headline app is ~183k output tokens — more than one 128k pass and inconsistent with "20 minutes, one pass" unless lines were very short. The paper itself asks for independent replication; it does not exist yet. |
| F | Errors cannot be fixed mid-stream; a failed stream can cost a full re-run (§11.3–11.4) | **True, and fixable in tooling.** | Their SSE incident forced a full re-stream. `scripts/omlcp.py` makes this cheap: raw stream persisted per chunk, nonce-delimited files, discard-incomplete-tail, resume at the first unfinished file, atomic writes, byte-level UTF-8 SSE decoding (§3 below). |
| G | Large-scale editing of existing code is "under-explored" (§11.9, §12.7) | **Open — and it is most of our work.** | Most of DEVDEPARTMENT's own 46 tasks were brownfield evolution of a 13k-line codebase with ~1,200 tests. The generate lane therefore targets **new territory only** and refuses to overwrite existing files. |

## 3. Where DEVDEPARTMENT already is OMLCP — and where it is the anti-pattern

**Already OMLCP (keep):**

- *Comprehensive upfront planning* (principle 1) is `/devteam-decompose` + spec-first discipline + dossiers.
  The paper's planning phase is our Phase 1.
- *Deterministic repair* (§3.2 step 5) is our review → rework loop with real test evidence.
- *Constrained input* is the stated aim of CLAUDE.md's "Context & prefix hygiene" section.

**The anti-pattern (fix):**

1. **PLAN.md rehydration.** 373 KB ≈ 93k tokens, re-read by every builder session and carried in the
   session prefix of every turn (~121k-token prefix including harness, briefing and dossier). Field projects
   reached 1.66 MB (KERYX). This is exactly the "every turn reloads context" cost — and it is paid by the
   *review* session too, where the generate lane changes nothing.
2. **One design, several builders.** Disjoint territories make overwrites impossible — that part is proven
   (zero cross-territory damage across 475+35 tasks). But splitting *one greenfield module* across GB/CX/S5
   plus integration tasks is the paper's "stitching partial views". Coherence is reconstructed at review.
3. **1–4 h agent loops for work that is fully specified.** A task with a frozen file layout, frozen
   interfaces and pinned dependencies does not need 60 tool turns of discovery.

## 4. The numbers for this pack

`DevDeptParams` in `scripts/omlcp_economics.py` models one task both ways. Measured from the repo: prefix
documents (CLAUDE.md, AGENTS.md, S5 briefing, agent definition, PLAN.md = 102,597 tokens), dossier median,
tokens/line. **Assumed** (labelled in code, replaced by ledger data once Wave F's F2 ledger exists):
15k harness tokens, 60 builder turns, 1,500 tokens of tool output per turn, 600 reasoning tokens per turn,
1.5 builder sessions per task, 40-turn Opus review, 90% cache hit.

| Task size | Paper model (T=3) | Paper flows, measured T, $ | DEVDEPARTMENT model, tokens | DEVDEPARTMENT model, $ (agentic → generate) |
|---|---|---|---|---|
| 1,500 lines | 2.7× | 3.7× | 3.0× | $11.84 → $5.49 (2.2×) |
| 4,000 lines | 6.9× | 4.2× | 2.7× | $13.20 → $6.70 (2.0×) |
| 10,000 lines (3 segments) | 16.0× | 4.5× | 2.2× | $16.44 → $9.65 (1.7×) |
| 4,000 lines, PLAN.md archived to ~15k-token prefix | 6.9× | 4.2× | 2.3× | $7.50 → $4.28 (1.8×) |

What the table says:

- **Builder-side tokens drop 14–31×** (generation + repair vs builder sessions). That is the paper's effect,
  and it is real at our scale.
- **Whole-task savings are ~2×, not 20×**, because the independent Opus review — which this pack must keep
  (maker ≠ checker; it caught real defects with green suites, efficiency backlog "What to keep") — reads the
  same 121k-token prefix for 40 turns. The review is untouched by OMLCP.
- **Archiving PLAN.md alone cuts the agentic task cost 43% ($13.20 → $7.50)** — about the same saving as the
  generate lane, for no new code (`scripts/plan_archive.py` exists; efficiency backlog item 5). Both together:
  $13.20 → $4.28 (3.1×).

## 5. Recommendations, in order

1. **Archive PLAN.md before anything else** (ORCH chore, zero code). It is the single largest
   context-rehydration cost in the system and it taxes review too.
2. **Adopt the generate lane for greenfield territory — as a measured experiment first.** The tooling ships in
   this change (`scripts/omlcp.py`, docs/OMLCP.md). Run the paper's §6.4 reproducibility protocol on three
   matched tasks (Wave O, O-C) before enabling supervisor auto-routing. That would also be the first
   independent replication of the paper.
3. **Change how greenfield work is decomposed.** For a new module whose layout and contracts can be fixed:
   one task, one territory, one generation (sized by output budget, ≤ ~4,500 lines per segment, ≤ 3 segments),
   instead of N builder tasks plus integration tasks. Shared registrations (`index.ts`, `router.dart`,
   `sync-manifest.json`) stay separate single-owner integration tasks.
4. **Use OMLCP-lite for small repos that will never install the pack** (synthesis §0.10: LekkerSwot never
   installed it; rwc abandoned dispatch after five days). `omlcp.py --brief brief.md` needs no PLAN.md. The
   same applies to well-specified MQL5 Expert Advisors, where module boundaries and inputs are known before
   coding (docs/OMLCP.md has an EA manifest example).
5. **Do not use it for:** brownfield edits, debugging, flaky tests, performance work, integration wiring,
   review, or anything whose Description says investigate/reproduce/diagnose. The classifier refuses these.

## 6. What OMLCP does not change here

- **The review gate.** Generated code is reviewed exactly like builder code, on a different model
  (`omlcp run` refuses a generator model equal to the judgment model).
- **The full suite.** Verification (`omlcp verify`) is a cheap pre-filter: manifest, syntax, declared
  exports, anti-stub. It never replaces the project's tests.
- **Territory.** The materializer enforces manifest ⊆ Owned_Paths, refuses protected paths (parity-tested
  against `hooks/lib.js`), and never writes PLAN.md, dossiers, `.git/` or `.devteam/`.

## 7. Risks and their mitigations

| Paper risk | Mitigation in this change |
|---|---|
| Stream failure forces full regeneration (§11.4) | Raw stream flushed per chunk; resume at first unfinished file; `omlcp run` resumes from state after a crash |
| Transport stitching corrupts a file (the §11.4 class, seen live in Claude Code's own output-cap recovery) | Seam markers at every extra `message_start`; the file containing a seam is regenerated, never stitched (docs/OMLCP.md §6) |
| A nested `claude -p` resuming the *parent* session | Session-bound environment variables are stripped from generator subprocesses (verified live) |
| Output stops below the ceiling (§11.7) | Bounded continuation (`max_continuations: 2`), stagnation stop, two-failures stop |
| Placeholders and summarised code (§11.6) | Generation contract forbids them; verifier fails on stub bodies and placeholder phrases |
| Early architectural defect makes repair dearer than regeneration (§11.4) | Classifier refuses unless layout + contracts are fixed; `--fresh` regenerates; review still rejects |
| Outdated dependencies (§11.8) | Manifest carries pinned dependencies; classifier flags unpinned ones |
| Evidence concentrated on one vendor (§11.10) | Adapters for Claude CLI, Anthropic API and any command (Codex/Grok); ledger records model + effort per run |
| "Green did not mean working" (synthesis §0.4) | Contract check on declared exports; wiring listed as out-of-scope integration work in the packet |

## 8. Decision heuristic for ORCH

Generate when **all** hold, otherwise iterate:

1. Every output file is new (`(new)` in Owned_Paths).
2. The dossier carries an `omlcp-manifest`: paths, purpose, exports, `est_lines`, pinned dependencies, test command.
3. Acceptance criteria trace to spec sections that fit in a ≤ 30k-token packet.
4. Estimated output ≤ 3 segments × 60k tokens (≈ 14,000 lines at 12.7 tokens/line; prefer one segment).
5. Nothing in the task requires running code to learn what to write.

`python scripts/omlcp.py classify TASK-NNN` applies this mechanically and prints its reasons.
