#!/usr/bin/env python3
"""plan_archive.py — keep PLAN.md small (LOOP_HYGIENE §6 E-D).

Moves ``done`` task blocks older than the current wave into
``plan/archive/<YYYY-MM>.md`` (append-only) and leaves a two-field stub
(``Status: done`` plus ``Archived:``) so ``validate_plan.parse_tasks`` and
every ``_deps_done`` that reads it still treat the id as done.

Also rotates ``orchestrator_notes`` that exceed ``plan.notes_max_chars``
(default 4,000) into ``docs/handovers/<date>-notes.md`` and replaces the
frontmatter value with a pointer. Callers pass a repo directory; this
module never picks the live checkout on its own.

Not a port. oikonomos has no ``plan_archive.py`` (checked 2026-09-27);
behaviour follows specs/LOOP_HYGIENE_2026-09.md §6.

Usage:
    python scripts/plan_archive.py --repo PATH [--dry-run] [--notes-cap N]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

log = logging.getLogger("plan_archive")

NOTES_MAX_CHARS_DEFAULT = 4000
_TASK_HEADER = re.compile(r"^### (TASK-[A-Z0-9-]+)[ \t]*\r?$", re.MULTILINE)
_FIELD = re.compile(r"^\*\*([A-Za-z_]+):\*\*[ \t]*(.*)$")
_WAVE = re.compile(r"\bWave\s+([A-Z])\b")
_MONTH = re.compile(r"^(\d{4}-\d{2})-\d{2}T")
_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class ArchivedBlock:
    task_id: str
    month: str
    rel: str
    block: str


@dataclass
class ArchiveResult:
    text: str
    changed: bool
    blocks: list[ArchivedBlock] = field(default_factory=list)


@dataclass
class NotesRotation:
    text: str
    rel: str | None
    body: str | None
    original_len: int


def _field(block: str, name: str) -> str:
    for line in block.splitlines():
        m = _FIELD.match(line.strip())
        if m and m.group(1) == name:
            return m.group(2).strip()
    return ""


def iter_blocks(text: str) -> list[tuple[str, str]]:
    """(task_id, exact block slice) for every ``### TASK-`` header.

    The slice runs from the header through the character before the next
    header, so it round-trips byte-for-byte (including trailing blank lines).
    """
    matches = list(_TASK_HEADER.finditer(text))
    out: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        out.append((m.group(1), text[m.start():end]))
    return out


def current_wave(blocks: list[tuple[str, str]]) -> str | None:
    """Highest wave letter still open, else the highest letter present.

    Stubs (an ``Archived`` field) are ignored. No lettered wave means there
    is nothing to compare against, and the archiver leaves the plan alone.
    """
    letters: list[str] = []
    open_letters: list[str] = []
    for _tid, block in blocks:
        if _field(block, "Archived"):
            continue
        m = _WAVE.search(_field(block, "Title"))
        if not m:
            continue
        letter = m.group(1).upper()
        letters.append(letter)
        if _field(block, "Status") != "done":
            open_letters.append(letter)
    pool = open_letters or letters
    return max(pool) if pool else None


def _month_of(block: str) -> str:
    m = _MONTH.match(_field(block, "Updated_At"))
    if m and re.fullmatch(r"\d{4}-\d{2}", m.group(1)):
        return m.group(1)
    return "0000-00"


def _should_archive(block: str, wave: str | None) -> bool:
    if wave is None or _field(block, "Archived"):
        return False
    if _field(block, "Status") != "done":
        return False
    m = _WAVE.search(_field(block, "Title"))
    if not m:
        return True
    return m.group(1).upper() < wave


def _stub(task_id: str, rel: str) -> str:
    return f"### {task_id}\n**Status:** done\n**Archived:** {rel}\n\n"


def plan_archive(text: str) -> ArchiveResult:
    """Return PLAN text with older done blocks replaced by stubs.

    Does not touch the filesystem. ``blocks`` carries the exact original
    slices to append under ``plan/archive/<YYYY-MM>.md``.
    """
    blocks = iter_blocks(text)
    if not blocks:
        return ArchiveResult(text=text, changed=False)
    wave = current_wave(blocks)
    archived: list[ArchivedBlock] = []
    first = _TASK_HEADER.search(text)
    if not first:
        return ArchiveResult(text=text, changed=False)
    parts = [text[:first.start()]]
    for task_id, block in blocks:
        if _should_archive(block, wave):
            month = _month_of(block)
            if not re.fullmatch(r"\d{4}-\d{2}", month):
                raise ValueError(f"{task_id}: archive month {month!r} is not YYYY-MM")
            rel = f"plan/archive/{month}.md"
            archived.append(ArchivedBlock(task_id=task_id, month=month, rel=rel, block=block))
            parts.append(_stub(task_id, rel))
        else:
            parts.append(block)
    new_text = "".join(parts)
    return ArchiveResult(text=new_text, changed=bool(archived), blocks=archived)


def read_archived_block(archive_text: str, task_id: str) -> str:
    """Pull one stored block back out. The slice is the original characters."""
    if not re.fullmatch(r"TASK-[A-Z0-9-]+", task_id):
        raise ValueError(f"illegal task id {task_id!r}")
    marker = re.compile(
        rf"<!-- archive-block {re.escape(task_id)} chars=(\d+) -->\n"
    )
    m = marker.search(archive_text)
    if not m:
        raise KeyError(f"{task_id} is not in this archive")
    n = int(m.group(1))
    start = m.end()
    if start + n > len(archive_text):
        raise ValueError(f"{task_id}: archive claims {n} chars but the file is shorter")
    return archive_text[start:start + n]


def _append_month(path: Path, month: str, blocks: list[ArchivedBlock]) -> None:
    if path.exists():
        existing = path.read_text(encoding="utf-8")
    else:
        existing = f"# Plan archive {month}\n\n"
    chunk: list[str] = []
    for b in blocks:
        token = f"<!-- archive-block {b.task_id} "
        if token in existing:
            log.info("skip %s; already in %s", b.task_id, path)
            continue
        chunk.append(
            f"<!-- archive-block {b.task_id} chars={len(b.block)} -->\n"
            f"{b.block}"
            f"<!-- /archive-block {b.task_id} -->\n"
        )
    if not chunk:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(existing + "".join(chunk), encoding="utf-8", newline="\n")
    log.info("appended %d block(s) to %s", len(chunk), path)


def apply_archive(repo: Path, text: str) -> ArchiveResult:
    """Archive eligible blocks under ``repo`` and return the stubbed PLAN text.

    Archive files are append-only. PLAN.md itself is not written; the caller
    decides whether to save ``result.text``.
    """
    result = plan_archive(text)
    if not result.changed:
        return result
    by_month: dict[str, list[ArchivedBlock]] = {}
    for b in result.blocks:
        by_month.setdefault(b.month, []).append(b)
    for month, group in by_month.items():
        _append_month(repo / "plan" / "archive" / f"{month}.md", month, group)
    return result


def orchestrator_notes(text: str) -> str:
    m = _FRONTMATTER.match(text)
    if not m:
        return ""
    for raw in m.group(1).splitlines():
        if not raw.startswith("orchestrator_notes:"):
            continue
        val = raw.split(":", 1)[1].strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            if val[0] == '"':
                try:
                    loaded = json.loads(val)
                    if isinstance(loaded, str):
                        return loaded
                except json.JSONDecodeError:
                    log.warning("orchestrator_notes is not valid JSON-quoted text; using the raw interior")
            return val[1:-1]
        return val
    return ""


def replace_orchestrator_notes(text: str, notes: str) -> str:
    m = _FRONTMATTER.match(text)
    if not m:
        raise ValueError("PLAN.md has no YAML frontmatter; refusing to rewrite orchestrator_notes")
    body = m.group(1).splitlines()
    rendered = "orchestrator_notes: " + json.dumps(notes, ensure_ascii=False)
    found = False
    for i, line in enumerate(body):
        if line.startswith("orchestrator_notes:"):
            body[i] = rendered
            found = True
            break
    if not found:
        body.append(rendered)
    return f"---\n" + "\n".join(body) + "\n---\n" + text[m.end():]


def plan_notes_rotation(text: str, now: datetime, cap: int = NOTES_MAX_CHARS_DEFAULT) -> NotesRotation:
    if cap < 1:
        raise ValueError(f"notes cap must be positive, got {cap}")
    notes = orchestrator_notes(text)
    if len(notes) <= cap:
        return NotesRotation(text=text, rel=None, body=None, original_len=len(notes))
    rel = f"docs/handovers/{now.strftime('%Y-%m-%d')}-notes.md"
    stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    pointer = f"Overflow rotated to {rel} ({len(notes)} chars) at {stamp}."
    if len(pointer) > cap:
        raise ValueError("notes pointer is longer than the cap; raise plan.notes_max_chars")
    body = f"## Rotated {stamp}\n\n{notes}\n"
    return NotesRotation(
        text=replace_orchestrator_notes(text, pointer),
        rel=rel,
        body=body,
        original_len=len(notes),
    )


def write_notes_rotation(repo: Path, rotation: NotesRotation) -> Path | None:
    if rotation.rel is None or rotation.body is None:
        return None
    path = repo / Path(rotation.rel)
    # rel is built from a date stamp, never caller input, but still refuse a climb.
    archive_root = (repo / "docs" / "handovers").resolve()
    resolved = path.resolve()
    if archive_root != resolved.parent:
        raise ValueError(f"refusing to write notes outside docs/handovers: {rotation.rel}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        prior = path.read_text(encoding="utf-8").rstrip() + "\n\n"
    else:
        prior = "# Rotated orchestrator notes\n\n"
    path.write_text(prior + rotation.body, encoding="utf-8", newline="\n")
    log.info("rotated orchestrator_notes (%d chars) to %s", rotation.original_len, path)
    return path


def rotate_notes(text: str, repo: Path, now: datetime, cap: int = NOTES_MAX_CHARS_DEFAULT) -> tuple[str, Path | None]:
    rotation = plan_notes_rotation(text, now, cap)
    path = write_notes_rotation(repo, rotation)
    return rotation.text, path


def _cap_from_repo(repo: Path) -> int:
    cfg_path = repo / "autopilot.json"
    if not cfg_path.exists():
        return NOTES_MAX_CHARS_DEFAULT
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        log.warning("autopilot.json is not valid JSON (%s); using notes cap %d", exc, NOTES_MAX_CHARS_DEFAULT)
        return NOTES_MAX_CHARS_DEFAULT
    raw = (cfg.get("plan") or {}).get("notes_max_chars", NOTES_MAX_CHARS_DEFAULT)
    try:
        cap = int(raw)
    except (TypeError, ValueError):
        log.warning("plan.notes_max_chars %r is not an int; using %d", raw, NOTES_MAX_CHARS_DEFAULT)
        return NOTES_MAX_CHARS_DEFAULT
    return cap


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description="Archive done PLAN.md blocks older than the current wave")
    ap.add_argument("--repo", default=".", help="project checkout whose PLAN.md is archived")
    ap.add_argument("--dry-run", action="store_true", help="print the plan; write nothing")
    ap.add_argument("--notes-cap", type=int, default=None, help="override plan.notes_max_chars")
    args = ap.parse_args(argv)
    repo = Path(args.repo).resolve()
    plan_path = repo / "PLAN.md"
    if not plan_path.is_file():
        print(f"ERROR: {plan_path} not found", file=sys.stderr)
        return 2
    try:
        text = plan_path.read_text(encoding="utf-8")
        now = datetime.now(timezone.utc)
        cap = args.notes_cap if args.notes_cap is not None else _cap_from_repo(repo)
        rotation = plan_notes_rotation(text, now, cap)
        planned = plan_archive(rotation.text)
    except (OSError, ValueError, UnicodeError) as exc:
        log.error("%s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if args.dry_run:
        if rotation.rel:
            print(f"would rotate orchestrator_notes ({rotation.original_len} chars) -> {rotation.rel}")
        else:
            print(f"orchestrator_notes {rotation.original_len} chars, within cap {cap}")
        if not planned.blocks:
            print("nothing to archive")
        for b in planned.blocks:
            print(f"would archive {b.task_id} -> {b.rel} ({len(b.block)} chars)")
        return 0
    try:
        write_notes_rotation(repo, rotation)
        # apply_archive re-derives the plan; the archive files must match planned.blocks.
        # Write them from the already-computed result so a second plan_archive pass
        # cannot see stubs and skip. Re-append via the same helper.
        if planned.changed:
            by_month: dict[str, list[ArchivedBlock]] = {}
            for b in planned.blocks:
                by_month.setdefault(b.month, []).append(b)
            for month, group in by_month.items():
                _append_month(repo / "plan" / "archive" / f"{month}.md", month, group)
        if rotation.rel or planned.changed:
            plan_path.write_text(planned.text, encoding="utf-8", newline="\n")
    except (OSError, ValueError, UnicodeError) as exc:
        log.error("%s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(
        f"archived {len(planned.blocks)} block(s); "
        f"notes {'rotated' if rotation.rel else 'unchanged'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
