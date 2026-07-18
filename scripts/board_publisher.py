#!/usr/bin/env python3
"""board_publisher.py — DEVDEPARTMENT v4 Mission Control publisher.

Projects PLAN.md (the single source of truth) into board JSON for the static
Kanban frontend (board/index.html). Supports the multi-machine, multi-project
portfolio: each host publishes <project>.json plus merges itself into a shared
projects.json index, so one page shows the MacBook project and the Windows
project side by side.

Publish modes (autopilot.json → "board"):
  local        write into ./board/ only (serve however you like / tailscale)
  central-path write into a local clone of a central boards repo, commit+push
  gh-pages     commit board/ to a gh-pages branch of THIS repo via a git worktree

Usage:
  python scripts/board_publisher.py            # one publish using autopilot.json
  python scripts/board_publisher.py --dry-run  # print JSON, write nothing
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_plan import parse_tasks, Report  # noqa: E402
try:
    from team_stats import compute as compute_team  # noqa: E402
except Exception:  # pragma: no cover
    compute_team = None

UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"
COLUMNS = ["pending", "claimed", "in_progress", "needs_review", "blocked", "done"]
EMPTY = {"", "—", "-", "--", "n/a", "none"}

DEFAULT_BOARD_CFG = {
    "enabled": True,
    "mode": "local",              # local | central-path | gh-pages
    "central_path": "",           # for central-path: local clone of the boards repo
    "branch": "gh-pages",
    "min_interval_seconds": 120,
    "project_name": "",           # default: repo folder name
    "public_note": "",            # optional strip note shown on the board
}


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(v: str):
    try:
        return datetime.strptime(v.strip(), UTC_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def field(t, key):
    v = (t.fields.get(key) or "").strip()
    return "" if v.lower() in EMPTY else v


def last_note(t) -> str:
    notes = [ln.strip() for ln in (t.fields.get("Progress_Notes") or "").split("\n")
             if ln.strip().startswith("-")]
    return notes[-1] if notes else ""


def age_minutes(ts_str: str, now: datetime):
    ts = parse_ts(ts_str)
    return round((now - ts).total_seconds() / 60.0, 1) if ts else None


def task_card(t, now: datetime) -> dict:
    status = field(t, "Status") or "pending"
    upd = field(t, "Updated_At")
    started = field(t, "Started_At")
    return {
        "id": t.task_id,
        "title": field(t, "Title"),
        "status": status,
        "assignee": field(t, "Assigned_To") or "TBD",
        "priority": field(t, "Priority") or "medium",
        "branch": field(t, "Branch"),
        "owned_paths": field(t, "Owned_Paths"),
        "depends_on": field(t, "Depends_On"),
        "blocked_reason": field(t, "Blocked_Reason"),
        "last_note": last_note(t)[:220],
        "updated_at": upd,
        "heartbeat_age_min": age_minutes(upd, now) if status in ("claimed", "in_progress") else None,
        "active_min": age_minutes(started, now) if status in ("claimed", "in_progress", "needs_review") and started else None,
        "updated_by": field(t, "Updated_By"),
    }


def read_frontmatter(text: str) -> dict:
    m = re.match(r"\A---\s*\n(.*?)\n---", text, re.DOTALL)
    fm = {}
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                fm[k.strip()] = v.strip().strip('"')
    return fm


def read_maintenance_summary(repo: Path) -> dict:
    """Wave B: surface the nightly self-audit's last result on the board.
    Reads only files Wave B already writes (.devteam/last_audit_date.txt,
    the most recent 'MAINTENANCE:' line in AUTOPILOT_LOG.md) — never
    invokes maintenance.py itself, so a broken audit can never break a
    board publish."""
    last_audit = ""
    marker = repo / ".devteam" / "last_audit_date.txt"
    if marker.exists():
        try:
            last_audit = marker.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    status = ""
    log_path = repo / "AUTOPILOT_LOG.md"
    if log_path.exists():
        try:
            for line in reversed(log_path.read_text(encoding="utf-8").splitlines()):
                if "MAINTENANCE:" in line:
                    status = line.split("MAINTENANCE:", 1)[1].strip()
                    break
        except OSError:
            pass
    return {"last_audit": last_audit, "status": status}


def build_board(repo: Path, cfg: dict, now: datetime | None = None) -> dict:
    now = now or now_utc()
    plan_path = repo / "PLAN.md"
    text = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
    fm = read_frontmatter(text)
    tasks = [t for t in parse_tasks(text, Report())
             if "EXAMPLE" not in (t.fields.get("Title") or "").upper()]

    columns = {c: [] for c in COLUMNS}
    for t in tasks:
        card = task_card(t, now)
        columns.setdefault(card["status"], columns["pending"]) if card["status"] in columns else None
        (columns[card["status"]] if card["status"] in columns else columns["pending"]).append(card)
    prio = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    for c in columns.values():
        c.sort(key=lambda x: (prio.get(x["priority"], 4), x["id"]))

    in_flight = [c for c in columns["claimed"] + columns["in_progress"] + columns["needs_review"]]

    team = {}
    hint = ""
    review_path = repo / "REVIEW.md"
    if compute_team and review_path.exists():
        try:
            stats = compute_team(review_path.read_text(encoding="utf-8"))
            hint = stats.pop("assignment_hint", "")
            team = stats
        except Exception:
            pass

    log_tail = []
    log_path = repo / "AUTOPILOT_LOG.md"
    if log_path.exists():
        lines = [ln for ln in log_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        log_tail = lines[-20:]

    stop = (repo / "STOP").exists()
    state = {}
    st_path = repo / ".autopilot_state.json"
    if st_path.exists():
        try:
            state = json.loads(st_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass

    esc = []
    for c in columns["blocked"]:
        esc.append({"priority": "P2", "task": c["id"], "question": c["blocked_reason"] or "blocked"})

    done_n = len(columns["done"])
    total = len(tasks)
    return {
        "schema": 1,
        "generated_at": now.strftime(UTC_FMT),
        "project": cfg.get("project_name") or repo.name,
        "host": cfg.get("host_label", ""),
        "plan_version": fm.get("plan_version", ""),
        "overall_status": fm.get("overall_status", ""),
        "orchestrator_notes": (fm.get("orchestrator_notes", "") or "")[:300],
        "columns": columns,
        "in_flight": in_flight,
        "burndown": {"total": total, "done": done_n,
                     "pct": round(100 * done_n / total) if total else 0},
        "team": team,
        "assignment_hint": hint,
        "escalations_open": esc,
        "log_tail": log_tail,
        "autopilot": {"stop_file": stop,
                      "rework_counts": state.get("rework_counts", {}),
                      "stale_resets": state.get("stale_resets", {})},
        "maintenance": read_maintenance_summary(repo),
        "public_note": cfg.get("public_note", ""),
    }


def merge_index(index_path: Path, board: dict) -> dict:
    idx = {"schema": 1, "projects": []}
    if index_path.exists():
        try:
            idx = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    entry = {
        "project": board["project"],
        "file": f"{board['project']}.json",
        "host": board.get("host", ""),
        "generated_at": board["generated_at"],
        "done": board["burndown"]["done"],
        "total": board["burndown"]["total"],
        "in_flight": len(board["in_flight"]),
        "blocked": len(board["columns"]["blocked"]),
        "stop": board["autopilot"]["stop_file"],
    }
    projects = [p for p in idx.get("projects", []) if p.get("project") != entry["project"]]
    projects.append(entry)
    projects.sort(key=lambda p: p["project"])
    idx["projects"] = projects
    return idx


def write_outputs(out_dir: Path, board: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{board['project']}.json").write_text(
        json.dumps(board, indent=1), encoding="utf-8")
    idx = merge_index(out_dir / "projects.json", board)
    (out_dir / "projects.json").write_text(json.dumps(idx, indent=1), encoding="utf-8")
    # ship the frontend alongside the data if present in the repo
    src_html = Path(__file__).resolve().parent.parent / "board" / "index.html"
    if src_html.exists():
        target = out_dir / "index.html"
        if not target.exists() or target.read_text(encoding="utf-8") != src_html.read_text(encoding="utf-8"):
            target.write_text(src_html.read_text(encoding="utf-8"), encoding="utf-8")


def git(repo: Path, *args) -> int:
    return subprocess.run(["git", *args], cwd=repo).returncode


def publish(repo: Path, cfg: dict, dry_run: bool = False) -> dict:
    board = build_board(repo, cfg)
    if dry_run:
        print(json.dumps(board, indent=1))
        return board

    mode = cfg.get("mode", "local")
    if mode == "local":
        write_outputs(repo / "board", board)

    elif mode == "central-path":
        central = Path(cfg.get("central_path", "")).expanduser()
        if not central.exists():
            print(f"[board] central_path {central} missing — falling back to local", file=sys.stderr)
            write_outputs(repo / "board", board)
        else:
            write_outputs(central, board)
            if (central / ".git").exists() or git(central, "rev-parse", "--git-dir") == 0:
                git(central, "add", "-A")
                git(central, "commit", "-m", f"chore(board): {board['project']} tick {board['generated_at']} [AUTOPILOT]")
                git(central, "push")

    elif mode == "gh-pages":
        branch = cfg.get("branch", "gh-pages")
        wt = repo.parent / f".board-wt-{repo.name}"
        if not wt.exists():
            if git(repo, "worktree", "add", str(wt), branch) != 0:
                git(repo, "branch", branch)
                git(repo, "worktree", "add", str(wt), branch)
        write_outputs(wt, board)
        git(wt, "add", "-A")
        git(wt, "commit", "-m", f"chore(board): tick {board['generated_at']} [AUTOPILOT]")
        git(wt, "push", "-u", "origin", branch)

    return board


_last_publish = {"at": 0.0}


def publish_throttled(repo: Path, cfg: dict) -> bool:
    """Called from the supervisor each tick; honors min_interval_seconds."""
    if not cfg.get("enabled", True):
        return False
    interval = int(cfg.get("min_interval_seconds", 120))
    if time.monotonic() - _last_publish["at"] < interval:
        return False
    try:
        publish(repo, cfg)
        _last_publish["at"] = time.monotonic()
        return True
    except Exception as e:  # never let the board break the loop
        print(f"[board] publish failed (non-fatal): {e}", file=sys.stderr)
        return False


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    repo = Path(args.repo).resolve()
    cfg = dict(DEFAULT_BOARD_CFG)
    ap_json = repo / "autopilot.json"
    if ap_json.exists():
        try:
            cfg.update(json.loads(ap_json.read_text(encoding="utf-8")).get("board", {}))
        except json.JSONDecodeError:
            pass
    board = publish(repo, cfg, dry_run=args.dry_run)
    if not args.dry_run:
        print(f"[board] published {board['project']} — {board['burndown']['done']}/{board['burndown']['total']} done, "
              f"{len(board['in_flight'])} in flight ({cfg.get('mode', 'local')})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
