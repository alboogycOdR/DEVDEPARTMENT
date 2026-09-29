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

import argparse
import fnmatch
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

VALID_STATUSES = {"pending", "claimed", "in_progress", "needs_review", "done", "blocked"}
ACTIVE_STATUSES = {"claimed", "in_progress", "needs_review"}
# Registry-driven (v4.7): the module-level values below are the LEGACY
# defaults, kept so this file works standalone (hooks/run-tests.js, a bare
# `python validate_plan.py PLAN.md` in a project with no registry yet).
# main() loads the real roster from autopilot.json via builder_registry and
# passes it into validate() — see _apply_registry().
VALID_UNITS = {"ORCH", "GB", "CX", "S5", "SV"}
VALID_ASSIGNEES = {"GB", "CX", "S5", "TBD"}
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
BRANCH_SUFFIX = {"GB": "-gb", "CX": "-cx", "S5": "-s5"}
# E-D: orchestrator_notes cap and the size at which PLAN.md should have been
# archived. Both are warnings — a fat plan is still a legal plan.
NOTES_MAX_CHARS_DEFAULT = 4000
PLAN_SIZE_WARN_BYTES = 150 * 1024


def _apply_registry(repo: str = "."):
    """Derive VALID_UNITS / VALID_ASSIGNEES / BRANCH_SUFFIX from the
    builder registry. Fail-open to the legacy module defaults on ANY error
    (including a malformed registry): the validator must keep validating —
    a broken autopilot.json is dispatch's problem to fail closed on, not a
    reason PLAN.md can't be linted. Returns (units, assignees, suffixes)."""
    try:
        import builder_registry as _br
        reg = _br.load_registry(repo)
        units = set(_br.STRUCTURAL_UNITS) | set(reg["defined"].keys())
        assignees = set(reg["defined"].keys()) | {"TBD"}
        suffixes = {u: "-" + e["branch_suffix"] for u, e in reg["defined"].items()}
        return units, assignees, suffixes
    except Exception:
        return set(VALID_UNITS), set(VALID_ASSIGNEES), dict(BRANCH_SUFFIX)
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
    # TASK-\d+ (TASK-001) is the original/common shape; TASK-[A-Z0-9-]+ also
    # accepts self-generated escalation IDs like TASK-MAINT-2026-07-19 (Wave
    # B's nightly self-audit) without weakening anything else — a superset,
    # not a redesign of the ID grammar.
    header_re = re.compile(r"^###\s+(TASK-[A-Z0-9-]+)\s*$")

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
    # " (new)" marks a path that must not exist yet (LOOP_HYGIENE E-F.5): an
    # annotation, not part of the glob, so isolation checks compare the real path.
    parts = (re.sub(r"\s+\(new\)$", "", p.strip(), flags=re.I) for p in re.split(r"[,\n]", raw))
    return [p for p in parts if p and p.lower() not in EMPTY_VALUES]


# E-F.5: Owned_Paths is a comma-separated list of path globs only. The single
# permitted annotation is a trailing " (new)"; anything else — prose,
# parenthetical asides, whitespace inside a token, TBD — is rejected so a
# territory-isolation check can never silently skip a malformed entry
# (oikonomos SB-10: prose in this field broke the parser and rejected a
# task's own paths). Notes belong in Description, not here.
_OWNED_PATH_TOKEN = re.compile(r"^[A-Za-z0-9_./*\[\]{},!-]+$")


def check_owned_paths_grammar(raw: str) -> list[str]:
    """Return a list of grammar violations for one raw Owned_Paths value
    (empty list = legal). Each entry, after stripping one trailing
    ' (new)', must be a bare path/glob token: no spaces, no parentheses,
    and not the literal 'TBD'."""
    problems: list[str] = []
    if not raw or raw.strip().lower() in EMPTY_VALUES:
        return problems
    for entry in re.split(r"[,\n]", raw):
        token = entry.strip()
        if not token:
            continue
        stripped = re.sub(r"\s+\(new\)$", "", token, flags=re.I)
        if stripped.upper() == "TBD":
            problems.append(f"'{token}' is TBD, not a path")
            continue
        if "(" in stripped or ")" in stripped:
            problems.append(f"'{token}' has a parenthetical other than the ' (new)' suffix")
            continue
        if not _OWNED_PATH_TOKEN.match(stripped):
            problems.append(f"'{token}' is not a bare path/glob (prose or illegal character)")
    return problems


