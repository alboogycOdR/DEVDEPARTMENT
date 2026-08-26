#!/usr/bin/env python3
"""commands.py — shared command-validation module.

Every command path (Telegram today; Slack listener and Tower inbox tomorrow)
validates through this module. H1 / SLACK §5: one vocabulary, one arg checker;
unknown commands are rejected, never guessed (TOWER §1 P2).

Telegram transport concerns (slash prefixes, @BotName, "reply with help")
stay in tg_commands.parse_command. This module's surface is transport-neutral:

    validate(command, args) -> (True, normalized) | (False, reason)

`command` may be a slash form ("/approve") or a bare name ("approve").
`args` may be a Telegram-style string or a Tower P2 dict
({task_id, text}). Normalized output always uses the bare name and a dict.

Git helpers, PLAN.md editors, and reply rendering are not command-validation
and do not live here.
"""
from __future__ import annotations

import re
from typing import Any

# Shared vocabulary (TOWER §1 P2 + SLACK §5 / task list). Bare names, no slash.
# `/board` is Telegram-only and is NOT in this set — it stays on the Telegram
# shim so Slack/Tower cannot grow a board command by accident.
VOCABULARY = frozenset({
    "approve", "rework", "answer", "stop", "resume", "wave",
    "dispatch", "status", "usage", "mute", "digest",
})

# Commands that take no arguments. Non-empty args → malformed (not guessed).
_NO_ARG = frozenset({
    "stop", "resume", "wave", "status", "usage", "digest",
})

# Telegram slash surface — the historical COMMANDS set. Includes /board
# (Telegram-only) and does NOT include /dispatch (supervisor has no Telegram
# handler; adding it would be a behaviour change). Re-exported as COMMANDS
# so tg_commands.COMMANDS stays byte-compatible for existing callers/tests.
TELEGRAM_COMMANDS = frozenset({
    "/status", "/board", "/answer", "/approve", "/rework",
    "/stop", "/resume", "/wave", "/digest", "/mute", "/usage",
})
COMMANDS = TELEGRAM_COMMANDS

# TASK-\d+ (TASK-001) is the common shape; TASK-[A-Z0-9-]+ also accepts
# self-generated escalation IDs like TASK-MAINT-2026-07-19.
_TASK_AND_TEXT_RE = re.compile(r"^(TASK-[A-Z0-9-]+)\s+(.+)$", re.DOTALL)
_TASK_ONLY_RE = re.compile(r"^(TASK-[A-Z0-9-]+)\s*$")
_DURATION_RE = re.compile(r"^(\d+)\s*([hm])$", re.IGNORECASE)
AMEND_RE = re.compile(r"^(AMEND-\d+)\s*$")
_AMEND_AND_TEXT_RE = re.compile(r"^(AMEND-\d+)\s+(.+)$", re.DOTALL)
_TASK_ID_RE = re.compile(r"^TASK-[A-Z0-9-]+$")
_AMEND_ID_RE = re.compile(r"^AMEND-\d+$")

# Tower P2 dict keys. Anything else is malformed (never guessed as a synonym).
_P2_KEYS = frozenset({"task_id", "text"})
_MUTE_KEYS = frozenset({"task_id", "text", "duration", "duration_s"})
_DISPATCH_KEYS = frozenset({"task_id", "text"})


def canonical_name(command: str | None) -> str | None:
    """Bare vocabulary name, or None if the token is not in VOCABULARY.

    Accepts "/approve", "/approve@Bot", "approve", "APPROVE". Does not parse
    arguments out of the command string — that is the caller's job.
    """
    if command is None:
        return None
    raw = str(command).strip()
    if not raw:
        return None
    token = raw.split(None, 1)[0]
    if token.startswith("/"):
        token = token[1:]
    if "@" in token:
        token = token.split("@", 1)[0]
    token = token.lower()
    return token if token in VOCABULARY else None


def parse_amend_args(args: str) -> str | None:
    m = AMEND_RE.match((args or "").strip())
    return m.group(1) if m else None


def parse_amend_and_text(args: str) -> tuple[str, str] | None:
    """Parse '<AMEND-NNN> <reason>' for /rework AMEND-NNN <reason>."""
    m = _AMEND_AND_TEXT_RE.match((args or "").strip())
    if not m:
        return None
    return m.group(1), m.group(2)


def parse_task_and_text(args: str) -> tuple[str, str] | None:
    """Parse '<TASK-NNN> <free text...>' shared by /answer and /rework."""
    m = _TASK_AND_TEXT_RE.match((args or "").strip())
    if not m:
        return None
    return m.group(1), m.group(2)


