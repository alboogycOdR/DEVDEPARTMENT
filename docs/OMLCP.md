# OMLCP — the generation lane

`scripts/omlcp.py` implements Output-Maximizing Long-Context Programming (Viviers 2026) as a lane inside
DEVDEPARTMENT: a fully specified greenfield task is implemented in **one long, coherent generation** (plus at
most two bounded continuations) instead of a long builder tool loop, then repaired and reviewed exactly like
any other task. Why, and where it does and does not pay: `docs/OMLCP_ASSESSMENT.md`. The wave that owns it:
`specs/OMLCP_GENERATION_LANE_2026-10.md`.

```
 ORCH /devteam-decompose ──▶ task block (+ **Lane:** generate) + dossier with omlcp-manifest
          │
          ▼
 ORCH /devteam-generate ──▶ omlcp classify → omlcp packet → omlcp stage
          │                   (generation committed on task/TASK-NNN-<suffix>, worktree removed)
          ▼
 /devteam-dispatch ──▶ builder claims, switches to the existing branch,
                       omlcp verify + project tests → repairs → omlcp log-repair → needs_review
          ▼
 /devteam-review (different model) ──▶ approved / rework, exactly as today
```

This is the paper's hybrid pattern (its Figure 2): generation first, agentic refinement after.

---

## 1. When a task qualifies

`python scripts/omlcp.py classify TASK-NNN` prints `GENERATE` or `ITERATE` with reasons. It blocks when:

- the dossier has no `omlcp-manifest` block, or the task has no acceptance criteria / readable spec;
- any manifest file already exists (the lane creates files; editing existing code stays agentic);
- a manifest path is outside `Owned_Paths`, is protected without a `Protected_Grants` entry, or is
  coordination state (PLAN.md, dossiers, `.git/`, `.devteam/` — never writable);
- the Title/Description names discovery work (investigate, debug, reproduce, root cause, profile, flaky, …);
- estimated output exceeds `1 + max_continuations` segments of `practical_ceiling_tokens`;
- ORCH set `**Lane:** iterate`.

It notes (without blocking) new paths not marked `(new)`, files without declared exports, unpinned
dependencies and a missing test command.

## 2. The manifest (ORCH writes it at decompose time)

Put one fenced block in `dossiers/TASK-NNN.md` (any section; `## Generation manifest` by convention):

````markdown
```omlcp-manifest
{
  "language": "python",
  "conventions": "PEP 8, type hints, stdlib logging, no new dependencies.",
  "files": [
    {"path": "scripts/report_export.py", "purpose": "CSV/JSON export of REVIEW.md verdicts.",
     "exports": ["export_reviews", "main"], "est_lines": 260},
    {"path": "tests/test_report_export.py", "purpose": "pytest suite: empty file, malformed rows, both formats.",
     "exports": [], "est_lines": 180}
  ],
  "context": ["scripts/validate_plan.py"],
  "dependencies": {"python": ["pytest==8.3.3"]},
  "wiring": ["sync-manifest.json registration is TASK-061 (integration task, not this generation)"],
  "tests": "python -m pytest -q tests/test_report_export.py"
}
```
````

| Key | Required | Meaning |
|---|---|---|
| `files[].path` | yes | Exact output path, in emission order. Must be inside `Owned_Paths`. |
| `files[].purpose` | yes | One or two sentences; goes into the packet verbatim. |
| `files[].exports` | recommended | Names the file must define. `omlcp verify` fails if one is missing (the contract check). |
| `files[].est_lines` | recommended | Sizes the run; without it the budget check is skipped. |
| `context` | no | Existing files the generator may read (included read-only, never re-emitted). Keep it small. |
| `dependencies` | no | `{ecosystem: [pinned specs]}`. The generator is told to use exactly these. |
| `conventions` | no | Project style rules that matter for this code. |
| `wiring` | no | Integration points that are *not* in this generation, so the model does not invent them. |
| `tests` | no | The command the builder will run. Shown to the generator. |

## 3. Commands

