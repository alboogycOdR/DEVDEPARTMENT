# DEVDEPARTMENT — Field Feedback Synthesis

**Date:** 2026-09-26 · **v2** (adds rwc-mobile-connect, the fourth and final report)
**Inputs:** four ORCH-written retrospectives, all produced with the same structured feedback prompt.

| Project | Stack | Pack version | How it was run | Scale | Framework-active window |
|---|---|---|---|---|---|
| **oikonomos** (`GROKBOT-CLONE`) | TS/pnpm monorepo, Postgres | v4.5, strict, ATLAS on | **L2 as a Windows Scheduled Task (`supervisor.py --once` every 5 min)** + interactive ORCH | 373 tasks, 2,628 commits in 26 d | continuous |
| **KERYX** (`walkietalkie-keryx`) | Flutter + LiveKit | v4.5 (partial v4.7) | Interactive ORCH on Windows, L1 waves | 114 tasks, 627 commits | continuous |
| **rwc-mobile-connect** | Flutter + Firebase | **~v1.2** (core v1.0/1.1 + AutoPilot v1.2 add-on), no manifest | Interactive ORCH, builders in Terminal windows; macOS then Windows | 40 tasks | **5 days (12–17 Jul)**, then abandoned for owner-direct work |
| **LekkerSwotMobileFinal** | Flutter | **not installed** | Interactive sessions straight to `main` | — | never |

**Read with:** `reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md` (code review) · `specs/LOOP_HYGIENE_2026-09.md` (Wave E v2.1) · `specs/VERIFICATION_GATES_2026-09.md` (Wave G v1.1) · `specs/CLAUDE_NATIVE_LEVERAGE_2026-09.md` (Wave F v1.2).

---

## 0. The findings in one page

1. **The review loop is the most expensive defect.** In oikonomos, `decide()` re-emitted `REVIEW` for every waiting task on every tick:
   - 761 REVIEW lines, TASK-340 alone ×109 over 11.5 h, ~1,917 ORCH session ends;
   - the owner's own fix (`a14f8976`) estimates ~150 Opus sessions in 30 h, about 75% of them redundant;
   - it caused three Claude usage-limit lockouts on 24–25 Sep.
2. **Escalations with no memory of what was already sent** (oikonomos): 4,196 P2 lines, 103 copies of one P1, and the owner acted on none of them.
3. **Territory carving is the one problem all three active projects share, and a fix is already proven:**
   - oikonomos had 58 OWNERSHIP_CONFLICT blocks in 26 days;
   - KERYX had 8+ tasks with repeat re-carves;
   - **rwc had 11 of its first 29 dispatched tasks (38%) block**, on paths that didn't exist, new files not marked as new, and missing shared files (`index.ts`, `router.dart`, `firestore.rules`);
   - after ORCH started checking paths against disk before dispatch, **the next 11 tasks had zero ownership blocks**. That is the strongest evidence in the set that path preflight plus shared-file handling (Wave G-D) works.
4. **"Green" did not mean working:**
   - KERYX's v2 wave was never wired into the running app;
   - oikonomos merged a cross-package type break, and CI stayed red for about two weeks under "baseline" excuses that burned paid Gemini credit;
   - rwc approved 32/32 tasks with zero rework, but builders couldn't compile in fresh worktrees (gitignored Firebase config), so builds were waved through as "pre-existing";
   - ORCH fixed integration gaps itself in 8 of 29 rwc reviews.
5. **Builders writing the shared PLAN.md is a recurring source of damage in every legacy-mode project:**
   - rwc: CX rewrote all 2,130 lines (line endings), committed claims to `main`, and wrote a claim into another task's block; GB split PLAN.md commits from its code;
   - KERYX: lost updates.

   **oikonomos, the only project in strict mode, reports none of this** and rates the CONTROL-block blackboard its best feature.
6. **Capacity limited throughput more than code quality did** (KERYX, oikonomos). Every failover was manual.
7. **Timestamps typed by the model make stale detection blind:**
   - KERYX: local time with a `Z` suffix; TASK-113 stuck for 4 days, never flagged;
   - rwc: ORCH and review timestamps are round, invented values (`…:00:00Z`).
8. **PLAN.md doesn't scale:** 1.66 MB (KERYX), 11,134 lines (oikonomos), 182 KB for only 40 tasks (rwc), and 43% of oikonomos's commits are plan bookkeeping.
9. **No field project has ever synced the pack:**
   - oikonomos and KERYX are on v4.5 with local fixes;
   - rwc is stranded at ~v1.2, **seven minor versions behind, with no manifest and no version stamp**, and fixes were hand-copied into both repos;
   - the best fixes in this synthesis already exist in project repos.