# Kept as distinct names because the grammar table lists them as distinct
# commands with independently-evolvable argument shapes, even though today
# both are literally "TASK-NNN <text>".
def parse_answer_args(args: str) -> tuple[str, str] | None:
    return parse_task_and_text(args)


def parse_rework_args(args: str) -> tuple[str, str] | None:
    return parse_task_and_text(args)


def parse_approve_args(args: str) -> str | None:
    m = _TASK_ONLY_RE.match((args or "").strip())
    return m.group(1) if m else None


def parse_mute_args(args: str) -> int | None:
    """'2h' / '30m' -> seconds. Anything else -> None (caller replies usage)."""
    m = _DURATION_RE.match((args or "").strip())
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    if n <= 0:
        return None
    return n * 3600 if unit == "h" else n * 60


def parse_dispatch_args(args: str) -> str | None:
    """Optional TASK-NNN, or empty. Anything else is not guessed as a unit."""
    raw = (args or "").strip()
    if not raw:
        return ""
    m = _TASK_ONLY_RE.match(raw)
    return m.group(1) if m else None


def _reject_unknown(command: Any) -> tuple[bool, str]:
    return False, f"unknown command: {command!r} — rejected, never guessed"


def _reject_malformed(name: str, detail: str) -> tuple[bool, str]:
    return False, f"malformed {name}: {detail}"


def _ok(name: str, args: dict[str, Any]) -> tuple[bool, dict[str, Any]]:
    return True, {"command": name, "args": args}


def _as_dict_args(args: Any) -> tuple[dict[str, Any] | None, str | None, str | None]:
    """Return (dict, raw_string, error).

    dict  — already a mapping (Tower P2).
    raw_string — Telegram-style argument string, possibly "".
    error — why the payload is unusable, if so.
    """
    if args is None:
        return {}, None, None
    if isinstance(args, dict):
        return dict(args), None, None
    if isinstance(args, str):
        return None, args, None
    return None, None, f"args must be a string or dict, not {type(args).__name__}"


def _nonempty_text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _dict_keys_ok(payload: dict[str, Any], allowed: frozenset[str]) -> str | None:
    extra = [k for k in payload.keys() if k not in allowed]
    if extra:
        extra_s = ", ".join(sorted(str(k) for k in extra))
        return f"unknown arg keys ({extra_s}) — rejected, never guessed"
    return None


def _validate_no_arg(name: str, payload: dict[str, Any] | None, raw: str | None) -> tuple[bool, Any]:
    if raw is not None:
        if raw.strip():
            return _reject_malformed(name, "takes no arguments")
        return _ok(name, {})
    assert payload is not None
    # Empty dict, or dict whose values are all empty/None, is fine.
    nonempty = {k: v for k, v in payload.items() if v not in (None, "", [], {})}
    if nonempty:
        return _reject_malformed(name, "takes no arguments")
    return _ok(name, {})


def _validate_mute(payload: dict[str, Any] | None, raw: str | None) -> tuple[bool, Any]:
    if raw is not None:
        secs = parse_mute_args(raw)
        if secs is None:
            return _reject_malformed("mute", "duration must be e.g. 2h or 30m")
        return _ok("mute", {"duration_s": secs})
    assert payload is not None
    bad = _dict_keys_ok(payload, _MUTE_KEYS)
    if bad:
        return _reject_malformed("mute", bad)
    if "duration_s" in payload and payload["duration_s"] not in (None, ""):
        secs = payload["duration_s"]
        if not isinstance(secs, int) or isinstance(secs, bool) or secs <= 0:
            return _reject_malformed("mute", "duration_s must be a positive int")
        return _ok("mute", {"duration_s": secs})
    duration = payload.get("duration")
    if duration in (None, ""):
        duration = payload.get("text")
    if not isinstance(duration, str):
        return _reject_malformed("mute", "duration must be e.g. 2h or 30m")
    secs = parse_mute_args(duration)
    if secs is None:
        return _reject_malformed("mute", "duration must be e.g. 2h or 30m")
    return _ok("mute", {"duration_s": secs})


def _task_or_amend(value: Any) -> tuple[str | None, str | None]:
    """Return ('task'|'amend', id) or (None, None)."""
    if not isinstance(value, str):
        return None, None
    token = value.strip()
    if _TASK_ID_RE.match(token):
        return "task", token
    if _AMEND_ID_RE.match(token):
        return "amend", token
    return None, None


