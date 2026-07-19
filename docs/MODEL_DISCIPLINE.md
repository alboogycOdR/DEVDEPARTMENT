# Model discipline — decision record & rationale

`CLAUDE.md` carries the **table** (which model for which operation) because ORCH needs it in every session. This file carries the **reasoning**, because rationale is read once when you're deciding whether the rule still makes sense — not on every turn. See "Prefix hygiene" below for why that split matters.

---

## Decision, 2026-07-19: judgment rows moved off `claude-sonnet-5`

**Context.** The S5 builder unit (added in `c69cdda`) runs `claude-sonnet-5` headlessly. Before S5 existed, ORCH's judgment rows running on sonnet-5 posed no parity problem: GB is Grok, CX is Codex, so the reviewer was already a different model from both makers, with plausibly different blind spots.

**The problem S5 introduced.** When sonnet-5 reviews sonnet-5's work, the reviewer shares the maker's exact failure distribution — the same rationalizations read as plausible, the same edge cases don't come to mind, the same subtly-wrong pattern looks idiomatic. Role separation (interactive ORCH vs. headless builder, different briefings, the S5 identity override) addresses *incentives*, but it cannot change what a model is capable of *noticing*. Maker–checker discipline depends on the checker having an independent perspective, not just an independent instruction set.

**Second-order cost.** A missed review produces no rework finding. No rework finding means nothing for the Wave C distiller to mine. Same-model review doesn't just let bugs through — it starves the learning loop of the evidence it exists to consume, and does so invisibly.

**Why the cost is acceptable.** The three upgraded rows (decompose, review, triage) are the *lowest-frequency* operations in the system — a handful of invocations per wave, against builders burning tokens continuously. The premium lands precisely where errors are most expensive and volume is smallest. That's the correct shape for a tiered system; flat sonnet-5 across all judgment rows paid a uniform price for non-uniform stakes.

**Why fable for decompose specifically.** Decomposition is the highest-leverage judgment in the pipeline: every builder faithfully executes whatever it produces, and review checks work *against* that spec — so a decomposition error is the one class of mistake the downstream gates structurally cannot catch. It also runs least often of any judgment row.

**Why review must not be fable either.** With decompose on fable, fable authors the specs. If review were also fable, the checker would share a model with the spec-author chain — the same parity problem, one layer up. Opus keeps review as the one judgment voice sharing a model with neither the maker (S5/sonnet-5) nor the planner (fable).

**Why the distiller stays on sonnet-5.** It is not a gate. Its confidence math is code-owned (`instincts.py`), its output is data (INSTINCTS.md), and its amendment proposals are locked behind the constitutional gate requiring explicit `/approve`. The same-model concern applies to *checkers*; the distiller is neither maker nor checker.

**Effort is a depth knob, not a discount knob.** Running fable at low effort to save budget defeats the purpose on decompose: territory carving and dependency sequencing are exactly where shallow reasoning misses interaction effects (two tasks that quietly share a file, a dependency chain that serialises what looked parallel). Medium is the floor; high for complex or many-task waves.

**Usage accounting.** Opus and fable draw from the same Claude 5h/7d windows as sonnet-5, so the Wave I meters and `budget.py`'s S5 usage gating cover them with zero code changes — they simply consume those windows faster per invocation. Acceptable at these rows' frequency; the standing rule is that neither model creeps into the high-frequency mechanical rows.

---

## Prefix hygiene — why this file exists separately

Everything stable at the front of a session's prompt (tool definitions, system prompt, auto-loaded `CLAUDE.md`) is cached after the first turn, and a cached token costs a fraction of a fresh one. Two consequences shape how this project organises its docs:

**1. `CLAUDE.md` is a hot file — every ORCH turn and every S5 builder turn pays for it.** It auto-loads into context. So it should carry *rules and pointers*, not rationale. A rule is consulted constantly; the argument for a rule is consulted when someone questions it, which is rare and is exactly what `docs/` is for — files read on demand, free until the moment they're needed. When a decision needs recording, the table row goes in `CLAUDE.md`, the reasoning comes here. That's the same principle as `instincts.py inject --limit 5`: the store can grow without bound, but only the slice the current task needs enters the prefix.

**2. Model switching invalidates the cache.** Each model keeps its own cache, so a mid-session `/model` swap re-reads the whole prefix at full price. This does *not* mean abandoning model discipline — a wrong rework verdict costs far more than a re-read — but it does mean **batching**: group mechanical operations together on sonnet-4-6, then switch once for the judgment operation, rather than alternating turn by turn. Where a judgment op is self-contained, prefer running it as a separate headless invocation (`claude -p "/devteam-review" --model claude-opus-4-8 …`), which gets its own clean cache and leaves the interactive session's prefix untouched — this is already how the autopilot does every judgment call, and it's the better pattern for interactive use too.

**3. Nothing dynamic belongs above the stable content.** Timestamps, run IDs, and session junk in a prefix silently break caching on every turn. `dispatch.sh`/`.ps1` are clean on this today (composed builder prompts contain no timestamps — `RUN_TS` only names log files); keep it that way when editing prompt composition.