10. **Adoption decays when the work doesn't fit the task shape (new in v2).**
    - rwc stopped dispatching after five days. Release, console and hardware work ran outside PLAN.md, with no review gate on the highest-stakes changes.
    - LekkerSwot never installed the framework.

    The framework has no representation for owner-gated work, external or console tasks, or a solo-ORCH lane, and no lightweight install for small repos.
11. **What worked is consistent across all three active projects:**
    - territorial isolation as a hard refusal, with zero cross-territory damage across 475 + 35 done tasks;
    - independent re-verification in review, which caught real gaps in each project;
    - dossiers and the git-visible state, where every mistake was recoverable;
    - honest builder self-reporting (acceptance criteria left unticked rather than falsely green);
    - strict CONTROL mode, where it was used.

**Human cost reported (all estimates, none of it logged):** oikonomos 25–35 h, KERYX 8–15 h, rwc 6–9 h in 5 active days.

---

## 1. Hypothesis scoreboard

| # | Hypothesis | KERYX | oikonomos | rwc | Verdict | Change to plan |
|---|---|---|---|---|---|---|
| H1 | P2 re-sent every tick | n/o | **Confirmed** (×1,241/task) | n/o (console/file only) | **Confirmed** | E-B, P1 included |
| H2 | Uncapped triage | can't tell | **Confirmed** ("attempt 1" ×35) | **Manual form confirmed** (TASK-013 re-triaged 3–4×) | **Confirmed** | E-B; the ceiling also covers block→triage cycles |
| H3 | Halt → restart → repeat | n/o | **`--once` analogue confirmed** | n/o | **Confirmed, different form** | E-C persisted park state |
| H4 | Wrong-checkout / stale-state hooks | **Confirmed** | **Confirmed, inverted** (claim race) | **Analogue:** builders edited from a stale PLAN.md copy (claim in wrong block) | **Confirmed** | E-A + E-F; strict by default for new projects |
| H5 | Per-task grant friction | re-carve form | can't tell | **Functional equivalent (Owned_Paths edits) was the #1 friction** | Grants: not a field problem. **Carving: the top problem** | D6 optional; G-D top |
| H6 | Model-written timestamps | **Confirmed** | can't tell | Invented round timestamps; stale path never fired | **Confirmed** | E-E; review stamps its own time |
| H7 | PLAN.md size | likely | **Confirmed** | **Partial** (182 KB for 40 tasks; mis-edits; ORCH context exhaustion) | **Confirmed** | E-D |
| H8 | Slash-form headless prompts do nothing | **Confirmed** | review confirmed; triage/approve unverified | can't tell (`review_cmd` still slash form, never exercised) | **Confirmed** | E-B |
| H9 | Duplicate / blocking reviews | n/o | **Duplicates massively confirmed** | n/o (interactive) | **Confirmed, #1 cost** | E-B review ledger |
| H10 | Full-suite completion | **Confirmed** | **Confirmed** | **Different cause:** fresh worktrees can't build without gitignored config | **Confirmed** | G-C test contract + `worktree_copy` |
| H11 | S5 identity confusion | n/o | task confusion only | n/o (no S5) | **Role: no; task: yes** | E-A `DEVTEAM_TASK` |
| H12 | Model choice problems | **Confirmed** | **Confirmed** | **CX model/effort changed 4× in 2 days on a misdiagnosis** (the real cause was bad paths) | **Confirmed** | G-E capacity; F2 per-run model/effort telemetry |

**Open question on H9.** The median 4 s between a REVIEW line and the next log line suggests most of oikonomos's repeated reviews exited almost immediately. E-B's REVIEW_START/END markers will settle it.

---

## 2. Merged stumbling blocks, ranked

**Src:** O = oikonomos, K = KERYX, R = rwc, L = LekkerSwot. Ranked by combined cost and by how many projects hit it.

