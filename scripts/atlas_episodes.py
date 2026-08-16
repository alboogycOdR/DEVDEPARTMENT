#!/usr/bin/env python3
"""ATLAS Layer 2 — episodic indexer.

Parses dossiers/, REVIEW.md, INSTINCTS.md and RETRO-*.md into the ``episodes``
table created by atlas_core. Pure parsing; zero LLM (R4).

Existing grammars are reused, never duplicated:
  * ``validate_plan.parse_tasks`` for PLAN.md and task-shaped dossier blocks
  * ``team_stats.ROW_RE`` for REVIEW.md verdict rows
  * ``instincts.parse_instincts`` for INSTINCTS.md
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import atlas_core as core  # noqa: E402
from instincts import parse_instincts  # noqa: E402
from team_stats import ROW_RE  # noqa: E402
from validate_plan import EMPTY_VALUES, Report, parse_tasks  # noqa: E402

DOSSIER_NAME_RE = re.compile(r"^(TASK-[A-Z0-9-]+)\.md$", re.IGNORECASE)
RETRO_GLOB = "RETRO-*.md"
SOURCE_HASH_PREFIX = "episodes_source_hash:"


@dataclass(frozen=True)
class Episode:
    kind: str
    ref: str
    ts: str | None
    unit: str | None
    indexed_hash: str
    body_fts: str


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped or stripped.lower() in EMPTY_VALUES:
        return None
    return stripped


def _task_index(repo: Path) -> dict[str, object]:
    text = _read_text(repo / "PLAN.md")
    if text is None:
        return {}
    return {task.task_id: task for task in parse_tasks(text, Report())}


def _task_meta(task) -> tuple[str | None, str | None, str]:
    ts = _clean(task.get("Updated_At")) or _clean(task.get("Started_At"))
    unit = _clean(task.get("Assigned_To"))
    extra_parts = [task.get("Title"), task.get("Description")]
    extra = "\n".join(part.strip() for part in extra_parts if part and part.strip())
    return ts, unit, extra


def _mix_hashes(*digests: str) -> str:
    hasher = hashlib.sha256()
    for digest in digests:
        hasher.update(digest.encode("ascii"))
        hasher.update(b"\0")
    return hasher.hexdigest()


def _episodes_from_dossier(path: Path, task_id: str, plan_tasks: dict, plan_hash: str | None) -> list[Episode] | None:
    text = _read_text(path)
    if text is None:
        return None
    digest = core.file_hash(path)
    if plan_hash:
        digest = _mix_hashes(digest, plan_hash)
    task = plan_tasks.get(task_id)
    if task is None:
        local = parse_tasks(text, Report())
        task = next((item for item in local if item.task_id == task_id), local[0] if local else None)
    ts = unit = None
    extra = ""
    if task is not None:
        ts, unit, extra = _task_meta(task)
    body = text if not extra else f"{text.rstrip()}\n{extra}\n"
    return [Episode("dossier", task_id, ts, unit, digest, body)]


def _episodes_from_review(path: Path) -> list[Episode]:
    text = _read_text(path)
    if text is None:
        return []
    digest = core.file_hash(path)
    episodes: list[Episode] = []
    for line in text.splitlines():
        match = ROW_RE.match(line.strip())
        if not match:
            continue
        task, unit, verdict, findings, first_pass, ts = match.groups()
        body = (
            f"{task} {unit} {verdict} {findings} "
            f"first-pass:{first_pass} {ts}"
        )
        episodes.append(Episode("review", task, _clean(ts), _clean(unit), digest, body))
    return episodes


def _episodes_from_instincts(path: Path) -> list[Episode]:
    text = _read_text(path)
    if text is None:
        return []
    digest = core.file_hash(path)
    episodes: list[Episode] = []
    for instinct in parse_instincts(text):
        body = " ".join(
            part
            for part in (
                instinct.inst_id,
                instinct.rule,
                " ".join(instinct.territory),
                " ".join(instinct.source),
                instinct.status,
            )
            if part
        )
        episodes.append(Episode("instinct", instinct.inst_id, None, None, digest, body))
    return episodes


def _episodes_from_retro(path: Path) -> list[Episode] | None:
    text = _read_text(path)
    if text is None:
        return None
    ref = core.slash(path.name)
    return [Episode("retro", ref, None, None, core.file_hash(path), text)]


def _source_key(kind: str, ref: str) -> tuple[str, str]:
    if kind == "review":
        return ("review", "REVIEW.md")
    if kind == "instinct":
        return ("instinct", "INSTINCTS.md")
    return (kind, ref)


def _existing_source_hashes(con) -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for row in con.execute("SELECT kind, ref, indexed_hash FROM episodes"):
        found[_source_key(row["kind"], row["ref"])] = row["indexed_hash"]
    for row in con.execute(
        "SELECT key, value FROM meta WHERE key LIKE ?", (SOURCE_HASH_PREFIX + "%",)
    ):
        key = _source_hash_key_to_source(row["key"])
        if key is not None:
            found[key] = row["value"]
    return found


def _source_hash_meta_key(key: tuple[str, str]) -> str:
    return f"{SOURCE_HASH_PREFIX}{key[0]}:{key[1]}"


def _source_hash_key_to_source(meta_key: str) -> tuple[str, str] | None:
    if not meta_key.startswith(SOURCE_HASH_PREFIX):
        return None
    kind, separator, ident = meta_key[len(SOURCE_HASH_PREFIX) :].partition(":")
    if not kind or not separator or not ident:
        return None
    return kind, ident


def _record_source_hash(con, key: tuple[str, str], digest: str) -> None:
    con.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)",
        (_source_hash_meta_key(key), digest),
    )


def _delete_source(con, key: tuple[str, str]) -> None:
    kind, ident = key
    if kind in {"review", "instinct"}:
        con.execute("DELETE FROM episodes WHERE kind=?", (kind,))
    else:
        con.execute("DELETE FROM episodes WHERE kind=? AND ref=?", (kind, ident))
    con.execute("DELETE FROM meta WHERE key=?", (_source_hash_meta_key(key),))


def _insert_episodes(con, episodes: list[Episode]) -> None:
    con.executemany(
        "INSERT INTO episodes(kind, ref, ts, unit, indexed_hash, body_fts) "
        "VALUES(?,?,?,?,?,?)",
        [
            (item.kind, item.ref, item.ts, item.unit, item.indexed_hash, item.body_fts)
            for item in episodes
        ],
    )


def _rebuild_fts(con) -> None:
    con.execute("DELETE FROM episodes_fts")
    con.executemany(
        "INSERT INTO episodes_fts(ref, body) VALUES(?, ?)",
        [(row["ref"], row["body_fts"]) for row in con.execute("SELECT ref, body_fts FROM episodes")],
    )


def collect_sources(repo: Path) -> list[tuple[tuple[str, str], str, list[Episode]]]:
    """Return (source_key, content_hash, episodes) for every present source file."""
    repo = repo.resolve()
    plan_tasks = _task_index(repo)
    plan_path = repo / "PLAN.md"
    plan_hash = core.file_hash(plan_path) if plan_path.is_file() else None
    sources: list[tuple[tuple[str, str], str, list[Episode]]] = []

    dossier_dir = repo / "dossiers"
    if dossier_dir.is_dir():
        for path in sorted(dossier_dir.iterdir()):
            if not path.is_file():
                continue
            match = DOSSIER_NAME_RE.match(path.name)
            if not match:
                continue
            task_id = match.group(1)
            parsed = _episodes_from_dossier(path, task_id, plan_tasks, plan_hash)
            if parsed is None:
                continue
            sources.append((("dossier", task_id), parsed[0].indexed_hash, parsed))

    review_path = repo / "REVIEW.md"
    if review_path.is_file():
        digest = core.file_hash(review_path)
        sources.append((("review", "REVIEW.md"), digest, _episodes_from_review(review_path)))

    instincts_path = repo / "INSTINCTS.md"
    if instincts_path.is_file():
        digest = core.file_hash(instincts_path)
        sources.append(
            (("instinct", "INSTINCTS.md"), digest, _episodes_from_instincts(instincts_path))
        )

    for path in sorted(repo.glob(RETRO_GLOB)):
        if not path.is_file():
            continue
        parsed = _episodes_from_retro(path)
        if parsed is None:
            continue
        sources.append((("retro", parsed[0].ref), parsed[0].indexed_hash, parsed))

    return sources


def index_episodes(repo: Path, reindex: bool = False) -> tuple[int, int, int]:
    """Populate ``episodes`` from the four source families.

    Returns ``(episode_count, sources_scanned, sources_changed)``.
    Incremental by source-file ``indexed_hash``; ``reindex`` rebuilds the table.
    """
    repo = repo.resolve()
    con = core.connect(repo)
    core.init_schema(con)
    if reindex:
        con.execute("DELETE FROM episodes")
        con.execute("DELETE FROM episodes_fts")
        con.execute("DELETE FROM meta WHERE key LIKE ?", (SOURCE_HASH_PREFIX + "%",))
        existing: dict[tuple[str, str], str] = {}
    else:
        existing = _existing_source_hashes(con)

    sources = collect_sources(repo)
    seen: set[tuple[str, str]] = set()
    scanned = changed = 0
    for key, digest, episodes in sources:
        scanned += 1
        seen.add(key)
        if existing.get(key) == digest:
            _record_source_hash(con, key, digest)
            continue
        changed += 1
        _delete_source(con, key)
        _insert_episodes(con, episodes)
        _record_source_hash(con, key, digest)

    for key in existing:
        if key not in seen:
            changed += 1
            _delete_source(con, key)

    if changed or reindex:
        _rebuild_fts(con)

    count = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    con.commit()
    con.close()
    return int(count), scanned, changed


def cmd_episodes(args: argparse.Namespace) -> int:
    repo = core._repo_arg(getattr(args, "repo", None))
    if not repo.is_dir():
        raise ValueError(f"repo not found: {core.slash(repo)}")
    count, scanned, changed = index_episodes(repo, reindex=bool(args.reindex))
    print(
        f"episodes indexed: {count}; sources scanned: {scanned}; "
        f"sources changed: {changed}"
    )
    return 0


def register(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "episodes",
        help="index dossiers, REVIEW.md, INSTINCTS.md, and RETRO-*.md",
    )
    parser.add_argument("--reindex", action="store_true", help="rebuild the episodes table")
    parser.add_argument("--repo", help="repository root (default: cwd)")
    parser.set_defaults(handler=cmd_episodes)
