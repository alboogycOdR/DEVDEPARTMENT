# DEVDEPARTMENT — End-to-End Improvement Review

**Date:** 2026-09-26
**Reviewed:** `github.com/alboogycOdR/DEVDEPARTMENT` @ `master` `b74d581` (2026-09-15, "stagnation circuit breaker")
**Suites at baseline (Linux sandbox):** `pytest` 1028 passed / 4 skipped in 46 s · `node hooks/run-tests.js` 36 passed / 0 failed
**Claude Code at review time:** 2.1.283
**Companion specs (ready for `/devteam-decompose`):**
- `specs/LOOP_HYGIENE_2026-09.md` (Wave E: defects in the current loop)
- `specs/CLAUDE_NATIVE_LEVERAGE_2026-09.md` (Wave F: model matrix and new Claude Code capabilities)

---

## 0. Summary

The architecture holds up. The blackboard, territorial isolation, the escalation contract and the checker-independence rule are all still right, and nothing released in the last two months makes them obsolete. Two things have moved underneath the system, though:

1. **The loop has defects that only show up when it runs unattended for hours.** The worst three: every P2 is re-sent on every tick while its condition persists, blocked `TOOLING_FAILURE` and `MISSING_DEPENDENCY` tasks launch a fresh Opus triage session every five minutes with no ceiling, and a P1 or wave-complete halt under PM2 becomes a restart loop that repeats the alert up to ten times.
2. **The model and CLI assumptions are two generations old.** Judgment runs on `claude-opus-4-8`. Since then Claude Opus 5 and **Claude Opus 5.5** (22 Sep, $4/$20 per MTok, 1M context) have shipped, as has **Claude Fable 5.1** (1 Sep, $10/$50). Claude Code has also added most of what the pack builds by hand or lists as deferred: structured output (`--json-schema`), hard session caps (`--max-turns`, `--max-budget-usd`), unattended auto mode (`--permission-prompts none`), `omitClaudeMd` for agents, headless resume fixes, `/advisor` in headless sessions, and dynamic workflows with schema-typed `agent()` calls.

### Top 12, ranked by value per unit of effort

