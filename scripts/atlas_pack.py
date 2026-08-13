"""ATLAS Layer 3: deterministic, budgeted context packs.

The pack is deliberately an extension module: ``atlas.py`` discovers it via
``register`` so this increment does not modify another task's territory.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import atlas_core as core  # noqa: E402
from validate_plan import Report, parse_tasks  # noqa: E402

R1_FOOTER = "This pack is a map, not the ground: read live any file you edit."
HEAD_LINES = 12
EPISODE_LIMIT = 5


def _tokens(text: str) -> int:
    """A stable, deliberately conservative token estimate for a text pack."""
    return len(re.findall(r"\w+|[^\s\w]", text, re.UNICODE))


def _task(repo: Path, task_id: str):
    plan = repo / "PLAN.md"
    if not plan.is_file():
        raise ValueError("PLAN.md was not found")
    report = Report()
    for task in parse_tasks(plan.read_text(encoding="utf-8"), report):
        if task.task_id == task_id:
            return task
    raise ValueError(f"task not found: {task_id}")


def _owned_paths(task) -> list[str]:
    return [item.strip() for item in task.get("Owned_Paths").split(",") if item.strip() and item.strip() != "—"]


def _live_files(repo: Path, owned: list[str]) -> list[str]:
    """Resolve owned territory using preflight_paths' glob/file/dir rules."""
    import glob

    paths: set[str] = set()
    for entry in owned:
        # ``preflight_paths.describe`` is a human-readable reporter, rather
        # than a resolver.  Keep its glob/file/directory classification rules
        # here so pack expansion returns the actual live files to compose.
        if any(ch in entry for ch in "*?["):
            for value in glob.glob(str(repo / entry), recursive=True):
                candidate = Path(value)
                if candidate.is_file():
                    paths.add(core.slash(candidate.relative_to(repo)))
        else:
            candidate = repo / entry
            if candidate.is_file():
                paths.add(core.slash(candidate.relative_to(repo)))
            elif candidate.is_dir():
                paths.update(core.slash(p.relative_to(repo)) for p in candidate.rglob("*") if p.is_file())
    return sorted(paths)


def _card(con, path: str):
    return con.execute(
        "SELECT c.*, f.content_hash FROM cards c JOIN files f ON f.id=c.file_id WHERE f.path=?",
        (path,),
    ).fetchone()


def _outline(con, path: str) -> str:
    row = con.execute("SELECT id, lang FROM files WHERE path=?", (path,)).fetchone()
    if not row:
        return f"- {path} (not indexed; read live)"
    symbols = con.execute(
        "SELECT kind,name,line_start,signature FROM symbols WHERE file_id=? AND kind!='call' ORDER BY line_start LIMIT 16",
        (row["id"],),
    ).fetchall()
    details = ", ".join(f"{s['kind']} {s['signature'] or s['name']}@{s['line_start']}" for s in symbols)
    return f"- {path} [{row['lang']}]: {details or 'no extracted symbols'}"


def _card_text(row, full: bool) -> str:
    if not row:
        return ""
    purpose = row["purpose"]
    if not full:
        return f"  card: {purpose}"
    return (
        f"  card purpose: {purpose}\n"
        f"  invariants: {row['invariants']}\n"
        f"  gotchas: {row['gotchas']}\n"
        f"  entry points: {row['entry_points']}"
    )


def _head(repo: Path, path: str) -> str:
    try:
        lines = (repo / path).read_text(encoding="utf-8", errors="replace").splitlines()[:HEAD_LINES]
    except OSError:
        return "  live head unavailable"
    return "  live head: " + " / ".join(lines)


def _episode_lines(con, terms: str) -> list[str]:
    words = [word for word in re.findall(r"[A-Za-z0-9_]{3,}", terms)][:8]
    if not words:
        return []
    clauses = " OR ".join("body_fts LIKE ?" for _ in words)
    rows = con.execute(
        f"SELECT ref,kind,body_fts FROM episodes WHERE {clauses} ORDER BY ts DESC, ref LIMIT ?",
        tuple(f"%{word}%" for word in words) + (EPISODE_LIMIT,),
    ).fetchall()
    return [f"- {core.slash(row['ref'])} ({row['kind']}): {row['body_fts'][:180].replace(chr(10), ' ')}" for row in rows]