| Command | Who | What it does |
|---|---|---|
| `omlcp classify TASK-NNN [--json]` | ORCH | Lane decision with reasons. |
| `omlcp packet TASK-NNN [--print]` | ORCH | Compiles `.devteam/omlcp/TASK-NNN/{system.md,packet.md,packet.json}`; reports input/output budget. |
| `omlcp stage TASK-NNN [--unit S5]` | ORCH | Creates/uses `task/TASK-NNN-<suffix>` in a throwaway worktree (`../wt-omlcp-<project>-<task>`), runs the generation, commits it as one commit, removes the worktree. Re-run to continue an incomplete stage. |
| `omlcp run TASK-NNN --target <dir>` | anyone | Generation + materialization + verification into `<dir>`, no git. Standalone mode uses this. |
| `omlcp verify TASK-NNN --target <dir>` | builder | Manifest present, syntax (Python/JSON), declared exports defined, no stubs or placeholder text. |
| `omlcp log-repair TASK-NNN --input-tokens N --output-tokens N` | builder | Records repair cost separately from generation (paper §6.4 item 6). |
| `omlcp materialize TASK-NNN --stream file.txt` | anyone | Manual adapter: append a stream you produced elsewhere (e.g. a long claude.ai reply) and write its files. |
| `omlcp continue TASK-NNN [--stateless]` | anyone | Prints the continuation prompt for the next segment (manual flow). |
| `omlcp report [--json]` | ORCH | Per-task segments, continuations, generation vs repair tokens, output share, $ — from `.devteam/ledger.jsonl`. |
| `omlcp economics --lines N` | ORCH | Paper model vs priced vs DEVDEPARTMENT session model, with this repo's measured prefix and tokens/line. |
| `omlcp brief-template` | anyone | Template for standalone mode. |

Every task-taking command accepts `--brief FILE` instead of a task ID, `--repo` (where PLAN.md, dossiers,
specs and `.devteam/` live) and, where it writes or checks files, `--target`.

## 4. The stream protocol

The packet's system prompt (the *generation contract*) fixes this output format; `scripts/omlcp_stream.py`
enforces it:

```
@@OMLCP 3f9c2a7b1e44 FILE scripts/report_export.py
...complete file content...
@@OMLCP 3f9c2a7b1e44 END scripts/report_export.py
@@OMLCP 3f9c2a7b1e44 FILE tests/test_report_export.py
...
@@OMLCP 3f9c2a7b1e44 END tests/test_report_export.py
@@OMLCP 3f9c2a7b1e44 DONE
```

- The 12-hex **nonce** is random per packet, so file content can never be mistaken for a marker.
- A file counts only once its `END` arrives. A truncated tail is discarded, never written half-done, and
  becomes the continuation point. A run is complete only when `DONE` arrived and every manifest file is on disk.
- Text outside FILE blocks is ignored and reported. One wrapping markdown fence inside a file is stripped
  with a warning. CRLF becomes LF. Files are written atomically as UTF-8 with LF endings.
- If a path is repeated across segments, the first complete version wins.
- The runner inserts `@@OMLCP <nonce> SEAM` wherever the transport joined two model messages into one reply
  (see §6). A file open at a seam is discarded and regenerated by the continuation — never stitched.
- An existing file is overwritten only if *this task's* earlier run wrote it and nobody has changed it since —
  a builder's repairs are never clobbered by a re-run.

## 5. Continuation, resume and failure handling

- Each segment streams into `segment-N.raw`, flushed per chunk. A dropped connection keeps everything received.
- After every segment all raw segments are re-parsed; completed files land immediately.
- Continuation resumes at the first unfinished file. `continuation_mode: "resume"` re-enters the same
  conversation (`claude -p --resume <session>`; for the API, the prior output is sent back as the assistant
  turn). `"stateless"` sends packet + completed file contents in a fresh call.
- The loop stops on: complete; the per-invocation `max_continuations` budget exhausted; a segment that completed no new file
  (stagnation); two failing segments in a row; a policy rejection (it would only repeat).
- `omlcp run` / `omlcp stage` on a task with existing state **resumes**. `--fresh` discards the state and
  starts with a new nonce — use it only when the packet itself was wrong.

## 6. Adapters and configuration

`autopilot.json` → `omlcp` (template ships `enabled: false`; override per machine in `autopilot.local.json`):

