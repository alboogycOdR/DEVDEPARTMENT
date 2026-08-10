#!/usr/bin/env python3
"""builder_registry.py — the single source of truth for which builder units
exist, loaded from autopilot.json's `builders` key.

Why: the roster (GB/CX/S5) was hardcoded and duplicated across ~10 files —
dispatch scripts, validator, hooks, budget, supervisor defaults — with no
single source of truth, which had already produced verified drift (stale
unit lists) and made adding a unit a 10-file hand-edit. This module is what
every Python consumer reads instead. (hooks/lib.js reads the same
autopilot.json shape independently in Node — schema agreement, not a code
dependency.)

Two accepted shapes for `builders` (BOTH must work forever — every project
synced before v4.7 still has the flat array, and sync's add_only_keys merge
deliberately never rewrites an existing key):

  Legacy flat array:   "builders": ["GB", "CX", "S5"]
      -> those units, with LEGACY_DEFINITIONS supplying their entries.
  Registry object:     "builders": {"active": [...], "defined": {...}}
      -> `defined` maps unit ID -> entry; `active` lists dispatchable units
         in supervisor dispatch-priority order. Defined-but-inactive is a
         real state (e.g. S5B configured but awaiting its live
         CLAUDE_CONFIG_DIR verification).

Fail-safe: absent key, unreadable file, malformed JSON, or an
unrecognizable shape -> the legacy 3-unit roster. This mirrors
control.mode's fail-safe-to-legacy precedent: there is always a known-good
answer to "which builders exist", so failing open to it is correct here —
unlike dispatch-time resolution of a SPECIFIC unit's entry, where guessing
wrong risks the wrong worktree/CLI, so consumers must fail closed on an
unknown unit (see resolve()).

Entry schema (per unit):
  cli                       "grok" | "codex" | "claude" — invocation family
  model                     pinned model string, or None for CLI default
  auth                      {"mode": "default"} or
                            {"mode": "config_dir", "value": "<path>"}
                            (sets CLAUDE_CONFIG_DIR for that unit's launch)
  worktree_suffix           -> wt-<suffix>-<project>
  branch_suffix             -> task/TASK-NNN-<suffix>
  briefing                  path to the unit's briefing file
  auto_loads_ambient_context  True for literal `claude` CLI units (they
                            auto-load CLAUDE.md, so dispatch prepends the
                            identity override)
  usage_provider            budget/usage bucket: "codex", "claude",
                            compound "claude:<tag>" for a separate login's
                            independent window, or None (never usage-gated)
  identity                  how a claude-CLI unit is told who it is:
                            "preamble" (default) prepends the IDENTITY
                            OVERRIDE text to the prompt; "agent" launches
                            with `--agent <agent_name>` so the role comes
                            from a real agent definition instead. See
                            docs/BUILDER_REGISTRY.md "Builder identity".
  agent_name                agent to use when identity == "agent"
                            (default "devteam-builder")
"""
from __future__ import annotations

import json
from pathlib import Path

# The pre-registry roster, byte-for-byte equivalent to the old hardcoded
# tables in dispatch.sh/.ps1, validate_plan.py, hooks/lib.js, budget.py.
LEGACY_DEFINITIONS: dict[str, dict] = {
    "GB": {
        "cli": "grok",
        "model": None,
        "auth": {"mode": "default"},
        "worktree_suffix": "grok",
        "branch_suffix": "gb",
        "briefing": "briefings/GROK_BUILD_BRIEFING.md",
        "auto_loads_ambient_context": False,
        "usage_provider": None,
    },
    "CX": {
        "cli": "codex",
        "model": "gpt-5.6-sol",
        "auth": {"mode": "default"},
        "worktree_suffix": "codex",
        "branch_suffix": "cx",
        "briefing": "briefings/CODEX_BRIEFING.md",
        "auto_loads_ambient_context": False,
        "usage_provider": "codex",
    },
    "S5": {
        "cli": "claude",
        "model": "claude-sonnet-5",
        "auth": {"mode": "default"},
        "worktree_suffix": "s5",
        "branch_suffix": "s5",
        "briefing": "briefings/S5_BUILD_BRIEFING.md",
        "auto_loads_ambient_context": True,
        "usage_provider": "claude",
    },
}