| # | Item | Type | Impact | Effort | Wave |
|---|---|---|---|---|---|
| 1 | P2 escalations re-sent every tick (no de-duplication) | Defect | Phone spam; `/mute` used as a workaround hides real P2s | S | E1 |
| 2 | Uncapped Opus triage loops on `TOOLING_FAILURE` / `MISSING_DEPENDENCY` | Defect | 12 judgment sessions/hour per blocked task, overnight | S | E1 |
| 3 | PLAN.md is 199 KB with 21 of 21 tasks `done` | Cost | ≈50k tokens read cold by every builder, review and triage session | M | E3 |
| 4 | Halt (P1 / wave complete) exits 0, so PM2 restarts it and repeats the alert | Defect | Up to 10 duplicate P1s or digests; `/resume` only works by accident | S | E2 |
| 5 | Hooks resolve the repo root to the **worktree**, not the main checkout | Defect | Circuit-breaker denial signal never reaches the supervisor; grants need a master merge | S | E4 |
| 6 | `REVIEW_TG` and `TRIAGE_UNBLOCK` use the slash-prompt form the repo itself recorded as a no-op in `-p` | Defect | `/approve` from Telegram and auto-triage may do nothing | S | E1 |
| 7 | One role→model matrix in `autopilot.json`; move judgment and planning to `claude-opus-5-5` (Fable 5.1 as an escalation seat), pin `--effort` | Upgrade | Better reviews at lower cost; model IDs today live in ~30 files | M | F1 |
| 8 | `--output-format json` + per-task cost ledger; `--max-turns`/`--max-budget-usd` on every headless call | Upgrade | Runaway sessions become clean, already-handled failures; cost becomes visible | M | F2 |
| 9 | `--json-schema` review verdicts and distiller output | Upgrade | Supervisor stops inferring verdicts by re-reading PLAN.md | M | F3 |
| 10 | Auto mode + `--permission-prompts none` instead of `--dangerously-skip-permissions` for Claude units | Upgrade | Second mechanical safety layer (answers BACKLOG #6 for S5 and headless ORCH) | M | F4 |
| 11 | Firewall grants as a task field, not code edits to `hooks/lib.js` | Defect/Design | Removes a merge-master ritual and a manual cleanup step on every self-hosting task | M | E4 |
| 12 | Heartbeat from git commit time, not LLM-written `Updated_At` | Defect | Stale detection is currently disarmed (open item in your own `orchestrator_notes`) | S | E2 |

---

## 1. What changed in the last two months

### 1.1 Models

| Model | ID | Released | List price (in/out per MTok) | Context | Where DEVDEPARTMENT uses its role today |
|---|---|---|---|---|---|
| **Claude Opus 5.5** | `claude-opus-5-5` | 22 Sep 2026 | $4 / $20, cache reads $0.20 | 1M | Judgment is pinned to `claude-opus-4-8` |
| Claude Opus 5 | `claude-opus-5` | between Sonnet 5 and Fable 5.1 | — | 1M | — |
| **Claude Fable 5.1** | `claude-fable-5-1` | 1 Sep 2026 | $10 / $50, cache reads $0.25 | 1M | Decompose is pinned to `claude-fable-5` |
| Claude Sonnet 5 | `claude-sonnet-5` | by Jul 2026 | $2 / $10 (promo pricing is now the list price) | 1M | S5/S5B builders, distiller |
| Claude Sonnet 5.5 / Haiku 5.5 | — | "in the coming weeks" per the Opus 5.5 announcement | — | — | Watch: changes the S5 parity question |

Relevant claims from the announcements (vendor-reported; treat as directional):
- Opus 5.5: Terminal-Bench 4.0 66.4%; "40% less to run on typical workloads" than Opus 5; "more than 30% faster" output; improved prompt-injection resistance.
- Fable 5.1: Terminal-Bench 4.0 55.8% (Fable 5: 42.0%); roughly 25% cheaper on typical workloads, up to ~45% on agentic ones; far fewer false-positive refusals.

Implication: on Terminal-Bench 4.0 as reported in each announcement, **Opus 5.5 (66.4%) beats Fable 5.1 (55.8%) at 40% of the price**. That inverts the current table, where Fable is the "highest-leverage judgment" seat. Section 3.1 has the recommended re-seat.

### 1.2 Claude Code (2.1.2xx → 2.1.283)

Only what bears on this system. All of it is quoted from the upstream `CHANGELOG.md` and the docs.

| Capability | What it replaces or enables here |
|---|---|
| `--json-schema` for `-p` (plus fixes for invalid-schema and re-call loops) | Typed review verdicts, distiller output, S5 CONTROL blocks |
| `--output-format json` / `stream-json` | Session ID, cost, turn count per headless run → cost ledger, resume |
| `--max-turns`, `--max-budget-usd` (now also halts background subagents) | Hard ceilings on every headless session |
| `claude -p --resume` fixes (cost totals preserved, thinking no longer dropped, no spurious continue turn) | BACKLOG #2's trigger is effectively met for stale redispatch |
| Auto mode with server-side classifier; `--permission-prompts none` "for unattended headless hosts"; dangerous-`rm` auto-deny after 2 min; Containment Escape rule | A real second layer behind the hooks for Claude units (BACKLOG #6) |
| `omitClaudeMd` in agent frontmatter and `--agents` JSON; `--agents` accepts a JSON file with `-p` | Removes the ORCH-identity problem for S5 at the root |
| `/advisor <model>` text form in headless sessions; the API advisor tool (`advisor_20260301`) | Sonnet 5 builder can consult a stronger model mid-task at advisor rates |
| Dynamic workflows: `.claude/workflows/*.js`, `agent(prompt, {schema})`, `parallel()`, `pipeline()`, run in `-p` behind a `Workflow(<name>)` allow rule | Parallel, schema-typed review and blind-verifier fan-out |
| `effort:` frontmatter now honoured on pinned-effort models; `maxEffortLevel`; Opus 4.8 / Fable 5 **no longer hold launch-default effort in `-p`** | A settings-level `effortLevel` can now silently lower review effort; pin `--effort` |
| Deprecation warning on stderr in `-p` when a model is deprecated or auto-updated (also for agent-frontmatter models) | Cheap nightly model-drift check |
| `/doctor prompt-audit` (2.1.283) audits CLAUDE.md, skills, agents and commands for prompting patterns written for older models | Free audit of briefings, the builder agent and commands |

### 1.3 Other builders (for completeness)

- **CX:** OpenAI shipped GPT-6 Sol and Luna into Codex on 22 Sep. `autopilot.json` and `.codex/config.toml` still pin `gpt-5.6-terra` (probe-verified 2026-08-13). Re-probe the account's `models_cache.json` before the next wave.
- **GB:** `model: null`, so Grok picks its own default. Pin it, so a vendor-side default change shows up as a config diff instead of a behaviour change.

---

## 2. Defects in the current source

Each item names the code, the failure it produces, and the fix. Wave E's spec carries the acceptance criteria.

### D1 — Every P2 is re-sent on every tick while its condition persists
`scripts/supervisor.py:311–337` appends `ESCALATE_P2` every time `decide()` sees a `blocked` task with `SPEC_AMBIGUITY`, a repeated `OWNERSHIP_CONFLICT`, or an unknown reason. The same holds for the stale (`:350–354`) and stagnant (`:381–386`) escalations once their reset budget is spent. `execute()` (`:561–565`) notifies unconditionally unless muted. Nothing records "already told the human".
**Failure:** a task blocked on a spec question at 23:00 produces 12 identical Telegram messages an hour until answered. `/mute` then suppresses *all* P2s, including new, different ones.
**Fix:** `RuntimeState.escalations: {key → {first_ts, last_sent_ts, digest}}` with `key = kind|task_id|reason-prefix`. Send on first occurrence and when the detail changes. After that, re-send only every `escalation.renotify_hours` (default 4) and list it in the P0 digest as "still waiting". Clear the key when the condition disappears. P1 keeps its current behaviour (it halts anyway, see D4).

### D2 — Uncapped judgment-model triage on two blocked reasons
- `TOOLING_FAILURE` (`:332–335`) decides between retry and escalate on `state.stale_resets`, but the `TRIAGE_UNBLOCK` executor (`:613–622`) only ever increments `conflict_counts`, and only for `OWNERSHIP_CONFLICT`. The counter the decision reads is never incremented by the action it gates, so the "retry once" path repeats forever.
- `MISSING_DEPENDENCY` (`:329–331`) has no counter at all.
**Failure:** each tick launches a headless `claude -p … --model claude-opus-4-8` triage session (`:622`). Rough order of magnitude with Opus 5.5 pricing and a ~60k-token cold start (PLAN.md alone is ~50k, see D11): on the order of $0.30–0.60 per session, so roughly $4–7 an hour per blocked task, or the equivalent share of a Max usage window. Your own `specs/L2_DISPATCH_RESILIENCE.md` rule L2 ("every repeat-failure class needs a ceiling") already covers this.
**Fix:** `state.triage_counts[task_id]` incremented in the executor for every triage kind. Escalate P2 once it reaches `max_triage_attempts` (default 1). Add a test that runs two ticks against a `TOOLING_FAILURE` fixture and asserts exactly one triage launch plus one P2.

### D3 — Three different prompt forms for three headless judgment calls
- `autopilot.json` `review_cmd` uses the explicit "Read `.claude/commands/devteam-review.md` and execute…" form. `_review_cmd_note` records that `/devteam-review` as a `-p` prompt was a **no-op** when verified live on 2026-08-13.
- `DEFAULT_CONFIG["review_cmd"]` (`:101`) still ships the broken slash form, and `tests/test_supervisor.py:262` pins it. Any project without a `review_cmd` key inherits the no-op.
- `REVIEW_TG` (`:596`) sends `"/devteam-review TASK-NNN"` and `TRIAGE_UNBLOCK` (`:619`) sends `"/devteam-status then triage…"`.
**Failure:** Telegram `/approve` and every auto-triage may launch a session that does nothing and exits 0. The supervisor counts it as a successful review (`reviews_since_distill += 1`).
**Fix:** a single `judgment_prompt(command, args)` helper that renders the explicit read-and-execute form, used by all three call sites. One live smoke test in `harness-audit.sh` that runs the rendered prompt with `--max-turns 1` and asserts the session read the command file. (The changelog fixed "unknown slash commands silently doing nothing in headless mode" long before 2026-08-13, so re-verify on 2.1.283. Either way the three call sites should agree.) Also fix the stale comment at `:100` ("review uses sonnet-5"), which contradicts the line below it.

### D4 — Halting exits the process; PM2 turns that into an alert loop
`execute()` returns `halt=True` for `HALT`, `ESCALATE_P1` and `DIGEST`. `main()` breaks and `return 0` (`:1490`). `deploy/ecosystem.config.js` has `autorestart: true, max_restarts: 10`. PM2 restarts on any exit unless the code is in `stop_exit_codes`.
**Failure:** under PM2, a protocol-illegal PLAN.md sends P1, exits, restarts, sends P1 again, up to 10 times. A completed wave sends its digest up to 10 times. The Telegram listener dies with each exit, so `/resume` and `/status` are unreachable between restarts. They only appear to work because PM2 brings the process back.
**Fix:** a **parked** state instead of exiting. On P1 or wave complete, set `state.parked = {reason, since}`, keep the listeners and command drain running, and skip `decide()`/`execute()` until `/resume` clears it (or, for P1, until `validate_plan.py` passes again). Exit only on `STOP`, `--max-ticks` or `--budget-minutes`, and use a distinct exit code (3) that `ecosystem.config.js` lists in `stop_exit_codes`.

### D5 — Hooks read and write the worktree, not the main checkout
`hooks/lib.js:17–20` resolves `repoRoot()` from `CLAUDE_PROJECT_DIR`, which is the **worktree** for every builder session (dispatch runs `cd "$WT"`). Consequences:
- `gateguard.js:197` writes denial counters to `<worktree>/.devteam/gateguard/denials/`. `supervisor._gateguard_denials()` (`:835–843`) reads `<repo>/.devteam/…`. The circuit breaker's denial signal, half of its design, never arrives. `.devteam/` is gitignored, so nothing syncs it.
- `territory-firewall.js:92` reads `<worktree>/PLAN.md`, the task branch's copy, not the live blackboard on `master`.
- `PROTECTED_EXCEPTIONS` is read from the worktree's copy of `lib.js`. That is why commit `bbfc3eb` exists: *"merge: master into TASK-022 (Owned_Paths widening + firewall grants)"*. The builder had to merge master to see a grant ORCH had already committed.
**Fix:** resolve the main checkout with `git rev-parse --git-common-dir`, exactly as `scripts/plan_commit.sh` already does (its 2026-08-16 fix describes this same bug class). Use it for PLAN.md, `autopilot.json`, `.devteam/` state and grants. Keep path relativisation (`relPath`) against the worktree, because that is where the edit lands.
**Also check:** `.claude/settings.json` is not tracked in this repo, so a worktree session only gets hooks through whatever user-level settings wire them on your machine. Confirm which file does, because D5's fix only matters where the hooks actually fire.

### D6 — Per-task firewall grants are code edits
`hooks/lib.js:181` `PROTECTED_EXCEPTIONS` is a hand-maintained array. Every self-hosting task that touches `scripts/**`, `docs/**` or `hooks/**` needs an ORCH commit adding a grant, a builder merge of master (D5), and an ORCH cleanup commit (`91d9e1d` → `12c700f` for TASK-022, and the same for TASK-019/020/021 per the comments). In DEVDEPARTMENT most tasks are self-hosting, so this is the common path, not the exception.
**Fix:** an ORCH-owned `**Protected_Grants:**` task field (a subset of that task's `Owned_Paths`). `validate_plan.py` enforces that only ORCH writes it and that it is a subset of `Owned_Paths`. The firewall reads it from the main-checkout PLAN.md (D5). Grants expire automatically when the task leaves `claimed`/`in_progress`/`needs_review`. The array stays for permanent project-level exceptions only.

### D7 — Reviews block the tick, and N pending reviews launch N review sessions
`decide()` emits one `REVIEW` per `needs_review` task (`:302–309`). `execute()` runs `review_cmd` synchronously for each (`:578–591`), and `review_cmd` reviews *every* `needs_review` task.
**Failure:** with two tasks awaiting review, session 1 reviews both and session 2 finds nothing, a wasted Opus cold start. While a 20–40-minute review runs, the tick is blocked: no dispatch reaping, no stale detection, no Telegram drain. A `/stop` waits for the review to finish.
**Fix:** emit at most one `REVIEW` per tick, scoped by task ID and passed as the prompt argument. Launch it in the background exactly like `DISPATCH` (`launch_shell_bg` plus an `inflight_reviews` map reaped next tick). Do the rework accounting at reap time. With F3, it reads the typed verdict instead of re-parsing PLAN.md.

### D8 — Runtime state is written non-atomically and corruption is silent
`RuntimeState.save()` (`:201–202`) is a plain `write_text`. `load()` (`:197–198`) swallows `JSONDecodeError` and returns an empty state.
**Failure:** a crash or power loss mid-write (Windows laptop) silently resets `rework_counts`, `stale_resets` and `dispatch_failures`. The `max_rework` guard, the stale ceiling and the parked-unit ceiling all start over with no log line.
**Fix:** temp file plus `os.replace` (the pattern already exists in `instincts.save_atomic`). On a decode error, move the bad file to `.autopilot_state.corrupt-<ts>.json`, log it, and send one P2.

### D9 — `--dry-run` mutates persisted state
`decide()` writes `state.stagnation_counts` (`:376–377`), and `main()` saves state even in dry-run (`:1448`). A dry-run preview advances the real stagnation streak, and `decide()` is no longer the pure function its docstring promises.
**Fix:** compute streak updates as returned values applied by `execute()`, or skip `state.save()` when `--dry-run` is set.

### D10 — Stale detection trusts timestamps written by the model
In legacy mode, step 4 reads `Updated_At` (`:342`). That string is composed by the LLM. `orchestrator_notes` already records that "builders/ORCH both stamp plausible-looking but wrong UTC, disarming stale-heartbeat detection" (e.g. CX's `17:03:00Z` entries on TASK-002, which were local time).
**Fix:** heartbeat = max(`Updated_At`, last commit time on the task branch in the unit's worktree, dossier mtime). `_stagnation_signal()` already shells into that worktree every tick, so this adds one `git log -1 --format=%ct`. Also have `plan_commit.sh`/`.ps1` stamp `Updated_At` from the system clock when the committed block's value is older than the commit, the fix your notes already recommend.

### D11 — The blackboard is mostly history
`PLAN.md` is 199,116 bytes. All 21 task blocks are `done`. Every builder session ("Read … PLAN.md, fresh from disk"), every review and every triage session reads it cold. That is about 50k tokens before any work starts, plus `COORDINATION_PROTOCOL.md` (20 KB), the briefing (18 KB) and, for reviews, `REVIEW.md` (49 KB).
**Fix:** `scripts/plan_archive.py` moves `done` blocks older than the current wave into `plan/archive/YYYY-MM.md` (append-only, committed), leaving one index line per task. `validate_plan.py` and `_deps_done` treat archived IDs as `done` for `Depends_On`. Run it from the nightly maintenance step and at wave close. Do the same rollover for `REVIEW.md`: keep the per-unit tallies on top and archive rows past N. Expected saving: roughly 45k tokens per session at current size, and it grows with every wave.

### D12 — Smaller items
- `README.md` H1 still says **v4.2**. The changelog runs to v4.8, and ATLAS and the circuit breaker (v4.9+) are not listed. Test counts in README and `onboard.md` (944/36) are behind the suite (1028/36).
- `DEFAULT_CONFIG["dispatch_cmd"]` still carries the frozen legacy `_DISPATCH_DEFAULTS` map (`:91`, `:109`), which `load_config` merges into every config. It is harmless today because `dispatch_cmd_for` prefers explicit entries, but the next person to read `cfg["dispatch_cmd"]` gets the frozen 3-unit map the v4.8 fix meant to retire.
- The test runtime is fine on Linux (46 s), but builders on Windows and Codex hit a **125 s command window** before the full suite finishes (TASK-002 notes). Add `pytest-xdist` (`-n auto`) as an optional speed-up and state the full-suite command, with its expected runtime, in each briefing.

---

## 3. Opportunities from the new capabilities

### 3.1 One role→model matrix, re-seated on current models (F1)

Model IDs today live in `supervisor.py`, `autopilot.json`, `builder_registry.py`, `distiller.py`, `atlas_cards.py`, `CLAUDE.md`, two command files, five docs and six test files. This is item 2 of your own Wave D proposal ("a dated, re-derivable model/price capability matrix replacing hardcoded assumptions").

Add to `autopilot.json`:

```json
"models": {
  "_verified_at": "2026-09-26",
  "roles": {
    "planner":    { "model": "claude-opus-5-5",   "effort": "xhigh" },
    "judgment":   { "model": "claude-opus-5-5",   "effort": "high"  },
    "mechanical": { "model": "claude-sonnet-5",   "effort": "medium"},
    "distiller":  { "model": "claude-sonnet-5",   "effort": "medium"},
    "cards":      { "model": "claude-haiku-4-5",  "effort": null    }
  },
  "escalation_planner": { "model": "claude-fable-5-1", "effort": "high" }
}
```

Rationale for each seat:
- **Judgment → `claude-opus-5-5`.** It is a stronger reviewer than `opus-4-8`, listed at $4/$20 with 1M context, so a full territory diff, the spec and the test summary fit comfortably. Checker independence still holds: the reviewer (Opus 5.5) is not the S5 builder's model (Sonnet 5). Re-check the day Sonnet 5.5 ships if S5 moves to it: a different tier is still independent, but record the decision in `MODEL_DISCIPLINE.md`.
- **Planner → `claude-opus-5-5` at `xhigh`, with `claude-fable-5-1` as an escalation seat.** On Terminal-Bench 4.0 as reported by Anthropic, Opus 5.5 is ahead at 40% of Fable 5.1's price. Keep Fable 5.1 for waves you tag as architecturally novel, and let `team_stats` evidence decide whether the escalation seat earns its price.
- **Mechanical → `claude-sonnet-5`.** $2/$10 list and the current Claude Code default model. Status scans and log appends don't need more. (Keeping `claude-sonnet-4-6` here is also fine; the point is that the seat is named once, in the matrix.)
- **Cards → `claude-haiku-4-5`.** ATLAS cards are one call per changed file and a summary, not a judgment. Move to Haiku 5.5 when it ships.
- **Pin effort explicitly on every headless call.** Since 2.1.280, Opus 4.8 and Fable 5 no longer hold their launch-default effort in `-p` over a settings-level `effortLevel`. A user-level setting can therefore silently lower review effort today.

Then: every call site reads `models.roles.<role>`; tests assert the invariant (judgment model ≠ any active builder's model), not a literal ID; and a nightly maintenance step runs one `--max-turns 1` probe per role and files a P2 when stderr carries the new "model is deprecated / automatically updated" warning.

### 3.2 Every headless session bounded and metered (F2)

Add to every `claude -p` the pack launches (review, triage, distiller, cards, S5 dispatch):

```
--output-format json --max-turns <limits.<role>.max_turns> --max-budget-usd <limits.<role>.max_usd> --effort <models.roles.<role>.effort>
```

- The JSON result carries `session_id`, `total_cost_usd` and `num_turns`. Append one line per session to `.devteam/ledger.jsonl` as `{ts, role, unit, task_id, model, cost_usd, turns, exit}`. Surface per-task cost on the board and in the P0 digest. That also answers BACKLOG #1 ("per-task cost, not just window %") without a second usage probe.
- A session that hits `--max-turns` or `--max-budget-usd` ends with a non-`success` result subtype in the JSON output. Have the reaper treat that as a failed run, so it flows into the ceilings you already built (`dispatch_failures`, stale and stagnation resets). A runaway becomes a handled failure instead of a silent bill.
- On Windows `dispatch.ps1` launches detached, so the ledger line is written by the launched runner script, not by the supervisor.

### 3.3 Typed outputs instead of parsed prose (F3)

- **Review verdict.** Run review with `--json-schema` for `{task_id, verdict: approved|rework, territory_violations[], tests: {ran, passed, failed, command}, findings[], merged_sha|null}`. The supervisor reads the verdict directly. Rework accounting and the D7 reap become exact, and "the review session exited 0 but did nothing" (D3) becomes impossible to miss because the schema requires `tests.ran`.
- **Distiller.** Replace the `## PROPOSED AMENDMENT` header split (`distiller.split_model_output`) with a schema `{instincts[], amendment|null}`. The deterministic confidence math is untouched, since it already runs before the model call.
- **S5 in strict control mode.** Emit the CONTROL block as the schema'd final output. `control.mode=strict` has shipped disabled since Wave I because free-text fences were unreliable. For Claude units, structured output removes that risk, so strict can be enabled for S5 first while GB and CX stay on legacy. Mixed mode is not supported today: it needs a per-unit `control_mode` in the registry, specified in F3.

### 3.4 Auto mode as a second mechanical layer for Claude units (F4)

Replace `--dangerously-skip-permissions` (S5 dispatch, review, triage, distiller, cards, usage probe) with auto permission mode plus `--permission-prompts none` (the flag the changelog adds "for unattended headless hosts"):
- The classifier blocks what the hooks cannot see, such as a `psql DROP`, a `kubectl delete` or cloud credential fetches (the new Containment Escape rule). That is exactly BACKLOG #6's trigger, for S5 and headless ORCH. GB and CX keep the prose rule.
- Dangerous `rm` is denied after 2 minutes with a rewrite hint instead of executing, and headless runs continue.
- The hooks stay first in line. They are deterministic; the classifier is a backstop.
- **Verify before flipping (ask-don't-auto-flip):** whether the server-side classifier is free on your Max subscription or counts toward the usage window (the changelog says free "for Claude API and Enterprise users"), and whether an auto-mode denial mid-build shows up as a `blocked` escalation or as a quiet stall. The circuit breaker would catch the latter once D5 is fixed.

### 3.5 Builder identity without CLAUDE.md (F5)

`.claude/agents/devteam-builder.md` currently spends a section explaining that CLAUDE.md's ORCH section "describes your counterpart, not you", and `dispatch.sh` keeps the preamble path that Sonnet 5 refused as prompt injection. Add `omitClaudeMd: true` to the agent frontmatter. Move the only builder-relevant CLAUDE.md content (protected paths, git conventions; in onboarded projects, the stack and coding conventions) into the briefing or a small `@CONVENTIONS.md` the agent imports. Result: no identity tax, no injection-shaped preamble path to maintain, and about 2k fewer tokens per S5 session.

### 3.6 Resume stale sessions instead of cold redispatch (F6)

With `session_id` captured (3.2), `REDISPATCH_STALE` for a Claude unit becomes `claude -p --resume <session_id> "Continue TASK-NNN from your last dossier entry."`. The fixes listed in 1.2 (cost totals kept, thinking kept, no spurious turn) were the reasons BACKLOG #2 deferred this.
- **Keep cold redispatch for `REDISPATCH_STAGNANT`.** A session that is alive but stuck will stay stuck with its own context. Stagnation is the case where a fresh context is the point.
- Cold redispatch stays the fallback when resume fails (session pruned, different host).

### 3.7 An advisor for the S5 builder, chosen so the reviewer stays independent (F7)

The advisor pattern (Sonnet 5 executor consulting a stronger model mid-task, billed at advisor rates only for the ~1.5k-token consultations) fits S5's "mostly mechanical, occasionally needs a plan" profile. It is available in headless via `/advisor <model>`.
**The constraint that matters here:** if S5's advisor is Opus 5.5 and the reviewer is Opus 5.5, the builder's key decisions were shaped by the checker's own model. That quietly erodes the independence rule. Choose one:
- advisor = `claude-fable-5-1`, reviewer = `claude-opus-5-5` (independent, higher advisor cost), or
- advisor = `claude-opus-5-5`, and review for advisor-assisted tasks runs on `claude-fable-5-1` (record the pairing in `MODEL_DISCIPLINE.md`).
Gate it per task (`Priority: critical`, or tasks that already bounced once), not globally.

### 3.8 Review as a saved dynamic workflow (F8, optional, after F3)

`.claude/workflows/devteam-review.js` would give, per `needs_review` task: a territory-audit agent, a spec-verification agent (reads the spec, returns quoted passages), a test-run agent and a **blind verifier** that gets only the original task text and the diff and defaults to "assume broken" (item 3 of your Wave D proposal). Each is an `agent({schema})` call, with a final `agent()` that merges them into the F3 verdict schema.
- Runs in `-p` if `Workflow(devteam-review)` is in the allow rules. Headless runs **do not** pause at a usage limit (the agent fails instead), so the supervisor must treat a failed workflow as a retryable review, not a rework.
- Worth it only once F3 exists. Today's single-session review with a test-running subagent is adequate.

### 3.9 Prompts written for older models (F9, free)

The dispatch prompts (`dispatch.sh:216`, `:224`), the briefings and several command files lean on all-caps warnings, repeated "NEVER"s and incident narration ("five builder sessions have died exactly there"). Current models follow instructions more literally and can over-apply shouted rules. Run `/doctor prompt-audit` (added in 2.1.283) across CLAUDE.md, `.claude/agents`, `.claude/commands` and `briefings/`, and take its findings into F9. Two concrete edits even without the audit:
- **Put static text first.** Both dispatch prompts open with per-unit variables (`You are $ID … Working directory: $WT …`) before ~2k characters of identical procedure. Moving the variable lines to the end lets the procedure prefix be shared across units and sessions.
- **Keep incident history in `docs/`.** The prompt needs the rule ("run verification in the foreground and wait"), not the story behind it.

### 3.10 Non-Anthropic builders (F10)

Re-probe CX against GPT-6 Sol on the ChatGPT-account Codex, since `gpt-5.6-terra` was picked by probe and the lineup changed on 22 Sep. Pin GB's model. Both land as config diffs through `autopilot.local.json` first, per TASK-022.

---

## 4. Proposed sequencing

| Wave | Contents | Why this order | Concurrency |
|---|---|---|---|
| **E — Loop hygiene** | D1–D12 | Pure defects; no dependency on any vendor feature; makes unattended L2 safe to leave overnight | 5 disjoint territories (see spec) |
| **F — Claude-native leverage** | F1 model matrix → F2 bounded/metered → F3 typed outputs → F4 auto mode → F5 omitClaudeMd → F6 resume → F7 advisor → F8 workflow review → F9 prompt audit → F10 re-probe | F1 first because every later increment reads the matrix; F2 before F6 because resume needs `session_id`; F4/F5/F7 each gated by a live verification, per the ask-don't-auto-flip rule | F1 → (F2 ∥ F5 ∥ F9 ∥ F10) → (F3 ∥ F4) → F6 → F7 → F8 |

Wave D items not covered here (capability-class routing, the four-status report contract, the citation taxonomy, seat step-down) still stand. F1's matrix and F3's typed verdict are the substrate they need, so do E and F first.

## 5. What to keep as it is

- **Cross-CLI blackboard over native agent teams** (BACKLOG #4). Nothing released changes the reasoning: agent teams are still Claude-only and in-session.
- **Fail-open signals, fail-closed identity.** Every recommendation above preserves the asymmetry (for example, D5 resolution falls back to the old behaviour if git cannot answer).
- **The constitutional gate.** F3's typed distiller output still lands in `.devteam/pending_amendments/` only.
- **Checker ≠ maker.** It is restated in F1 as a tested invariant rather than a pinned literal, and F7 extends it to advisors.

## 6. What was verified, and what was not

**Verified here:** every file and line reference above against `b74d581`; both suites green at baseline; model IDs, prices and release dates against Anthropic's announcement pages and the upstream Claude Code `CHANGELOG.md`; advisor-tool and workflow behaviour against the official docs.

**Not verifiable from here (needs your machine):**
- whether `/devteam-review`-style slash prompts expand in `-p` on 2.1.283 (D3's fix is correct either way);
- auto-mode classifier billing on a Max subscription (F4);
- `dispatch.ps1` detached-window behaviour with `--output-format json` (F2 ledger writing on Windows);
- CX model availability on your ChatGPT-account Codex (F10).

---

## Sources

- [Introducing Claude Opus 5.5 — Anthropic](https://www.anthropic.com/claude-opus-5-5)
- [Introducing Claude Fable 5.1 and Claude Mythos 5.1 — Anthropic](https://www.anthropic.com/claude-fable-and-mythos-5-1)
- [Anthropic releases Opus 5.5 with lower prices and Fable-level performance — TechCrunch](https://techcrunch.com/2026/09/22/anthropic-releases-opus-5-5-with-lower-prices-and-fable-level-performance/)
- [Claude Code CHANGELOG.md — anthropics/claude-code](https://github.com/anthropics/claude-code/releases) (raw file read at 2.1.283)
- [Claude Code changelog — Claude Code Docs](https://code.claude.com/docs/en/changelog)
- [Orchestrate subagents at scale with dynamic workflows — Claude Code Docs](https://code.claude.com/docs/en/workflows)
- [Advisor tool — Claude Platform Docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/advisor-tool)
- [OpenAI launches GPT-6 Sol and Luna minutes after Anthropic drops Claude Opus 5.5 — Decrypt](https://decrypt.co/378986/openai-launches-gpt-6-sol-luna-anthropic-claude-opus-5-5)
