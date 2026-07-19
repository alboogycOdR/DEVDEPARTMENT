# AGENTS.md — Shared Conventions (v1.0.0)

Read this **first**, every session, fresh from disk. Full rules: `docs/COORDINATION_PROTOCOL.md` (authoritative). This is the operational summary that Claude Code, Grok Build, and Codex AI all obey.

## Who you are

Identify yourself as exactly one of: `ORCH` (Claude Code, orchestrator/reviewer), `GB` (Grok Build), `CX` (Codex AI), `S5` (Sonnet 5 — Claude Code again, but dispatched headless as a third builder, never as ORCH). Every PLAN.md write you make carries your ID in `Updated_By` and a UTC ISO-8601 `Updated_At`.

`S5` runs the same underlying CLI as `ORCH` — if you are a headless `claude -p` session launched by `scripts/dispatch.ps1`/`dispatch.sh -Builder claude`, your dispatch prompt and `briefings/S5_BUILD_BRIEFING.md` override this file's ORCH-flavored context: you are `S5`, a builder, with none of ORCH's exclusive powers (no merges, no review verdicts, no PLAN.md frontmatter, no other unit's task blocks).

## The ten commandments

1. **PLAN.md is the blackboard.** All coordination flows through it. Never rely on memory of a previous session — re-read it at session start.
2. **Builders only touch their own task block** in PLAN.md, and only the fields marked builder-mutable (Status per lifecycle, Branch/Started_At once, Progress_Notes/Artifacts/Test_Evidence append-only, acceptance-criteria checkboxes, Blocked_Reason). Never edit frontmatter, other tasks, or another unit's lines. Never delete anything.
3. **Only claim tasks assigned to your ID.** `Assigned_To: TBD` is untouchable. Claim = one atomic edit+commit setting `Status: claimed`, `Branch`, `Started_At`.
4. **Owned_Paths is your exclusive territory.** You may create/modify files only under your claimed task's `Owned_Paths`. Need a file outside it? STOP → `Status: blocked`, `Blocked_Reason: OWNERSHIP_CONFLICT`, note what you need. Never edit shared files "quickly".
5. **Work on your task branch in your worktree** (`task/TASK-NNN-gb` or `-cx`). Never commit to `main`. Conventional Commits, every message ends with `[TASK-NNN]`.
6. **specs/ is read-only for builders.** Spec unclear? → `blocked`, `Blocked_Reason: SPEC_AMBIGUITY`, with the precise question.
7. **Status lifecycle is law:** `pending → claimed → in_progress → needs_review → done`, with `blocked` reachable from claimed/in_progress. Builders never set `done` — that verdict belongs to ORCH after review.
8. **No `needs_review` without Test_Evidence.** Run the tests, paste command + result summary. Untested work is unfinished work.
9. **Leave no silent state.** Session end = `in_progress` + note, `needs_review` + evidence, or `blocked` + reason. Heartbeat Progress_Note at every milestone.
10. **When in doubt, block — don't improvise.** A blocked task costs minutes; an overwrite or spec drift costs days.

## Progress_Notes format

```
- [2026-07-12T15:28:00Z] [GB] Login route + JWT handling complete; unit tests green, integration pending.
```

## Project conventions

- Commit style: Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `chore:` ...), scope where useful, `[TASK-NNN]` suffix.
- Code: production-ready — error handling, input validation, logging. No stubs left behind unless the task explicitly scopes them.
- Docs: Markdown. Every non-trivial artifact gets/updates a README section.
- Language/tooling per spec; if unspecified, prefer the dominant language already in `src/`.
