#!/usr/bin/env python3
"""team_stats.py — Learning assignment engine.

Parses REVIEW.md verdict rows and emits per-unit performance metrics that
/plan and /dispatch use for evidence-based assignment (protocol §8), and that
the autopilot includes in P0 digests.

Expected verdict row format (as written by /devteam-review):
    | TASK-006 | GB | approved | Territory clean; tests verified | yes | 2026-07-12T19:55:00Z |

Usage:
    python scripts/team_stats.py [REVIEW.md] [--json]
    python scripts/team_stats.py [REVIEW.md] --write-tallies
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROW_RE = re.compile(
    r"^\|\s*(TASK-[A-Z0-9-]+)\s*\|\s*(GB|CX|S5)\s*\|\s*(approved|rework)\s*\|"
    r"\s*(.*?)\s*\|\s*(?:first-pass:\s*)?(yes|no)\s*\|\s*([0-9T:Z-]+)\s*\|\s*$",
    re.IGNORECASE,
)

BUILDER_UNITS = ("GB", "CX", "S5")

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
        for u in BUILDER_UNITS
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
    # Assignment hint — generalized over however many builder units have
    # evidence (originally GB-vs-CX only; now N-way so a third+ unit like S5
    # participates in the same evidence-based heuristic instead of being
    # silently excluded from the comparison).
    total_reviews = sum(out[u]["reviews"] for u in BUILDER_UNITS)
    rated = {u: out[u]["first_pass_rate"] for u in BUILDER_UNITS if out[u]["first_pass_rate"] is not None}
    if total_reviews >= 10 and len(rated) >= 2:
        best_unit = max(rated, key=rated.get)
        worst_unit = min(rated, key=rated.get)
        diff = rated[best_unit] - rated[worst_unit]
        if diff >= 0.2:
            out["assignment_hint"] = (f"{best_unit} has a materially higher first-pass rate "
                                      f"({rated[best_unit]} vs {worst_unit}'s {rated[worst_unit]}) — "
                                      f"weight critical-priority tasks toward {best_unit}.")
        else:
            out["assignment_hint"] = "Units performing comparably — keep balanced assignment."
    else:
        out["assignment_hint"] = "Insufficient evidence (<10 reviews, or <2 rated units) — use protocol §8 static heuristics."
    return out


def _format_causes(causes: dict) -> str:
    if not causes:
        return "—"
    items = sorted(causes.items(), key=lambda kv: (-kv[1], kv[0]))
    return "; ".join(f"{name} ({count})" for name, count in items)


def tally_counts(text: str) -> dict:
    """Recount the verdict rows into the tallies-table columns.

    ``first_pass_approvals`` counts approved rows whose first-pass cell is
    ``yes``. That is the table column, not ``compute()``'s rate.
    """
    counts: dict[str, dict] = {
        u: {"reviews": 0, "first_pass_approvals": 0, "rework": 0, "causes_map": defaultdict(int)}
        for u in BUILDER_UNITS
    }
    for line in text.splitlines():
        m = ROW_RE.match(line.strip())
        if not m:
            continue
        _task, unit, verdict, findings, first_pass, _ts = m.groups()
        unit = unit.upper()
        if unit not in counts:
            continue
        bucket = counts[unit]
        bucket["reviews"] += 1
        if verdict.lower() == "approved" and first_pass.lower() == "yes":
            bucket["first_pass_approvals"] += 1
        if verdict.lower() == "rework":
            bucket["rework"] += 1
            bucket["causes_map"][categorize(findings)] += 1
    for bucket in counts.values():
        bucket["causes"] = _format_causes(bucket.pop("causes_map"))
    return counts


def render_tally_table(counts: dict) -> str:
    lines = [
        "| Unit | Reviews | First-pass approvals | Rework | Common rework causes |",
        "|---|---|---|---|---|",
    ]
    for unit in BUILDER_UNITS:
        c = counts[unit]
        lines.append(
            f"| {unit} | {c['reviews']} | {c['first_pass_approvals']} | "
            f"{c['rework']} | {c['causes']} |"
        )
    return "\n".join(lines)


_TALLY_SECTION = re.compile(
    r"(## Per-unit performance tallies[ \t]*\n)(.*?)(?=\n## |\Z)",
    re.DOTALL,
)


def write_tallies(text: str) -> str:
    """Replace the tallies table with one generated from the verdict rows.

    Verdict rows are not rewritten. A missing tallies section is inserted
    immediately above ``## Verdicts`` (or at the end).
    """
    table = render_tally_table(tally_counts(text))
    m = _TALLY_SECTION.search(text)
    if not m:
        block = f"## Per-unit performance tallies\n\n{table}\n\n"
        verdicts = re.search(r"^## Verdicts[ \t]*$", text, re.MULTILINE)
        if verdicts:
            return text[:verdicts.start()] + block + text[verdicts.start():]
        return text.rstrip() + "\n\n" + block
    body_lines = m.group(2).splitlines()
    start = next((i for i, line in enumerate(body_lines) if line.strip().startswith("|")), None)
    if start is None:
        new_body = "\n" + table + "\n" + m.group(2)
        if not new_body.endswith("\n"):
            new_body += "\n"
        return text[:m.start(2)] + new_body + text[m.end(2):]
    end = start
    while end < len(body_lines) and body_lines[end].strip().startswith("|"):
        end += 1
    body_lines[start:end] = table.splitlines()
    new_body = "\n".join(body_lines)
    if m.group(2).endswith("\n") or not new_body.endswith("\n"):
        if not new_body.endswith("\n"):
            new_body += "\n"
    return text[:m.start(2)] + new_body + text[m.end(2):]


def rounded_stamps(text: str) -> list[tuple[str, str]]:
    """Verdict timestamps ending in ``:00:00Z`` — the invented-time pattern."""
    found: list[tuple[str, str]] = []
    for line in text.splitlines():
        m = ROW_RE.match(line.strip())
        if m and m.group(6).endswith(":00:00Z"):
            found.append((m.group(1), m.group(6)))
    return found


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default="REVIEW.md")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-tallies", action="store_true",
                    help="rewrite the per-unit tallies table from the verdict rows")
    args = ap.parse_args(argv)

    p = Path(args.path)
    if not p.exists():
        print(f"ERROR: {p} not found", file=sys.stderr)
        return 2
    text = p.read_text(encoding="utf-8")
    if args.write_tallies:
        rewritten = write_tallies(text)
        if rewritten != text:
            p.write_text(rewritten, encoding="utf-8")
        text = rewritten
    for task, ts in rounded_stamps(text):
        print(f"WARN  rounded stamp {task} {ts}", file=sys.stderr)
    stats = compute(text)
    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        for unit in BUILDER_UNITS:
            s = stats[unit]
            print(f"{unit}: {s['reviews']} reviews | {s['approved']} approved | {s['rework']} rework | "
                  f"first-pass rate: {s['first_pass_rate']} | rework causes: {s['rework_causes']}")
        print(f"HINT: {stats['assignment_hint']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
