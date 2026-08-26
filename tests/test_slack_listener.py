"""Tests for scripts/slack_listener.py — Socket Mode Slack command listener
(P1b-2). No real slack_sdk, no network: the socket layer is stubbed with an
injected client_factory / fake client / fake request objects, matching the
rest of this codebase's convention of pure/injectable I/O boundaries (see
test_tg_listener.py). This also naturally exercises the real absent-
dependency path, since slack_sdk is not installed in this environment —
exactly the "importable-absent" case the module must degrade cleanly on."""
import queue
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import slack_listener as sl  # noqa: E402
from slack_listener import SlackListener  # noqa: E402


def make_listener(client_factory=None, log=None):
    q: "queue.Queue" = queue.Queue()
    logs: list[str] = []
    listener = SlackListener(
        app_token="xapp-test",
        bot_token="xoxb-test",
        out_queue=q,
        client_factory=client_factory,
        log_fn=log or (lambda msg: logs.append(msg)),
    )
    return listener, q, logs


class FakeClient:
    """Minimal double for slack_sdk's SocketModeClient — exposes exactly
    the surface slack_listener.py touches: an appendable request-listener
    list, connect()/close(), and send_socket_mode_response()."""

    def __init__(self):
        self.socket_mode_request_listeners: list = []
        self.connected = False
        self.closed = False
        self.acked: list[dict] = []
        self.connect_calls = 0

    def connect(self):
        self.connect_calls += 1
        self.connected = True

    def close(self):
        self.closed = True

    def send_socket_mode_response(self, response):
        self.acked.append(response)


def slash_request(command, text="", channel_id="C123", envelope_id="env-1"):
    return SimpleNamespace(
        type="slash_commands",
        envelope_id=envelope_id,
        payload={"command": command, "text": text, "channel_id": channel_id},
    )


# ----------------------------------------------------------- dependency guard
class TestDependencyGuard:
    def test_slack_sdk_not_installed_in_this_environment(self):
        # The pack ships zero third-party runtime dependencies; slack_sdk
        # is the first OPTIONAL one and is not present here — this is the
        # real absent-dependency environment, not a simulation.
        assert sl.SLACK_SDK_AVAILABLE is False
        assert SlackListener.dependency_available() is False

    def test_listener_available_flag_reflects_module_constant(self):
        listener, _, _ = make_listener()
        assert listener.available is sl.SLACK_SDK_AVAILABLE
        assert listener.available is False

    def test_start_refuses_with_one_warning_when_unavailable(self):
        listener, _, logs = make_listener()
        listener.start()
        assert listener.is_alive() is False
        assert len(logs) == 1
        assert "slack_sdk not installed" in logs[0]
        assert "pip install slack_sdk" in logs[0]

    def test_start_disabled_has_zero_impact_on_other_channels(self):
        # Constructing/starting an unavailable listener must never raise —
        # the whole point is every other notify channel stays unaffected.
        listener, q, _ = make_listener()
        listener.start()  # no exception
        assert q.empty()

    def test_run_is_defensive_noop_if_called_directly_while_unavailable(self):
        listener, q, _ = make_listener()
        listener.run()  # should return immediately, not raise, not enqueue
        assert q.empty()
        assert listener._client is None


# --------------------------------------------------------- socket-mode events
class TestSlashCommandHandling:
    def test_valid_slash_command_enqueued_with_tg_parity_shape(self):
        listener, q, _ = make_listener()
        client = FakeClient()
        listener._on_socket_request(client, slash_request("/approve", "TASK-045"))

        assert q.qsize() == 1
        item = q.get_nowait()
        assert set(item.keys()) == {"cmd", "args", "chat_id", "update_id", "raw"}
        assert item["cmd"] == "/approve"
        assert item["args"] == "TASK-045"
        assert item["chat_id"] == "C123"
        assert item["update_id"] is None
        assert item["raw"] == "/approve TASK-045"

    def test_no_arg_command_enqueued_with_empty_args(self):
        listener, q, _ = make_listener()
        client = FakeClient()
        listener._on_socket_request(client, slash_request("/status", ""))

        item = q.get_nowait()
        assert item["cmd"] == "/status"
        assert item["args"] == ""
        assert item["raw"] == "/status"

    def test_envelope_acked_before_processing(self):
        listener, _, _ = make_listener()
        client = FakeClient()
        listener._on_socket_request(client, slash_request("/status", envelope_id="env-42"))

        assert client.acked == [{"envelope_id": "env-42"}]

    def test_ack_sent_even_for_non_slash_command_request_type(self):
        listener, q, _ = make_listener()
        client = FakeClient()
        req = SimpleNamespace(type="events_api", envelope_id="env-7", payload={"anything": True})
        listener._on_socket_request(client, req)

        assert client.acked == [{"envelope_id": "env-7"}]
        assert q.empty()  # out of listener scope — Tower's /slack/interactions territory

    def test_ack_failure_does_not_raise_or_block_processing(self):
        listener, q, logs = make_listener()

        class BoomClient(FakeClient):
            def send_socket_mode_response(self, response):
                raise OSError("socket closed")

        listener._on_socket_request(BoomClient(), slash_request("/status"))
        assert any("failed to ack" in m for m in logs)
        # ack failing must not prevent the command from still being queued
        assert q.qsize() == 1


