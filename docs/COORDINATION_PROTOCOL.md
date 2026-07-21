# Coordination Protocol

**Pack version:** tracks the DEVDEPARTMENT pack release (see README.md version history). A project's copy of this file is kept current via `scripts/sync_from_pack.py` (docs/SYNC.md) — if this file in YOUR project lacks the sections referenced by newer briefings/scripts, run a sync.

This document is the constitution of the multi-agent dev team. **AGENTS.md summarises it; this file is authoritative.** All configured units (ORCH = Claude Code, GB = Grok Build, CX = Codex AI, S5 = Sonnet 5 — Claude Code again, dispatched headless as a third builder) must comply. The orchestrator enforces compliance via `scripts/validate_plan.py` and git history review.

---

## 1. Identities

> The authoritative roster for THIS project is `autopilot.json`'s `builders` registry (see `docs/BUILDER_REGISTRY.md`); ORCH and SV are always present. The rows below show the units as configured at the last protocol sync — illustrative, not an exhaustive enum.

| ID | Tool | Writes to PLAN.md? | Writes to specs/? | Writes to src/? | Writes to REVIEW.md? |
|---|---|---|---|---|---|
| `ORCH` | Claude Code | Yes — full authority | Yes (spec clarifications only, versioned) | No (except merge operations) | Yes — exclusive |
| `GB` | Grok Build | Only its own task blocks, append-only fields | **Never** | Only within `Owned_Paths` of its claimed task | Never |
| `CX` | Codex AI | Only its own task blocks, append-only fields | **Never** | Only within `Owned_Paths` of its claimed task | Never |
| `S5` | Claude Code (headless builder session) | Only its own task blocks, append-only fields | **Never** | Only within `Owned_Paths` of its claimed task | Never |

`S5` runs the identical CLI as `ORCH` — the only thing that distinguishes them is the session: `ORCH` is the interactive session a human drives; `S5` is a headless `claude -p` session launched by `dispatch.ps1`/`dispatch.sh -Builder claude`, whose prompt and `briefings/S5_BUILD_BRIEFING.md` explicitly override CLAUDE.md's ORCH-role framing for that session only. `S5` never has ORCH's powers, regardless of which CLI process happens to be running it.

Every write to PLAN.md must set `Updated_By` to the writer's ID and `Updated_At` to UTC ISO-8601 (`YYYY-MM-DDTHH:MM:SSZ`).

## 2. Status Lifecycle (strict state machine)

```
pending ──▶ claimed ──▶ in_progress ──▶ needs_review ──▶ done
   │            │             │                │
   │            └────────────┴───▶ blocked ◀───┘
   │                                  │
   └──────────── (ORCH only) ◀────────┘   blocked → pending (re-assign) or in_progress (unblock)
```

**Legal transitions and who may perform them:**

| From | To | Actor |
|---|---|---|
| pending | claimed | Assigned builder |
| claimed | in_progress | Assigned builder |
| in_progress | needs_review | Assigned builder (all acceptance criteria self-checked) |
| in_progress | blocked | Assigned builder (must set `Blocked_Reason`) |
| claimed | blocked | Assigned builder |
| needs_review | done | **ORCH only**, after review |
| needs_review | in_progress | **ORCH only** (rework — creates review findings in the task) |
| blocked | pending / in_progress | **ORCH only** |
| pending | pending (re-assignment) | **ORCH only** (change `Assigned_To`) |

Any other transition is a protocol violation. Builders **never** mark their own work `done`.

## 3. Task Schema (PLAN.md blocks)

Each task is a `### TASK-NNN` block. Fields:

| Field | Required | Mutable by builder? | Notes |
|---|---|---|---|
| `Title` | Yes | No | Set by ORCH |
| `Status` | Yes | Per §2 only | |
| `Assigned_To` | Yes | No | `GB`, `CX`, `S5`, or `TBD` |
| `Priority` | Yes | No | `critical` / `high` / `medium` / `low` |
| `Spec_References` | Yes | No | Paths under `specs/` |
| `Owned_Paths` | Yes | No | **Exclusive write territory.** Glob patterns, e.g. `src/auth/**` |
| `Depends_On` | No | No | Task IDs that must be `done` first |
| `Description` | Yes | No | |
| `Acceptance_Criteria` | Yes | No | Checklist; builder ticks `[x]` only |
| `Branch` | Yes | Set once at claim | `task/TASK-NNN-gb` or `-cx` |
| `Started_At` | — | Set once at claim | UTC ISO-8601 |
| `Progress_Notes` | — | **Append-only** | `- [UTC timestamp] [ID] note` |
| `Artifacts` | — | Append-only | Files created/modified |
| `Test_Evidence` | — | Append-only | Command + result summary. Required before `needs_review` |
| `Review_Findings` | — | ORCH only | |
| `Blocked_Reason` | — | Yes, when blocking | |
| `Updated_By` / `Updated_At` | Yes | Yes (every write) | |

