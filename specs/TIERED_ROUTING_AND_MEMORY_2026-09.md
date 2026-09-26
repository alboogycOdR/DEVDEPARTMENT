# Tiered routing and evidence-based memory — spend tokens where they change outcomes

**Status:** SPEC v1 — Wave H. Decompose after Wave G. It needs the per-run ledger and role→model matrix from Wave F1/F2.
**Origin:** 2026-09-26 discussion following the field synthesis (`docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md`). The two standing struggles it addresses are token efficiency by model tier, and memory across sessions (short- to medium-term).
**Baseline:** Wave E, G, F1 and F2 merged. Claude Code ≥ 2.1.283 (agent `model:`/`effort:` frontmatter, `maxEffortLevel`, `/advisor` in headless sessions, `--json-schema`, `--resume`, `subagentPromptCacheTtl`, auto-memory).

## 0. What is already correct — do not rebuild it

- **Wave G's tier-0 scripts** (the gate, preflight, test runs) are the cheapest tier and stay first.
- **Maker ≠ checker** (G-A, F7's N5), extended here to advisors.
- **ATLAS** (FTS5 over files, symbols and episodes) is the retrieval substrate. H-C writes to it; it does not build a second store.
- **Dossiers** stay the per-task working memory; they were proven in oikonomos across a unit retirement.
- **The confidence math and the constitutional gate** stay: memory never edits protocol files.

## 1. Design rules

**T1 — Cheapest tier that can be checked.** A task goes to the lowest tier whose output can be *verified by code*. Unverifiable output needs a tier strong enough to be trusted, or it doesn't happen. LekkerSwot: Haiku narrated false causes when its prose was trusted.
**T2 — Escalate on evidence, not by default.** A higher tier is invoked because a signal fired (a stagnation streak, a failed gate, a rework, a novel task tag), never "just in case".
**T3 — Every tier pays rent.** The ledger measures cost and outcome per (role, model, effort). A tier that doesn't improve outcomes for its cost is demoted by config, with a dated note.
**M1 — Memory is mined from evidence, not narrated.** Facts come from REVIEW rows, gate results, block reasons and the ledger. Model prose may *phrase* a fact but never *originates* one.
**M2 — Retrieve; don't preload.** Nothing enters a builder's prefix unless it matched the task and fits the budget.
**M3 — Memory expires unless it helps.** A fact that doesn't improve the tasks it is injected into loses confidence and retires.
**M4 — Short-term memory is a session; medium-term memory is a fact.** Resume the session when you can. Otherwise distil to facts and dossiers, and don't carry transcripts forward.

---

## 2. H-A — role agents and the tier ladder

**Required:**
1. `autopilot.json` → `models.roles` (F1) gains the full ladder:

   | Role | Default model | Effort | Output contract |
   |---|---|---|---|
   | `gate` (tier 0) | none (scripts) | — | exit codes + JSON |
   | `fast` (tier 1) | `claude-haiku-4-5` (→ Haiku 5.5 on release, after a probe) | low | **schema-only** (`--json-schema`), validated by code |
   | `builder` (tier 2) | per unit (`claude-sonnet-5` / codex / grok) | medium | CONTROL block |
   | `advisor` (tier 3) | `claude-fable-5-1` | high | advice only, never a gate |
   | `judge` (tier 4) | `claude-opus-5-5` | high (decompose: xhigh) | verdict schema |

2. **Role agent files.** `.claude/agents/devteam-{fast,builder,reviewer,verifier,status}.md` each carry `model:`, `effort:`, `omitClaudeMd:` (F5) and a tool allowlist (`fast` gets read-only tools plus Bash restricted to named scripts). Headless calls use `--agent <role>` so model and effort come from one place. `headless.claude_argv(role)` (F1) resolves the agent file first, then the matrix.
3. **Fast-tier jobs** (initial set, each with a JSON schema and a code validator):
   - `classify_run_log` → `{kind: ok|capacity|quota|auth|crash|timeout, reset_at?, evidence_line}` (feeds G-E);
   - `repair_control_block` → a CONTROL block reconstructed from the run log, used only when the original failed schema **and** every field can be traced to a log line;
   - `digest_compose` → P0 digest text from structured inputs;
   - `atlas_card` → the ATLAS card for one changed file;
   - `triage_prefilter` → `{reason_prefix, needs_judge: bool, why}` for a blocked task. `needs_judge: false` resolves by rule; otherwise the task escalates to `judge`.

   **A fast-tier result that fails validation is retried once, then the job escalates one tier. It is never accepted "mostly right".**
4. `maxEffortLevel` is set per role in the generated `.claude/settings.json` fragment, so no session can silently exceed its tier.

**Acceptance:**
- Each fast job has a fixture corpus of at least 20 real (redacted) inputs from field logs and a validator. Accuracy is at least 95% on the corpus, or the job ships routed to `builder`-tier instead (T1), with the measured accuracy recorded.
- A malformed fast output escalates exactly once and is logged `TIER_ESCALATE`.
- `claude_argv('fast')` includes `--agent devteam-fast` and never `--model claude-opus-*`.

## 3. H-B — advisor on demand, with escalation triggers

**Required:**
1. Advisor calls are **triggered** (T2), not ambient. The dispatcher enables `/advisor <advisor model>` for a builder session only when one of these holds:
   - the task is tagged `Novel: yes` at decompose;
   - it is a stagnation redispatch (circuit breaker fired);
   - it is a rework round ≥ 2;
   - the task's `Priority: critical`.

   Each trigger is logged `ADVISOR_ON reason=…`. `advisor.max_uses` (default 3) per session caps the calls.
2. **Independence (extends F7's N5).** Advisor model ≠ `judge` model ≠ builder model for the same task. `validate_plan.py --config` enforces the matrix. If a unit's advisor would equal the judge, the task's review routes to the alternate judge (`models.roles.judge_alt`, default `claude-fable-5-1`), and the verdict records it.
3. Advisor cost is attributed separately in the ledger. The API reports advisor iterations separately; for CLI runs, record `advisor_calls` and let `cost_usd` carry the total.
4. **Non-Claude builders** (codex, grok) cannot host the advisor tool. For them, T2 triggers instead run a one-shot `advisor`-role session (via `headless.run`) that reads the task, dossier and last run log and writes `dossiers/<TASK>.md#advice`. The builder is re-dispatched with that section.

**Acceptance:** a fixture task with none of the triggers never gets an advisor. A stagnation redispatch gets one, logged with its reason. A config where advisor == judge fails validation. A codex task on rework round 2 gets a dossier advice section before redispatch.

## 4. H-C — memory v2: evidence-mined facts in ATLAS

Replaces `distiller.py`'s free-text instinct extraction. `INSTINCTS.md` becomes a rendered view.

**Required:**
1. **Fact schema** (new ATLAS table `facts`, FTS5-indexed):
   ```
   id, kind, scope, subject, statement, evidence[], created, last_seen,
   injections, wins, losses, confidence, status (active|probation|retired), origin_project
   ```
   - `kind` ∈ `hot_file | test_command | carve_rule | unit_tendency | env_quirk | spec_gotcha | review_catch`.
   - `scope` is `project` or `stack:<tag>` (see H-E).
   - `evidence` is a list of citations (REVIEW row IDs, commit SHAs, gate result paths, ledger IDs). **A fact with no evidence cannot be created (M1).**
2. **Miners** (deterministic code, no model):
   - `hot_file` from OWNERSHIP_CONFLICT counts (≥ 2 in 30 days);
   - `test_command` from gate configs that passed;
   - `unit_tendency` from rework causes grouped by unit and category (≥ 3);
   - `carve_rule` from G-D auto-grants that were applied;
   - `env_quirk` from `classify_run_log` kinds that repeat;
   - `review_catch` from rework findings whose category repeats.

   Miners run after each merged review and nightly.
3. **Phrasing only.** When a mined fact needs a human-readable statement, the `fast` tier phrases it under a schema that must quote the evidence subjects. The miner's structured fields stay authoritative.
4. **Retrieval (M2).** `atlas.py pack --task` (already the dispatch injector) adds a `## Relevant facts` section:
   - facts whose `subject` matches the task's Owned_Paths, their import neighbours, the unit, or the stack tags;
   - ranked by `confidence × recency`;
   - capped by `memory.inject_tokens` (default 600) and `memory.inject_max` (default 5).

   Each injection increments `injections` and is recorded in the ledger line for that dispatch.
5. **Effectiveness (M3).** At review time, every fact injected into the task gets `wins += 1` on a first-pass approve, or `losses += 1` on rework when the finding category matches the fact's kind or subject. `confidence` is a Beta posterior over wins and losses with the seed from the current confidence math (0.6). A fact under 0.35 after at least 5 injections moves to `probation`, where it is not injected. Retire it after 30 days in probation. Surface retirements in the retro.
6. **Constitutional gate unchanged.** A fact with confidence ≥ 0.85 over ≥ 10 injections may produce an AMEND proposal (as today), **de-duplicated per E-I**. Facts never edit protocol files.
7. **Migration.** Import existing `INSTINCTS.md` entries as `kind: review_catch`, `status: probation`, with `evidence: ["legacy:INSTINCTS.md#INST-NNN"]`. They earn their way back through H-C.5. Regenerate `INSTINCTS.md` from active facts, read-only with a header saying so.

**Acceptance:**
- On a fixture history with 3 OWNERSHIP_CONFLICTs on `ports.ts`, a `hot_file` fact exists with 3 evidence citations.
- A task touching `ports.ts` gets it injected within budget, and one that doesn't never does.
- After 5 injections and 4 losses the fact goes to probation and is no longer injected.
- Creating a fact without evidence raises an error.
- A distiller-style free-text path no longer exists (grep).

## 5. H-D — short-term memory: resume first, distil second

**Required:**
1. Extends F6. Every Claude-unit dispatch records `session_id` in the ledger. On a **same-task** redispatch within `memory.resume_window_hours` (default 24) and not a stagnation case, dispatch resumes the session (`--resume`). Otherwise it cold-starts with the dossier plus H-C facts.
2. **Session-end distillation to the dossier** (not to memory). The existing `session-end` hook appends a structured `## Session <n> summary` to the task's dossier: files touched (from git), tests run (from G-C testrun files), open questions, next step. It is composed from data, not model prose, with at most one `fast`-tier sentence for "next step" when the builder didn't write one. This is what the next session reads.
3. **Claude Code auto-memory is off for builders.** Generated settings for builder agents disable auto-memory, because unreviewed model-written memory would enter every future builder prefix (M1, M2). The interactive ORCH seat may keep it on. `docs/MEMORY.md` records the split and why.
4. Cache hygiene. `subagentPromptCacheTtl: "1h"` for workflow and review fan-outs. Static-first prompt ordering (F9). The dossier and facts section go **after** the static procedure text, so the procedure prefix is shared across units.

**Acceptance:** a stale redispatch within 24 h passes `--resume <id>`; one after 30 h cold-starts with the dossier summary present. A session end appends a summary whose "files touched" equals `git diff --name-only` for that session's commits. Builder settings have auto-memory disabled.

## 6. H-E — cross-project memory

**Required:**
1. **Stack tags.** `autopilot.json` → `stack: ["flutter", "firebase", "windows"]` (onboarding proposes it from lockfiles and platform).
2. **Shared store.** Facts with `scope: stack:<tag>` can be exported to a shared fact file, `~/.devteam/shared_facts.jsonl` or `sync.shared_facts_path`, via `atlas.py facts export --scope stack`. **Only kinds `test_command | env_quirk | carve_rule` and confidence ≥ 0.7 qualify. Never `unit_tendency` or `spec_gotcha`, which are project-specific.** The export strips project paths down to their pattern form (e.g. `lib/**/x_test.dart`).
3. **Import on onboarding and nightly.** Matching stack facts are imported as `status: probation`, `origin_project` recorded. They must win in *this* project to become active (M3).
4. **Privacy.** A fact's `statement` and `evidence` are scanned by `hooks/secret-scan.js` rules before export. Evidence citations are rewritten to `origin_project:<sha>` without content.

**Acceptance:** a `test_command` fact for Flutter (`--concurrency=2`) exported from a KERYX-shaped fixture is imported into a fresh Flutter fixture as probation, and activates after 3 wins. A `unit_tendency` fact is never exported. A fact containing an API-key pattern is refused.

## 7. H-F — cost and outcome reporting per tier

**Required:**
- The P0 digest and the retro gain a per-role table: sessions, tokens or cost, first-pass rate on tasks where the role participated, and escalations up and down.
- `team_stats.py --tiers` computes whether each tier is **paying rent** (T3):
  - fast-tier jobs: validation pass rate and the cost saved against running the same jobs on the builder tier;
  - advisor sessions: first-pass-rate delta against matched non-advisor tasks;
  - memory: win rate of injected facts against baseline.
- When a tier fails its rent test for 2 consecutive weeks, the retro proposes a config change (demote or disable) with the numbers. It never applies it automatically (N4).

**Acceptance:** fixture ledgers produce the table. A synthetic advisor with zero delta is flagged by the rent test and a config proposal is written; `autopilot.json` is unchanged.

---

## 8. Territories and order

| # | Owned_Paths | Depends_On |
|---|---|---|
| **H-A** | `.claude/agents/devteam-*.md`, `scripts/headless.py`, `scripts/fast_jobs.py` (new), `schemas/fast/**`, `tests/test_fast_jobs.py`, `tests/fixtures/fast/**`, `autopilot.json` | F1, F2, G-E (log classes) |
| **H-B** | `scripts/dispatch.sh`, `scripts/dispatch.ps1`, `scripts/supervisor.py`, `scripts/validate_plan.py`, `tests/test_dispatch_worktree.py`, `tests/test_supervisor.py`, `tests/test_validate_plan.py` | H-A, F7 |
| **H-C** | `scripts/atlas_core.py` (facts table), `scripts/atlas_facts.py` (new), `scripts/miners.py` (new), `scripts/atlas_pack.py`, `scripts/instincts.py` (render + migrate), `scripts/distiller.py` (retire path), `tests/test_atlas_facts.py`, `tests/test_miners.py` | H-A (fast phrasing), G-A (verdicts), E-I |
| **H-D** | `hooks/session-end.js`, `scripts/dispatch.sh`, `scripts/dispatch.ps1`, `scripts/supervisor.py`, `docs/MEMORY.md` (ORCH), `tests/test_session_end.js` | H-B, F6 |
| **H-E** | `scripts/atlas_facts.py` (export/import), `scripts/sync_from_pack.py` (onboard stack tags), `tests/test_facts_shared.py` | H-C |
| **H-F** | `scripts/team_stats.py`, `scripts/retro.py`, `scripts/board_publisher.py`, `tests/test_team_stats.py`, `tests/test_retro.py` | H-A, H-C |

Order: H-A → (H-B ∥ H-C) → (H-D ∥ H-E) → H-F. H-F's reporting can start early on the F2 ledger alone.

## 9. Exit criteria

Suites green on both CI platforms. On a replay of one real field project's last 50 tasks (oikonomos or KERYX, with redacted logs as fixtures):
- judge-tier sessions per task ≤ 1.2 (from ~5+ in oikonomos before Wave E);
- at least 30% of mechanical jobs handled at the fast tier with a validation pass rate ≥ 95%;
- advisor invoked on ≤ 25% of tasks, each with a logged trigger;
- every injected fact carries evidence, and at least one fact has retired itself on measured losses;
- the digest shows the per-tier rent table.

## 10. Note for the reviewer

This wave optimises cost, so it is easy to "prove" with a happy path. Acceptance evidence must include cases where cheaper is **wrong** and the system notices: a fast job that fails validation and escalates, a fact that loses and retires, and an advisor that doesn't help and is flagged. Savings without those cases don't count.
