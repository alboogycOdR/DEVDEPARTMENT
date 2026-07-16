#!/usr/bin/env python3
"""validate_plan.py — Protocol linter for PLAN.md (Coordination Protocol v1.0.0).

Checks:
  1. Frontmatter present with required keys (plan_version, last_updated, overall_status).
  2. Every task block has all required fields.
  3. Status values are legal; Assigned_To is GB/CX/TBD.
  4. State-dependent requirements:
       - blocked        → Blocked_Reason set (and from the allowed vocabulary)
       - needs_review   → Test_Evidence non-empty
       - claimed+       → Branch and Started_At set, Branch suffix matches assignee
  5. Territorial isolation: Owned_Paths of simultaneously ACTIVE tasks
     (claimed / in_progress / needs_review) are pairwise disjoint.
  6. Timestamps parse as UTC ISO-8601 (YYYY-MM-DDTHH:MM:SSZ).
  7. Duplicate task IDs.
  8. Updated_By is a known unit ID.

Exit code 0 = plan legal. Non-zero = violations (printed to stderr).

Usage:
    python scripts/validate_plan.py [path/to/PLAN.md]
"""
from __future__ import annotations

import fnmatch
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

VALID_STATUSES = {"pending", "claimed", "in_progress", "needs_review", "done", "blocked"}
ACTIVE_STATUSES = {"claimed", "in_progress", "needs_review"}
VALID_UNITS = {"ORCH", "GB", "CX"}
VALID_ASSIGNEES = {"GB", "CX", "TBD"}
VALID_PRIORITIES = {"critical", "high", "medium", "low"}
BLOCKED_REASONS = {
    "SPEC_AMBIGUITY", "MISSING_DEPENDENCY", "OWNERSHIP_CONFLICT",
    "SYNC_MISMATCH", "TOOLING_FAILURE",
}
REQUIRED_FIELDS = [
    "Title", "Status", "Assigned_To", "Priority", "Spec_References",
    "Owned_Paths", "Description", "Acceptance_Criteria",
    "Updated_By", "Updated_At",
]
BRANCH_SUFFIX = {"GB": "-gb", "CX": "-cx"}
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
EMPTY_VALUES = {"", "—", "-", "--", "n/a", "none"}


@dataclass
class Task:
    task_id: str
    line: int
    fields: dict[str, str] = field(default_factory=dict)

    def get(self, key: str) -> str:
        return self.fields.get(key, "").strip()

    def is_empty(self, key: str) -> bool:
        return self.get(key).lower() in EMPTY_VALUES


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, msg: str) -> None:
        self.errors.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_frontmatter(text: str, rep: Report) -> dict[str, str]:
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n", text, re.DOTALL)
    if not m:
        rep.error("FRONTMATTER: missing or malformed YAML frontmatter block at top of PLAN.md")
        return {}
    fm: dict[str, str] = {}
    for raw in m.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        fm[k.strip()] = v.strip().strip('"')
    for key in ("plan_version", "last_updated", "overall_status"):
        if key not in fm:
            rep.error(f"FRONTMATTER: required key '{key}' missing")
    if "last_updated" in fm and not TS_RE.match(fm["last_updated"]):
        rep.error(f"FRONTMATTER: last_updated '{fm['last_updated']}' is not UTC ISO-8601 (YYYY-MM-DDTHH:MM:SSZ)")
    return fm


def parse_tasks(text: str, rep: Report) -> list[Task]:
    tasks: list[Task] = []
    current: Task | None = None
    current_field: str | None = None
    field_re = re.compile(r"^\*\*([A-Za-z_]+):\*\*\s*(.*)$")
    header_re = re.compile(r"^###\s+(TASK-\d+)\s*$")

    for i, line in enumerate(text.splitlines(), start=1):
        h = header_re.match(line.strip())
        if h:
            current = Task(task_id=h.group(1), line=i)
            tasks.append(current)
            current_field = None
            continue
        if current is None:
            continue
        f = field_re.match(line.strip())
        if f:
            current_field = f.group(1)
            current.fields[current_field] = f.group(2).strip()
        elif current_field and line.strip():
            # Continuation line (multi-line field, e.g. Progress_Notes bullets)
            current.fields[current_field] += "\n" + line.rstrip()
    return tasks


def check_timestamp(value: str, ctx: str, rep: Report) -> None:
    if not TS_RE.match(value):
        rep.error(f"{ctx}: timestamp '{value}' is not UTC ISO-8601 (YYYY-MM-DDTHH:MM:SSZ)")
        return
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        rep.error(f"{ctx}: timestamp '{value}' is not a real datetime")


def globs_intersect(globs_a: list[str], globs_b: list[str]) -> bool:
    """Conservative intersection test for '**'-style path globs.

    Two globs are considered intersecting if either's prefix (portion before
    the first wildcard) is a path-prefix of the other's, or either glob
    fnmatch-matches the other's prefix. Conservative = may flag false
    positives, never false negatives for prefix-style territories.
    """
    def prefix(g: str) -> str:
        for i, ch in enumerate(g):
            if ch in "*?[":
                return g[:i]
        return g

    for a in globs_a:
        for b in globs_b:
            pa, pb = prefix(a).rstrip("/"), prefix(b).rstrip("/")
            if not pa or not pb:
                return True  # a bare wildcard territory intersects everything
            if pa == pb or pa.startswith(pb + "/") or pb.startswith(pa + "/"):
                return True
            if fnmatch.fnmatch(pa, b) or fnmatch.fnmatch(pb, a):
                return True
    return False