REQUIRED_FIELDS = ("cli", "worktree_suffix", "branch_suffix", "briefing")

# Structural (non-builder) units — always valid, never in the registry.
STRUCTURAL_UNITS = frozenset({"ORCH", "SV"})


class RegistryError(ValueError):
    """A specific unit's entry is unusable — consumers of a SPECIFIC unit
    (dispatch) must treat this as fail-closed, not guess."""


def _normalize_entry(unit: str, entry: dict) -> dict:
    """Fill optional fields with safe defaults; reject entries missing
    required ones (a builder with no worktree/branch suffix is a silent-
    collision risk, not a defaultable situation)."""
    if not isinstance(entry, dict):
        raise RegistryError(f"builders.defined['{unit}'] is not an object")
    missing = [f for f in REQUIRED_FIELDS if not entry.get(f)]
    if missing:
        raise RegistryError(
            f"builders.defined['{unit}'] is missing required field(s): {', '.join(missing)}")
    out = dict(entry)
    out.setdefault("model", None)
    out.setdefault("auth", {"mode": "default"})
    out.setdefault("auto_loads_ambient_context", entry.get("cli") == "claude")
    out.setdefault("usage_provider", None)
    # Identity mechanism for claude-CLI units. Default "preamble" = today's
    # behavior exactly; "agent" is opt-in per unit after the live
    # verification in docs/BUILDER_REGISTRY.md, mirroring how control.mode
    # and S5B activation are gated. An unknown value falls back to
    # "preamble" rather than failing: identity is not a safety boundary
    # (the firewall is), and a typo here should not strand a builder.
    if out.get("identity") not in ("preamble", "agent"):
        out["identity"] = "preamble"
    out.setdefault("agent_name", "devteam-builder")
    auth = out["auth"]
    if not isinstance(auth, dict) or auth.get("mode") not in ("default", "config_dir"):
        raise RegistryError(
            f"builders.defined['{unit}'].auth must be "
            f'{{"mode": "default"}} or {{"mode": "config_dir", "value": "<path>"}}')
    if auth.get("mode") == "config_dir" and not auth.get("value"):
        raise RegistryError(f"builders.defined['{unit}'].auth.mode=config_dir requires a value")
    return out


def load_registry(repo: str | Path = ".") -> dict:
    """Returns {"active": [unit,...], "defined": {unit: entry,...}}.

    Never raises for file-level problems (absent/corrupt autopilot.json,
    absent/flat/unrecognizable builders key) — falls back to the legacy
    roster, because "which builders exist" always has a known-good answer.
    DOES raise RegistryError for a structurally-present registry containing
    a broken entry: a project that wrote a registry and got an entry wrong
    should hear about it loudly, not have that entry silently vanish.
    """
    # Normalized so the legacy shape yields IDENTICAL entry shapes to the
    # registry shape — otherwise a consumer sees different fields depending
    # on which config shape the project happens to have, which is exactly
    # the class of drift this module exists to remove.
    legacy = {"active": list(LEGACY_DEFINITIONS.keys()),
              "defined": {u: _normalize_entry(u, e) for u, e in LEGACY_DEFINITIONS.items()}}
    try:
        cfg = json.loads((Path(repo) / "autopilot.json").read_text(encoding="utf-8"))
    except (OSError, FileNotFoundError, json.JSONDecodeError):
        return legacy
    builders = cfg.get("builders")
    if builders is None:
        return legacy
    if isinstance(builders, list):
        # Legacy flat array: the listed units, defined by the built-ins.
        # Unknown IDs in the array are kept in `active` but have no entry —
        # resolve() will fail closed on them, matching the old behavior
        # where dispatch.sh would reject an unknown builder argv.
        return {"active": [str(u) for u in builders],
                "defined": {u: _normalize_entry(u, LEGACY_DEFINITIONS[u])
                            for u in builders if u in LEGACY_DEFINITIONS}}
    if isinstance(builders, dict):
        defined_raw = builders.get("defined")
        if not isinstance(defined_raw, dict) or not defined_raw:
            return legacy
        defined = {u: _normalize_entry(u, e) for u, e in defined_raw.items()}
        active = builders.get("active")
        if not isinstance(active, list) or not active:
            active = list(defined.keys())
        active = [str(u) for u in active]
        unknown_active = [u for u in active if u not in defined]
        if unknown_active:
            raise RegistryError(
                f"builders.active lists undefined unit(s): {', '.join(unknown_active)}")
        return {"active": active, "defined": defined}
    return legacy


