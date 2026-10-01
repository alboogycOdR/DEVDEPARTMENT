#!/usr/bin/env python3
"""Read-only PLAN.md health checks for frontmatter freshness and untracked work.

Freshness compares PLAN.md's ``last_updated`` with the newest task-level
``Updated_At`` and newest [TASK-NNN] commit reachable from any local ref.
The frontmatter fields are treated as one status snapshot: when stale, the
report names ``last_updated``, ``overall_status`` and ``orchestrator_notes``.

The untracked-work check is narrower: it counts commits on the configured base
branch in the last 14 days whose subject has neither coordination tags nor
task bookkeeping (a named task with a unit tag, or a task-branch merge).
These are observations only; the command exits successfully when a check is
stale or when Git data is unavailable, so status reporting never blocks work.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
TASK_HEADER_RE = re.compile(r"^###\s+(TASK-\d{3})\s*$")
UPDATED_AT_RE = re.compile(r"^\*\*Updated_At:\*\*\s*(.*?)\s*$")
TAG_RE = re.compile(r"\[(?:TASK-\d{3}|ORCH|MAINT)\]")
TASK_REFERENCE_RE = re.compile(r"\bTASK-\d{3}\b")
# Unit IDs are extensible and historical units can be retired from the registry.
UNIT_TAG_RE = re.compile(r"\[[A-Z][A-Z0-9_-]*\]")
TASK_BRANCH_RE = re.compile(r"\btask/TASK-\d{3}-[a-zA-Z0-9_-]+\b")


@dataclass(frozen=True)
class FreshnessReport:
    stale: bool
    message: str
    frontmatter_time: datetime | None
    newest_source: datetime | None


@dataclass(frozen=True)
class UntrackedReport:
    total_commits: int
    untagged_commits: int
    message: str


def _parse_time(value: str) -> datetime | None:
    value = value.strip().strip("\"'")
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _git(repo: Path, *args: str, timeout: int = 10) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip() or f"git exited {result.returncode}"
        raise RuntimeError(detail)
    return result.stdout


def _frontmatter(plan: str) -> dict[str, str]:
    match = re.match(r"\A---\s*\n(.*?)\n---\s*(?:\n|$)", plan, re.DOTALL)
    if not match:
        return {}
    values: dict[str, str] = {}
    for raw in match.group(1).splitlines():
        if ":" not in raw or raw.lstrip().startswith("#"):
            continue
        key, value = raw.split(":", 1)
        values[key.strip()] = value.strip().strip("\"'")
    return values


def _task_updated_times(plan: str) -> list[tuple[datetime, str]]:
    current_task: str | None = None
    found: list[tuple[datetime, str]] = []
    for line in plan.splitlines():
        header = TASK_HEADER_RE.match(line)
        if header:
            current_task = header.group(1)
            continue
        if current_task:
            field = UPDATED_AT_RE.match(line)
            if field:
                stamp = _parse_time(field.group(1))
                if stamp:
                    found.append((stamp, current_task))
                current_task = None
    return found


def _newest_task_commit(repo: Path) -> tuple[datetime, str] | None:
    try:
        rows = _git(repo, "log", "--all", "--format=%cI%x09%s", timeout=20)
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        return None
    newest: tuple[datetime, str] | None = None
    for row in rows.splitlines():
        stamp_text, sep, subject = row.partition("\t")
        if not sep or not re.search(r"\[TASK-\d{3}\]", subject):
            continue
        stamp = _parse_time(stamp_text)
        if stamp and (newest is None or stamp > newest[0]):
            newest = (stamp, subject.strip())
    return newest


def freshness_report(repo: Path | str) -> FreshnessReport:
    repo = Path(repo).resolve()
    try:
        plan = (repo / "PLAN.md").read_text(encoding="utf-8")
    except OSError as exc:
        return FreshnessReport(False, f"[plan_health] freshness unavailable: cannot read PLAN.md ({exc})", None, None)
    frontmatter = _frontmatter(plan)
    frontmatter_time = _parse_time(frontmatter.get("last_updated", ""))

    sources = _task_updated_times(plan)
    newest_task = max(sources, default=None, key=lambda item: item[0])
    newest_commit = _newest_task_commit(repo)
    candidates: list[tuple[datetime, str]] = []
    if newest_task:
        candidates.append((newest_task[0], f"{newest_task[1]} Updated_At"))
    if newest_commit:
        candidates.append((newest_commit[0], f"[TASK] commit {newest_commit[1]} "
                           "(all refs, including unmerged task branches)"))
    newest_source_pair = max(candidates, default=None, key=lambda item: item[0])
    newest_source = newest_source_pair[0] if newest_source_pair else None

    if not frontmatter:
        return FreshnessReport(True, "[plan_health] STALE frontmatter: PLAN.md frontmatter is missing or malformed", None, newest_source)
    if not frontmatter_time:
        return FreshnessReport(
            True,
            "[plan_health] STALE frontmatter: last_updated is missing or invalid; "
            "status snapshot fields: last_updated, overall_status, orchestrator_notes",
            None,
            newest_source,
        )
    if newest_source_pair and frontmatter_time < newest_source_pair[0]:
        source_time = newest_source_pair[0].strftime("%Y-%m-%dT%H:%M:%SZ")
        return FreshnessReport(
            True,
            f"[plan_health] STALE frontmatter: last_updated {frontmatter_time.strftime('%Y-%m-%dT%H:%M:%SZ')} "
            f"is older than {newest_source_pair[1]} at {source_time}; "
            "status snapshot fields: last_updated, overall_status, orchestrator_notes",
            frontmatter_time,
            newest_source,
        )
    return FreshnessReport(
        False,
        f"[plan_health] frontmatter fresh: last_updated {frontmatter_time.strftime('%Y-%m-%dT%H:%M:%SZ')} "
        "covers the newest task update and tagged task commit "
        "(all refs, including unmerged task branches)",
        frontmatter_time,
        newest_source,
    )


def _configured_base_branch(repo: Path, override: str | None) -> str:
    if override:
        return override
    try:
        config = json.loads((repo / "autopilot.json").read_text(encoding="utf-8"))
        value = config.get("git", {}).get("base_branch")
        if isinstance(value, str) and value.strip():
            return value.strip()
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
    return "main"


def _is_tracked_subject(subject: str) -> bool:
    if TAG_RE.search(subject):
        return True
    if TASK_REFERENCE_RE.search(subject) and UNIT_TAG_RE.search(subject):
        return True
    return subject.startswith("Merge ") and TASK_BRANCH_RE.search(subject) is not None


def untracked_report(repo: Path | str, *, now: datetime | None = None,
                     base_branch: str | None = None) -> UntrackedReport:
    repo = Path(repo).resolve()
    now = (now or datetime.now(UTC)).astimezone(UTC)
    branch = _configured_base_branch(repo, base_branch)
    cutoff = now - timedelta(days=14)
    try:
        rows = _git(repo, "log", branch, "--format=%cI%x09%s", timeout=20)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        return UntrackedReport(0, 0, f"[plan_health] work outside the plan unavailable: cannot read {branch} history ({exc})")
    recent = []
    for row in rows.splitlines():
        stamp_text, sep, _subject = row.partition("\t")
        stamp = _parse_time(stamp_text) if sep else None
        if stamp and cutoff <= stamp <= now:
            recent.append(row)
    untagged = [row for row in recent if not _is_tracked_subject(row.partition("\t")[2])]
    return UntrackedReport(
        len(recent),
        len(untagged),
        f"[plan_health] work outside the plan: {len(untagged)}/{len(recent)} base-branch commits "
        "in the last 14 days lack [TASK-NNN]/[ORCH]/[MAINT] tags or task bookkeeping "
        "(task-naming unit tags or task-branch merges)",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("check", nargs="?", choices=("all", "freshness", "untracked"), default="all")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="project checkout (default: current directory)")
    parser.add_argument("--base-branch", help="override autopilot.json git.base_branch")
    args = parser.parse_args(argv)
    repo = args.repo.resolve()
    if args.check in ("all", "freshness"):
        print(freshness_report(repo).message)
    if args.check in ("all", "untracked"):
        print(untracked_report(repo, base_branch=args.base_branch).message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