def _glob_prefix(glob: str) -> str:
    for i, ch in enumerate(glob):
        if ch in "*?[":
            return glob[:i].rstrip("/")
    return glob.rstrip("/")


def grant_within_owned(grant: str, owned: list[str]) -> bool:
    """True when a Protected_Grants entry is a subset of Owned_Paths.

    Equal tokens pass. A more-specific grant passes when an owned glob
    covers it (`hooks/lib.js` under `hooks/**`). A wider grant fails
    (`scripts/**` is not inside `scripts/validate_plan.py`).
    """
    if grant in owned:
        return True
    gp = _glob_prefix(grant)
    if not gp:
        return False
    for entry in owned:
        op = _glob_prefix(entry)
        if not op:
            return True
        if gp == op or gp.startswith(op + "/"):
            return True
    return False


_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _deps_done(task: Task, by_id: dict[str, Task]) -> bool:
    raw = task.get("Depends_On")
    if task.is_empty("Depends_On"):
        return True
    for dep in re.split(r"[,\s]+", raw):
        if re.match(r"^TASK-[A-Z0-9-]+$", dep):
            other = by_id.get(dep)
            if other is None or other.get("Status") != "done":
                return False
    return True


def predict_dispatch_task(repo: str = ".", unit: str = "") -> str:
    """Task id dispatch should export as DEVTEAM_TASK, or '' when it must not pin.

    One claimed/in_progress task for the unit is that task. More than one is
    ambiguous (H11: the first PLAN block is not necessarily this session) and
    returns '' so the firewall keeps every active territory. With nothing to
    resume, the highest-priority pending task whose dependencies are done
    wins; ties break by task id, matching the builder's claim rule.
    Fail-open: any read/parse error returns ''.
    """
    try:
        text = (Path(repo) / "PLAN.md").read_text(encoding="utf-8")
        tasks = parse_tasks(text, Report())
        by_id = {t.task_id: t for t in tasks}
        mine = [t for t in tasks if t.get("Assigned_To") == unit]
        resuming = [t for t in mine if t.get("Status") in ("in_progress", "claimed")]
        if len(resuming) == 1:
            return resuming[0].task_id
        if len(resuming) > 1:
            return ""
        pending = [t for t in mine if t.get("Status") == "pending" and _deps_done(t, by_id)]
        if not pending:
            return ""
        pending.sort(key=lambda t: (_PRIORITY_ORDER.get(t.get("Priority"), 9), t.task_id))
        return pending[0].task_id
    except Exception:
        return ""


def has_resumable_task(repo: str = ".", unit: str = "") -> bool:
    """True when this unit has exactly one claimed/in_progress task to
    resume (LOOP_HYGIENE E-F.4 legacy-mode pinned base): dispatch must NOT
    reset the worktree to the base tip in that case, only when the coming
    session is (or may be) a fresh claim. Fail-closed to True on any
    read/parse error -- an unreadable PLAN.md must never cause a worktree
    reset that could discard resumable in-flight work."""
    try:
        text = (Path(repo) / "PLAN.md").read_text(encoding="utf-8")
        tasks = parse_tasks(text, Report())
        mine = [t for t in tasks if t.get("Assigned_To") == unit]
        resuming = [t for t in mine if t.get("Status") in ("in_progress", "claimed")]
        return len(resuming) > 0
    except Exception:
        return True


