#!/usr/bin/env python3
"""ATLAS command-line façade.

The implementation intentionally lives in ``atlas_core`` so later ATLAS
increments can add commands without modifying this file.
"""
from __future__ import annotations

import argparse
import importlib
import sys

from atlas_core import register as register_core


class AtlasArgumentParser(argparse.ArgumentParser):
    """Keep ATLAS's public exit-code contract distinct from argparse's 2."""

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(1, f"atlas: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    parser = AtlasArgumentParser(prog="atlas.py", description="ATLAS project map")
    subparsers = parser.add_subparsers(dest="command")
    register_core(subparsers)

    # Extension modules are separate task territories.  Their absence is normal
    # during a staged rollout; retain a helpful hint without making core fail.
    missing: list[str] = []
    for name in ("atlas_episodes", "atlas_cards", "atlas_pack"):
        try:
            module = importlib.import_module(name)
        except ModuleNotFoundError as exc:
            if exc.name == name:
                missing.append(name.removeprefix("atlas_"))
                continue
            raise
        module.register(subparsers)
    parser.set_defaults(_missing_extensions=missing)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    missing = getattr(parser.parse_args([]), "_missing_extensions", [])
    if argv and not argv[0].startswith("-") and argv[0] in missing:
        print(f"atlas: '{argv[0]}' is not installed in this ATLAS increment", file=sys.stderr)
        return 1
    args = parser.parse_args(argv)
    if not hasattr(args, "handler"):
        parser.print_help()
        if args.command in getattr(args, "_missing_extensions", []):
            print(f"atlas: '{args.command}' is not installed in this ATLAS increment", file=sys.stderr)
        return 1
    try:
        return int(args.handler(args) or 0)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"atlas: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
