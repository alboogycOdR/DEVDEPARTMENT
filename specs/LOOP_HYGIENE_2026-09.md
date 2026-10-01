# Loop hygiene v2 — making the loop cheap, quiet and durable

**Status:** SPEC v2.2 — Wave E. Supersedes v1, v2 and v2.1 (same path; v2.2 amended 2026-10-01).
**v2.2 (2026-10-01, ORCH, raised by TASK-043 SPEC_AMBIGUITY):** E-B.3 re-send timer is now capped — each escalation key is re-sent by timer at most `escalation.max_timer_resends` times (default 1), reconciling E-B.3's 1 h P1 interval with §15's "re-sent at most once more by timer". The interval still sets *when* the re-send happens; the cap sets *how many*. See E-B.3.
**v2.1:** adds rwc-mobile-connect evidence (stranded ~v1.2 install, builder PLAN.md damage in legacy mode, CLI launch breakage, work that doesn't fit the task shape). The changes are in E-0.5–6, E-D, E-F.6–7, E-H.4–5 and E-J.5–8.
**Origin:** code review (`reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md` §2) **plus field evidence** from KERYX and oikonomos (`reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md`). v2 re-ranks by measured cost and adds the defects that only the field could show.
**Baseline:** pack `master` @ `b74d581`; suites 1028 Python (4 skipped) / 36 Node green.

## 0. What is already correct — do not rebuild it

- `reap_inflight`, `max_dispatch_failures`, the parked-unit P2, the circuit breaker's arithmetic, `plan_commit.sh`'s `--git-common-dir` resolution, and `inbox.py`'s two-phase drain/ack. This wave reuses their shapes.
- **Field-tested fixes already exist in oikonomos.** E-0 ports them. Do not re-derive from this spec what a project has already proven in production. Where this spec and a harvested fix disagree, the harvested behaviour wins unless the reviewer records why not.

## 1. Design rules

**H1 — Hear once.** A condition reaches a human on first occurrence, on change, and then on a slow timer. Never every tick. **This applies to P1 too:** "never muted" does not mean "repeated".
**H2 — Every repeat-action class has a ceiling and logs its real attempt number.**
**H3 — Cross-tick state is on disk.** `--once` under a scheduler and `--loop` must behave identically. Nothing that matters lives only in process memory.
**H4 — Coordination state is read from the main checkout, and claims are verified before anyone acts on them.**
**H5 — Timestamps come from clocks and git, never from model text.**
**H6 — Fail-open for decisions, loud for observability.** A missing source never blocks a tick. It is logged once, never treated as "empty".
**H7 — Regression tests must fail against current code, and must span multiple ticks** where the defect is about repetition.

---

## 2. E-0 — harvest field fixes; make sync real (FIRST)

**Required:**
1. From oikonomos (commits named in the synthesis), port into the pack after review:
   - `a14f8976`: review ledger, `review.lock`, one review per tick, backoff, `escalate_repeat_hours` de-dup, MISSING_DEPENDENCY triage cap;
   - `bceb8eb2`: dispatch pre-creates `task/<id>-<suffix>` from the base tip on a fresh claim;
   - `d3f5fc08`: conftest bash redirect, CR stripping, cygpath handling;
   - `7baeedf3`: portable `autopilot-tick.ps1`;
   - `3e8c3e79`: test runner scrubs the operator environment.
   Each port is a separate commit citing its origin SHA. E-B, E-F and E-H then *extend* these ports rather than duplicating them.
2. **Tracked role marker.** `sync-manifest.json` gains `"role": "pack" | "project"`. `tests/test_sync_from_pack.py::_is_pack_repo()` reads it instead of the gitignored `.devteam/sync_state.json`. Onboarding writes `"project"`.
3. **Upstream report.** `sync_from_pack.py --diverged` lists framework-owned files that differ between project and pack, with a unified diff, so field fixes can be proposed upstream. Read-only.
4. **Legacy sync path.** A project with an empty `sync_state.json` (KERYX, oikonomos) gets a first sync that treats every diverged framework-owned file as a *conflict to review*, never an overwrite.
5. **Version stamp.** Onboarding and every sync write `framework_version` (pack semver + pack commit SHA) into `autopilot.json`, which is project-owned after first write. `/devteam-status`, the session-start hook and the P0 digest compare it with the pack checkout configured in `sync.pack_path`, when present. When the project is behind they print `framework vX behind pack vY — run: python <pack>/scripts/sync_from_pack.py --project .` (rwc SB-2: seven minor versions behind, and nobody was told).
6. **No-manifest upgrade path.** For a pre-v4.6 install with no `sync-manifest.json` (rwc, ~v1.2), `sync_from_pack.py --adopt` fingerprints the known framework files against pack history. It writes a manifest with `role: project`, records each file as `matches vX`, `diverged` or `absent`, and then runs the legacy sync path (item 4). It never overwrites a diverged file. Absent layers (hooks, `.claude/agents`, `control.py`) are proposed as adds with a checklist, not silently installed.

**Acceptance:** the pack self-tests are skipped in a checkout whose manifest says `project`, and run in the pack. `--diverged` on a fixture project lists exactly the modified framework files. The first sync on a fixture with an empty sync state overwrites nothing and reports conflicts. A fixture built from pack v1.2 files with one local edit is adopted with a correct manifest (that one file `diverged`, the rest matched) and zero overwrites. `/devteam-status` prints the behind-pack warning.

## 3. E-A — hooks resolve the main checkout; session-start knows the task; config is human-only

**Required:**
1. `hooks/lib.js` `mainRoot()` via `git rev-parse --git-common-dir`, with fallback to `repoRoot()`. It is used for PLAN.md, `autopilot.json` and `.devteam/**` (gateguard denials included). `relPath` stays worktree-relative.
2. Dispatch exports `DEVTEAM_TASK=<id>` alongside `DEVTEAM_UNIT`. `session-start.js` and the firewall use it as *the* active task when set, fixing H11's wrong-task banner (oikonomos TASK-332 vs TASK-338).
3. **`autopilot.json` and `autopilot.local.json` are writable only by a human session.** The firewall denies writes to them for any session with `DEVTEAM_UNIT` set **or** `DEVTEAM_DELEGATED=1`. Dispatch and every ORCH-spawned headless or background agent export the latter. This addresses KERYX SB-10, where a 14 h delegated agent flipped `control.mode`.
4. *(Optional, self-hosting)* `**Protected_Grants:**` task field replacing per-task `PROTECTED_EXCEPTIONS` edits (v1 E-A.3).

**Acceptance:** the real-worktree Node tests from v1 (claim visible only in main → allowed; denial counter lands in main). A session with `DEVTEAM_TASK=TASK-332` reports TASK-332 even when PLAN.md's first in-progress block for that unit is another task. A write to `autopilot.json` with `DEVTEAM_DELEGATED=1` is denied, and allowed with neither variable set.

## 4. E-B — ledgers: review, escalation, triage (the cost fix)

Extends the E-0 port of `a14f8976`. All ledgers live in `RuntimeState` (on disk, H3).

**Required:**
1. **Review ledger.** Keyed by `task_id` + the task branch's **head SHA** at emission. `decide()` emits `REVIEW` only when:
   - the task is `needs_review`, and
   - no ledger entry exists for that key, or the entry ended **without a verdict** and its backoff (5 min × 2^n, capped at `review.max_backoff_minutes`, default 120) has elapsed.

   A new head SHA (the builder pushed a fix) resets the entry. At most **one** `REVIEW` per tick; the oldest task is served first. `.devteam/review.lock` (PID + start time; stale after `review.lock_stale_minutes`, default 90) prevents overlap across `--once` processes.
2. **Markers.** `REVIEW_START task=… sha=… session=…` and `REVIEW_END task=… verdict=approved|rework|none duration=…` in AUTOPILOT_LOG. They make the "4-second review" question in the synthesis answerable.
3. **Escalation ledger (H1).** Key = `kind|task_id|reason_prefix|digit-masked detail` (the masking follows `a14f8976`).
   - Send on first sight or on change; re-send after `escalation.renotify_hours` (default 4 for P2, 1 for P1).
   - **(v2.2)** Timer re-sends per key are capped at `escalation.max_timer_resends` (default 1). After the cap the key is held — `ESCALATION_HELD` logged once per hold period, nothing sent — until the condition changes (new key) or clears. This is H1's "slow timer" made finite: a P1 is never muted (first sight, change and one reminder always go out) but is not repeated indefinitely. The same cap applies to the parked-loop frozen-task P1 reminder (E-C).
   - Otherwise log `ESCALATION_HELD` **once per hold period**, not per tick.
   - HALT from a STOP file logs once per STOP-file mtime.
   - Clear the key when the condition is gone.
4. **Triage ledger (H2).** `triage_counts[task_id][reason_prefix]`, incremented in the executor for **every** reason. The logged detail shows the real attempt number. Ceiling `max_triage_attempts` (default 1; OWNERSHIP_CONFLICT 1; MISSING_DEPENDENCY 2 per `a14f8976`), then one P2 through the escalation ledger. It resets when the task leaves `blocked`.
   - Before shipping, investigate oikonomos's "attempt 1 forever" symptom (SB-8): was it state loss between `--once` processes (see D8 atomicity) or the `stale_resets` mix-up? The regression test must reproduce whichever it was.
5. **One judgment-prompt form** for review, `REVIEW_TG` and triage (v1 E-B.3). No `-p` argument begins with `/`.
6. **Atomic state** (temp file + `os.replace`); a corrupt file is renamed aside with one P2. **Dry-run never saves state.**

**Acceptance (multi-tick, H7):**
- 30 simulated ticks with one `needs_review` task and a review command that exits without a verdict: exactly the backoff schedule of launches (1, then at +5, +15, +35 … minutes), not 30. **Fails today.**
- A new head SHA on that task triggers exactly one immediate review.
- 30 ticks with three `SPEC_AMBIGUITY` tasks produce 3 P2s, not 90. A frozen `max_rework` task produces 1 P1, and a second only after 1 h.
- A STOP file present for 6 h logs HALT once.
- Two `--once` processes started 1 s apart launch at most one review.
- A TOOLING_FAILURE triage runs once, logs "attempt 1", and the next tick emits one P2.

## 5. E-C — durable park state; git heartbeat; durable in-flight tracking

**Required:**
1. **Park state** `RuntimeState.parked = {kind: P1|WAVE_DONE, reason, since}` replaces halt-and-exit for P1 and wave completion. It is honoured on every start, so it works under `--once`. While parked, a tick drains commands, runs maintenance and board/Tower, reaps, and **skips `decide()`/`execute()`**.
   - Unpark on `/resume`, on a P1 condition clearing (plan validates, frozen task changed), or on a `pending` task appearing after WAVE_DONE.
   - The process exits only on STOP, `--max-ticks` or `--budget-minutes`. STOP exits with code 3; `ecosystem.config.js` gets `stop_exit_codes: [3]`.
2. **Heartbeat** = max(`Updated_At`, last commit time on the task branch, dossier mtime) (H5). Surface any `in_progress` task with no heartbeat source newer than `stale_minutes × 4` in `/devteam-status` and the digest, even when redispatch is exhausted (KERYX TASK-113).
3. **In-flight PIDs on disk.** Dispatch and review launches write `.devteam/inflight/<unit|review>.json` `{pid, task_id, cmd, started}`. The reaper checks liveness by PID each tick, replacing the in-memory `inflight` dict, so reaping works across `--once` runs.

**Acceptance:** under 10 simulated `--once` processes, a P1 produces 1 notification and the loop stays parked across all of them. `/resume` from the file inbox (E-K) unparks. A task whose `Updated_At` is 3 days old but whose branch has a 5-minute-old commit is not stale. A dispatch launched by process N and failing is reaped and counted by process N+1.

## 6. E-D — PLAN.md and REVIEW.md stay small

**Required:** v1 E-D (archive `done` blocks to `plan/archive/<YYYY-MM>.md`, leaving stubs; validator and parsers treat stubs as `done`; nightly when over `maintenance.plan_archive_kb`, default 60), **plus**:
- `orchestrator_notes` is capped at `plan.notes_max_chars` (default 4,000). Overflow rotates into `docs/handovers/<date>-notes.md` with a pointer. `validate_plan.py` warns over the cap.
- The REVIEW.md tallies block is **generated** from the rows by `team_stats.py --write-tallies` (KERYX and oikonomos both had hand tallies that matched neither parser).
- `validate_plan.py` warns when PLAN.md exceeds 150 KB.
- **REVIEW.md is machine-readable.** `validate_plan.py --review` rejects blank lines or broken rows inside the verdict table (rwc: a blank line split the table, so later rows fell outside it). The review command writes each verdict's timestamp from the system clock (`date -u` / `[DateTime]::UtcNow`); `team_stats.py` flags `:00:00Z`-rounded stamps (rwc, KERYX: invented times).

**Acceptance:** on a copy of oikonomos's 11,134-line PLAN.md, or a synthetic equivalent with 366 done tasks, the result is under 60 KB, validates, and every archived block round-trips. The generated tallies equal a recount of the rows.

## 7. E-E — clocks stamp time

v1 E-E unchanged: `plan_commit.sh`/`.ps1` rewrite missing, unparseable, future or stale `Updated_At` values in changed blocks to the system UTC time. **Promoted** on field evidence (KERYX SB-9).

## 8. E-F — concurrency-safe blackboard writes; verified claims; pinned bases

**Required:**
1. **`plan_commit` compare-and-swap.** Record PLAN.md's blob SHA at read time. At commit time, if HEAD's PLAN.md differs, re-read, re-apply **only this unit's task-block change** (3-way at block granularity), and retry up to 3 times. Otherwise fail with a clear error; never commit a lost update (KERYX SB-6: 4+ incidents).
2. **Idempotent claim.** A claim for a task already `claimed`/`in_progress` by the same unit is a no-op exit 0 with **no commit** (oikonomos: 21 duplicate claim commits, TASK-347 claimed ×3).
3. **Verified claim before launch.** In strict mode dispatch claims itself. In legacy mode, dispatch polls PLAN.md on the main checkout for up to `dispatch.claim_verify_seconds` (default 120) after launch, looking for the unit's claim flip. If none appears it logs `CLAIM_UNVERIFIED`; the builder's first commit is not rejected, it is held for the next tick's reconciliation.
4. **Pinned base** (extends the E-0 port of `bceb8eb2`). On every fresh claim, dispatch creates `task/<id>-<suffix>` from `<base>` tip in the worktree itself, in both `dispatch.sh` and `.ps1`. Dispatch refuses if PLAN.md has uncommitted changes in the main checkout (oikonomos SYNC_MISMATCH).
5. **Strict `Owned_Paths` grammar.** A comma-separated list of path globs only. `validate_plan.py` rejects prose, parentheses and `TBD` (oikonomos SB-10: parse failures rejected a task's own paths). Notes go in `Description`. A path that must not exist yet is written with the suffix ` (new)`, the only permitted annotation; Wave G-D's preflight uses it.
6. **Legacy-mode blackboard guard** (rwc SB-3: whole-file rewrites, claims committed to `main`, a claim written into another task's block; KERYX lost updates). Until a project is in strict mode, `plan_commit` and the dispatch post-run validation reject a builder PLAN.md change that:
   - touches any block other than the unit's claimed task;
   - changes more than `plan.max_builder_diff_lines` lines (default 40);
   - changes line endings (`.gitattributes` ships `PLAN.md text eol=lf`).

   The rejection is a clear error telling the builder to use `plan_commit` for its own block only.
7. **Strict by default for new projects.** Onboarding writes `control.mode: "strict"` for any project whose active units are all verified CONTROL emitters (every `cli: claude` unit once F3's schema output lands; Codex units per oikonomos's evidence). Existing projects are offered strict in the E-0 upgrade checklist, never flipped silently. The field basis: the only strict-mode project had none of the write-damage class.

**Acceptance:** a builder PLAN.md diff touching another task's block, or rewriting line endings, is rejected by the legacy guard. Two concurrent `plan_commit` calls editing different task blocks both land. Two editing the same block: the second fails loudly and changes nothing. A duplicate claim creates no commit. A fresh claim's branch has the base tip as its parent even if the worktree was on another task branch. `validate_plan.py` rejects `Owned_Paths: src/a.ts (and its tests)`.

## 9. E-G — bookkeeping push policy

**Required:** `git.push_policy`: `"every"` (today) | `"batch"` | `"merge_only"`, with the default **`batch`** for new projects.
- **batch:** plan-only commits (`chore(plan)`, CONTROL applications, status scans) are pushed at most every `git.push_batch_minutes` (default 30), and always on merge or park.
- **merge_only:** plan-only commits are pushed only on merge or park.

The reason: 64 pushes and 64 CI emails in one day in oikonomos, with 43% of commits being bookkeeping. Also, status scans that change nothing produce no commit.

**Acceptance:** under `batch`, 20 plan-only commits in 10 minutes produce 0 pushes, then 1 at the batch boundary. A merge pushes immediately. A no-change status scan creates no commit.

## 10. E-H — Windows as a first-class platform

**Required:**
1. **Runner lifecycle** (KERYX SB-4). `dispatch.ps1` records the runner window PID in `.devteam/launch/<unit>.pid`. Headless runs drop `-NoExit`; the window closes when the builder exits. `worktree.ps1 remove` kills recorded PIDs first, then removes; on `Access is denied` it retries with `robocopy /MIR` from an empty folder and `\\?\` long paths.
2. **Windows CI.** GitHub Actions matrix `windows-latest` + `ubuntu-latest`, each with a fixture repo whose base branch is `master`, running both suites and `harness-audit`. The PS 5.1 parser check runs on Windows.
3. Every place a shell script parses Python output strips CR (extends the E-0 port of `d3f5fc08`).
4. **CLI launch smoke test** (rwc SB-4: 8 dispatch-fix commits in 5 days, covering single-turn flags, sandbox modes, a nonexistent `--yolo`, a TTY requirement, and a non-ASCII prompt). `harness-audit.sh`/`.ps1` launch every *active* unit's CLI through the real dispatch argv, in a scratch worktree, with a no-op prompt that must write one file under its territory and exit. It checks: exit 0, the file written, no TTY prompt, and the CLI version recorded. It also runs on `builder_registry` changes and before the first dispatch of an onboarding.
5. **Line-ending defaults.** The pack's `.gitattributes` (synced as framework-owned) ships `*.sh text eol=lf`, `*.ps1 text eol=crlf` and `PLAN.md text eol=lf`. The smoke test fails on a CR in any `*.sh` in the checkout.

**Acceptance:** the CI matrix is green. A Windows test launches a dummy runner, removes the worktree, and nothing is left locked. The smoke test fails on a fixture registry whose codex argv lacks write access to the worktree, and on a CRLF `dispatch.sh`.

## 11. E-I — the learning loop earns its sessions or stays off

The field evidence: KERYX has 0 instincts; oikonomos ran 75 distiller sessions, got 10 duplicate and unapplied amendments (one targets a missing file), and a retro showing matched work did no better (0.466 vs 0.484).

**Required:**
1. `learning.enabled` (default **false** for new projects; existing projects keep their current behaviour with a one-time notice).
2. Amendments are de-duplicated by `(target file, rule text hash)`, and one targeting a missing file is rejected at creation. Pending amendments appear once in the P0 digest with `/approve AMEND-NNN` / `/rework AMEND-NNN`. After `learning.amend_expiry_days` (default 14) they auto-expire with a log line.
3. `retro.py` gets its unit list from the registry (CX9 included), counts active instincts correctly, and buckets by file, not directory.
4. **Effectiveness gate.** When the retro shows matched-task first-pass rate ≤ overall for 2 consecutive weeks, distillation pauses itself and says so in the digest.
5. `/devteam-status` shows distiller last-run, runs and instincts produced (KERYX: "never ran" was invisible).

**Acceptance:** fixture tests for de-dup, missing-target rejection, expiry, retro counts (including CX9) and auto-pause.

## 12. E-J — one source of truth for roster, status and briefings

**Required:**
1. **Rendered roster.** CLAUDE.md, AGENTS.md and each briefing carry a marked section `<!-- devteam:roster -->…<!-- /devteam:roster -->` that `sync_from_pack.py --render` regenerates from `builders`. The per-unit briefing is chosen by the registry's `briefing` key, and a unit without a real briefing file fails `validate_plan.py --config` (oikonomos: CX9 ran on a briefing that said "unit ID: CX"). A `retire_unit.py <UNIT>` command removes a unit from `active`, re-renders, and leaves history intact.
2. **`superseded` status.** A terminal state alongside `done`, requiring `Superseded_By:` (ADR or task ID). It is excluded from "awaiting ORCH" banners (KERYX TASK-059..062).
3. **Briefing lint** (`validate_plan.py --lint-briefings`). It warns when CLAUDE.md names a task or feature ID as "parked" or "do not" while an open task references it (KERYX FR-025), and when a briefing names a file path that doesn't exist (LekkerSwot SB-1/3).
4. The review standing rules move out of the `review_cmd` JSON string into `.claude/commands/devteam-review.md` (oikonomos SB-12). `review_cmd` only points at it.
5. **Work that isn't a parallel code task** (rwc SB-6/11: the release work left the framework; prose holds in `Blocked_Reason`):
   - **`owner_hold` status** with a required `Hold_On:` (`CREDENTIALS | HARDWARE | ACCOUNT | DECISION | EXTERNAL: <detail>`). The dispatcher never picks it; it is not "blocked" and never triaged. The digest lists holds with their age.
   - **`Type: external`** tasks: no `Owned_Paths`, checklist acceptance criteria, completed by a human or ORCH with an evidence line (URL, console screenshot path, store-review ID). They get a REVIEW row like any task.
   - **Solo lane:** `Assigned_To: ORCH-SOLO` for changes too small to dispatch. The work goes direct to the base branch with `[TASK-NNN]` commits and **still requires a REVIEW row from a different model or a human** (G-A's maker ≠ checker applies: maker = the ORCH session's model). The lane is capped by `plan.solo_max_files` (default 5) per task; over that, it must be dispatched.
6. **`Blocked_Reason` enforcement.** `CATEGORY: detail` with the category from the vocabulary (plus `CAPACITY` from G-E). Prose-only values fail validation (rwc: 7 standing prose reasons).
7. **Frontmatter freshness.** `/devteam-status` and the session-start hook flag `last_updated` / `overall_status` / `orchestrator_notes` older than the newest task `Updated_At` or the newest `[TASK-…]` commit (rwc: frontmatter still describes July).
8. **Untracked-work detector.** `/devteam-status` counts base-branch commits in the last 14 days with no `[TASK-NNN]`/`[ORCH]`/`[MAINT]` tag and reports them as "work outside the plan". rwc's release work would have shown up as ~all commits since 14 Aug.

**Acceptance:** retiring a unit changes all rendered sections in one commit. A `superseded` task without `Superseded_By` fails validation. The lint catches both fixture drifts. An `owner_hold` task is never dispatched or triaged and appears in the digest. An `ORCH-SOLO` task cannot reach `done` without a REVIEW row whose reviewer model differs from the solo session's. A prose `Blocked_Reason` fails validation. Untagged commits are counted.

## 13. E-K — commands through the durable inbox; honest observability

**Required:**
1. The Telegram and Slack listeners **write each accepted command to `.devteam/inbox/<ts>-<update_id>.json`**, and only then persist the Telegram offset. The supervisor drains the inbox through `inbox.drain_inbox` → handler → `inbox.ack` (the existing two-phase path). The in-memory queue is removed. This makes two-way commands work under `--once`, where today they are acked and lost (synthesis §3).
2. Under `--once`, the listener runs one bounded long-poll (≤ `telegram.once_poll_seconds`, default 10) before the drain, so a command sent between ticks is handled in the next tick.
3. **Source-missing is logged once (H6)** by `_dossier_heartbeats`, `inbox.drain_inbox`, `usage_probe` and the gateguard reader: `SOURCE_MISSING <name> <path>`, once per process start and once per day.
4. **Template CONTROL blocks** (task `TASK-NNN`, or fields equal to the prompt example) are classified `UNREPORTED`, not `REJECTED`, with a detail naming a probable provider error. The run log is grepped for known capacity strings (`at capacity`, `402`, `usage limit`), which set `blocked_reason: CAPACITY` via Wave G's registry when available (oikonomos SB-15).
5. Onboarding warns loudly when `telegram` is in `notify_channels` but the env vars are unset, and when the supervisor runs under `--once` without the inbox path (legacy install).

**Acceptance:** under a `--once` harness, a fake Telegram update sent between two process runs is executed exactly once, and the offset advances only after the inbox file exists. A killed process between fetch and ack loses nothing on the next run. A template CONTROL block yields UNREPORTED with the capacity hint when the log contains "at capacity".

---

## 14. Territories and order

| # | Owned_Paths | Depends_On |
|---|---|---|
| **E-0** | `scripts/supervisor.py`, `scripts/dispatch.sh`, `scripts/dispatch.ps1`, `scripts/sync_from_pack.py`, `sync-manifest.json`, `tests/conftest.py`, `tests/test_sync_from_pack.py`, `tests/test_supervisor.py`, `tests/test_dispatch_worktree.py`, `scripts/autopilot-tick.ps1`, plus the ported test-env scrub helper (exact path fixed at decompose from `3e8c3e79`), `autopilot.json` (`framework_version`, `sync.pack_path`), `.claude/commands/devteam-status.md` | — |
| **E-A** | `hooks/**`, `tests/test_gateguard.js`, `scripts/validate_plan.py`, `tests/test_validate_plan.py` | E-0 |
| **E-B** | `scripts/supervisor.py`, `tests/test_supervisor*.py`, `autopilot.json` | E-0 |
| **E-C** | `scripts/supervisor.py`, `tests/test_supervisor.py`, `tests/test_stagnation_signal.py`, `deploy/ecosystem.config.js` | E-B |
| **E-D** | `scripts/plan_archive.py`, `tests/test_plan_archive.py`, `scripts/team_stats.py`, `tests/test_team_stats.py`, `scripts/maintenance.py`, `tests/test_maintenance.py`, `plan/**` | E-A (validator) |
| **E-E** | `scripts/plan_commit.sh`, `scripts/plan_commit.ps1`, `tests/test_plan_commit.py` | E-0 |
| **E-F** | `scripts/plan_commit.sh`, `scripts/plan_commit.ps1`, `tests/test_plan_commit.py`, `scripts/dispatch.sh`, `scripts/dispatch.ps1`, `tests/test_dispatch_worktree.py`, `scripts/validate_plan.py`, `tests/test_validate_plan.py` | E-E, E-A, E-D |
| **E-G** | `scripts/plan_commit.*`, `scripts/control.py`, `tests/test_control.py`, `tests/test_plan_commit.py` | E-F |
| **E-H** | `scripts/dispatch.ps1`, `scripts/worktree.ps1`, `.github/workflows/**`, `tests/test_dispatch_worktree.py`, `scripts/harness-audit.sh`, `scripts/harness-audit.ps1`, `.gitattributes`, `tests/test_harness_smoke.py` | E-F |
| **E-I** | `scripts/distiller.py`, `scripts/retro.py`, `scripts/instincts.py`, `tests/test_distiller.py`, `tests/test_retro.py`, `tests/test_instincts*.py`, `scripts/board_publisher.py` | E-0 |
| **E-J** | `scripts/sync_from_pack.py`, `scripts/retire_unit.py`, `scripts/validate_plan.py`, `scripts/supervisor.py` (owner_hold/solo exclusions, after E-K), `tests/**` (matching), `.claude/commands/devteam-review.md`, `.claude/commands/devteam-status.md`, `.claude/commands/devteam-decompose.md`, `hooks/session-start.js`, `briefings/**`, `docs/COORDINATION_PROTOCOL.md` (ORCH-applied) | E-D, E-F, E-K |
| **E-K** | `scripts/tg_listener.py`, `scripts/slack_listener.py`, `scripts/inbox.py`, `scripts/control.py`, `scripts/supervisor.py`, `tests/test_tg_listener.py`, `tests/test_inbox.py`, `tests/test_control.py` | E-C |

**Critical path:** E-0 → E-B → E-C → E-K. E-A → E-D → E-F → (E-G, E-H) → E-J. E-E and E-I run alongside. Every self-hosting task needs protected-path grants (legacy array until E-A's optional field lands).

## 15. Exit criteria

Suites green on both CI platforms. On a fixture project run as **10 scheduled `--once` processes and then a 12 h `--loop`** with one unreviewable task, three `SPEC_AMBIGUITY` tasks, one frozen task and one Telegram `/answer` sent mid-run:
- **≤ 5 review launches** for the unreviewable task (backoff);
- **3 P2s and 1 P1** in total, each re-sent at most once more by timer;
- the `/answer` is applied exactly once;
- PLAN.md stays under 60 KB;
- in `batch` mode, pushes number ≤ run hours × 2.

## 16. Note for the reviewer

The field says repetition is where this system bleeds. Single-tick tests prove nothing here. Every acceptance item above that mentions ticks, processes or hours must be demonstrated with a multi-tick or multi-process harness and an advanced clock. Harvested fixes (E-0) are reviewed like any task: port evidence, tests, and a note on anything intentionally not ported.