def validate(text: str, control_mode: str = "legacy",
             registry_views: tuple | None = None,
             notes_max_chars: int = NOTES_MAX_CHARS_DEFAULT) -> Report:
    # registry_views = (valid_units, valid_assignees, branch_suffixes) from
    # _apply_registry(); None (the safe default, e.g. standalone/test calls)
    # means the legacy module constants — same precedent as control_mode.
    valid_units, valid_assignees, branch_suffix = (
        registry_views if registry_views is not None
        else (set(VALID_UNITS), set(VALID_ASSIGNEES), dict(BRANCH_SUFFIX)))
    builder_units = valid_units - {"ORCH", "SV"}
    rep = Report()
    fm = parse_frontmatter(text, rep)
    tasks = parse_tasks(text, rep)

    if not tasks:
        rep.warn("No task blocks found (### TASK-NNN). Empty plan.")
        _warn_plan_bulk(text, fm, rep, notes_max_chars)
        return rep

    seen: dict[str, int] = {}
    for t in tasks:
        ctx = f"{t.task_id} (line {t.line})"
        if t.task_id in seen:
            rep.error(f"{ctx}: duplicate task ID (first seen line {seen[t.task_id]})")
        else:
            seen[t.task_id] = t.line

        status = t.get("Status")
        # E-D stubs carry only Status + Archived. The full block lives in
        # plan/archive/<YYYY-MM>.md; the id stays done for Depends_On.
        archived = not t.is_empty("Archived")
        if archived:
            if status != "done":
                rep.error(f"{ctx}: an archived stub must have Status done (got '{status or 'empty'}')")
        else:
            for fld in REQUIRED_FIELDS:
                if fld not in t.fields or t.is_empty(fld):
                    rep.error(f"{ctx}: required field '{fld}' missing or empty")
        if status and status not in VALID_STATUSES:
            rep.error(f"{ctx}: illegal Status '{status}' (allowed: {sorted(VALID_STATUSES)})")

        if not t.is_empty("Owned_Paths"):
            for problem in check_owned_paths_grammar(t.get("Owned_Paths")):
                rep.error(f"{ctx}: Owned_Paths {problem}")

        assignee = t.get("Assigned_To")
        if assignee and assignee not in valid_assignees:
            rep.error(f"{ctx}: illegal Assigned_To '{assignee}' (allowed: {sorted(valid_assignees)})")

        prio = t.get("Priority")
        if prio and prio not in VALID_PRIORITIES:
            rep.error(f"{ctx}: illegal Priority '{prio}'")

        upd_by = t.get("Updated_By")
        if upd_by and upd_by not in valid_units:
            rep.error(f"{ctx}: Updated_By '{upd_by}' is not a known unit ({'/'.join(sorted(valid_units))})")
        # Wave I (control.mode=strict): the supervisor is the sole PLAN.md
        # writer, so a builder-state transition (needs_review/blocked) whose
        # Updated_By is GB/CX directly (not SV) suggests a bypassed CONTROL
        # block — worth a warning, not a hard failure (this check only ever
        # warns; it never blocks a tick the way an error does).
        if control_mode == "strict" and status in ("needs_review", "blocked") and upd_by in builder_units:
            rep.warn(f"{ctx}: control.mode=strict but Updated_By is '{upd_by}', not 'SV' — "
                    f"this task's PLAN.md state may have been written directly by a builder "
                    f"instead of via a CONTROL block")

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
            if branch and assignee in branch_suffix:
                expected = f"task/{t.task_id}{branch_suffix[assignee]}"
                if branch != expected:
                    rep.error(f"{ctx}: Branch '{branch}' should be '{expected}' for assignee {assignee}")

        # E-A.4: Protected_Grants is optional and must be a subset of Owned_Paths.
        # A missing field or an em-dash is fine. A done task may still carry
        # the field; the firewall ignores it once the task leaves the active set.
        if not t.is_empty("Protected_Grants"):
            owned = parse_owned_paths(t.get("Owned_Paths"))
            for grant in parse_owned_paths(t.get("Protected_Grants")):
                if not grant_within_owned(grant, owned):
                    rep.error(
                        f"{ctx}: Protected_Grants entry '{grant}' is outside Owned_Paths "
                        f"({t.get('Owned_Paths') or 'none'})"
                    )

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

    # Latent territorial collisions between tasks that are not active YET.
    #
    # The check above fires only once two tasks are already claimed — by which
    # point two builders are in the same files and the damage is done. On this
    # project that gap let four separate collisions through (TASK-006/019 and
    # TASK-008/019/020 on models/__init__.py among them); each was caught by a
    # manual pre-dispatch check, which is exactly the kind of vigilance that
    # works until the one time it doesn't.
    #
    # WARNING, not error, and deliberately so: overlapping territory between
    # two PENDING tasks is perfectly legal as long as they never run together,
    # and Depends_On sequencing is the normal way to guarantee that. Making it
    # an error would flag correctly-sequenced plans and train people to ignore
    # the validator. A warning says "this pair can never be dispatched
    # concurrently" — which is a fact worth knowing at planning time, not at
    # dispatch time.
    def _depends_chain(task_id: str, by_id: dict, seen: set) -> set:
        """All tasks this one transitively depends on."""
        if task_id in seen:
            return seen
        seen.add(task_id)
        t = by_id.get(task_id)
        if t and not t.is_empty("Depends_On"):
            for dep in re.split(r"[,\s]+", t.get("Depends_On")):
                if re.match(r"^TASK-\d+$", dep):
                    _depends_chain(dep, by_id, seen)
        return seen

    by_id = {t.task_id: t for t in tasks}
    unfinished = [t for t in tasks if t.get("Status") != "done"]
    for i in range(len(unfinished)):
        for j in range(i + 1, len(unfinished)):
            a, b = unfinished[i], unfinished[j]
            if a.get("Status") in ACTIVE_STATUSES and b.get("Status") in ACTIVE_STATUSES:
                continue  # already reported as an error above
            pa, pb = parse_owned_paths(a.get("Owned_Paths")), parse_owned_paths(b.get("Owned_Paths"))
            if not (pa and pb and globs_intersect(pa, pb)):
                continue
            # Sequenced by a dependency chain in either direction → cannot be
            # concurrent → nothing to warn about.
            if b.task_id in _depends_chain(a.task_id, by_id, set()) or \
               a.task_id in _depends_chain(b.task_id, by_id, set()):
                continue
            rep.warn(
                f"LATENT ISOLATION: {a.task_id} and {b.task_id} have intersecting Owned_Paths "
                f"and neither depends on the other — they are legal now only because they are not "
                f"both active. Sequence them with Depends_On, or never dispatch them together"
            )

    _warn_plan_bulk(text, fm, rep, notes_max_chars)
    return rep