def _assemble(repo: Path, task, card_bodies: bool = True, neighborhood: bool = True, episodes: bool = True) -> tuple[str, bool]:
    owned = _owned_paths(task)
    files = _live_files(repo, owned)
    con = core.connect(repo)
    core.init_schema(con)
    core_rows = []
    cards_missing = False
    neighbors: set[str] = set()
    for path in files:
        row = _card(con, path)
        fresh = bool(row and row["source_hash"] == row["content_hash"])
        core_rows.append(_outline(con, path))
        if fresh:
            core_rows.append(_card_text(row, card_bodies))
        else:
            cards_missing = True
            core_rows.append(_head(repo, path))
        neighbors.update(core.impact(repo, path, 1))
    neighborhood_rows: list[str] = []
    if neighborhood:
        for path in sorted(neighbors - set(files)):
            row = _card(con, path)
            fresh = bool(row and row["source_hash"] == row["content_hash"])
            neighborhood_rows.append(f"- {path} (pointer only){_card_text(row, False) if fresh else ' [no fresh card]'}")
    episode_rows = _episode_lines(con, task.get("Title") + " " + task.get("Description")) if episodes else []
    stale_count = con.execute(
        "SELECT COUNT(*) FROM cards c JOIN files f ON f.id=c.file_id WHERE c.source_hash != f.content_hash"
    ).fetchone()[0]
    last = con.execute("SELECT value FROM meta WHERE key='last_full_scan'").fetchone()
    con.close()
    text = "\n".join([
        f"# ATLAS PACK — {task.task_id}: {task.get('Title')}",
        "## TERRITORY CORE",
        *(core_rows or ["- No live files currently resolve from Owned_Paths."]),
        "## ONE-HOP NEIGHBORHOOD",
        *(neighborhood_rows or ["- No indexed reverse-dependency pointers."]),
        "## EPISODIC HITS",
        *(episode_rows or ["- No matching indexed episodes."]),
        "## FRESHNESS",
        f"scan timestamp: {last['value'] if last else 'never'}; stale cards in pack: {stale_count}",
        R1_FOOTER,
    ])
    return text, cards_missing


def compose_pack(repo: Path, task_id: str, budget: int, output_format: str = "prompt") -> str:
    if budget <= 0:
        raise ValueError("budget must be a positive integer")
    task = _task(repo.resolve(), task_id)
    omitted: list[str] = []
    text, degraded = _assemble(repo, task)
    # Reserve room for the mandatory degradation and truncation disclosure.
    # The disclosure itself is part of the hard cap, not postscript prose.
    disclosure_reserve = 40
    # Enforce the specified priority order before ever removing territory data.
    if _tokens(text) + disclosure_reserve > budget:
        text, _ = _assemble(repo, task, episodes=False); omitted.append("episodic hits")
    if _tokens(text) + disclosure_reserve > budget:
        text, _ = _assemble(repo, task, episodes=False, neighborhood=False); omitted.append("one-hop neighborhood")
    if _tokens(text) + disclosure_reserve > budget:
        text, _ = _assemble(repo, task, card_bodies=False, episodes=False, neighborhood=False); omitted.append("territory card bodies")
    degradation = "A1-only degradation: one or more territory cards are absent or stale." if degraded else "Fresh cards available for indexed territory files."
    truncation = "Truncation: " + (", ".join(omitted) if omitted else "none") + "."
    text += f"\n{degradation}\n{truncation}"
    # Preserve the required footer and explicit report even for exceptionally
    # small valid budgets; they are the safety-critical part of the response.
    if _tokens(text) > budget:
        raise ValueError("budget is too small for required territory outline and freshness footer")
    if output_format == "prompt":
        return text
    if output_format == "json":
        payload = {"task": task.task_id, "estimated_tokens": _tokens(text), "pack": text, "truncated": omitted, "degraded": degraded}
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)
    raise ValueError(f"unsupported format: {output_format}")


def cmd_pack(args: argparse.Namespace) -> int:
    try:
        print(compose_pack(Path.cwd(), args.task, args.budget, args.format))
        return 0
    except (OSError, ValueError, sqlite3.Error) as exc:  # type: ignore[name-defined]
        print(f"atlas: pack: {exc}", file=sys.stderr)
        return 1


def register(subparsers: argparse._SubParsersAction) -> None:
    pack = subparsers.add_parser("pack", help="compose a budgeted task context pack")
    pack.add_argument("--task", required=True, help="PLAN.md task ID, e.g. TASK-005")
    pack.add_argument("--budget", type=int, default=3000, help="maximum estimated tokens (default 3000)")
    pack.add_argument("--format", choices=("prompt", "json"), default="prompt")
    pack.set_defaults(handler=cmd_pack)
