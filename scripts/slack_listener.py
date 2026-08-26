#!/usr/bin/env python3
"""slack_listener.py — Socket Mode command listener for the DEVDEPARTMENT
autopilot supervisor (P1b-2, pre-Tower command path).

specs/DEVDEPARTMENT_SLACK_SPEC.md §5 (slack_listener.py), §1 (transport
note), §8 (env vars). Two ORCH-resolved ambiguities shape this module,
both recorded in full in dossiers/TASK-016.md:

1. Socket Mode vs Tower request URLs. §1's manifest points slash-commands
   and interactions at Tower's HTTP endpoints, but Socket Mode and request
   URLs are mutually exclusive per Slack app, and Tower does not exist yet.
   Socket Mode is therefore the PRE-Tower command path; once Tower's P1b-3
   lands, the app flips to request URLs and this listener becomes the
   no-Tower fallback.
2. slack_sdk is the pack's FIRST optional runtime dependency — stdlib has
   no WebSocket client and Socket Mode needs one. Import-guarded: absent
   -> the listener refuses to start with ONE clear warning naming the
   remedy, and every other channel (telegram, console, file, slack SENDING
   via slack_notify.py's stdlib-only urllib transport) is unaffected. Never
   a hard requirement; requirements stay out of the pack's install path.

Runs as a `threading.Thread(daemon=True)` started from supervisor.py,
exactly as TelegramListener does today (SLACK §5: "the architecture does
not change, only the transport"). Mirrors TelegramListener's interface
byte-for-byte at the seams that matter to the caller: constructor takes an
`out_queue: queue.Queue`, exposes `.start()` / `.stop()`, and enqueues the
SAME raw command-dict shape TelegramListener does (tg_listener.py:132 is
the reference: {cmd, args, chat_id, update_id, raw}) so TASK-018's shared
drain (`_drain_command_queue`, replacing `_drain_tg_queue`) can validate
BOTH queues once, through scripts/commands.py.

FABLE-RATIFICATION CORRECTION (supersedes any validate-then-enqueue
wording elsewhere in the spec): validation's real locus is the shared
drain in supervisor.py, which validates every queued item once through
commands.py. This listener performs no second validation pass and makes
no command-vocabulary judgments — it only drops transport-level garbage:
an unparseable Socket Mode envelope, or a slash-command payload missing
its `command` field. Everything else — known or unknown command name,
malformed argument shape — is queued exactly as received and rejected (or
accepted) by the drain, same as Telegram's parse_command already does.

Only slash-command requests are enqueued (`request.type == "slash_commands"`)
here, matching TelegramListener's own scope of text commands only (it
silently ignores non-text updates like photos/stickers as "nothing to
command"). Interactive components (button clicks) are Tower's
`/slack/interactions` concern per SLACK §6, not part of this pre-Tower
per-project listener.

Nothing in this module ever touches PLAN.md or git. Mutation stays on the
main thread (see supervisor.py's `_drain_command_queue`, TASK-018) so there
is exactly one writer to the repo at any moment — the same single-writer
discipline the territory firewall enforces for builders, just applied to
threads instead of processes.
"""
from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable

log = logging.getLogger("slack_listener")

# --------------------------------------------------------------- optional dep
# slack_sdk is imported here and ONLY here in the pack. Absent -> the
# listener refuses to start (see `start()` below); nothing else breaks.
try:
    from slack_sdk import WebClient
    from slack_sdk.socket_mode import SocketModeClient

    SLACK_SDK_AVAILABLE = True
except ImportError:  # pragma: no cover — exercised via the availability flag
    WebClient = None  # type: ignore[assignment,misc]
    SocketModeClient = None  # type: ignore[assignment,misc]
    SLACK_SDK_AVAILABLE = False

INSTALL_HINT = "pip install slack_sdk"
MAX_BACKOFF_S = 60
_IDLE_WAIT_S = 1