## 4. Isolation Model — how overwrites become impossible

Three independent layers; all must hold:

**Layer 1 — Path ownership (logical).**
`Owned_Paths` of any two tasks that are simultaneously active (`claimed`/`in_progress`/`needs_review`) **must not intersect**. ORCH guarantees this at assignment time; `validate_plan.py` re-checks it mechanically. A builder must refuse (→ `blocked`, reason `OWNERSHIP_CONFLICT`) if it discovers it needs to touch a path outside its territory — it never "just quickly edits" a shared file. Shared/cross-cutting files (e.g. a common include, a route registry) are handled by dedicated **integration tasks** owned by exactly one unit, sequenced via `Depends_On`.

**Layer 2 — Git worktrees + task branches (physical).**
Each builder operates in its own worktree:

```
main checkout                          →  ORCH (planning, review, merging)
../wt-grok-<project>   (branch task/TASK-NNN-gb)  →  GB
../wt-codex-<project>  (branch task/TASK-NNN-cx)  →  CX
../wt-s5-<project>     (branch task/TASK-NNN-s5)  →  S5
```

Worktree paths are namespaced by the project's own folder name — not a bare `../wt-grok`/`../wt-codex`/`../wt-s5` — because they're created as siblings of the project root itself, not of DEVDEPARTMENT. Two DEVDEPARTMENT-onboarded projects sharing a parent directory would otherwise compute the identical worktree path and collide; `dispatch.sh`/`.ps1` also refuse to reuse a directory at that path unless it's a confirmed registered worktree of the current repo.

Builders commit **code** to their task branch only, referencing the task ID in every commit (Conventional Commits): `feat(auth): implement login route [TASK-001]`. Only ORCH merges code to `main`, and only after review verdict. (PLAN.md coordination commits are the deliberate exception — see Layer 3 below.)

**Layer 3 — PLAN.md write discipline (coordination).**
> **Named failure mode — "which tree do I commit PLAN.md to?"** This has now been misread in both directions, by different builder CLIs, in live sessions. Getting it backwards is silent — the content of the commit is usually correct either way, so nothing looks broken until coordination state goes stale or a second builder claims an already-claimed task.
>
> The rule, stated once, unambiguously:
> - **Code** → your task branch, in your worktree. Never on `main`. ORCH merges after review.
> - **PLAN.md coordination commits** (claim, status transitions, Progress_Notes, `needs_review`) → **`main`, immediately**, via `git add PLAN.md && git commit && git push . HEAD:main`. Leaving these on your task branch is the violation: a claim nobody else can see is not a claim, and the blackboard stops being a blackboard.
>
> Note the asymmetry is deliberate and not a wart: `main` is the *coordination* surface (must be current for everyone) and simultaneously the *integration* surface (must be gated by review). PLAN.md is coordination, code is integration, so they go to different places by different routes. Anyone auditing a builder's behaviour against "never commit to `main`" without that distinction will flag correct behaviour as a violation — which has happened.
>
> `control.mode: "strict"` (Wave I) removes this class of ambiguity entirely: builders never write PLAN.md at all, and the supervisor is the sole writer. If this failure mode recurs, that is the structural fix, not more prompt wording.

PLAN.md lives on `main`. Builders update it via a **pull → edit own block → commit → push/merge immediately** micro-transaction (or, in the simplest single-machine setup, edit the main-checkout PLAN.md directly since blocks are disjoint — blocks never overlap, so line-level merges are trivially clean). If a merge conflict on PLAN.md ever occurs, the builder resolves only within its own block and never deletes another unit's lines. Frontmatter (`plan_version`, `overall_status`, `orchestrator_notes`) is ORCH-only.

