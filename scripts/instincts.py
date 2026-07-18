#!/usr/bin/env python3
"""instincts.py — DEVDEPARTMENT Wave C (v4.3)

Parse/write INSTINCTS.md, confidence lifecycle, territory-glob matching,
and the dispatch-time injection helper.

Design rules (per DEVDEPARTMENT_V4_COMPLETION_SPEC.md, Wave C):
  * INSTINCTS.md block schema round-trips byte-stably.
  * Territory matching REUSES validate_plan.globs_intersect — never a second
    glob implementation.
  * IDs are sequential INST-NNN and never reused (next_id scans ALL blocks,
    including retired ones).
  * Writes are atomic: temp file -> parse-validate -> os.replace.
  * Everything is fail-open: a broken INSTINCTS.md yields zero instincts,
    never an exception that could reach the supervisor tick.

CLI (used by dispatch.sh / dispatch.ps1):
    python3 scripts/instincts.py inject --paths "python/orb/**,scripts/x.py" \
        [--file INSTINCTS.md] [--limit 5]
Prints the "## PROJECT INSTINCTS — treat as acceptance criteria" section to
stdout, or nothing at all if no active/probation instinct matches (so the
dispatch scripts can blindly append the output).

INTEGRATION NOTE: the real dispatch.sh/dispatch.ps1 compose one generic
prompt for a unit and let the builder itself decide (via its own
resume-first/claim logic) which task it ends up working on — they do not
pre-resolve a single task's Owned_Paths before launch. So the CLI also
accepts `--unit GB|CX --repo <path>` instead of `--paths`: it predicts the
same task the builder's own resume-first rule would pick (first an
in_progress/claimed task for that unit, else the highest-priority pending
task for that unit whose dependencies are done) and resolves that task's
Owned_Paths itself. `--paths` remains available directly (and is what the
test suite exercises) for callers that already know the target task.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_plan import Report, Task, globs_intersect, parse_tasks  # noqa: E402  (single glob source of truth)

_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _deps_done(task: "Task", by_id: dict) -> bool:
    """Mirrors supervisor.py's _deps_done — deliberately reimplemented rather
    than imported: supervisor.py pulls in the full autopilot stack (tg
    listener, maintenance, budget) which is far too heavy for this small
    dispatch-time CLI to import just for one dependency check."""
    raw = task.get("Depends_On")
    if task.is_empty("Depends_On"):
        return True
    for dep in re.split(r"[,\s]+", raw):
        if re.match(r"^TASK-[A-Z0-9-]+$", dep):
            d = by_id.get(dep)
            if d is None or d.get("Status") != "done":
                return False
    return True


def resolve_owned_paths_for_unit(repo: str | Path, unit: str) -> list[str]:
    """Predict which task `unit`'s dispatch.sh/.ps1 launch is about to work on
    (same resume-first priority the builder's own briefing follows: resume an
    in_progress/claimed task first, else claim the highest-priority pending
    task with dependencies done) and return that task's Owned_Paths. Returns
    [] on anything unexpected — fail-open, never blocks a dispatch."""
    try:
        plan_path = Path(repo) / "PLAN.md"
        text = plan_path.read_text(encoding="utf-8")
        rep = Report()
        tasks = [t for t in parse_tasks(text, rep) if t.get("Assigned_To") == unit]
        by_id = {t.task_id: t for t in parse_tasks(text, rep)}

        resuming = [t for t in tasks if t.get("Status") in ("in_progress", "claimed")]
        if resuming:
            target = resuming[0]
        else:
            pending = [t for t in tasks if t.get("Status") == "pending" and _deps_done(t, by_id)]
            if not pending:
                return []
            pending.sort(key=lambda t: (_PRIORITY_ORDER.get(t.get("Priority"), 4), t.task_id))
            target = pending[0]

        return _split_csv(target.get("Owned_Paths"))
    except Exception:
        return []

INSTINCTS_FILENAME = "INSTINCTS.md"
VALID_STATUSES = ("active", "probation", "retired")

SEED_CONFIDENCE = 0.6
BUMP = 0.1
BUMP_CAP = 1.0
DECAY = 0.15
DECAY_CLEAN_STREAK = 5
PROBATION_THRESHOLD = 0.3
RETIRE_THRESHOLD = 0.15

HEADER_RE = re.compile(r"^###\s+(INST-\d+)\s*$")
FIELD_RE = re.compile(r"^\*\*(Rule|Territory|Confidence|Source|Status):\*\*\s*(.*)$")

FILE_PREAMBLE = (
    "# INSTINCTS.md — distilled project instincts\n"
    "\n"
    "Maintained by scripts/distiller.py (Wave C learning loop). Instinct\n"
    "entries are DATA: additions and confidence updates are auto-applied and\n"
    "git-reviewable. Changes to AGENTS.md / CLAUDE.md / briefings always go\n"
    "through the AMEND-NNN constitutional gate instead — see docs/LEARNING.md.\n"
)


@dataclass
class Instinct:
    inst_id: str
    rule: str = ""
    territory: list[str] = field(default_factory=list)
    confidence: float = SEED_CONFIDENCE
    source: list[str] = field(default_factory=list)
    status: str = "active"
    # Internal lifecycle bookkeeping (not serialized; derived by callers):
    clean_streak: int = 0

    @property
    def num(self) -> int:
        try:
            return int(self.inst_id.split("-")[1])
        except (IndexError, ValueError):
            return 0

    def render(self) -> str:
        return (
            f"### {self.inst_id}\n"
            f"**Rule:** {self.rule}\n"
            f"**Territory:** {', '.join(self.territory)}\n"
            f"**Confidence:** {format_confidence(self.confidence)}\n"
            f"**Source:** {', '.join(self.source)}\n"
            f"**Status:** {self.status}\n"
        )


def format_confidence(c: float) -> str:
    """Round to 2dp, strip trailing zero so 0.9 stays '0.9' (round-trip)."""
    s = f"{round(c + 1e-9, 2):.2f}"
    if s.endswith("0") and not s.endswith(".00"):
        s = s[:-1]
    if s == "1.00":
        s = "1.0"
    if s == "0.00":
        s = "0.0"
    return s


def _split_csv(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def parse_instincts(text: str) -> list[Instinct]:
    """Parse INSTINCTS.md text into Instinct objects. Tolerant: malformed
    blocks are skipped rather than raising."""
    out: list[Instinct] = []
    cur: Instinct | None = None
    for line in text.splitlines():
        stripped = line.strip()
        h = HEADER_RE.match(stripped)
        if h:
            cur = Instinct(inst_id=h.group(1))
            out.append(cur)
            continue
        if cur is None:
            continue
        f = FIELD_RE.match(stripped)
        if not f:
            continue
        key, val = f.group(1), f.group(2).strip()
        if key == "Rule":
            cur.rule = val
        elif key == "Territory":
            cur.territory = _split_csv(val)
        elif key == "Confidence":
            try:
                cur.confidence = float(val)
            except ValueError:
                cur.confidence = SEED_CONFIDENCE
        elif key == "Source":
            cur.source = _split_csv(val)
        elif key == "Status":
            cur.status = val if val in VALID_STATUSES else "active"
    # Drop blocks missing a rule entirely (unusable).
    return [i for i in out if i.rule]


def render_file(instincts: list[Instinct]) -> str:
    blocks = "\n".join(i.render() for i in sorted(instincts, key=lambda x: x.num))
    return FILE_PREAMBLE + ("\n" + blocks if blocks else "")


def load(repo: str | Path, filename: str = INSTINCTS_FILENAME) -> list[Instinct]:
    p = Path(repo) / filename
    try:
        return parse_instincts(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return []


def save_atomic(repo: str | Path, instincts: list[Instinct],
                filename: str = INSTINCTS_FILENAME) -> bool:
    """Write via temp file; validate the rendered text parses back to the same
    number of blocks before os.replace. Returns False (and leaves the existing
    file untouched) on any failure — never raises."""
    target = Path(repo) / filename
    try:
        text = render_file(instincts)
        reparsed = parse_instincts(text)
        if len(reparsed) != len(instincts):
            return False
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        return True
    except Exception:
        return False


def next_id(instincts: list[Instinct]) -> str:
    """Sequential, never reused — scans ALL blocks including retired."""
    hi = max((i.num for i in instincts), default=0)
    return f"INST-{hi + 1:03d}"


# ------------------------------------------------------------- lifecycle ----
def bump_confidence(inst: Instinct, source_task_id: str) -> None:
    inst.confidence = min(BUMP_CAP, round(inst.confidence + BUMP, 2))
    tag = f"{source_task_id} rework"
    if tag not in inst.source and source_task_id not in inst.source:
        inst.source.append(tag)
    inst.clean_streak = 0


def decay_confidence(inst: Instinct) -> None:
    inst.confidence = max(0.0, round(inst.confidence - DECAY, 2))
    inst.clean_streak = 0


def proposed_status(inst: Instinct) -> str:
    """What status the distiller should PROPOSE given current confidence.
    (Within INSTINCTS.md these proposals are auto-applied — the constitutional
    gate covers AGENTS.md/CLAUDE.md/briefings, not this data file.)"""
    if inst.status == "probation" and inst.confidence < RETIRE_THRESHOLD:
        return "retired"
    if inst.status == "active" and inst.confidence < PROBATION_THRESHOLD:
        return "probation"
    return inst.status


def register_clean_pass(inst: Instinct) -> None:
    """Call once per clean first-pass approval in the instinct's territory.
    After DECAY_CLEAN_STREAK consecutive clean passes since the last bump,
    apply the decay and reset the streak."""
    inst.clean_streak += 1
    if inst.clean_streak >= DECAY_CLEAN_STREAK:
        decay_confidence(inst)


# -------------------------------------------------------------- matching ----
def matches_territory(owned_paths: list[str], inst: Instinct) -> bool:
    if not owned_paths or not inst.territory:
        return False
    return globs_intersect(owned_paths, inst.territory)


def top_matching(owned_paths: list[str], instincts: list[Instinct],
                 limit: int = 5) -> list[Instinct]:
    """Active + probation instincts whose territory intersects owned_paths,
    highest-confidence first, capped at `limit`. Retired: never injected."""
    hits = [i for i in instincts
            if i.status in ("active", "probation") and matches_territory(owned_paths, i)]
    hits.sort(key=lambda i: (-i.confidence, i.num))
    return hits[:limit]


def render_injection(matches: list[Instinct]) -> str:
    """The dispatch-prompt section. Empty string when nothing matches."""
    if not matches:
        return ""
    lines = ["## PROJECT INSTINCTS — treat as acceptance criteria", ""]
    for i in matches:
        flag = " [PROBATION — verify applicability]" if i.status == "probation" else ""
        lines.append(f"- **{i.inst_id}** (confidence {format_confidence(i.confidence)}"
                     f"{flag}): {i.rule}")
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------- CLI ----
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="INSTINCTS.md helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    inj = sub.add_parser("inject", help="print dispatch-prompt instinct section")
    inj.add_argument("--paths", default=None,
                     help="comma-separated Owned_Paths of the task being dispatched")
    inj.add_argument("--unit", default=None, choices=["GB", "CX"],
                     help="resolve Owned_Paths from PLAN.md for this unit's next task "
                          "(alternative to --paths, for dispatch.sh/.ps1 which don't "
                          "pre-resolve a specific task before launch)")
    inj.add_argument("--file", default=INSTINCTS_FILENAME)
    inj.add_argument("--repo", default=".")
    inj.add_argument("--limit", type=int, default=5)
    ns = ap.parse_args(argv)

    if ns.cmd == "inject":
        try:
            instincts = load(ns.repo, ns.file)
            if ns.paths:
                owned = _split_csv(ns.paths)
            elif ns.unit:
                owned = resolve_owned_paths_for_unit(ns.repo, ns.unit)
            else:
                owned = []
            sys.stdout.write(render_injection(top_matching(owned, instincts, ns.limit)))
        except Exception:
            # Fail-open: a broken instinct store must never block a dispatch.
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