def _warn_plan_bulk(text: str, fm: dict[str, str], rep: Report, notes_max_chars: int) -> None:
    notes = fm.get("orchestrator_notes", "")
    if len(notes) > notes_max_chars:
        rep.warn(
            f"PLAN: orchestrator_notes is {len(notes)} chars, "
            f"over plan.notes_max_chars={notes_max_chars}"
        )
    nbytes = len(text.encode("utf-8"))
    if nbytes > PLAN_SIZE_WARN_BYTES:
        rep.warn(f"PLAN: PLAN.md is {nbytes} bytes, over 150 KB")


_REVIEW_HEADER = re.compile(r"^\|\s*Task\s*\|\s*Unit\s*\|\s*Verdict\s*\|", re.IGNORECASE)
_REVIEW_SEP = re.compile(r"^\|[\s:\-|]+\|$")


def lint_review(text: str) -> Report:
    """Reject a verdict table that a blank line splits, or a row the grammar misses.

    The row grammar is team_stats.ROW_RE — the same parser the tallies use.
    """
    from team_stats import ROW_RE

    rep = Report()
    lines = text.splitlines()
    header_at = next((i for i, line in enumerate(lines) if _REVIEW_HEADER.match(line.strip())), None)
    if header_at is None:
        rep.error("REVIEW: verdict table header not found")
        return rep
    if header_at + 1 >= len(lines) or not _REVIEW_SEP.match(lines[header_at + 1].strip()):
        rep.error("REVIEW: verdict table is missing its separator row")
        return rep
    i = header_at + 2
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("##"):
            break
        if stripped == "":
            for nxt in lines[i + 1:]:
                nxt_s = nxt.strip()
                if nxt_s.startswith("##") or (nxt_s and not nxt_s.startswith("|")):
                    break
                if nxt_s.startswith("|"):
                    rep.error(
                        f"REVIEW: blank line inside the verdict table at line {i + 1} "
                        f"— later rows fall outside the table"
                    )
                    return rep
            break
        if stripped.startswith("|"):
            if not ROW_RE.match(stripped):
                rep.error(f"REVIEW: broken verdict row at line {i + 1}")
        else:
            break
        i += 1
    return rep