## 5. Sync Protocol — how drift becomes impossible

1. **Session start (builder):** `git pull` (worktree base), re-read AGENTS.md + PLAN.md **fresh from disk** — never from memory of a previous session. Verify its claimed task still exists, is still assigned to it, and status is consistent. If not: stop, write a Progress_Note, set `blocked` with reason `SYNC_MISMATCH`.
2. **Claim is atomic:** builder sets `Status: claimed`, `Branch`, `Started_At`, `Updated_By/At` in one single edit + commit (`chore(plan): claim TASK-NNN [GB]`). If two builders could ever race for one task, assignment (`Assigned_To`) already prevents it — a builder may only claim tasks assigned to its own ID. `Assigned_To: TBD` tasks are untouchable by builders.
3. **Heartbeat:** at every meaningful milestone (≥ every 45 min of active work), append a Progress_Note and commit. Long-silent tasks are flagged by `/status`.
4. **Session end:** builder must leave the task in `in_progress` (with a note), `needs_review` (with Test_Evidence), or `blocked` (with reason). Never `claimed`-and-silent.
5. **Orchestrator sync scan (`/status`):** ORCH re-reads PLAN.md + `git log --all`, cross-checks that every `Artifacts` entry exists on the stated branch, that no active `Owned_Paths` intersect, that timestamps are sane, and runs `validate_plan.py`. Discrepancies become `orchestrator_notes` + corrective actions.

## 6. Phase Workflow

**Phase 1 — Planning (ORCH).** Read all of `specs/`. Decompose into tasks sized for one builder session (roughly 1–4 h of agent work). Define `Owned_Paths` so active sets are disjoint. Sequence cross-cutting work with `Depends_On`. Assign per heuristics (§8). Bump `plan_version`. Commit: `docs(plan): plan vX.Y — N tasks from M specs`.

**Phase 2 — Delegation & Execution.** ORCH creates worktrees (`scripts/worktree.ps1`) and launches builders headlessly (`scripts/dispatch.ps1`) or hands the human the launch commands. Builders claim → implement against specs → self-test → update block → `needs_review`.

**Phase 3 — Monitoring (ORCH).** Periodic `/status`. Unblock, re-assign, split, or re-scope tasks as evidence accumulates.

**Phase 4 — Review & Integration (ORCH).** For each `needs_review` task: check out the branch, diff against `Owned_Paths` (any out-of-territory change = automatic rework), verify acceptance criteria against spec, re-run tests independently (never trust Test_Evidence blindly), record verdict in REVIEW.md + `Review_Findings`. Verdict `approved` → merge branch to main (`--no-ff`, message references TASK-ID), set `done`, delete branch. Verdict `rework` → status back to `in_progress` with findings. Follow-up issues become new tasks, closing the loop.

## 7. Blocking & Escalation

`Blocked_Reason` must be one of: `SPEC_AMBIGUITY`, `MISSING_DEPENDENCY`, `OWNERSHIP_CONFLICT`, `SYNC_MISMATCH`, `TOOLING_FAILURE`, `OTHER:<free text>`. ORCH triage order: spec clarification (edit spec with a versioned changelog note) → dependency re-sequencing → re-assignment → escalate to the human with a concrete decision request (never an open-ended "what should we do?").

## 8. Assignment Heuristics (initial — refine with evidence)