| Key | Default | Notes |
|---|---|---|
| `enabled` | `false` | Gates supervisor auto-routing (Wave O, O-D). The CLI works regardless when a person or ORCH runs it. |
| `adapter` | `claude-cli` | `claude-cli`, `anthropic-api`, `command`, or `manual` (no model call). |
| `model` | `claude-sonnet-5` | Never the judgment model — `run`/`stage` refuse (maker ≠ checker). |
| `effort` | `medium` | The paper recommends minimal reasoning during generation; try `low` as an experiment arm and compare in `omlcp report`. |
| `max_output_tokens` | `64000` | Exported as `CLAUDE_CODE_MAX_OUTPUT_TOKENS` (Claude Code caps it per model; 128k on current Opus/Sonnet 5.5) or sent as `max_tokens`. |
| `practical_ceiling_tokens` | `60000` | Sizing assumption from paper §11.7. |
| `max_continuations` | `2` | At most three segments **per invocation**. Re-running `run`/`stage` is a deliberate decision to spend one more bounded budget (and is how a crashed run resumes). |
| `tokens_per_line` | `12` | Measured on this pack (12.7). The paper's 3 is too low for real code. |
| `input_warn_tokens` / `input_max_tokens` | `30000` / `150000` | Packet budget: warn / refuse. |
| `continuation_mode` | `resume` | Or `stateless`. |
| `timeout_seconds` | `3600` | Wall-clock cap per segment. |
| `cli` | `claude` | Command name or argv prefix list. |
| `command` | `[]` | For `adapter: command`: argv with `{system}` / `{packet}` placeholders; prompt also on stdin. |
| `api_base`, `api_version`, `api_extra_body` | Anthropic defaults | For `adapter: anthropic-api` (needs `ANTHROPIC_API_KEY`; bills the API, not a subscription). |
| `extra_stub_patterns` | `[]` | Extra regexes the verifier treats as placeholders. |

**`claude-cli` specifics.** Runs `claude -p --output-format stream-json --verbose --include-partial-messages
--tools "" --disallowedTools "mcp__*" --system-prompt-file <contract> --model … --effort …` from an empty temp
directory, so no CLAUDE.md, project hooks or MCP servers load and the model has no tools — the whole budget is
output. It uses your subscription login; `--bare` is deliberately not used because bare mode requires an API
key. Flags were verified against the Claude Code CLI reference on 2026-10-06; if your installed CLI
disagrees, the CLI wins — adjust `cli`/`command` and record what you observed here.

Two behaviours of the CLI that the adapter handles, both **observed live on Claude Code 2.1.291**
(2026-10-06):

1. **Silent output-cap recovery creates seams.** When a reply hits `CLAUDE_CODE_MAX_OUTPUT_TOKENS`, Claude
   Code asks the model to continue in a new message inside the same `-p` run (stream-json shows a second
   `message_start` after a `max_tokens` stop). The continuation can restart the interrupted line — the live
   run produced `def test_simple_text(self)    def test_simple_text(self) -> None:`, a syntax error the
   verifier caught. The adapter now marks every such seam; the parser regenerates the affected file. Keep
   `max_output_tokens` high (64k) so seams are rare.
2. **Nested sessions inherit the parent's identity.** Run from inside a Claude Code session (ORCH running
   `omlcp stage`, a builder running a script), `claude -p` inherits `CLAUDE_CODE_SESSION_ID` and friends and
   reports the *parent's* session id — so `--resume` would have appended to the parent conversation. The
   adapter strips session-bound variables (`SESSION_BOUND_ENV` in `omlcp_generate.py`) and keeps
   authentication variables. With the scrub, the live run got its own session id and resumed it correctly.

The live acceptance run (four-file Python package, `max_output_tokens: 1200` to force the recovery path):
segment 0 completed three files and hit a seam inside the fourth; segment 1 resumed the generator's own
session and regenerated it; `omlcp verify` passed and the 22 generated tests passed. Total $0.097.

**Batch discount.** Generation is non-interactive, so the Anthropic Batch API (50% off input and output) is a
natural fit for large backlogs of generate-lane tasks. It is not wired yet; Wave O lists it as O-F.

## 7. Standalone mode (no PLAN.md)

For a small repo, an EA, or a client project that will never install the pack:

```bash
python scripts/omlcp.py brief-template > brief.md     # fill in Goal, Specification, Acceptance, manifest
python scripts/omlcp.py classify --brief brief.md
python scripts/omlcp.py run --brief brief.md --target .
python scripts/omlcp.py verify --brief brief.md --target .
```