def lint_briefings(repo: str | Path = ".") -> Report:
    """Warn about briefing drift without making ordinary plan validation noisy."""
    root = Path(repo)
    rep = Report()
    try:
        import builder_registry
        registry = builder_registry.load_registry(root)
    except Exception as exc:
        rep.error(f"CONFIG: invalid builders registry: {exc}")
        return rep
    for unit, entry in registry["defined"].items():
        briefing = root / entry["briefing"]
        if not briefing.is_file():
            rep.error(f"CONFIG: {unit} briefing does not exist: {entry['briefing']}")
            continue
        text = briefing.read_text(encoding="utf-8")
        for candidate in re.findall(r"(?<![`\w])(?:scripts|tests|briefings|hooks)/[A-Za-z0-9_./-]+", text):
            if not (root / candidate).exists():
                rep.warn(f"BRIEFING: {entry['briefing']} names nonexistent path {candidate}")
    claude = root / "CLAUDE.md"
    plan = root / "PLAN.md"
    if claude.is_file() and plan.is_file():
        open_ids = {t.task_id for t in parse_tasks(plan.read_text(encoding="utf-8"), Report())
                    if t.get("Status") not in {"done", "superseded"}}
        for line in claude.read_text(encoding="utf-8").splitlines():
            if re.search(r"\b(parked|do not)\b", line, re.I):
                for task_id in re.findall(r"TASK-\d+", line):
                    if task_id in open_ids:
                        rep.warn(f"BRIEFING: CLAUDE.md calls open {task_id} parked/do not")
    return rep


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Protocol linter for PLAN.md")
    ap.add_argument("path", nargs="?", default="PLAN.md")
    ap.add_argument(
        "--review", nargs="?", const="", default=None,
        help="lint a REVIEW.md verdict table instead of PLAN.md (default path: REVIEW.md)",
    )
    ap.add_argument("--config", action="store_true",
                    help="validate the builder registry, including briefing files")
    ap.add_argument("--lint-briefings", action="store_true",
                    help="warn about briefing references that have drifted")
    args = ap.parse_args(argv)
    if args.config or args.lint_briefings:
        repo = Path(args.path).resolve() if Path(args.path).is_dir() else Path(args.path).resolve().parent
        rep = lint_briefings(repo)
        for warning in rep.warnings:
            print(f"WARN  {warning}", file=sys.stderr)
        for error in rep.errors:
            print(f"ERROR {error}", file=sys.stderr)
        if args.config and not rep.ok:
            return 1
        return 0
    if args.review is not None:
        path = Path(args.review) if args.review else Path("REVIEW.md")
        if not path.exists():
            print(f"ERROR: {path} not found", file=sys.stderr)
            return 2
        rep = lint_review(path.read_text(encoding="utf-8"))
        for e in rep.errors:
            print(f"ERROR {e}", file=sys.stderr)
        if rep.ok:
            print(f"OK    {path} verdict table is machine-readable")
            return 0
        print(f"FAIL  {len(rep.errors)} violation(s) in {path}", file=sys.stderr)
        return 1

    path = Path(args.path)
    if not path.exists():
        print(f"ERROR: {path} not found", file=sys.stderr)
        return 2
    # Load the project's real roster (and control mode) from autopilot.json
    # next to the plan; fail-open to legacy defaults per _apply_registry().
    repo_dir = str(path.resolve().parent)
    control_mode = "legacy"
    notes_max = NOTES_MAX_CHARS_DEFAULT
    try:
        _cfg = json.loads((Path(repo_dir) / "autopilot.json").read_text(encoding="utf-8"))
        if (_cfg.get("control") or {}).get("mode") == "strict":
            control_mode = "strict"
        notes_max = int((_cfg.get("plan") or {}).get("notes_max_chars", NOTES_MAX_CHARS_DEFAULT))
    except Exception:
        pass
    rep = validate(path.read_text(encoding="utf-8"), control_mode=control_mode,
                   registry_views=_apply_registry(repo_dir), notes_max_chars=notes_max)
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
    sys.exit(main(sys.argv[1:]))
