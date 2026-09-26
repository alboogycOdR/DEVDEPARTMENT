# Verification gates and capacity — green must mean working

**Status:** SPEC v1.1 — Wave G. Decompose after Wave E's critical path (E-0, E-B, E-C). **G-D goes first within this wave.**
**v1.1:** adds rwc-mobile-connect evidence. Territory preflight is now proven: 38% of rwc's first 29 dispatched tasks blocked, and the next 11 had **zero** blocks once ORCH checked paths against disk. Changes are in G-C.5, G-D.0 and G-D.5, and in the order.
**Origin:** field evidence (`reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md` ranks 2, 3, 5, 6, 9, 13, 18). These are the quality and throughput failures the code review could not see. They appear only when real projects run for weeks.
**Absorbs:** Wave D items 1 (capability-class routing, as capacity-aware routing), 3 (blind verifier; hooks in via G-A's gate result) and 4 (report contract, via the gate result and verdict schema).

## 0. What is already correct — do not rebuild it

- Territorial isolation as a hard gate. G-D makes carves **correct**, never looser, and never lets builders self-widen silently.
- The model review. G-A moves the *mechanical* checks out of it; judgment stays with the model.
- Cross-model review. G-A enforces it instead of trusting prose.

## 1. Design rules

**V1 — Mechanical checks are scripts, not model judgment.** Territory, build, typecheck, full suite, reachability and mutation all have exit codes. The model reviewer reads their results; it never re-derives them.
**V2 — A test result belongs to a SHA.** Run once per head SHA and reuse it. A result for another SHA is not evidence.
**V3 — "Pre-existing" is not a verdict.** A failure on the base branch is acceptable only if an open task ID owns it.
**V4 — Capacity is state, not surprise.** A unit that is out of quota is known before dispatch, and work routes around it automatically.
**V5 — The maker never grades its own work.** This is enforced at verdict time, from recorded facts.

---

## 2. G-A — the scripted pre-review gate

**Required:**
1. `autopilot.json` → `gate` (per project, filled at onboarding and reviewed by the human):
   ```json
   "gate": {
     "build":      "pnpm -r build",
     "typecheck":  "pnpm -r typecheck",
     "test_full":  "pnpm -r test",
     "test_env":   {"scrub": true, "allow": ["PATH","HOME","NODE_OPTIONS"]},
     "timeout_minutes": 30
   }
   ```
   Every key is optional. An absent key is reported as `skipped`, never as `passed`.
2. `scripts/review_gate.py <TASK-ID>` runs **in the task's worktree at its head SHA**:
   - territory diff against `Owned_Paths`;
   - `build`, `typecheck` and `test_full`, with the environment scrubbed per `test_env` (oikonomos: operator keys leaked into tests and made paid API calls);
   - G-B's reachability and mutation checks;
   - base-branch comparison: the same commands on the merge-base, cached per SHA.

   It writes `.devteam/gate/<TASK-ID>-<sha>.json` with `{sha, checks: {name: {status: passed|failed|skipped, duration, summary, log_path}}, new_failures: [...], base_failures: [...]}`.
3. **Baseline rule (V3).** A failure present on the merge-base is listed in `base_failures`, and the gate passes it only if the task block or the verdict names an open task ID that owns it. Otherwise the gate fails with `UNOWNED_BASELINE_FAILURE` (oikonomos SB-6).
4. The supervisor runs the gate **before** launching a model review:
   - gate failed → the task goes straight back to `in_progress` with the gate summary in `Review_Findings`, and **no model session runs**;
   - gate passed → the review prompt receives the gate JSON path, and the reviewer judges spec fidelity, design and the diff, never re-running what the gate proved.
5. **Reviewer ≠ maker, enforced (V5).** The review verdict (Wave F3 schema, or a REVIEW.md row until then) records `reviewer_model` and `reviewer_unit`. The merge step refuses when `reviewer_model` equals the model of the task's `Assigned_To` unit, or when `reviewer_unit == Assigned_To` (ORCH self-fixes under an override count as maker = ORCH). The refusal escalates P2 with the two ways to resolve it: re-review on a different model, or an explicit human `/approve` (KERYX SB-3: 11 reviews on the builder's own model).
6. `/devteam-status` and the P0 digest show **base-branch CI state** (the GitHub checks for the base head, via `gh` when available) and red-for-N-days (oikonomos: CI red for ~2 weeks, unseen).