# --------------------------------------------------- transport-level garbage
class TestGarbageRejection:
    def test_unparseable_payload_rejected_not_guessed(self):
        listener, q, logs = make_listener()
        client = FakeClient()
        req = SimpleNamespace(type="slash_commands", envelope_id="env-1", payload="not-a-dict")
        listener._on_socket_request(client, req)

        assert q.empty()
        assert listener.rejected_count == 1
        assert any("REJECTED unparseable" in m for m in logs)

    def test_missing_command_field_rejected(self):
        listener, q, logs = make_listener()
        client = FakeClient()
        req = SimpleNamespace(type="slash_commands", envelope_id="env-1", payload={"text": "TASK-045"})
        listener._on_socket_request(client, req)

        assert q.empty()
        assert listener.rejected_count == 1
        assert any("REJECTED slash-command payload with empty/missing command" in m for m in logs)

    def test_blank_command_field_rejected(self):
        listener, q, _ = make_listener()
        client = FakeClient()
        listener._on_socket_request(client, slash_request("   "))
        assert q.empty()
        assert listener.rejected_count == 1

    def test_unknown_command_name_is_queued_not_judged(self):
        # Vocabulary judgment belongs to the shared drain (commands.py via
        # TASK-018), never to this listener — an unrecognised command still
        # gets queued so the drain can reject it uniformly with Telegram's.
        listener, q, _ = make_listener()
        client = FakeClient()
        listener._on_socket_request(client, slash_request("/frobnicate", "whatever"))
        assert q.qsize() == 1
        assert q.get_nowait()["cmd"] == "/frobnicate"


# -------------------------------------------------------------- run()/lifecycle
class TestRunLifecycleWithInjectedClient:
    def test_run_connects_registers_listener_and_stops_cleanly(self):
        fake_client = FakeClient()

        def factory(app_token, bot_token):
            assert app_token == "xapp-test"
            assert bot_token == "xoxb-test"
            return fake_client

        listener, q, _ = make_listener(client_factory=factory)
        listener.available = True  # simulate slack_sdk present, socket layer stubbed

        listener.start()
        # Give the daemon thread a moment to reach its connect() call.
        for _ in range(50):
            if fake_client.connect_calls:
                break
            time.sleep(0.01)
        assert fake_client.connect_calls == 1
        assert listener._on_socket_request in fake_client.socket_mode_request_listeners

        # Drive an event through the now-registered callback, same as Slack would.
        listener._on_socket_request(fake_client, slash_request("/wave"))
        assert q.get_nowait()["cmd"] == "/wave"

        listener.stop()
        listener.join(timeout=5)
        assert listener.is_alive() is False
        assert fake_client.closed is True

    def test_connect_failure_backs_off_and_retries(self):
        attempts = {"n": 0}

        def flaky_factory(app_token, bot_token):
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise ConnectionError("socket refused")
            return FakeClient()

        listener, _, logs = make_listener(client_factory=flaky_factory)
        listener.available = True

        listener.start()
        for _ in range(200):
            if attempts["n"] >= 2:
                break
            time.sleep(0.01)
        listener.stop()
        listener.join(timeout=5)

        assert attempts["n"] >= 2
        assert any("connect failed" in m for m in logs)


# ------------------------------------------------------------------ lifecycle
class TestLifecycle:
    def test_stop_sets_event(self):
        listener, _, _ = make_listener()
        assert not listener._stop_event.is_set()
        listener.stop()
        assert listener._stop_event.is_set()

    def test_is_daemon_thread(self):
        listener, _, _ = make_listener()
        assert listener.daemon is True

    def test_rejected_count_starts_at_zero(self):
        listener, _, _ = make_listener()
        assert listener.rejected_count == 0
