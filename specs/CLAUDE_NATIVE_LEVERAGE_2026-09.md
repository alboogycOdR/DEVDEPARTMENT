# Claude-native leverage — current models, bounded sessions, typed outputs

**Status:** SPEC v1.2 — Wave F. (v1.2: F2 ledger records model + effort per run, per rwc's CX model churn on a misdiagnosis.) Decompose F1 and F2 alongside Wave G. The rest follow Wave E.
**v1.1 field update (2026-09-26):** re-prioritised against the field synthesis (`reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md`):
- **F1 is promoted.** Judgment sessions were the dominant cost in oikonomos (three usage lockouts), so moving judgment to `claude-opus-5-5` ($4/$20) matters for cost, not only quality.
- **F2 is promoted.** A delegated agent ran 14 h and changed config (KERYX SB-10). Every ORCH-spawned headless or background session gets `--max-turns`/`--max-budget-usd` and a wall-clock cap, with `DEVTEAM_DELEGATED=1` exported (Wave E-A).
- **F3's review schema is now required by Wave G-A.** It must carry `reviewer_model`, `reviewer_unit` and `gate_result_path`/`gate_sha`. The typed distiller output is **deprioritised** until Wave E-I shows the learning loop earns its sessions.
- **Per-unit strict mode (F3.3) is confirmed valuable.** oikonomos ran strict with CX9 and rated the CONTROL blackboard its best feature.
- **F4 (auto mode) is supported by the field.** oikonomos's CLAUDE.md already bans bypass modes, while the pack's own `review_cmd` uses `--dangerously-skip-permissions` (oikonomos SB-12).
**Origin:** end-to-end review, 2026-09-26 (`DEVDEPARTMENT_REVIEW_2026-09-26.md` §1, §3). It absorbs item 2 of the Wave D proposal (a dated, re-derivable model matrix) and supplies the substrate for Wave D items 3–4 (blind verifier, report contract).
**Baseline:** Claude Code 2.1.283. Current models: `claude-opus-5-5` ($4/$20, 1M), `claude-fable-5-1` ($10/$50, 1M), `claude-sonnet-5` ($2/$10, 1M), `claude-haiku-4-5`. Sonnet 5.5 and Haiku 5.5 have been announced as "coming weeks".

## 0. What is already correct — do not rebuild it

- The checker-independence rule is right and stays. This wave moves it from a pinned literal (`"claude-opus-4-8" in review_cmd`) to a tested invariant.
- The deterministic confidence math in `instincts.py`/`distiller.py` stays in code. F3 changes only the *transport* of model output.
- `usage_probe.py`'s window meters stay. F2's ledger adds per-task cost and does not replace the window gate.

## 1. Design rules

**N1 — A model ID appears in exactly one tracked place** (`autopilot.json` → `models`), plus tests that assert invariants, not literals.
**N2 — Every headless session has a ceiling and leaves a receipt.** Receipt = one ledger line.
**N3 — Machine-read model output is schema-typed.** Prose is for humans.
**N4 — Ask, don't auto-flip.** Every increment that changes runtime safety posture ships **disabled** and is enabled per project after its live verification, the same posture as `control.mode`, ATLAS and Tower.
**N5 — Maker ≠ checker extends to advisors.** A model that shaped the work may not judge it.

---

## 2. F1 — role→model matrix and one headless-argv builder

**Required:**
1. `autopilot.json` gains:
   ```json
   "models": {
     "_verified_at": "2026-09-26",
     "roles": {
       "planner":    {"model": "claude-opus-5-5",  "effort": "xhigh"},
       "judgment":   {"model": "claude-opus-5-5",  "effort": "high"},
       "mechanical": {"model": "claude-sonnet-5",  "effort": "medium"},
       "distiller":  {"model": "claude-sonnet-5",  "effort": "medium"},
       "cards":      {"model": "claude-haiku-4-5", "effort": null}
     },
     "escalation_planner": {"model": "claude-fable-5-1", "effort": "high"}
   }
   ```
   `judgment_model`, `learning.model` and `review_cmd`'s `--model` become derived. Keep reading the legacy keys when `models` is absent, so onboarded projects keep working until they sync.
2. New `scripts/headless.py` with `claude_argv(role, prompt, cfg, *, extra=()) -> list[str]`: the single place a `claude -p` argv is built (`--model`, `--effort` when set, permission flags). `supervisor.py` (review, `REVIEW_TG`, triage), `distiller.call_model`, `atlas_cards` and `usage_probe` call it. Shell-string `review_cmd` stays supported as an explicit override.
3. **Invariant test** (replaces the literal pins at `tests/test_supervisor.py:258–283` and `test_supervisor_telegram.py:231`): for the shipped `autopilot.json`, `models.roles.judgment.model` ∉ {`model` of every *active* unit in `builders.defined`}. The same check runs in `validate_plan.py --config` so a local override that breaks it fails loudly.
4. Nightly maintenance step `models_probe`: for each distinct role model, run `claude_argv(role, "ok", extra=["--max-turns","1"])`. File a P2 if stderr contains the deprecation or auto-update warning, or if the run fails. Update nothing automatically (N4).
5. ORCH-direct doc edits: CLAUDE.md's model table becomes a pointer to `models`; `docs/MODEL_DISCIPLINE.md` records the 2026-09-26 re-seat and its rationale; the command files drop hard-coded IDs.

**Acceptance:** `grep -rE "claude-(opus|sonnet|fable|haiku)-[0-9]" scripts/ hooks/` returns only `headless.py`'s legacy-fallback constants. The invariant test fails if `judgment` is set to `claude-sonnet-5` while S5 is active. `models_probe` files a P2 against a fake CLI that prints the deprecation warning.

## 3. F2 — bounded, metered sessions

**Required:**
1. `autopilot.json` → `limits`: per role `{max_turns, max_usd}`. Defaults: judgment 60 / 6.00, mechanical 20 / 1.00, distiller 10 / 1.00, cards 3 / 0.10, builder 200 / 15.00. `claude_argv` adds `--output-format json --max-turns … --max-budget-usd …`.
2. `headless.run(role, prompt, cfg, task_id=None, unit=None) -> HeadlessResult` parses the JSON result (`session_id`, `total_cost_usd`, `num_turns`, `subtype`, `is_error`) and appends to `.devteam/ledger.jsonl`: `{ts, role, unit, task_id, model, effort, session_id, cost_usd, turns, subtype, exit}`. A non-`success` subtype counts as failure for every existing ceiling. Non-Claude builders (codex, grok) get a ledger line from the dispatch runner with `model`/`effort` from the registry and `cost_usd: null`.
   - `team_stats.py` groups first-pass rate, rework causes and blocks by `(unit, model, effort)`, and the retro reports it.
   - Any change to a unit's `model` or `effort` in `autopilot.json` must carry a `_model_note` with a date and a reason (validated). That turns a model switch into an experiment with a before/after comparison (rwc SB-8: CX changed 4× in 2 days with no measurement, on the wrong diagnosis).
3. S5 dispatch (`dispatch.sh` and `dispatch.ps1`, identical behaviour): the claude row adds the same flags from `limits.builder`. The launched runner writes the ledger line on exit. Detached Windows windows write it too.
4. `board_publisher.py` and the P0 digest: per-task and per-role cost for the digest period.

**Acceptance:** a fake CLI returning `subtype: "error_max_turns"` increments the unit's failure counter. The ledger has one line per headless run, including a detached PS launch (live Windows evidence). The digest shows cost per task.

**Depends_On:** F1.

## 4. F3 — typed review verdicts and distiller output; strict control per unit

**Required:**
1. `schemas/review_verdict.json`: `{task_id, verdict: "approved"|"rework", reviewer_model: str, reviewer_unit: str, gate_sha: str, gate_result_path: str, territory_violations: [str], tests: {command, ran: bool, passed: int, failed: int}, findings: [str], merged_sha: str|null}`. `reviewer_model`/`reviewer_unit` feed Wave G-A's maker≠checker refusal. The review runs with `--json-schema`. The supervisor's reap reads the verdict: `rework` bumps `rework_counts`, and `tests.ran == false` on an `approved` verdict is a **P1** (a review that did not run tests must never merge silently). `.claude/commands/devteam-review.md` step 9 names the schema.
2. `schemas/distill.json`: `{instincts: [...], amendment: {title, rationale, diff}|null}`. `distiller.split_model_output` is retired. Deterministic lifecycle math unchanged.
3. Per-unit control mode: `builders.defined.<U>.control_mode` (`legacy`|`strict`, default = global `control.mode`). `dispatch.sh`/`.ps1` and `supervisor.maybe_drain_control` honour it per unit. For `cli: claude` units in strict mode, the CONTROL block is requested through `--json-schema` (`schemas/control_v1.json`, same fields as `control_version: 1`) instead of a fenced block, and `control.py extract` reads either. Ships with every unit on `legacy` (N4).

**Acceptance:** a verdict missing `tests` is rejected by the schema. A fake `approved` with `ran: false` raises P1 and does not count as a review. With S5 on strict and GB on legacy in one tick, S5's state arrives through CONTROL and GB's through its PLAN.md commit, and the validator stays green.

**Depends_On:** F2 (uses `headless.run`).

## 5. F4 — auto mode for Claude units (disabled by default)

**Required:** `autopilot.json` → `permissions.claude_mode`: `"bypass"` (today, default) | `"auto"`. When `"auto"`, `claude_argv` and the dispatch claude row emit auto permission mode plus `--permission-prompts none` instead of `--dangerously-skip-permissions`. An auto-mode denial surfacing in the JSON result is logged as `AUTO_DENIAL` and counted as a denial for the circuit breaker (same counter as gateguard).
**Live verification before enabling** (recorded in `docs/HOOKS.md`): (a) classifier billing on the account in use; (b) one S5 task end to end under auto; (c) a deliberate `DROP TABLE` against a scratch SQLite DB is refused and the builder escalates `blocked` rather than stalling.
**Acceptance:** argv tests for both modes. Default output is byte-identical to today.

**Depends_On:** F1.

## 6. F5 — builder identity without CLAUDE.md (disabled by default)

**Required:** `.claude/agents/devteam-builder.md` gains `omitClaudeMd: true` behind a registry flag `builders.defined.<U>.omit_claude_md` (default `false`). When true, dispatch appends the project's `CONVENTIONS.md` (new, ORCH-owned, created by onboarding from the non-ORCH parts of CLAUDE.md) to the prompt. The `IDENTITY OVERRIDE` preamble path is deleted once every Claude unit runs `identity: agent`. `sync-manifest.json` registers `CONVENTIONS.md` as project-owned.
**Live verification:** one S5 task with the flag on: the session transcript contains no CLAUDE.md content, and the builder still follows project conventions (reviewer judgment).

**Depends_On:** F1.

## 7. F6 — resume stale Claude sessions

**Required:** `REDISPATCH_STALE` for a `cli: claude` unit whose last ledger line for the task has a `session_id` launches `claude -p --resume <session_id> "<resume prompt>"` with the same limits. If that fails, it falls back to today's cold redispatch. `REDISPATCH_STAGNANT` **always** cold-starts (a stuck context is the thing to discard). Update `docs/BACKLOG.md` #2 to point here.
**Acceptance:** a fake CLI records that stale redispatch passed `--resume <id>` and stagnant redispatch did not. A resume failure falls back within the same tick.

**Depends_On:** F2.

## 8. F7 — advisor for the S5 builder (disabled by default)

**Required:** registry field `builders.defined.<U>.advisor_model` (default `null`). Step 1 is a live check of how a headless session sets its advisor on 2.1.283: the `/advisor <model>` text form the changelog lists for `-p`, or a settings key. Record the working form in `docs/BUILDER_REGISTRY.md` before writing code. **N5 enforcement:** `validate_plan.py --config` fails if `advisor_model` equals `models.roles.judgment.model`. The recommended pairing is advisor `claude-fable-5-1` with reviewer `claude-opus-5-5`. Enable per task via a `**Advisor:** yes` field (ORCH-owned), not globally.
**Acceptance:** the config check rejects advisor == judgment. The ledger shows advisor cost separately when the result exposes it.

**Depends_On:** F2, F5.

## 9. F8 — review as a saved dynamic workflow (optional)

**Required:** `.claude/workflows/devteam-review.js` with phases *territory*, *spec*, *tests*, *blind-verify* and *verdict*. Each is an `agent({schema})` call. The blind verifier receives only the original task block and the diff and is told to assume the change is broken until shown otherwise. The final agent emits `schemas/review_verdict.json`. Invoked headless through the allow rule `Workflow(devteam-review)`. Headless workflow agents fail rather than pause at a usage limit, so the supervisor treats a failed workflow review as **retry next tick**, never as rework.
**Acceptance:** on a fixture task with a planted out-of-territory file and a planted failing test, the verdict is `rework` with both findings. Cost per review from the ledger is recorded in Test_Evidence next to a single-session review of the same task.

**Depends_On:** F3.

## 10. F9 / F10 — ORCH-direct

- **F9:** run `/doctor prompt-audit` over CLAUDE.md, `.claude/agents`, `.claude/commands` and `briefings/`, and apply its findings. Also reorder both dispatch prompts so static procedure text comes first and per-unit variables come last (prefix-cache reuse across units). Move incident narratives out of prompts into `docs/`.
- **F10:** re-probe CX's model on the ChatGPT-account Codex (GPT-6 Sol shipped 2026-09-22) and pin GB's model. Change both through `autopilot.local.json` first, then the template.

## 11. Territories

| # | Territory (Owned_Paths) |
|---|---|
| F1 | `autopilot.json`, `scripts/headless.py`, `tests/test_headless.py`, `scripts/supervisor.py`, `scripts/distiller.py`, `scripts/atlas_cards.py`, `scripts/usage_probe.py`, `scripts/maintenance.py`, `scripts/validate_plan.py`, matching `tests/**` |
| F2 | `scripts/headless.py`, `tests/test_headless.py`, `scripts/dispatch.sh`, `scripts/dispatch.ps1`, `tests/test_dispatch_worktree.py`, `scripts/board_publisher.py`, `tests/test_board_publisher.py`, `autopilot.json` |
| F3 | `schemas/**`, `scripts/supervisor.py`, `scripts/distiller.py`, `scripts/control.py`, `scripts/builder_registry.py`, matching `tests/**` |
| F4 | `scripts/headless.py`, `scripts/dispatch.sh`, `scripts/dispatch.ps1`, `tests/test_headless.py`, `tests/test_dispatch_worktree.py` |
| F5 | `.claude/agents/devteam-builder.md`, `scripts/dispatch.sh`, `scripts/dispatch.ps1`, `scripts/builder_registry.py`, `sync-manifest.json`, matching `tests/**` |
| F6 | `scripts/supervisor.py`, `tests/test_supervisor.py` |
| F7 | `scripts/builder_registry.py`, `scripts/validate_plan.py`, `scripts/dispatch.sh`, `scripts/dispatch.ps1`, matching `tests/**` |
| F8 | `.claude/workflows/**`, `scripts/supervisor.py`, `tests/test_supervisor.py` |

Many increments share `supervisor.py`, `headless.py` or the dispatch scripts, so this wave is **mostly sequential**: F1 → F2 → (F3 ∥ F5) → F4 → F6 → F7 → F8. **v1.1:** F1 and F2 may run alongside Wave G if they are sequenced after G-A's `supervisor.py` changes, or before them with G-A rebasing. Because Wave E-0 ports oikonomos code into `supervisor.py` first, re-derive this file's line references at decompose time. F9 and F10 are ORCH-direct and can run any time after F1. Every row needs `Protected_Grants` (Wave E-A).

## 12. Exit criteria

Suites green. The model-ID grep in F1 is clean. Every headless run in a 24 h `--loop` window has a ledger line. The P0 digest shows cost per task. With F4/F5/F7 still disabled, observable behaviour is unchanged apart from the models, the flags and the ledger. Each of those three has a recorded live verification before any project enables it.

## 13. Note for the reviewer

Vendor-behaviour claims in this spec (flag names, JSON fields, advisor invocation form) come from the Claude Code changelog and docs as of 2.1.283. Where the implementing session's CLI disagrees, **the CLI wins**: record the observed form in the relevant doc and adjust, rather than coding to the spec text.
