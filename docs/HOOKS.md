# DEVDEPARTMENT Hooks — Write-Time Protocol Enforcement (Waves 1 & 2)

Vendored from the ECC hook-architecture pattern, reimplemented for the
Coordination Protocol. Zero npm dependencies (Node stdlib only), cross-platform
(Windows / macOS / Linux), fail-open by design.

## What each hook does

| Hook | Event | Enforces | Blocks? |
|---|---|---|---|
| `territory-firewall.js` | PreToolUse (Edit/Write/MultiEdit/NotebookEdit) | Owned_Paths isolation + protected-paths hard prohibitions for GB/CX. Inert for ORCH. | Yes (exit 2) |
| `secret-scan.js` | PreToolUse (same matcher) | No credentials written to the repo — API keys, tokens, private key blocks, hardcoded password assignments. Applies to ALL units including ORCH. | Yes (exit 2) |
| `session-start.js` | SessionStart | §10 sync-and-orient: injects unit identity, STOP status, active tasks + last Progress_Note, orchestrator_notes, unresolved checkpoints into fresh context. | Never |
| `pre-compact.js` | PreCompact | §10b automated: snapshots resumable state to `.devteam/CHECKPOINT.md` before compaction. | Never |
| `session-end.js` | SessionEnd | Appends a `SESSION_END` audit line to AUTOPILOT_LOG.md + refreshes the checkpoint so abrupt endings resume like compactions. | Never |

## The enforcement model (three rings, now)

1. **Write-time (new):** the firewall physically blocks out-of-territory and
   protected-path writes in hook-capable harnesses. A violation now fails at
   the keystroke, not at review.
2. **Plan-time:** `validate_plan.py` rejects illegal plans before dispatch.
3. **Review-time:** `/devteam-review` diffs every branch against territory.

Rings 2 and 3 remain fully authoritative — the firewall **fails open** on any
internal error precisely because they backstop it. A hook bug can never brick
a session; it can only lose the early warning.

## Unit identity & harness coverage

Hooks resolve the unit from the `DEVTEAM_UNIT` environment variable
(`ORCH` default, `GB`, `CX`, `S5`).

| Unit / harness | Firewall coverage |
|---|---|
| ORCH (Claude Code) | Hooks active; firewall intentionally inert (ORCH has structural authority), secret-scan + lifecycle fully active |
| CX (Codex CLI) | **No hook execution parity in Codex yet** — enforcement remains instruction-based (AGENTS.md/briefing) + sandbox config + review audit. `.codex/config.toml` sets `DEVTEAM_UNIT=CX` so hooks activate automatically if/when Codex ships hook support |
| GB (Grok) | Depends on harness hook support; briefing + review remain the enforcement path |
| S5 (Claude Code, headless builder) | **Full firewall coverage** — S5 runs the literal `claude` CLI, so `.claude/settings.json`'s hooks load exactly as they do for ORCH. `dispatch.ps1`/`dispatch.sh` export `DEVTEAM_UNIT=S5` before launching it specifically so `territory-firewall.js` enforces Owned_Paths mechanically instead of relying on instruction-following alone — the one builder unit that actually gets this today |
| Any Claude Code-based builder session | Full firewall coverage with `DEVTEAM_UNIT=GB|CX|S5` exported |

Asymmetric coverage is accepted and documented: the unit with merge authority
(ORCH) and any Claude-based session are mechanically protected; the rest keep
the existing three-layer protocol guarantees.

## Installation (done by the onboarding/merge prompt)

1. Copy `hooks/` into the project root.
2. Merge the `hooks` object from `hooks/hooks.json` into the project's
   `.claude/settings.json` (create the file if absent; **never** overwrite
   other settings keys). Commands are project-relative and require Node.js
   on PATH.
3. Verify: `node hooks/run-tests.js` → 19 passing.

Do **not** additionally register these hooks in any plugin manifest — the
ECC ecosystem's most common failure is duplicate hook loading.

## Tuning

- Disable temporarily: remove the relevant entry from `.claude/settings.json`
  (there is deliberately no env-var kill switch for the firewall — silent
  disablement would defeat its purpose; disabling must be a visible settings
  edit that review can see).
- Secret patterns live in `hooks/lib.js` (`SECRET_PATTERNS`) — extend there,
  add a test in `run-tests.js` for every new pattern.
- Protected paths live in `hooks/lib.js` (`PROTECTED_FOR_BUILDERS`) and must
  stay in sync with AGENTS.md hard prohibitions.

## Wave 2: harness audit gate

`scripts/harness-audit.sh` is the release gate for DEVDEPARTMENT itself:

```bash
bash scripts/harness-audit.sh              # AgentShield + validator + all test suites
bash scripts/harness-audit.sh --no-shield  # offline mode
```

Run it: before every DEVDEPARTMENT version bump, after any change to `hooks/`,
`.claude/`, `.codex/`, `autopilot.json`, or `scripts/`, and after onboarding
DEVDEPARTMENT into a new project. AgentShield (`npx ecc-agentshield scan`)
audits the agent-config surface itself — secrets in configs, permission
posture, hook injection risks, MCP server risk profiling — and exits ≥2 on
critical findings, which fails the gate.