def _validate_approve(payload: dict[str, Any] | None, raw: str | None) -> tuple[bool, Any]:
    if raw is not None:
        amend_id = parse_amend_args(raw)
        if amend_id:
            return _ok("approve", {"amend_id": amend_id})
        task_id = parse_approve_args(raw)
        if task_id:
            return _ok("approve", {"task_id": task_id})
        return _reject_malformed("approve", "expected TASK-NNN or AMEND-NNN")
    assert payload is not None
    bad = _dict_keys_ok(payload, _P2_KEYS)
    if bad:
        return _reject_malformed("approve", bad)
    kind, ident = _task_or_amend(payload.get("task_id"))
    if kind == "amend":
        if _nonempty_text(payload.get("text")):
            return _reject_malformed("approve", "AMEND-NNN takes no extra text")
        return _ok("approve", {"amend_id": ident})
    if kind == "task":
        if _nonempty_text(payload.get("text")):
            return _reject_malformed("approve", "TASK-NNN takes no extra text")
        return _ok("approve", {"task_id": ident})
    return _reject_malformed("approve", "expected TASK-NNN or AMEND-NNN")


def _validate_text_command(name: str, payload: dict[str, Any] | None, raw: str | None) -> tuple[bool, Any]:
    """answer / rework: TASK-NNN <text>, or rework AMEND-NNN <reason>."""
    if raw is not None:
        if name == "rework":
            amend_parsed = parse_amend_and_text(raw)
            if amend_parsed:
                amend_id, reason = amend_parsed
                if not _nonempty_text(reason):
                    return _reject_malformed(name, "empty text")
                return _ok(name, {"amend_id": amend_id, "text": reason})
        parsed = parse_task_and_text(raw)
        if not parsed:
            return _reject_malformed(name, "expected TASK-NNN <text>")
        task_id, text = parsed
        if not _nonempty_text(text):
            return _reject_malformed(name, "empty text")
        return _ok(name, {"task_id": task_id, "text": text})
    assert payload is not None
    bad = _dict_keys_ok(payload, _P2_KEYS)
    if bad:
        return _reject_malformed(name, bad)
    kind, ident = _task_or_amend(payload.get("task_id"))
    text = _nonempty_text(payload.get("text"))
    if not text:
        return _reject_malformed(name, "expected TASK-NNN <text>")
    if kind == "amend":
        if name != "rework":
            return _reject_malformed(name, "AMEND-NNN is not valid for this command")
        return _ok(name, {"amend_id": ident, "text": text})
    if kind == "task":
        return _ok(name, {"task_id": ident, "text": text})
    return _reject_malformed(name, "expected TASK-NNN <text>")


def _validate_dispatch(payload: dict[str, Any] | None, raw: str | None) -> tuple[bool, Any]:
    if raw is not None:
        parsed = parse_dispatch_args(raw)
        if parsed is None:
            return _reject_malformed("dispatch", "expected empty args or TASK-NNN")
        if parsed == "":
            return _ok("dispatch", {})
        return _ok("dispatch", {"task_id": parsed})
    assert payload is not None
    bad = _dict_keys_ok(payload, _DISPATCH_KEYS)
    if bad:
        return _reject_malformed("dispatch", bad)
    out: dict[str, Any] = {}
    tid = payload.get("task_id")
    if tid not in (None, ""):
        kind, ident = _task_or_amend(tid)
        if kind != "task":
            return _reject_malformed("dispatch", "task_id must be TASK-NNN")
        out["task_id"] = ident
    text = _nonempty_text(payload.get("text"))
    if text:
        out["text"] = text
    return _ok("dispatch", out)


def validate(command: Any, args: Any = None) -> tuple[bool, dict[str, Any] | str]:
    """Validate one command.

    Returns (True, {"command": <bare name>, "args": {...}}) on success,
    or (False, reason) on unknown / malformed input. Unknown names are
    rejected with an explicit "never guessed" reason — they are never
    rewritten to a nearby valid command.
    """
    name = canonical_name(command if isinstance(command, str) else None)
    if name is None:
        return _reject_unknown(command)

    payload, raw, err = _as_dict_args(args)
    if err:
        return _reject_malformed(name, err)

    if name in _NO_ARG:
        return _validate_no_arg(name, payload, raw)
    if name == "mute":
        return _validate_mute(payload, raw)
    if name == "approve":
        return _validate_approve(payload, raw)
    if name in ("answer", "rework"):
        return _validate_text_command(name, payload, raw)
    if name == "dispatch":
        return _validate_dispatch(payload, raw)
    # VOCABULARY is closed; reaching here would mean the sets drifted.
    return _reject_unknown(command)
