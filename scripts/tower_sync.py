"""Fail-open Tower snapshot transport (P1).

``state`` may provide the live supervisor values ``mode``, ``tick``,
``wave_started_at`` and ``prev_wave_minutes``.  When it does not, these are
``None``: this module never invents telemetry it cannot derive itself.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from validate_plan import Report, parse_tasks
import usage_probe

# Spec §1 v1.2 (2026-08-28): recent_events.kind is an OPEN uppercase token
# (`[A-Z][A-Z0-9_]*`), not a closed enum — the producer skips only lines it cannot
# parse; it never filters by vocabulary (that is a Tower rendering decision).
# Measured against supervisor.py's real call sites the pack emits 17 distinct
# kinds; this list is documentation of what's known to occur, not a filter.
_KNOWN_KINDS = {
    "CONTROL", "DEFER_BUDGET", "DEFER_USAGE", "DIGEST", "DISPATCH",
    "DISPATCH_COMMAND", "DISTILL", "HALT", "IDLE", "MAINTENANCE", "MUTED",
    "REDISPATCH_STALE", "RETRO", "REVIEW", "REVIEW_TG", "TG_COMMAND",
    "TRIAGE_UNBLOCK",
}

# AUTOPILOT_LOG.md lines are written by supervisor.py's log_line() in one of
# two real shapes: `- [<ISO8601>] <KIND>: <text>` (the generic per-action
# line, e.g. "DIGEST: ...") or `- [<ISO8601>] <KIND> key=value ...` with no
# colon at all (DISPATCH_COMMAND / TG_COMMAND, e.g.
# "DISPATCH_COMMAND unit=GB task=TASK-117 command=..."). The colon is
# therefore optional, but the separating whitespace after the kind token is
# not.
_LOG_LINE_RE = re.compile(r"^- \[(?P<ts>[^\]]+)\] (?P<kind>[A-Z][A-Z0-9_]*):?\s(?P<text>.*)$")

# task_id/unit (§1 v1.1) are both PARSED from data already present in the
# line, never inferred — absent means null, never a guess (H2).
# `DISPATCH_COMMAND`/`TG_COMMAND` lines carry an explicit `task=TASK-NNN`
# field; dispatch/review/triage lines instead name TASK-NNN inline in prose.
# Try the explicit field first, fall back to the first inline mention.
_TASK_ID_FIELD_RE = re.compile(r"\btask=(TASK-\d+)\b")
_TASK_ID_INLINE_RE = re.compile(r"\b(TASK-\d+)\b")
# unit is only ever parsed from the explicit `unit=<U>` field — never
# guessed from prose that merely mentions a unit's name (H2).
_UNIT_FIELD_RE = re.compile(r"\bunit=([A-Za-z0-9_]+)\b")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_task_id(text: str) -> str | None:
    match = _TASK_ID_FIELD_RE.search(text)
    if match:
        return match.group(1)
    match = _TASK_ID_INLINE_RE.search(text)
    return match.group(1) if match else None


def _parse_unit(text: str) -> str | None:
    match = _UNIT_FIELD_RE.search(text)
    return match.group(1) if match else None


def _parse_log_line(line: str) -> dict | None:
    """Parse one AUTOPILOT_LOG.md line into a spec §1 v1.2 recent_events entry.

    Returns ``None`` (skip) only when the line does not match the fixed log
    format at all — an unparseable line is not a snapshot event, and must
    never be emitted with null/placeholder/guessed fields (H2). ``kind`` is
    emitted verbatim for any well-formed line regardless of whether it is in
    ``_KNOWN_KINDS`` (§1 v1.2: open vocabulary, not a whitelist — vocabulary
    curation is Tower's job, not the producer's).
    """
    match = _LOG_LINE_RE.match(line)
    if not match:
        return None
    text = match.group("text")
    return {
        "ts": match.group("ts"),
        "kind": match.group("kind"),
        "text": text,
        "task_id": _parse_task_id(text),
        "unit": _parse_unit(text),
    }


def _field(task, name: str):
    value = task.get(name)
    return None if value.lower() in {"", "—", "-", "none", "n/a"} else value


def _minutes(value: str | None, now: datetime) -> float | None:
    if not value:
        return None
    try:
        then = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return round((now - then).total_seconds() / 60, 1)
    except ValueError:
        return None


def build_snapshot(repo: str | Path, cfg: dict, state=None) -> dict:
    """Assemble schema v1 without network access or live usage probing."""
    repo = Path(repo)
    now = datetime.now(timezone.utc)
    text = (repo / "PLAN.md").read_text(encoding="utf-8") if (repo / "PLAN.md").exists() else ""
    tasks = parse_tasks(text, Report())
    cards = []
    for task in tasks:
        status = _field(task, "Status") or "pending"
        updated = _field(task, "Updated_At")
        cards.append({"id": task.task_id, "title": _field(task, "Title") or "",
                      "status": status, "assignee": _field(task, "Assigned_To") or "TBD",
                      "branch": _field(task, "Branch"), "started_at": _field(task, "Started_At"),
                      "updated_at": updated, "rework_count": None,
                      "blocked_reason": _field(task, "Blocked_Reason"),
                      "heartbeat_age_min": _minutes(updated, now) if status in ("claimed", "in_progress") else None})
    active = {"claimed", "in_progress", "needs_review"}
    builders = []
    for unit in (cfg.get("builders", {}).get("active", []) if isinstance(cfg.get("builders"), dict) else cfg.get("builders", [])):
        mine = next((c for c in cards if c["assignee"] == unit and c["status"] in active), None)
        builders.append({"unit": unit, "state": "active" if mine else "idle",
                         "task": mine["id"] if mine else None,
                         "heartbeat_age_min": mine["heartbeat_age_min"] if mine else None})
    log = repo / "AUTOPILOT_LOG.md"
    events = []
    if log.exists():
        try:
            for line in log.read_text(encoding="utf-8").splitlines()[-20:]:
                if not line.strip(): continue
                event = _parse_log_line(line)
                if event is not None: events.append(event)
        except OSError: pass
    st = state or {}
    get = (lambda key, default=None: st.get(key, default)) if isinstance(st, dict) else (lambda key, default=None: getattr(st, key, default))
    done = sum(c["status"] == "done" for c in cards)
    return {"schema": 1, "project_id": cfg.get("tower", {}).get("project_id", ""), "ts": _now(),
            "pack_version": get("pack_version"),
            "supervisor": {"mode": get("mode"), "autonomy_level": cfg.get("autonomy_level"), "tick": get("tick"), "stop_file": (repo / "STOP").exists()},
            "wave": {"total": len(cards), "done": done, "started_at": get("wave_started_at"), "prev_wave_minutes": get("prev_wave_minutes")},
            "tasks": cards, "builders": builders,
            "review_queue": [{"id": c["id"], "age_min": _minutes(c["updated_at"], now)} for c in cards if c["status"] == "needs_review"],
            "usage": usage_probe.load_cache(repo), "recent_events": events}


def _request(method: str, url: str, token: str, data=None):
    body = json.dumps(data).encode("utf-8") if data is not None else None
    req = Request(url, data=body, method=method, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urlopen(req, timeout=10) as response:
        if not 200 <= response.status < 300: raise OSError(f"HTTP {response.status}")
        return json.loads(response.read().decode("utf-8") or "[]")


def sync_tick(repo: str | Path, cfg: dict, state=None, transport=_request) -> None:
    """POST snapshot, pull queue, write commands, then acknowledge them; never raises."""
    tower = cfg.get("tower", {})
    if not tower.get("enabled") or not tower.get("url"): return
    try:
        token = os.environ.get(tower.get("_token_env", ""), "")
        base, project = tower["url"].rstrip("/"), tower.get("project_id", "")
        transport("POST", base + "/ingest", token, build_snapshot(repo, cfg, state))
        queued = transport("GET", base + "/queue/" + project, token) or []
        inbox = Path(repo) / ".devteam" / "inbox"; inbox.mkdir(parents=True, exist_ok=True)
        for item in queued:
            ident = str(item.get("id", ""))
            if not ident: continue
            tmp = inbox / (ident + ".json.tmp"); dest = inbox / (ident + ".json")
            tmp.write_text(json.dumps(item), encoding="utf-8"); tmp.replace(dest)
            transport("DELETE", base + "/queue/" + project + "/" + ident, token)
    except Exception as exc:
        print(f"[tower] warning: {exc}", file=sys.stderr)
