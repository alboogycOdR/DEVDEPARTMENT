#!/usr/bin/env python3
"""tg_listener.py — Long-polling Telegram command listener for the
DEVDEPARTMENT autopilot supervisor (Wave A-remainder, two-way Telegram).

Runs as a `threading.Thread(daemon=True)` started from supervisor.py. Long-
polls Telegram's getUpdates, checks the sender's chat_id against the
allowlist (silently dropping anyone else — no reply, so the bot's existence
isn't confirmed to strangers), parses recognised commands, and pushes ONLY
validated {cmd, args, chat_id, update_id, raw} dicts onto a thread-safe
queue.Queue for the main tick loop to drain.

Nothing in this module ever touches PLAN.md or git. Mutation stays on the
main thread (see supervisor.py's _drain_tg_queue) so there is exactly one
writer to the repo at any moment — the same single-writer discipline the
territory firewall enforces for builders, just applied to threads instead of
processes.

The offset (last processed update_id + 1) is persisted to
.devteam/tg_offset.txt after every update, so a supervisor restart never
replays old commands — including commands that would otherwise re-fire
side-effecting actions like /stop or /approve.
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import urllib.request
from pathlib import Path
from typing import Callable

import tg_commands as tgc

log = logging.getLogger("tg_listener")

API_ROOT = "https://api.telegram.org"
LONG_POLL_TIMEOUT_S = 25
MAX_BACKOFF_S = 60


class TelegramListener(threading.Thread):
    """Daemon thread: long-polls Telegram, enqueues validated commands."""

    def __init__(
        self,
        token: str,
        allowlist: list[str],
        default_chat: str,
        out_queue: "queue.Queue",
        offset_path: Path,
        poll_interval_seconds: int = 20,
        fetch: Callable[[str, int], dict] | None = None,
        log_fn: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(daemon=True, name="tg-listener")
        self.token = token
        self.allowlist = allowlist or []
        self.default_chat = default_chat or ""
        self.out_queue = out_queue
        self.offset_path = Path(offset_path)
        self.poll_interval_seconds = max(1, int(poll_interval_seconds or 20))
        self._fetch = fetch or self._http_fetch
        self._log = log_fn or (lambda msg: log.info(msg))
        self._stop_event = threading.Event()
        self._offset = self._load_offset()
        self.rejected_count = 0  # exposed for tests/observability

    # ---------------------------------------------------------- offset -----
    def _load_offset(self) -> int:
        try:
            return int(self.offset_path.read_text(encoding="utf-8").strip())
        except (FileNotFoundError, ValueError, OSError):
            return 0

    def _save_offset(self, offset: int) -> None:
        try:
            self.offset_path.parent.mkdir(parents=True, exist_ok=True)
            self.offset_path.write_text(str(offset), encoding="utf-8")
        except OSError as exc:  # persistence failure must never crash the thread
            self._log(f"[tg_listener] failed to persist offset {offset}: {exc}")
        self._offset = offset

    @property
    def offset(self) -> int:
        return self._offset

    # ---------------------------------------------------------- network ----
    def _http_fetch(self, method: str, timeout: int) -> dict:
        url = f"{API_ROOT}/bot{self.token}/{method}?offset={self._offset}&timeout={timeout}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
            return json.loads(resp.read().decode())

    # ------------------------------------------------------- one cycle -----
    def poll_once(self) -> bool:
        """Fetch + handle exactly one batch of updates. Returns True on success,
        False on any failure (caller is responsible for backing off)."""
        try:
            data = self._fetch("getUpdates", LONG_POLL_TIMEOUT_S)
        except Exception as exc:  # noqa: BLE001 — network errors must never kill the thread
            self._log(f"[tg_listener] poll failed: {exc}")
            return False
        if not isinstance(data, dict) or not data.get("ok", False):
            self._log(f"[tg_listener] Telegram API returned not-ok: {data}")
            return False
        for update in data.get("result", []) or []:
            try:
                self._handle_update(update)
            except Exception as exc:  # noqa: BLE001 — one malformed update must not stop the rest
                self._log(f"[tg_listener] failed to handle update {update.get('update_id')}: {exc}")
        return True

    def _handle_update(self, update: dict) -> None:
        update_id = update.get("update_id")
        if isinstance(update_id, int):
            self._save_offset(update_id + 1)

        message = update.get("message") or update.get("edited_message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", "")) if chat.get("id") is not None else ""
        text = message.get("text", "") or ""
        if not text.strip():
            return  # non-text update (photo, sticker, etc.) — nothing to command

        if not tgc.is_allowed(chat_id, self.allowlist, self.default_chat):
            self.rejected_count += 1
            self._log(f"[tg_listener] REJECTED chat_id={chat_id!r} — not in allowlist, no reply sent")
            return

        cmd, args = tgc.parse_command(text)
        self.out_queue.put({
            "cmd": cmd,
            "args": args,
            "chat_id": chat_id,
            "update_id": update_id,
            "raw": text,
        })

    # ------------------------------------------------------------ thread ---
    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:  # pragma: no cover — exercised via poll_once() in tests
        backoff = 1
        while not self._stop_event.is_set():
            ok = self.poll_once()
            if ok:
                backoff = 1
                self._stop_event.wait(timeout=1)  # brief idle between long-polls
            else:
                self._stop_event.wait(timeout=backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_S)
