# Multi-Agent Dev Team — Orchestrator + Heterogeneous Builders

**Version:** 1.0.0 | **Owner:** Alister Witbooy / Basileia Technologies

One cohesive development team built from three tools:

| Unit | Tool | Role |
|---|---|---|
| **ORCH** | Claude Code | Orchestrator, planner, reviewer. Owns PLAN.md structure, task assignment, review verdicts. |
| **GB** | Grok Build | Builder sub-agent. Executes assigned tasks in an isolated git worktree. |
| **CX** | Codex AI | Builder sub-agent. Executes assigned tasks in an isolated git worktree. |

Coordination happens through **one shared, git-versioned folder** (this repo). No tool shares session memory with another; the **PLAN.md blackboard** plus a strict **update protocol** is the entire coordination fabric.

## Repository Layout

```
/project-root/
├── README.md                      # This file
├── PLAN.md                        # Living blackboard — the single source of coordination truth
├── AGENTS.md                      # Shared conventions ALL three tools must obey
├── CLAUDE.md                      # Claude Code orchestrator briefing (auto-loaded by Claude Code)
├── REVIEW.md                      # Review log (orchestrator-only writes)
├── specs/                         # Spec documents (read-only for builders) — drop your 5 specs here
├── src/                           # Builder output (code)
├── docs/
│   └── COORDINATION_PROTOCOL.md   # Full protocol: lifecycle, ownership, sync, conflict rules
├── briefings/
│   ├── GROK_BUILD_BRIEFING.md     # Paste/point Grok Build at this on launch
│   └── CODEX_BRIEFING.md          # Paste/point Codex at this on launch
├── scripts/
│   ├── validate_plan.py           # Protocol linter — run before/after every builder session
│   ├── dispatch.ps1               # Windows: launch a builder headlessly against PLAN.md
│   ├── dispatch.sh                # Bash equivalent
│   └── worktree.ps1               # Create/remove per-builder git worktrees
├── tests/
│   └── test_validate_plan.py      # Test suite for the validator (pytest)
└── .claude/commands/              # Orchestrator slash commands
    ├── plan.md                    # /plan     — decompose specs into PLAN.md tasks
    ├── dispatch.md                # /dispatch — assign + kick off builders
    ├── status.md                  # /status   — sync scan + health report
    └── review.md                  # /review   — review done items, verdict, follow-ups
```

## Quick Start

```powershell
# 1. Initialise
git init; git add -A; git commit -m "chore: bootstrap multi-agent dev team system"

# 2. Drop your 5 spec documents into specs/

# 3. Open Claude Code in the repo root and run:
#      /plan          → decomposes specs into TASK items in PLAN.md
#      /dispatch      → creates worktrees + launches builders (or prints launch commands)
#      /status        → any time, for a health scan
#      /review        → when tasks hit needs_review

# 4. Builders (manual launch alternative):
.\scripts\dispatch.ps1 -Builder grok    # or -Builder codex
```

## The Three Guarantees

1. **No overlap.** Every task lists `Owned_Paths`. The orchestrator must never assign two active tasks with intersecting paths. `validate_plan.py` enforces this mechanically.
2. **No overwrites.** Builders work in **git worktrees on task branches** (`task/TASK-###-gb` / `-cx`) and may only edit *their own task block* in PLAN.md, append-only.
3. **No drift.** Status lifecycle is a strict state machine; every mutation is timestamped, attributed, and committed. The validator rejects any PLAN.md that violates the protocol, and the orchestrator runs it at every phase boundary.

Full rules: `docs/COORDINATION_PROTOCOL.md`.