| Rank | Theme | Src | Headline evidence | Fixed in |
|---|---|---|---|---|
| 1 | **Territory carving:** paths wrong or too narrow; shared files omitted; OWNERSHIP_CONFLICT loops; ORCH writing "integration" code | **O, K, R** | O 58 blocks; K TASK-104 ~7 h; R 38% of dispatched tasks blocked, **0 after pre-verification**; R 8/29 reviews needed ORCH integration commits | **G-D** (preflight + auto-carve + shared-file integration task) |
| 2 | Review re-launched every tick | O | 761 REVIEW lines; 3 usage lockouts | **E-B** (port `a14f8976`) |
| 3 | Review gate misses build/integration/deploy defects; "baseline" excuses; fresh worktree can't build | **O, K, R** | K v2 dead in production; O TASK-327 broke master; O CI red ~2 weeks; R builds waved through (gitignored config) | **G-A, G-B, G-C** |
| 4 | Builders writing PLAN.md: races, wrong-block edits, whole-file rewrites, commits to `main` | **K, O, R** | R CX rewrote 2,130 lines and claimed into TASK-016's block; K 4+ lost updates; O 21 duplicate claims | **E-F** (CAS, idempotent claims, legacy guard); **strict the default for new projects** |
| 5 | Escalation and halt spam | O | P2 ×4,196; P1 ×103; HALT ×73 | **E-B, E-C** |
| 6 | Stranded installs: no sync, no version stamp, fixes hand-ported | **O, K, R** | R at ~v1.2 (7 minors behind); O/K v4.5 with an empty sync state | **E-0** |
| 7 | Capacity collapse, no failover | K, O | 7+ cap/402 events; tasks lost mid-run | **G-E** |
| 8 | Test contract: exits before the suite ends; shared DB; OOM; 600 s ceiling; missing config in worktrees | K, O, R | O 91+ mutex exits; K S5 killed; R ≥4 reviews with scoped analyze only | **G-C** |
| 9 | Adoption decay: no shape for owner-gated/release work; no solo lane; too heavy for small repos | **R, L** | R abandoned after 5 d, release work ungated; L never installed | **E-J** (`owner_hold`, `external`, solo lane) + BACKLOG "L0 lite" |
| 10 | PLAN.md bloat + bookkeeping pushes | K, O, R | 1.66 MB / 11k lines / 182 KB at 40 tasks; 43% of commits | **E-D, E-G** |
| 11 | Launch tooling wrong on first use (CLI flags, sandbox modes, CRLF, TTY) | **R, O, K** | R: 8 dispatch fix commits in 5 days; O: bare-bash→WSL; K: hard-coded `main` | **E-H** (CLI launch smoke test, eol=lf defaults, Windows CI) |
| 12 | Tests that pass without the fix | K | Top rework cause; TASK-031 took 4 rounds | **G-B** mutation check |
| 13 | Stale base branches at dispatch | O | TASK-327 on the wrong tip | **E-F** (port `bceb8eb2`) |
| 14 | Model-written timestamps | K, R | TASK-113 unflagged for 4 d; invented `:00:00Z` values | **E-E** |
| 15 | Hand-maintained REVIEW.md (stale tallies, broken table, approximated times) | **K, O, R** | All three tallies disagree with their own rows | **E-D** (generated tallies, table lint, stamped time) |
| 16 | Maker/checker collapse; ORCH model discipline not applied | K, R | K 11 reviews on sonnet-5; R ORCH commits span 6 models; R `review_cmd` pinned to sonnet-5 | **G-A** (record and refuse) + **F1** |
| 17 | Model/effort changed without evidence | R, K | R CX changed 4× in 2 days on the wrong diagnosis | **F2** (model+effort per run in ledger → `team_stats`) |
| 18 | Learning loop costs sessions, returns nothing | K, O | 0 instincts; 75 runs, 10 unapplied duplicate amendments | **E-I** |
| 19 | Unbounded delegated agents | K | 14 h agent changed config and claimed approval never given | **F2 + E-A** |
| 20 | Roster/doc drift; prose as status | O, K, R, L | Roster patched in 4 places; prose Blocked_Reason on 7 R tasks | **E-J** |
| 21 | Multi-machine identity; stale clones | K, R | CX9 exists on one machine only; R's waves moved between macOS and Windows | **G-E** |

---

## 3. Two structural findings no single report states outright

### 3.1 `--once` is a different runtime (oikonomos)

oikonomos ran L2 as a Scheduled Task calling `supervisor.py --once`. Several mechanisms assume one long-lived process:

| Mechanism | Assumes | Under `--once` every 5 min |
|---|---|---|
| Two-way Telegram | Listener thread + in-memory queue drained next tick | The listener starts *after* the drain. `tg_listener._handle_update` persists the offset (acks) **before** enqueueing, then the process exits. **Commands are consumed and lost**, which explains "0 /answer replies". (`chat_allowlist` is additive with `DEVTEAM_TG_CHAT`, so the empty allowlist was not the cause.) |
| Halt on P1 / wave done | Process stays down | The next run repeats it (P1 ×103, HALT ×73) |
| `inflight` reaping | Popen handles across ticks | Lost at exit |
| `/wave` | Wakes the sleeping loop | No-op |

**Fix:** durable state for everything cross-tick (E-C), commands routed through the file inbox with ack-after-handle (E-K), and the portable `autopilot-tick.ps1` as the supported Windows runner.

