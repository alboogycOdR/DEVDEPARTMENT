---
plan_version: 0.1
last_updated: 2026-07-12T00:00:00Z
overall_status: not_started
orchestrator_notes: "Bootstrap plan. Awaiting spec ingestion — drop spec documents into specs/ and run /plan. TASK-000/001 below are worked EXAMPLES demonstrating the schema; ORCH deletes them during first real planning pass."
---

# Project Plan

Coordination blackboard for ORCH (Claude Code), GB (Grok Build), CX (Codex AI).
Rules: `AGENTS.md` (summary) and `docs/COORDINATION_PROTOCOL.md` (authoritative).
Status lifecycle: `pending → claimed → in_progress → needs_review → done`, `blocked` from claimed/in_progress. Builders never set `done`.

## Work Items

### TASK-000
**Title:** EXAMPLE — Implement user authentication endpoints
**Status:** in_progress
**Assigned_To:** GB
**Priority:** high
**Spec_References:** specs/auth-flow.md, specs/data-model.md
**Owned_Paths:** src/auth/**, tests/auth/**
**Depends_On:** —
**Description:** Implement login/logout/refresh endpoints per auth-flow spec §2–4, using the session model from data-model spec §3. JWT with refresh rotation.
**Acceptance_Criteria:**
- [x] POST /login issues access + refresh tokens per spec §2.1
- [ ] Refresh rotation invalidates prior refresh token (spec §3.4)
- [ ] All error paths return the spec §4 error envelope
- [ ] Unit + integration tests green
**Branch:** task/TASK-000-gb
**Started_At:** 2026-07-12T14:15:00Z
**Progress_Notes:**
- [2026-07-12T15:28:00Z] [GB] Login route + JWT handling complete. Unit tests passing; integration tests pending.
**Artifacts:** src/auth/login.ts, tests/auth/login.test.ts
**Test_Evidence:**
- [2026-07-12T15:27:00Z] [GB] `npm test -- auth` → 14/14 unit tests pass
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** GB
**Updated_At:** 2026-07-12T15:28:00Z

### TASK-001
**Title:** EXAMPLE — Data-layer migrations and repository module
**Status:** pending
**Assigned_To:** CX
**Priority:** high
**Spec_References:** specs/data-model.md
**Owned_Paths:** src/db/**, migrations/**, tests/db/**
**Depends_On:** —
**Description:** Create schema migrations and a typed repository layer per data-model spec §1–2. No auth logic — that territory belongs to TASK-000.
**Acceptance_Criteria:**
- [ ] Migrations create all spec §1 tables with constraints
- [ ] Repository CRUD covered by tests
- [ ] Rollback path verified
**Branch:** —
**Started_At:** —
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** ORCH
**Updated_At:** 2026-07-12T00:00:00Z
