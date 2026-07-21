# BUILDER_REGISTRY.md — the configurable builder roster (v4.7)

## What this replaces

Before v4.7 the roster (GB/CX/S5) was hardcoded in ~10 files — dispatch
scripts, validator, firewall hooks, budget, supervisor defaults, docs — with
no single source of truth. Adding a unit meant hand-editing all of them in
lockstep, and verified drift (stale unit lists) had already happened twice.
Now: `autopilot.json`'s `builders` key is the single source of truth, read
by `scripts/builder_registry.py` (Python consumers) and directly by
`hooks/lib.js` (Node — schema agreement, not a code dependency). Adding a
builder is one config entry.

## The two accepted shapes (both work forever)

```jsonc
// Legacy flat array — every pre-v4.7 project has this; never auto-rewritten:
"builders": ["GB", "CX", "S5"]

// Registry object:
"builders": {
  "active": ["GB", "CX", "S5"],          // dispatchable, in supervisor priority order
  "defined": { "<UNIT_ID>": { ...entry... } }   // may include inactive units
}
```

Defined-but-inactive is a real state: the unit resolves, validates, and its
historical PLAN.md entries stay legal, but the supervisor never dispatches
it. The pack ships **S5B** in exactly this state (see Activation below).

## Entry schema

| Field | Meaning |
|---|---|
| `cli` | Invocation family: `grok` / `codex` / `claude`. Picks the CLI-quirk row in dispatch (flags, non-interactive conventions) — deliberately NOT what determines worktree/branch/briefing. |
| `model` | Pinned model string, or `null` for the CLI's own default. |
| `auth` | `{"mode": "default"}` (ambient credentials) or `{"mode": "config_dir", "value": "~/.claude-s5b"}` — dispatch sets `CLAUDE_CONFIG_DIR` to that path **scoped to the launch only** (bash: `env(1)` in the launch subshell; PS 5.1: save/restore in `finally`). |
| `worktree_suffix` | → `wt-<suffix>-<project>` (sibling of the project root). |
| `branch_suffix` | → `task/TASK-NNN-<suffix>`. |
| `briefing` | The unit's briefing file. Two same-cli units may share one (S5/S5B do). |
| `auto_loads_ambient_context` | `true` for literal `claude`-CLI units: they auto-load CLAUDE.md (which says "You are ORCH"), so dispatch prepends the identity-override preamble with a registry-computed peer list. Defaults to `cli == "claude"`. |
| `usage_provider` | Budget/usage bucket: `"codex"`, `"claude"`, `null` (never usage-gated), or compound `"claude:<tag>"` reserved for a separate login's independent window — gated only once the per-account probe exists (increment 9); until then compound providers simply never trip, per fail-open. |

Required: `cli`, `worktree_suffix`, `branch_suffix`, `briefing`. A defined
entry missing any of these is **rejected loudly at load** (a builder with an
unresolved suffix is a silent-collision risk, not a defaultable situation).

## Failure posture — two different rules, on purpose

- **"Which builders exist?"** fails **open** to the legacy 3-unit roster
  (absent/corrupt autopilot.json, absent/unrecognizable builders key) —
  there is always a known-good answer, same precedent as `control.mode`.
- **"Resolve THIS unit for dispatch"** fails **closed** (`RegistryError`,
  dispatch exits 1) — guessing a worktree/CLI wrong hands a builder the
  wrong checkout; there is no safe default.
- **The firewall** fails **closed** on a set-but-unknown `DEVTEAM_UNIT`
  (exit 2 through its normal verdict path — deliberately *not* a thrown
  exception, which its outer fail-open-on-bug catch would convert back into
  an allow). Before v4.7 an unrecognized unit silently got unrestricted
  ORCH permissions; that was a latent security bug, now fixed. An *unset*
  `DEVTEAM_UNIT` still means ORCH (interactive sessions unaffected).

## Dispatch argv

`dispatch.sh` / `dispatch.ps1` accept a **unit ID** (`GB`/`CX`/`S5`/`S5B`)
— the convention the supervisor now generates — or, as a permanent
compatibility shim, a **legacy cli name** (`grok`/`codex`/`claude`), which
resolves to the *first active* unit on that cli, so `dispatch.sh claude`
keeps meaning S5 even with S5B defined.

## Sharing an AI family across tiers

ORCH's own models (`review_cmd`, `judgment_model`, `learning.model`) and any
builder's `model` are independent keys and may reference the same family at
different tiers **on purpose** — Opus orchestrating/reviewing while Sonnet
builds is a supported, documented configuration, not a coincidence of
separate keys. The one discipline that must hold (see
`docs/MODEL_DISCIPLINE.md`): the *checker's exact model* must not equal any
builder's model.

## Activating S5B (the shipped defined-but-inactive unit)

S5B = a second Claude Code builder on a **separate Pro-plan login**, giving
a second independent usage window. It is configured but inactive until you:

1. **Live-verify `CLAUDE_CONFIG_DIR`** (the load-bearing assumption; ~10
   minutes, once per machine):
   ```powershell
   # a) Log the second account into its own config dir:
   $env:CLAUDE_CONFIG_DIR = "$env:USERPROFILE\.claude-s5b"
   claude   # complete the OAuth login for the SECOND account, then exit
   # b) Confirm it stuck and is isolated:
   claude -p "whoami check" --model claude-sonnet-5   # runs as account 2
   Remove-Item Env:\CLAUDE_CONFIG_DIR
   claude -p "whoami check" --model claude-sonnet-5   # runs as account 1
   # c) Concurrency: run one session under each config dir simultaneously;
   #    confirm no errors about locks/shared state.
   ```
2. Adjust `auth.value` in the S5B entry if you used a different path.
3. Add `"S5B"` to `builders.active`.
4. Smoke it: `powershell -File scripts\dispatch.ps1 -Builder S5B -DryRun`
   — the preview must show `wt-s5b-<project>` and the scoped
   `CLAUDE_CONFIG_DIR` note. Then a real dispatch on a small task.

S5B reuses `S5_BUILD_BRIEFING.md` deliberately — same CLI, same model, same
procedure; only credentials differ, and those live in the registry, not the
briefing. (Briefing templating was evaluated and deferred: the three
briefings carry hard-won CLI-specific content — grok's trust-dialog
behavior, S5's identity override — that a shared template would flatten.)

## Adding any future unit

1. Add a `defined` entry (+ `active` when ready).
2. Point `briefing` at an existing file for a same-cli twin, or write one.
3. If it's a new CLI family: add one row to the CLI-invocation table in
   `dispatch.sh`/`.ps1` (the *only* remaining per-CLI code — invocation
   quirks are properties of the binaries, not project config).
4. Nothing else: validator, firewall, supervisor, budget all read the
   registry.
5. If qualitative assignment guidance matters, hand-add a row to
   `docs/COORDINATION_PROTOCOL.md` §8 — that's judgment content, ORCH-only,
   never generated.
