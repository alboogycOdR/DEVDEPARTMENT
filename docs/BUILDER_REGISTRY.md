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
| `identity` | How a claude-CLI unit is told who it is: `preamble` (default) or `agent`. See "Builder identity" below. |
| `agent_name` | Agent used when `identity: "agent"` (default `devteam-builder`). |
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

0. **Prerequisites, added after reviewing the Claude Code changelog:**
   - **Claude Code ≥ 2.1.225.** Earlier builds had a bug where a transient
     401 replaced a long-lived `CLAUDE_CODE_OAUTH_TOKEN` with a stored
     login's short-lived token, *breaking headless sessions until restart* —
     precisely the silent failure mode a second, unattended login would hit.
     Check with `claude --version`.
   - **Watch for a workspace trust prompt on the first dispatch.** 2.1.225
     added a trust prompt for untrusted directories to `claude agents`,
     matching `claude`'s own behavior. A freshly-created worktree is a
     plausible "untrusted directory", and this is the same *class* of gate
     as Grok's trust dialog that once hung a headless dispatch for hours.
     Your first S5B dispatch into a brand-new worktree should be watched,
     not fired and forgotten.

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


---

## Builder identity — why `identity: "agent"` exists

### The problem

A unit running the literal `claude` CLI auto-loads the project's `CLAUDE.md`,
which is written for ORCH and says *"You are ORCH"*. With `identity:
"preamble"` (the pre-v4.8 behavior, still the default) dispatch works around
this by prepending:

> IMPORTANT IDENTITY OVERRIDE: your project context auto-loaded CLAUDE.md …
> **Ignore CLAUDE.md's ORCH role assignment entirely** for this session …

That text has the exact shape of a prompt-injection attempt: an instruction,
arriving in the prompt, telling the model to disregard its loaded context.
A safety-trained model that treats it with suspicion — or refuses it — is
**behaving correctly, not malfunctioning**. This is not theoretical: a builder
refused a dispatch as prompt injection when embedded quotes truncated the
override mid-sentence (fixed in `deacac3` by passing the prompt as a file,
which fixed the *truncation* but not the underlying shape).

Wording the override more forcefully makes this worse, not better. The fix is
to stop needing an override at all.

### The fix

`identity: "agent"` launches the unit with `--agent devteam-builder`, so its
role comes from a real agent definition (`.claude/agents/devteam-builder.md`)
rather than from prompt text arguing with the loaded context. That file states
the builder role, the authority boundary, and — crucially — frames CLAUDE.md's
ORCH section as *"your counterpart, not you"*: a normal division of labour
between two roles reading one shared handbook, with nothing to override or
ignore. No override text is prepended at all in this mode.

### It is opt-in until you verify it (same gating as control.mode and S5B)

`--agent` behavior with `-p` has **not** been verified against a live CLI by
the pack author (no `claude` CLI in the build environment). The default
therefore remains `preamble`, byte-identical to v4.7. Verify, then flip:

```powershell
# 1. Does --agent work in print mode at all, and does the agent's role land?
claude -p "In one sentence: what is your role, and may you merge branches?" `
  --agent devteam-builder --model claude-sonnet-5
#    Expect: describes itself as a BUILDER unit and says it may not merge.
#    If it answers as ORCH, or errors on the flag, stop — keep `preamble`.

# 2. Does CLAUDE.md still auto-load underneath the agent? (Either answer is
#    fine — the agent file is written to work both ways — but you want to know.)
claude -p "Does a file named CLAUDE.md appear in your context? Quote its first heading." `
  --agent devteam-builder --model claude-sonnet-5

# 3. Flip S5 in autopilot.json:  "identity": "agent"
#    Then preview — the override text must be GONE and --agent present:
powershell -File scripts\dispatch.ps1 -Builder S5 -DryRun
```

Roll back by deleting the `identity` key (or setting it to `"preamble"`).
An unrecognised value also falls back to `preamble` rather than erroring:
identity is not a safety boundary — the territory firewall is — so a typo here
must never strand a builder.

### Scope

`identity` is only consulted for units with `auto_loads_ambient_context: true`
(i.e. the `claude` CLI). GB (Grok) and CX (Codex) never load `CLAUDE.md` and
never receive `--agent`, which is a Claude-only flag.
