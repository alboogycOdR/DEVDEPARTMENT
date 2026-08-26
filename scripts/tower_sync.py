"""Fail-open Tower snapshot transport (P1).

``state`` may provide the live supervisor values ``mode``, ``tick``,
``wave_started_at`` and ``prev_wave_minutes``.  When it does not, these are
``None``: this module never invents telemetry it cannot derive itself.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from validate_plan import Report, parse_tasks
import usage_probe


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
                if line.strip(): events.append({"ts": None, "kind": None, "text": line.strip()})
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
