# OMLCP generation lane — one long coherent generation for greenfield territory

**Status:** SPEC v1.0 — Wave O. O-A and O-B are **delivered** with this spec (ORCH-direct pack change,
reviewed as one unit, like F9/F10). O-C is an owner-run measurement. O-D…O-F wait for O-C's result and for
Wave F's F2 (`headless.py` + ledger).
**Origin:** owner request 2026-10-06 to apply Viviers (2026), *Output-Maximizing Long-Context Programming*,
to the DEVDEPARTMENT lifecycle. Assessment and numbers: `docs/OMLCP_ASSESSMENT.md`. Operator guide:
`docs/OMLCP.md`.
**Position in the queue:** does **not** displace `docs/NEXT_WAVE_PRIORITY_2026-10.md` items 1–5. Its
cheapest companion — archiving PLAN.md (`scripts/plan_archive.py`, efficiency backlog item 5) — should run
first: on this repo it cuts the modelled agentic task cost by 43% on its own.

## 0. What is already correct — do not rebuild it

- Spec-first planning, `/devteam-decompose`, dossiers: these *are* the paper's planning phase.
- Territory isolation (Owned_Paths + worktrees + validator + firewall): the generate lane writes inside it,
  never around it.
- Independent review on a different model and the full-suite rule: unchanged for generated code.
- `dispatch.sh`/`.ps1` pre-create a task branch only when it does not exist, and reset worktrees with
  `checkout --detach` (branches survive). That is what lets `omlcp stage` hand a pre-generated branch to a
  builder without touching dispatch.

## 1. Design rules

**O1 — Generate only what is fixed.** A task enters the lane only when its file layout, exports and
dependencies are written down (the `omlcp-manifest`) and every output file is new. Brownfield edits,
debugging and runtime discovery stay agentic (paper §8.4, §11.1, §11.9).
**O2 — Constrained input.** The generator sees a compiled packet (contract, conventions, cited spec
sections, read-only context, manifest) — never PLAN.md, briefings or the whole repo. Target ≤ 30k tokens.
**O3 — The stream is untrusted until parsed.** Nonce-delimited files, discard-incomplete-tail, path policy
before any write, atomic writes, no overwrite of foreign or repaired files.
**O4 — Bounded continuation, never full regeneration by default.** Resume at the first unfinished file;
stop on budget, stagnation or repeated failure.
**O5 — Maker ≠ checker.** The generator model is never the judgment model (Wave F N5 extended).
**O6 — Every segment leaves a receipt.** One ledger line per segment in the F2 shape, generation and repair
kept apart, so the paper's §6.4 metrics are data, not anecdote.
**O7 — Ask, don't auto-flip.** `omlcp.enabled` ships `false`; auto-routing is enabled per project after O-C.

---

## 2. O-A — generation tooling *(delivered)*

`scripts/omlcp.py` (façade) over `omlcp_stream.py` (protocol, path policy, materializer, run state),
`omlcp_packet.py` (brief, manifest, spec excerpts, classifier, packet, continuation), `omlcp_generate.py`
(claude-cli / anthropic-api / command adapters, runner, ledger), `omlcp_verify.py` (manifest, syntax,
contract, anti-stub), `omlcp_economics.py` (paper model, priced model, DEVDEPARTMENT session model, ledger
report). Subcommands: `classify packet stage run materialize continue verify log-repair report economics
brief-template`. Standalone `--brief` mode for repos without the pack.

**Acceptance (met in this change):** 166 tests in `tests/test_omlcp_*.py`, including: marker parsing under
1-byte chunking; byte-level SSE decoding of multi-byte characters split across chunks; a fake `claude` CLI
end to end with `--resume` continuation run outside the repo; a dropped connection that resumes instead of
regenerating; stagnation, budget and two-failure stops; refusal to overwrite foreign or builder-repaired
files; protected-path parity with `hooks/lib.js`; `stage` committing onto a real git task branch and freeing
it; the paper's published 880k/55k/5.04M figures reproduced exactly; transport seams regenerating the
affected file; no parent-session environment leaking into generator subprocesses. **Live run** on Claude
Code 2.1.291 (claude-sonnet-5, effort low, output cap forced to 1,200 tokens): two segments, one seam
caught and regenerated, `--resume` into the generator's own session, verify PASS, 22/22 generated tests
pass, $0.097 (`docs/OMLCP.md` §6).