class SlackListener(threading.Thread):
    """Daemon thread: Socket Mode connection, enqueues raw slash-command
    dicts. Mirrors TelegramListener's constructor/interface shape so
    supervisor wiring (TASK-018) is symmetric between the two listeners."""

    def __init__(
        self,
        app_token: str,
        bot_token: str,
        out_queue: "queue.Queue",
        client_factory: Callable[[str, str], Any] | None = None,
        log_fn: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(daemon=True, name="slack-listener")
        self.app_token = app_token
        self.bot_token = bot_token
        self.out_queue = out_queue
        self._client_factory = client_factory or self._default_client_factory
        self._log = log_fn or (lambda msg: log.info(msg))
        self._stop_event = threading.Event()
        self._client: Any = None
        self.rejected_count = 0  # exposed for tests/observability, parity w/ tg_listener
        # Captured at construction time so a test can flip it post-construct
        # to exercise the "as if available" path without a real slack_sdk.
        self.available = SLACK_SDK_AVAILABLE

    @staticmethod
    def dependency_available() -> bool:
        """True iff slack_sdk is importable in this interpreter. Lets a
        caller (TASK-018) decide whether to even construct a listener,
        without needing to import this module's internal flag directly."""
        return SLACK_SDK_AVAILABLE

    # ------------------------------------------------------------- transport
    def _default_client_factory(self, app_token: str, bot_token: str) -> Any:
        if not SLACK_SDK_AVAILABLE:
            raise RuntimeError("slack_sdk is not installed")
        return SocketModeClient(app_token=app_token, web_client=WebClient(token=bot_token))

    def _on_socket_request(self, client: Any, request: Any) -> None:
        """Registered as a `socket_mode_request_listeners` callback. ACKs
        every envelope immediately (Slack retries/re-delivers unacked
        requests), then enqueues slash-commands only; everything else is
        transport scope, not command scope, and is dropped after ack."""
        try:
            envelope_id = getattr(request, "envelope_id", None)
            if envelope_id:
                client.send_socket_mode_response({"envelope_id": envelope_id})
        except Exception as exc:  # noqa: BLE001 — ack failure must never kill the thread
            self._log(f"[slack_listener] failed to ack envelope: {exc}")

        req_type = getattr(request, "type", None)
        if req_type != "slash_commands":
            return  # interactive/events_api etc. — out of this listener's scope (SLACK §6)

        payload = getattr(request, "payload", None)
        if not isinstance(payload, dict):
            self.rejected_count += 1
            self._log(f"[slack_listener] REJECTED unparseable slash-command payload: {payload!r}")
            return

        cmd = str(payload.get("command") or "").strip()
        if not cmd:
            self.rejected_count += 1
            self._log("[slack_listener] REJECTED slash-command payload with empty/missing command")
            return

        args = payload.get("text", "") or ""
        chat_id = str(payload.get("channel_id", "") or "")
        raw = f"{cmd} {args}".strip()

        self.out_queue.put({
            "cmd": cmd,
            "args": args,
            "chat_id": chat_id,
            "update_id": None,  # Socket Mode has no Telegram-style replay offset to carry
            "raw": raw,
        })

    # --------------------------------------------------------------- thread
    def stop(self) -> None:
        self._stop_event.set()

    def start(self) -> None:  # noqa: D102 — overrides threading.Thread.start
        """Refuses to start (one warning, no thread spawned) when slack_sdk
        is unavailable — the whole point of the import guard is that this
        failure mode is silent to every OTHER channel, not just quiet."""
        if not self.available:
            self._log(
                f"[slack_listener] slack_sdk not installed — Slack listener "
                f"disabled ({INSTALL_HINT}); every other channel is unaffected."
            )
            return
        super().start()

    def run(self) -> None:  # pragma: no cover — exercised via _on_socket_request in tests
        if not self.available:
            return  # defensive: start() already refused; guards direct run() calls too
        backoff = 1
        while not self._stop_event.is_set():
            if self._client is None:
                try:
                    client = self._client_factory(self.app_token, self.bot_token)
                    client.socket_mode_request_listeners.append(self._on_socket_request)
                    client.connect()
                    self._client = client
                    self._log("[slack_listener] Socket Mode connection opened.")
                    backoff = 1
                except Exception as exc:  # noqa: BLE001 — connect failures must never kill the thread
                    self._log(f"[slack_listener] connect failed: {exc}")
                    self._client = None
                    self._stop_event.wait(timeout=backoff)
                    backoff = min(backoff * 2, MAX_BACKOFF_S)
                    continue
            # Slack rotates socket URLs and drops idle connections routinely;
            # slack_sdk's client reconnects internally. We just idle-wait and
            # let _on_socket_request (its callback) do the enqueue work.
            self._stop_event.wait(timeout=_IDLE_WAIT_S)

        if self._client is not None:
            try:
                self._client.close()
            except Exception as exc:  # noqa: BLE001 — close failure must never raise out of the thread
                self._log(f"[slack_listener] error closing connection: {exc}")