Only the manifest constrains paths in standalone mode (no Owned_Paths, no protected list beyond VCS and
`.devteam/`). State and ledger go to `./.devteam/`.

### Example: an MQL5 Expert Advisor

EAs are a good fit when the strategy rules, inputs and module boundaries are decided before coding. The
contract check knows MQL5 definitions (functions, classes, `input` variables):

````markdown
```omlcp-manifest
{
  "language": "mql5",
  "conventions": "MQL5 strict; CTrade for orders; every input prefixed Inp; risk in % of equity; Print() logging with [EA] prefix; no DLLs.",
  "files": [
    {"path": "MQL5/Include/CRT/SessionClock.mqh", "purpose": "Broker-time to New York session windows.", "exports": ["CSessionClock"], "est_lines": 140},
    {"path": "MQL5/Include/CRT/RangeDetector.mqh", "purpose": "H4 three-candle range: accumulation, sweep, distribution states.", "exports": ["CRangeDetector"], "est_lines": 260},
    {"path": "MQL5/Include/CRT/ConfluenceScorer.mqh", "purpose": "FVG / order block / MSS scoring with configurable weights.", "exports": ["CConfluenceScorer"], "est_lines": 300},
    {"path": "MQL5/Include/CRT/RiskManager.mqh", "purpose": "Position sizing, daily loss cap, max open trades.", "exports": ["CRiskManager"], "est_lines": 200},
    {"path": "MQL5/Experts/CRT/CRT_EA.mq5", "purpose": "EA shell: inputs, OnInit/OnTick/OnDeinit wiring the modules.", "exports": ["OnInit", "OnTick", "OnDeinit", "InpRiskPercent", "InpMinConfluence"], "est_lines": 320}
  ],
  "tests": "Compile in MetaEditor (0 errors, 0 warnings), then Strategy Tester visual run on XAUUSD H4."
}
```
````

Roughly 1,200 lines ≈ 15k output tokens: one segment. Compilation and the Strategy Tester remain the
verification that matters; `omlcp verify` only proves the declared modules and inputs exist and nothing is
stubbed.

## 8. Measuring it (the paper's §6.4 protocol, adapted)

Before enabling auto-routing, run a matched comparison and keep the numbers:

1. **Pair tasks.** Pick three greenfield tasks with fixed acceptance tests (e.g. a CLI tool + tests, a
   Flutter screen + widget tests, an MQL5 module). Write each spec and manifest once.
2. **Two conditions.** (a) the normal builder loop on the S5 unit; (b) `omlcp stage` + builder repair.
   Same model family and effort for builder and generator; same reviewer.
3. **Record.** Builder condition: session usage from the CLI's JSON result or the F2 ledger once it exists.
   Generate condition: `omlcp report` (generation and repair kept apart). For both: wall-clock, review
   verdict, first-pass yes/no, rework causes, post-merge defects within two weeks.
4. **Coherence.** Count declared-export misses, interface mismatches found in review, and files outside
   territory (should be zero in both — the firewall and materializer refuse them).
5. **Decide.** Enable `omlcp.enabled` for a project only if the generate condition is no worse on first-pass
   approval and rework, and cheaper or faster. Record the result in `docs/MODEL_DISCIPLINE.md`-style prose in
   this file's history.

## 9. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `classifies as ITERATE` | Read the BLOCK lines. Usually a missing manifest, an existing file, or discovery wording. Fix the plan, don't `--force-lane` past it. |
| `REFUSED: generator model … is the reviewer model` | Change `omlcp.model` (or the reviewer). Maker ≠ checker. |
| `stagnation: segment completed no new file` | The model answered in prose or broke protocol. Inspect `segment-N.raw`; tighten the packet or switch model; `--fresh` if the packet changed. |
| `REJECT … already exists` | The file was there before generation, or a builder repaired it. Intended: the lane never overwrites foreign or repaired work. |
| `packet is ~N input tokens, above …` | Trim spec excerpts (cite sections, not whole specs) or context files. |
| `stage` leaves a worktree behind | Generation incomplete: re-run `omlcp stage TASK-NNN`. The builder cannot switch to the branch until the worktree is removed. |
| Windows | Pure Python; paths are validated for Windows-reserved names and characters before writing. The CLI is resolved with `shutil.which`, so `claude.exe`/`claude.cmd` both work. |
