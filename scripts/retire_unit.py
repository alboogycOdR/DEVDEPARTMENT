#!/usr/bin/env python3
"""Retire an active builder and regenerate every marked roster section."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from sync_from_pack import render_rosters


def retire_unit(unit: str, project: Path) -> list[Path]:
    config_path = project / "autopilot.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    active = config.get("builders", {}).get("active")
    if not isinstance(active, list) or unit not in active:
        raise ValueError(f"{unit} is not an active builder")
    active.remove(unit)  # Preserve builders.defined as the historical registry.
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8", newline="\n")
    return render_rosters(project)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("unit")
    parser.add_argument("--project", default=".")
    args = parser.parse_args(argv)
    try:
        changed = retire_unit(args.unit, Path(args.project).resolve())
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))
    print("Retired " + args.unit + "; rendered: " + ", ".join(str(p) for p in changed))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