**Acceptance:**
- A fixture task that compiles alone but breaks another package fails `build`/`typecheck` in the gate, and no model session starts (oikonomos TASK-327).
- A failure present on base with no owning task fails the gate. The same failure with `Owns_Failure: TASK-900` passes.
- A test that reads `GEMINI_API_KEY` sees it unset under `scrub: true`.
- A verdict with `reviewer_model == claude-sonnet-5` on an S5 task does not merge, and raises one P2.
- A gate result for SHA A is never used for SHA B.

## 3. G-B — reachability and mutation checks

**Required:**
1. **Reachability** (KERYX SB-1: a component never constructed in production). A task may declare `**Entry_Points:**`, the symbols or files that must be reachable from a production composition root (`gate.roots`, e.g. `lib/main.dart`, `services/*/src/index.ts`).
   - `review_gate.py` checks, via ATLAS `impact`/`where` when ATLAS is enabled or a grep import-closure otherwise, that each entry point is transitively imported from a root.
   - Every new public class or file added by the diff is checked the same way, and unreachable ones are reported as `warn`.
   - A decompose rule: any task that adds a component a user should experience must declare `Entry_Points`.
2. **Deployed smoke (optional per wave).** `gate.smoke` (a command, e.g. a script that hits the deployed services) runs at **wave close**, not per task. The wave is not `done` until it passes or a human `/approve`s the wave with the failure noted.
3. **Mutation check** (KERYX SB-13: tests that pass without the fix, the top rework cause). For tasks whose diff contains both non-test and test changes, the gate:
   - reverts the non-test hunks in a scratch copy;
   - runs the tests the diff added or changed;
   - requires at least one of them to fail.

   It is skipped, with a reason, for pure-test, pure-docs and config-only diffs. Its result is a gate check like any other.

**Acceptance:** a fixture where the new class is only used from tests reports it unreachable. Adding a constructor call in the root makes it pass. A fixture where the tests pass with the implementation reverted fails the mutation check. Removing the check's scratch copy leaves the worktree clean.

## 4. G-C — the test contract: SHA-tagged, isolated, foreground