### 3.2 The framework only covers work that looks like a parallel code task (rwc + LekkerSwot)

rwc's last framework-dispatched task was on 17 Jul. The Play release, account deletion purge and App Check (its highest-stakes work) ran from a checklist in `docs_production_prepare/` and a CLAUDE.md "Current Focus" paragraph, with commit tags `[A3]`/`[B2]` instead of task IDs. PLAN.md's frontmatter is now wrong about the project's state. Holds live in prose `Blocked_Reason`s.

The protocol has no shape for:
- **owner-gated** work (credentials, hardware, console clicks, approvals);
- **external/ops** tasks (release checklists, store listings);
- **ORCH-solo** code changes that are too small to dispatch.

So that work leaves the system, and with it the review gate and the audit trail. LekkerSwot is the same pattern one step earlier: a small interactive repo never adopted the framework at all.

**Fix (E-J):**
- an `owner_hold` status the dispatcher never picks, with `Hold_On:`;
- an `external` task type with checklist-style acceptance and no `Owned_Paths`;
- a **solo lane** (`Assigned_To: ORCH-SOLO`, direct to the base branch) that still requires a REVIEW row from a different model or a human, with a machine-stamped time;
- `/devteam-status` flags frontmatter older than the newest task.

"L0 lite" (PLAN.md + REVIEW.md + validator only) goes to BACKLOG with its trigger: the next small repo onboarded.

---

## 4. Changes to the code review's conclusions

| Review item | Field verdict | Change |
|---|---|---|
| D7 (duplicate/blocking reviews) | Duplicates far worse; blocking not seen | **#2 overall.** E-B review ledger |
| D4 (PM2 restart loop) | PM2 unused anywhere; `--once` analogue confirmed | Park state in `RuntimeState` |
| D5 (hooks read the worktree) | Confirmed, plus the inverse claim race, plus stale-copy edits in rwc | Main-checkout reads **and** verified claims **and** strict by default for new projects |
| D6 (grants as code) | Not a field problem | Optional |
| D11 (PLAN.md size) | Confirmed everywhere, up to 8× what the review measured | Promoted |
| D10 (timestamps) | Confirmed in two projects | Promoted; the review command stamps its own time too |
| F1 (reviewer ≠ builder) | Violated in two projects | Enforced at verdict time (G-A) |
| F2 (ledger) | rwc's model churn shows why | Ledger records model + effort; `team_stats` compares outcomes |
| F3 (typed distiller output) | The learning loop produced nothing | Deprioritised behind E-I |
| **New** | Territory preflight/auto-carve, pre-review gate, reachability, mutation, capacity, test contract, worktree config copy | **Wave G** |
| **New** | Owner-gated / external / solo lanes; version stamping; CLI launch smoke tests | **E-J, E-0, E-H** |

---

## 5. What to protect

- **Territorial isolation as a hard refusal.** It was praised by all three active projects, with zero cross-territory damage. G-D makes carves correct; it never loosens the gate.
- **Independent re-verification in review.** It caught real defects in every project. G-A moves the *mechanical* part into scripts; the model review remains.
- **Strict CONTROL mode.** It is the only configuration that avoided the PLAN.md write-damage class entirely.
- **Dossiers and git-visible state.** Every mistake in all three projects was recoverable.
- **Honest self-reporting.** Builders left acceptance criteria unticked rather than claiming green. Gates must reward that, never pressure against it.
- **The review-to-briefing feedback loop.** In rwc, findings became script or briefing fixes within hours. Make that loop cheaper (E-0 upstream path), not heavier.

---

## 6. Lessons from LekkerSwot (framework not installed)

1. **"Source missing" must never read as "empty".** A poller reported "inbox clear" for about 6 days against a directory that had moved. → E-K: `SOURCE_MISSING`, logged once.
2. **Cheap models narrate false causes.** → Wave F3 typed outputs.
3. **One canonical briefing, linted against the code.** → E-J briefing lint.
4. **Wished for an "L0 lite" install.** → BACKLOG, with rwc's adoption-decay evidence attached.

---

## 7. Recommended order

1. **E-0:**
   - harvest the field fixes (oikonomos `a14f8976`, `bceb8eb2`, `d3f5fc08`, `7baeedf3`, `3e8c3e79`);
   - version-stamp every install;
   - make sync work, including the legacy **no-manifest** upgrade path that rwc needs.
2. **Rest of Wave E** (the loop's cost and noise, blackboard safety, lanes for non-code work).
3. **Wave G**, **starting with G-D** (territory preflight). It is the one problem all three active projects share, and rwc's zero-blocks-after-preflight result shows it works.
4. **Wave F:** F1/F2 alongside G; the rest after.