- **GB (Grok Build):** terminal-native, plan/approve loops, git-heavy refactors, infrastructure/scripting, tasks benefiting from parallel sub-agents within one territory.
- **CX (Codex AI):** broad implementation sweeps, sandboxed execution/verification, UI or multi-file feature builds, tasks with heavy tool/computer-use.
- **S5 (Sonnet 5, headless builder):** same model as ORCH's own judgment — tasks with real spec ambiguity risk, cross-cutting refactors that benefit from careful reading before writing, or anything where a lower-capability builder has shown repeat rework. Shares ORCH's own usage-window budget (`scripts/budget.py`'s `UNIT_TO_PROVIDER`), so heavy S5 dispatch genuinely competes with ORCH's own session for quota — weigh that against GB/CX when assigning.
- Track per-unit outcomes in REVIEW.md (`first-pass approval rate`, rework causes). After ~10 reviews, ORCH updates this section with observed strengths.

## 9. Change Control

This protocol and AGENTS.md are versioned. Only ORCH edits them, only between batches (never mid-flight), with a changelog entry and a `plan_version` minor bump so builders re-read on next session start.

---

## 10. Session Continuity — context window exhaustion

All three units can hit context limits. The design principle is that **PLAN.md + git history = complete recoverable state**. No information that matters for resumption should live only in a unit's context window.

### 10a. Builders (GB, CX, and S5)

**When approaching context limit (~80% used):**
1. Commit all pending code changes to the task branch with a conventional commit message.
2. Write a Progress_Note containing: what was finished, what file or function is in progress, the exact next step a cold reader needs to take. Be specific — "next: implement `watchPending()` in testimony_repository.dart, signature already stubbed at line 42" is useful; "continuing implementation" is not.
3. Commit the PLAN.md Progress_Note update. Status stays `in_progress`.
4. Stop cleanly. Do not attempt work you cannot finish before the limit.

**When re-dispatched after a context limit:**
The dispatch script reads PLAN.md fresh. Before looking for a new `pending` task, a builder **must first check for any task Assigned_To its own ID with Status `in_progress` or `claimed`**. If found, resume that task: re-read the Owned_Paths files and last Progress_Notes, then continue from the documented stopping point. Do not re-claim or re-branch — use the existing branch.

> This resume-first rule is the critical one. A re-dispatched builder that skips its own `in_progress` task and claims a new `pending` one creates a ghost task: unfinished code on a dangling branch and a new claim that blocks something else.

**What ORCH does after a builder context limit:**
Nothing special unless the builder left a `blocked` state. A well-behaved builder leaves `in_progress` + Progress_Note + committed code. ORCH re-dispatches the builder using `scripts/dispatch.sh <builder>`. The builder resumes automatically. If the builder left the task in an inconsistent state (no commit, no note), ORCH must inspect `git log -- PLAN.md` and the branch, repair the state, and then re-dispatch.

### 10b. ORCH (Claude Code)

**When approaching context limit:**

Claude Code auto-compacts conversation history, but compaction has a ceiling. When ORCH is at ~80% and rising:

1. Write a detailed `orchestrator_notes` update to PLAN.md frontmatter: what wave/phase is in progress, which tasks are active, what the next ORCH action is, any open decisions awaiting the human. This is the primary handover document.
2. Commit: `chore(plan): ORCH context checkpoint [ORCH]`.
3. Update MEMORY.md if there is project-level context worth preserving across sessions (the auto-memory system handles this, but check it captured the key state).
4. Inform the human that a new session is needed.

**Resuming ORCH in a new Claude Code session:**

Start a new session in the project root. Then recover state in this order:

```bash
# 1. Orient
git log --oneline -15              # see recent ORCH + builder commits
git branch --list "task/*"         # see any open task branches

# 2. Read coordination files (in order)
# CLAUDE.md → PLAN.md → REVIEW.md
```

With those three reads plus the git log, ORCH has full recovery:
- `orchestrator_notes` frontmatter tells you where you were
- Task `Status` fields tell you what's in flight
- `Progress_Notes` in each active task tell you what each builder last did
- REVIEW.md tells you which tasks have already been reviewed and what the verdicts were
- git log confirms which branches exist and what was merged

Then proceed with whatever phase PLAN.md indicates (monitoring, review, dispatch).

**The human's role in ORCH resumption:**
The human starts the new session and can simply say "Resume DEVDEPARTMENT orchestration." A well-written `orchestrator_notes` makes this a one-liner — ORCH reads it and continues without needing a recap.

### 10c. What belongs in Progress_Notes vs. what belongs in code

Progress_Notes are for resumption context, not for code documentation. A good stopping-point note answers: *What is the code state, and what do I do next?* It does NOT need to explain the design — that belongs in specs or commit messages.

Good:
```
- [2026-07-12T18:00:00Z] [GB] Context limit approaching. testimony_repository.dart: watchTestimonies() and watchMyPendingTestimonies() done and committed. Next: implement watchPending() (admin stream, no auth filter) — stub is at line 87. Then setStatus() and updateTestimony(). firestore.rules not started yet.
```

Useless:
```
- [2026-07-12T18:00:00Z] [GB] Pausing due to context limit. Will continue next session.
```