## 3. O-B — lifecycle integration *(delivered)*

1. `**Lane:**` optional task field (`generate` | `iterate`), ORCH-owned — `docs/COORDINATION_PROTOCOL.md` §3.
2. `/devteam-decompose` step 3a: for greenfield tasks, write the `omlcp-manifest` into the dossier and set
   `**Lane:** generate` when `omlcp classify` agrees.
3. `/devteam-generate` (new ORCH command): classify → packet → stage, then dispatch as usual.
4. All three builder briefings: a "Generate-lane tasks" section — the branch already holds the generation
   commit; verify, run the full suite, repair in separate commits, `log-repair`, hand off.
5. `autopilot.json` → `omlcp` block, `enabled: false` (pinned by a test).
6. `sync-manifest.json` registers every new script, test, doc and command as framework_owned.

## 4. O-C — the measurement *(owner-run; gates O-D)*

Run `docs/OMLCP.md` §8 on three matched greenfield tasks in a field project (one Python, one Flutter, one
MQL5 if available). Record both conditions' tokens, cost, wall-clock, first-pass approval, rework causes
and post-merge defects in this spec's changelog.
**Exit:** owner decision recorded — enable, keep manual-only, or drop. This is also the first independent
replication of the paper's Tier 2 claims.

## 5. O-D — supervisor auto-routing *(after O-C and F2)*

**Required:** when `omlcp.enabled` is true, `supervisor.decide()` emits `GENERATE` for an eligible
`pending` task with `**Lane:** generate` before its `DISPATCH`; `execute()` runs `omlcp stage` through
`headless.run`-style ceilings (wall clock = `timeout_seconds × (1 + max_continuations)`), files a P2 with the
stage output on failure, and dispatches the builder only after a successful stage. A failed stage never
blocks the task — it falls back to the normal builder loop with a Progress_Note.
**Acceptance:** fake-CLI tick tests for success, failure-fallback and the disabled default (byte-identical
decisions to today).
**Territory:** `scripts/supervisor.py`, `tests/test_supervisor_omlcp.py` (new), `autopilot.json`.
**Depends_On:** F2, G-A (both touch `supervisor.py`).

## 6. O-E — board and digest *(after F2)*

Per-lane cost and first-pass rate in `board_publisher.py` and the P0 digest, read from the ledger
(`omlcp_economics.ledger_report`).
**Territory:** `scripts/board_publisher.py`, `tests/test_board_publisher.py`, `scripts/status_digest.py`.

## 7. O-F — Batch API adapter *(optional)*

`adapter: anthropic-batch` submits packets through the Message Batches API (50% discount on input and
output), polls, and feeds results through the same materializer. Continuations go through the streaming
adapter. Only worth building if O-C shows several generate-lane tasks per wave.
**Territory:** `scripts/omlcp_generate.py`, `tests/test_omlcp_generate.py`.

## 8. Territories and order

| # | Territory (Owned_Paths) | Order |
|---|---|---|
| O-A | `scripts/omlcp*.py`, `tests/test_omlcp_*.py` | delivered |
| O-B | `docs/OMLCP*.md`, `.claude/commands/devteam-generate.md`, `.claude/commands/devteam-decompose.md`, `briefings/*.md`, `docs/COORDINATION_PROTOCOL.md`, `autopilot.json`, `sync-manifest.json`, `CLAUDE.md`, `README.md` | delivered |
| O-C | none (measurement) | next |
| O-D | see §5 | after O-C, F2, G-A |
| O-E | see §6 | after F2 |
| O-F | see §7 | optional |

## 9. Exit criteria

Suites green. `omlcp economics` reproduces the paper's published figures. O-C recorded with an owner
decision. With `omlcp.enabled: false`, observable supervisor and dispatch behaviour is byte-identical to
before this wave.

## 10. Note for the reviewer

Vendor-behaviour claims (Claude Code flags, stream-json event shapes, SSE event names, prices) were
verified against Anthropic's docs on 2026-10-06. Where an installed CLI disagrees, **the CLI wins**: record
the observed form in `docs/OMLCP.md` §6 and adjust the adapter. The economics module separates measured
inputs from assumptions by name; do not quote its DEVDEPARTMENT-model ratios as measurements until the
ledger replaces the assumptions.
