#!/usr/bin/env python3
"""Fail-open consumer for Tower's local command inbox.

Tower only writes JSON files to ``.devteam/inbox``; it never edits project
state directly.  This module validates each envelope through :mod:`commands`
and returns the normalized command dictionaries for supervisor.py to handle.

Consumption is deliberately two phase: :func:`drain_inbox` never deletes a
valid file, and its caller must call :func:`ack` after the action completed.
That makes a crash between draining and handling replay-safe.  Rejected input
is retained, with a ``.reason`` sidecar, for audit rather than being guessed
or silently deleted.
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

import commands

REQUIRED_KEYS = frozenset({"id", "issued_at", "source", "actor", "command", "args"})
_CONSUMED_FILE = ".consumed_ids.json"
_PATH_KEY = "_inbox_path"


def _warn(message: str) -> None:
    print(f"[inbox] {message}", file=sys.stderr)


def _inbox_dir(repo: Path) -> Path:
    return Path(repo) / ".devteam" / "inbox"


def _load_consumed(directory: Path) -> set[str]:
    path = directory / _CONSUMED_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {item for item in data if isinstance(item, str)} if isinstance(data, list) else set()
    except FileNotFoundError:
        return set()
    except (OSError, json.JSONDecodeError) as exc:
        _warn(f"could not read consumed-id ledger: {exc}; continuing safely")
        return set()


def _write_consumed(directory: Path, consumed: set[str]) -> None:
    path = directory / _CONSUMED_FILE
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    tmp.write_text(json.dumps(sorted(consumed), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _reject(path: Path, reason: str) -> None:
    """Move one bad envelope to rejected/ and retain its rejection reason."""
    rejected = path.parent / "rejected"
    rejected.mkdir(parents=True, exist_ok=True)
    destination = rejected / path.name
    if destination.exists():
        destination = rejected / f"{path.stem}-{uuid.uuid4().hex[:8]}{path.suffix}"
    path.replace(destination)
    destination.with_name(destination.name + ".reason").write_text(reason + "\n", encoding="utf-8")


def _validate_envelope(value: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(value, dict):
        return None, "malformed JSON envelope: expected an object"
    keys = set(value)
    if keys != REQUIRED_KEYS:
        missing = sorted(REQUIRED_KEYS - keys)
        extra = sorted(keys - REQUIRED_KEYS)
        detail = []
        if missing:
            detail.append("missing " + ", ".join(missing))
        if extra:
            detail.append("unknown " + ", ".join(extra))
        return None, "malformed envelope: " + "; ".join(detail)
    for name in ("id", "issued_at", "source", "actor", "command"):
        if not isinstance(value[name], str) or not value[name].strip():
            return None, f"malformed envelope: {name} must be a non-empty string"
    if not isinstance(value["args"], dict):
        return None, "malformed envelope: args must be an object"
    ok, normalized = commands.validate(value["command"], value["args"])
    if not ok:
        return None, str(normalized)
    assert isinstance(normalized, dict)
    return {
        "id": value["id"],
        "issued_at": value["issued_at"],
        "source": value["source"],
        "actor": value["actor"],
        "command": normalized["command"],
        "args": normalized["args"],
    }, None


def drain_inbox(repo: Path, cfg: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Return validated, unacknowledged Tower commands without deleting them.

    ``cfg`` is accepted for the supervisor integration contract; the inbox is
    intentionally configuration-free.  Per-file failures are logged and
    skipped so Tower can never wedge a supervisor tick.
    """
    del cfg
    directory = _inbox_dir(Path(repo))
    if not directory.is_dir():
        return []
    consumed = _load_consumed(directory)
    accepted_ids: set[str] = set()
    commands_out: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.json")):
        if path.name == _CONSUMED_FILE:
            continue
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception as exc:  # a read failure must never terminate the tick
            _warn(f"skipping {path.name}: {exc}")
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            try:
                _reject(path, f"malformed JSON: {exc}")
            except Exception as reject_exc:  # pragma: no cover - filesystem failure
                _warn(f"skipping {path.name}: could not reject malformed JSON: {reject_exc}")
            continue
        try:
            item, reason = _validate_envelope(value)
            if reason:
                _reject(path, reason)
                continue
            assert item is not None
            command_id = item["id"]
            if command_id in consumed or command_id in accepted_ids:
                _reject(path, f"duplicate command id: {command_id}")
                continue
            accepted_ids.add(command_id)
            item[_PATH_KEY] = str(path)
            commands_out.append(item)
        except Exception as exc:  # a bad file must never terminate the tick
            _warn(f"skipping {path.name}: {exc}")
    return commands_out


def ack(repo: Path, command: dict[str, Any]) -> bool:
    """Record a successfully handled command, then remove its inbox file.

    The consumed-id ledger is written before unlinking.  A crash in between
    can at worst retain a file that will be rejected as already consumed; it
    cannot replay a completed state-changing command.
    """
    directory = _inbox_dir(Path(repo))
    raw_path = command.get(_PATH_KEY)
    command_id = command.get("id")
    if not isinstance(raw_path, str) or not isinstance(command_id, str) or not command_id:
        _warn("ack skipped malformed drained command")
        return False
    path = Path(raw_path)
    try:
        if path.parent != directory or path.suffix != ".json":
            raise ValueError("command path is outside the inbox")
        consumed = _load_consumed(directory)
        consumed.add(command_id)
        _write_consumed(directory, consumed)
        path.unlink(missing_ok=True)
        return True
    except Exception as exc:  # ack failures remain recoverable and non-fatal
        _warn(f"could not acknowledge {command_id}: {exc}")
        return False