**Required:**
1. **One test run per SHA (V2).** `scripts/run_tests.py` (called by builders, the gate and review alike) runs `gate.test_full` or a named subset, and writes `.devteam/testruns/<sha>-<name>.json` (`{sha, cmd, passed, failed, duration, log_path, env_fingerprint}`). A second call for the same SHA, name and environment fingerprint returns the cached result. Builders cite this file in `Test_Evidence` and CONTROL blocks; `needs_review` without a finished run for the head SHA is rejected by `control.py`/validation (oikonomos SB-4).
2. **Per-worktree test resources.** `gate.test_env.per_worktree` (a map of env var → template, e.g. `{"DATABASE_URL": "postgres://…/app_test_{unit}"}`) is exported per worktree, so builder runs and review runs never share a database or mutex (oikonomos: a global mutex serialised every run; a review's `-Init` invalidated a builder run).
3. **Time budget in the brief.** `gate.test_full_expected_minutes` and the harness ceiling (e.g. a 600 s background-task kill) are injected into every dispatch prompt: "Run `python scripts/run_tests.py full` in the foreground; it takes about N minutes. If that exceeds your harness ceiling, run the named subsets in sequence and let review run full." This replaces the prose-only rule (KERYX SB-8).
4. `gate.test_concurrency` (e.g. `--concurrency=2` for Flutter's OOM) is part of the command, not tribal knowledge.
5. **Worktree config copy** (rwc SB-5: fresh worktrees couldn't compile without the gitignored `firebase_options.dart`/`google-services.json`; builds were waved through as "pre-existing"). `autopilot.json` → `worktree_copy: ["lib/firebase_options.dart", "android/app/google-services.json"]`.
   - Dispatch copies each file from the main checkout into the worktree at creation and on every refresh, and verifies each is still gitignored in the worktree, refusing if not, so a secret never gets committed.
   - `worktree.ps1`/`.sh remove` deletes them.
   - A missing source file is a dispatch refusal naming the file, not a silent skip (LekkerSwot lesson: missing ≠ empty).

**Acceptance:** the builder and the gate on the same SHA run the suite once in total. Two worktrees get distinct `DATABASE_URL`s. A CONTROL block claiming `needs_review` without a testrun file for the head SHA is rejected with a message naming the missing file.

## 5. G-D — territory auto-carve and reach check

The field's most frequent human task: 58 + 8 tasks with OWNERSHIP_CONFLICT loops.

**Required:**
0. **Path preflight (hard gate).** `validate_plan.py --preflight` (and dispatch, before launch) requires every `Owned_Paths` entry of a non-done task to either exist on the base branch or carry ` (new)` (E-F.5). A glob must match at least one file unless it is marked ` (new)`. A failure blocks dispatch with the offending paths listed, **before** any builder session starts. Where present, this absorbs and replaces `scripts/preflight_paths.py` (the pack has it; the field projects never received it).
1. **Carve completion at decompose.** `scripts/carve.py complete <TASK-ID>` proposes additions to `Owned_Paths`:
   - the colocated test for each owned source file (`foo.ts` → `foo.test.ts` / `__tests__/foo.test.ts`, `lib/x.dart` → `test/x_test.dart`, per `gate.test_colocation` patterns);
   - the package barrel (`index.ts`, `mod.rs`, a `lib/<pkg>.dart` export file) when it exports an owned file;
   - declared composition roots when an `Entry_Points` symbol must be wired there.

   `/devteam-decompose` runs it for every task and applies the proposals unless they overlap another active task's territory. Overlaps become explicit `Depends_On` sequencing, or a single-owner integration task, and are listed for the human.
2. **Reach check** `validate_plan.py --reach` (warning, not error): for each non-done task, it lists files that import or are imported by owned files, restricted to the same package and to `gate.shared_files` (the project's recurring hot files, e.g. `services/control-api/src/ports.ts`). It flags those outside the carve and also outside every other active carve. The output goes into the dispatch prompt as "likely neighbours; if you need one, block with OWNERSHIP_CONFLICT naming it."
3. **Bounded self-grant.** A builder may add its task's **colocated test file or package barrel** to its own territory by emitting `grant_request` in CONTROL (strict) or a `Progress_Note` line `[GRANT] <path>` (legacy). The supervisor applies it automatically if the path matches a G-D.1 rule and no other active task owns it, logging `GRANT_AUTO`. Anything else still blocks. This keeps isolation intact while removing the most common round trip.
4. The retro reports OWNERSHIP_CONFLICT counts per file, so recurring hot files become `gate.shared_files` entries.
5. **An integration task per wave for shared files** (rwc SB-7: ORCH wrote "integration" code in 8 of 29 reviews, e.g. the missing `index.ts` export or a rules gap; oikonomos: the same few composition files blocked dozens of tasks).
   - Decompose collects every change a wave's tasks need in `gate.shared_files` (routers, barrels, rules files, DI/provider roots) and emits **one builder-owned integration task** that owns those files.
   - It is sequenced with `Depends_On` after the feature tasks it wires, with acceptance criteria that name each wiring point, and G-B reachability as its gate.
   - Feature tasks get a `**Wiring_Needs:**` field (plain list) that feeds the integration task instead of widening their own territory.
   - ORCH never commits code outside the solo lane (E-J.5).

**Acceptance:** on a fixture monorepo, a task owning `src/a.ts` gets `src/a.test.ts` and `src/index.ts` (which re-exports `a`) proposed. An overlap with an active task becomes a dependency, not a merge. A `[GRANT] src/a.test.ts` is auto-applied, and `[GRANT] src/other.ts` is not. `--reach` lists `ports.ts` for a task that imports from it.

## 6. G-E — capacity-aware builder registry

**Required:**
1. **Registry state** (`.devteam/capacity.json`, not tracked): per unit `{available_after, last_error, last_error_kind: QUOTA|BALANCE|CAPACITY|AUTH|OTHER, consecutive_errors}`, written by the reaper when a run log or exit matches known patterns. Patterns live in the registry per CLI family (`codex`: "usage limit", "at capacity"; `grok`: "402"; `claude`: "usage limit reached" plus the reset time when printed).
2. **Pre-dispatch probe.** Before dispatch, a unit with `last_error` newer than `capacity.probe_after_minutes` gets a one-token probe using the CLI's own cheapest call. A pass clears the state. A fail sets `available_after` (from the parsed reset time, or exponential backoff) and skips the unit. This is not budget-gated.
3. **Automatic failover.** When a unit is unavailable and `capacity.failover: true`, pending tasks assigned to it may be reassigned by the supervisor to an available active unit, **only if** that unit's CLI family is in the task's `Allowed_Units` (a new optional field; default: any active unit) and it is not the task's designated reviewer family (V5). Reassignment is a logged `REASSIGN` action with an ORCH commit, and a P0 digest line. Resuming an in-progress task on another unit uses the dossier (proven in oikonomos: CX9 resumed S5's TASK-338).
4. **`CAPACITY` blocked reason.** The Wave E-K classification lands here. A task blocked `CAPACITY:` is never triaged by a model; it waits for failover or `available_after`.
5. **Machine-aware units** (KERYX SB-14). `builders.defined.<U>.machines: ["hostname", …]`. Dispatch on any other host refuses the unit with a clear message and treats it as unavailable for routing. `session-start.js` runs `git fetch` and warns when the main checkout is behind its upstream.

**Acceptance:** a fake codex run log with "usage limit … try again at 14:05" sets `available_after` to 14:05, and the next tick routes the unit's pending work to S5 (when allowed) with one REASSIGN commit. A unit with `machines: ["other-pc"]` is never dispatched here. A `CAPACITY` block produces no triage session.

---

## 7. Territories and order

| # | Owned_Paths | Depends_On |
|---|---|---|
| **G-C** | `scripts/run_tests.py`, `tests/test_run_tests.py`, `scripts/control.py`, `tests/test_control.py`, `scripts/dispatch.sh`, `scripts/dispatch.ps1`, `scripts/worktree.ps1`, `scripts/worktree.sh`, `tests/test_dispatch_worktree.py` | Wave E (E-F, E-K), G-D.0 |
| **G-A** | `scripts/review_gate.py`, `tests/test_review_gate.py`, `scripts/supervisor.py`, `tests/test_supervisor.py`, `.claude/commands/devteam-review.md`, `autopilot.json` | G-C |
| **G-B** | `scripts/review_gate.py` (reach + mutation modules: `scripts/gate_reach.py`, `scripts/gate_mutation.py`), `tests/test_gate_*.py` | G-A |
| **G-D.0** | `scripts/validate_plan.py`, `tests/test_validate_plan.py`, `scripts/preflight_paths.py` (absorbed), `tests/test_preflight_paths.py`, `scripts/dispatch.sh`, `scripts/dispatch.ps1` | E-F (`(new)` grammar) |
| **G-D** | `scripts/carve.py`, `tests/test_carve.py`, `scripts/validate_plan.py`, `tests/test_validate_plan.py`, `.claude/commands/devteam-decompose.md`, `scripts/control.py` (grant_request) | G-C (control.py) |
| **G-E** | `scripts/capacity.py`, `tests/test_capacity.py`, `scripts/builder_registry.py`, `tests/test_builder_registry.py`, `scripts/supervisor.py`, `hooks/session-start.js` | G-A (supervisor.py) |

Order (v1.1): **G-D.0 (preflight) first, alone, since it is small and proven** → G-C → G-A → (G-B ∥ rest of G-D) → G-E. G-E may start earlier on `capacity.py` and `builder_registry.py` if its supervisor wiring waits for G-A.

## 8. Exit criteria

Suites green on both CI platforms. On the two field-shaped fixtures (a pnpm monorepo and a Flutter app):
- the TASK-327-style cross-package break never reaches a model reviewer;
- a KERYX-v2-style unwired component fails reachability;
- a test-that-can't-fail fails mutation;
- a builder and a review on one SHA run the suite once;
- an out-of-quota unit is routed around without a human;
- a builder-model review cannot merge.

## 9. Note for the reviewer

The gate is itself code that says "green". Its tests must include fixtures where the gate **should fail**, and every check's `skipped` status must be visible in the verdict. A gate that silently skips is the failure mode this wave exists to remove.
