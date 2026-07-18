#!/usr/bin/env python3
"""team_stats.py — Learning assignment engine.

Parses REVIEW.md verdict rows and emits per-unit performance metrics that
/plan and /dispatch use for evidence-based assignment (protocol §8), and that
the autopilot includes in P0 digests.

Expected verdict row format (as written by /devteam-review):
    | TASK-006 | GB | approved | Territory clean; tests verified | yes | 2026-07-12T19:55:00Z |

Usage:
    python scripts/team_stats.py [REVIEW.md] [--json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROW_RE = re.compile(
    r"^\|\s*(TASK-[A-Z0-9-]+)\s*\|\s*(GB|CX)\s*\|\s*(approved|rework)\s*\|"
    r"\s*(.*?)\s*\|\s*(?:first-pass:\s*)?(yes|no)\s*\|\s*([0-9T:Z-]+)\s*\|\s*$",
    re.IGNORECASE,
)

REWORK_CATEGORIES = {
    "territory": ["territory", "owned_paths", "outside"],
    "tests": ["test", "coverage", "failing", "evidence"],
    "spec": ["spec", "criterion", "criteria", "requirement"],
    "quality": ["error handling", "validation", "logging", "dead code", "credential", "security"],
    "protocol": ["plan.md", "frontmatter", "protocol", "block"],
}


def categorize(findings: str) -> str:
    low = findings.lower()
    for cat, keys in REWORK_CATEGORIES.items():
        if any(k in low for k in keys):
            return cat
    return "other"


def compute(text: str) -> dict:
    units: dict[str, dict] = {
        u: {"reviews": 0, "approved": 0, "rework": 0, "first_pass": 0,
            "rework_causes": defaultdict(int), "tasks": []}
        for u in ("GB", "CX")
    }
    for line in text.splitlines():
        m = ROW_RE.match(line.strip())
        if not m:
            continue
        task, unit, verdict, findings, first_pass, ts = m.groups()
        unit = unit.upper()
        u = units[unit]
        u["reviews"] += 1
        u["tasks"].append({"task": task, "verdict": verdict.lower(), "ts": ts})
        if verdict.lower() == "approved":
            u["approved"] += 1
            if first_pass.lower() == "yes":
                u["first_pass"] += 1
        else:
            u["rework"] += 1
            u["rework_causes"][categorize(findings)] += 1

    out = {}
    for unit, u in units.items():
        n = u["reviews"]
        out[unit] = {
            "reviews": n,
            "approved": u["approved"],
            "rework": u["rework"],
            "first_pass_rate": round(u["first_pass"] / n, 2) if n else None,
            "rework_causes": dict(u["rework_causes"]),
        }
    # Assignment hint
    gb, cx = out["GB"], out["CX"]
    if (gb["reviews"] + cx["reviews"]) >= 10 and gb["first_pass_rate"] is not None and cx["first_pass_rate"] is not None:
        diff = gb["first_pass_rate"] - cx["first_pass_rate"]
        if abs(diff) >= 0.2:
            better = "GB" if diff > 0 else "CX"
            out["assignment_hint"] = (f"{better} has a materially higher first-pass rate "
                                      f"({max(gb['first_pass_rate'], cx['first_pass_rate'])} vs "
                                      f"{min(gb['first_pass_rate'], cx['first_pass_rate'])}) — "
                                      f"weight critical-priority tasks toward {better}.")
        else:
            out["assignment_hint"] = "Units performing comparably — keep balanced assignment."
    else:
        out["assignment_hint"] = "Insufficient evidence (<10 reviews) — use protocol §8 static heuristics."
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default="REVIEW.md")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    p = Path(args.path)
    if not p.exists():
        print(f"ERROR: {p} not found", file=sys.stderr)
        return 2
    stats = compute(p.read_text(encoding="utf-8"))
    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        for unit in ("GB", "CX"):
            s = stats[unit]
            print(f"{unit}: {s['reviews']} reviews | {s['approved']} approved | {s['rework']} rework | "
                  f"first-pass rate: {s['first_pass_rate']} | rework causes: {s['rework_causes']}")
        print(f"HINT: {stats['assignment_hint']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