def resolve(unit_or_cli: str, repo: str | Path = ".") -> tuple[str, dict]:
    """Resolve a dispatch argv token to (unit_id, entry).

    Accepts a unit ID (GB/CX/S5/S5B/...) or, as a compatibility shim, a
    legacy CLI-family name (grok/codex/claude) which resolves to the FIRST
    active unit running that cli — preserving every existing caller of
    `dispatch.sh grok|codex|claude`. Raises RegistryError when nothing
    matches: dispatch must fail closed, never guess a worktree/CLI.
    """
    reg = load_registry(repo)
    token = str(unit_or_cli)
    if token in reg["defined"]:
        return token, reg["defined"][token]
    for u in reg["active"]:
        entry = reg["defined"].get(u)
        if entry and entry.get("cli") == token:
            return u, entry
    raise RegistryError(
        f"'{token}' is neither a defined unit ID nor the cli of any active unit "
        f"(defined: {', '.join(sorted(reg['defined'])) or 'none'}; "
        f"active: {', '.join(reg['active']) or 'none'})")


def active_units(repo: str | Path = ".") -> list[str]:
    return load_registry(repo)["active"]


def all_unit_ids(repo: str | Path = ".") -> set[str]:
    """Every unit ID that may legally appear in PLAN.md's Updated_By /
    Assigned_To — structural units plus every DEFINED builder (a
    defined-but-inactive unit's past PLAN.md entries stay legal)."""
    return set(STRUCTURAL_UNITS) | set(load_registry(repo)["defined"].keys())


def branch_suffixes(repo: str | Path = ".") -> dict[str, str]:
    return {u: e["branch_suffix"] for u, e in load_registry(repo)["defined"].items()}


def _main(argv: list[str]) -> int:
    """CLI for shell consumers (dispatch.sh): prints KEY=VALUE lines for one
    unit, or the active list. Fail-closed exit 1 with a message on stderr —
    dispatch.sh must not launch anything on an unresolved unit."""
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["resolve", "active"])
    ap.add_argument("token", nargs="?")
    ap.add_argument("--repo", default=".")
    args = ap.parse_args(argv)
    try:
        if args.command == "active":
            print(" ".join(active_units(args.repo)))
            return 0
        if not args.token:
            print("resolve requires a unit/cli token", file=__import__("sys").stderr)
            return 1
        unit, e = resolve(args.token, args.repo)
        auth = e.get("auth") or {}
        print(f"UNIT={unit}")
        print(f"CLI={e['cli']}")
        print(f"MODEL={e.get('model') or ''}")
        print(f"WORKTREE_SUFFIX={e['worktree_suffix']}")
        print(f"BRANCH_SUFFIX={e['branch_suffix']}")
        print(f"BRIEFING={e['briefing']}")
        print(f"AUTO_LOADS_CONTEXT={'true' if e.get('auto_loads_ambient_context') else 'false'}")
        print(f"AUTH_MODE={auth.get('mode', 'default')}")
        print(f"AUTH_VALUE={auth.get('value', '')}")
        print(f"IDENTITY={e.get('identity', 'preamble')}")
        print(f"AGENT_NAME={e.get('agent_name', 'devteam-builder')}")
        return 0
    except RegistryError as exc:
        print(f"builder_registry: {exc}", file=__import__("sys").stderr)
        return 1


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