def parse_owned_paths(raw: str) -> list[str]:
    return [p.strip() for p in re.split(r"[,\n]", raw) if p.strip() and p.strip() not in EMPTY_VALUES]


def validate(text: str) -> Report:
    rep = Report()
    parse_frontmatter(text, rep)
    tasks = parse_tasks(text, rep)

    if not tasks:
        rep.warn("No task blocks found (### TASK-NNN). Empty plan.")
        return rep

    seen: dict[str, int] = {}
    for t in tasks:
        ctx = f"{t.task_id} (line {t.line})"
        if t.task_id in seen:
            rep.error(f"{ctx}: duplicate task ID (first seen line {seen[t.task_id]})")
        else:
            seen[t.task_id] = t.line

        for fld in REQUIRED_FIELDS:
            if fld not in t.fields or t.is_empty(fld):
                rep.error(f"{ctx}: required field '{fld}' missing or empty")

        status = t.get("Status")
        if status and status not in VALID_STATUSES:
            rep.error(f"{ctx}: illegal Status '{status}' (allowed: {sorted(VALID_STATUSES)})")

        assignee = t.get("Assigned_To")
        if assignee and assignee not in VALID_ASSIGNEES:
            rep.error(f"{ctx}: illegal Assigned_To '{assignee}' (allowed: {sorted(VALID_ASSIGNEES)})")

        prio = t.get("Priority")
        if prio and prio not in VALID_PRIORITIES:
            rep.error(f"{ctx}: illegal Priority '{prio}'")

        upd_by = t.get("Updated_By")
        if upd_by and upd_by not in VALID_UNITS:
            rep.error(f"{ctx}: Updated_By '{upd_by}' is not a known unit (ORCH/GB/CX)")

        if not t.is_empty("Updated_At"):
            check_timestamp(t.get("Updated_At"), f"{ctx}: Updated_At", rep)
        if not t.is_empty("Started_At"):
            check_timestamp(t.get("Started_At"), f"{ctx}: Started_At", rep)

        # State-dependent requirements
        if status == "blocked":
            reason = t.get("Blocked_Reason")
            if t.is_empty("Blocked_Reason"):
                rep.error(f"{ctx}: Status is blocked but Blocked_Reason is empty")
            elif reason not in BLOCKED_REASONS and not reason.startswith("OTHER:"):
                rep.error(f"{ctx}: Blocked_Reason '{reason}' not in vocabulary {sorted(BLOCKED_REASONS)} or 'OTHER:<text>'")

        if status == "needs_review" and t.is_empty("Test_Evidence"):
            rep.error(f"{ctx}: Status is needs_review but Test_Evidence is empty — untested work is unfinished work")

        if status in ACTIVE_STATUSES:
            if t.is_empty("Branch"):
                rep.error(f"{ctx}: Status '{status}' requires Branch to be set")
            if t.is_empty("Started_At"):
                rep.error(f"{ctx}: Status '{status}' requires Started_At to be set")
            if assignee == "TBD":
                rep.error(f"{ctx}: active task cannot be Assigned_To TBD")
            branch = t.get("Branch")
            if branch and assignee in BRANCH_SUFFIX:
                expected = f"task/{t.task_id}{BRANCH_SUFFIX[assignee]}"
                if branch != expected:
                    rep.error(f"{ctx}: Branch '{branch}' should be '{expected}' for assignee {assignee}")

        # Dependencies exist
        deps = t.get("Depends_On")
        if not t.is_empty("Depends_On"):
            for dep in re.split(r"[,\s]+", deps):
                if dep and dep not in EMPTY_VALUES and not re.match(r"^TASK-\d+$", dep):
                    rep.error(f"{ctx}: malformed Depends_On entry '{dep}'")

    # Dependency references resolve
    ids = set(seen)
    for t in tasks:
        if not t.is_empty("Depends_On"):
            for dep in re.split(r"[,\s]+", t.get("Depends_On")):
                if re.match(r"^TASK-\d+$", dep) and dep not in ids:
                    rep.error(f"{t.task_id}: Depends_On references unknown task '{dep}'")

    # Territorial isolation across active tasks
    active = [t for t in tasks if t.get("Status") in ACTIVE_STATUSES]
    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            a, b = active[i], active[j]
            pa, pb = parse_owned_paths(a.get("Owned_Paths")), parse_owned_paths(b.get("Owned_Paths"))
            if pa and pb and globs_intersect(pa, pb):
                rep.error(
                    f"ISOLATION: active tasks {a.task_id} ({a.get('Owned_Paths')}) and "
                    f"{b.task_id} ({b.get('Owned_Paths')}) have intersecting Owned_Paths — "
                    f"two builders must never share territory"
                )

    return rep


def main(argv: list[str]) -> int:
    path = Path(argv[1]) if len(argv) > 1 else Path("PLAN.md")
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 2
    rep = validate(path.read_text(encoding="utf-8"))
    for w in rep.warnings:
        print(f"WARN  {w}", file=sys.stderr)
    for e in rep.errors:
        print(f"ERROR {e}", file=sys.stderr)
    if rep.ok:
        print(f"OK    {path} is protocol-legal ({len(rep.warnings)} warning(s))")
        return 0
    print(f"FAIL  {len(rep.errors)} violation(s) in {path}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
