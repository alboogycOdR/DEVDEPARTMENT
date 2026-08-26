"""Tests for scripts/slack_notify.py — Web API sender, thread tracking,
Block Kit designs, §2 routing, rate-limit backoff, fail-open (P1b-1).

specs/DEVDEPARTMENT_SLACK_SPEC.md §2, §3, §5, §9, §10. Stubbed transport
throughout — zero live Slack calls."""
import json
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import slack_notify as sn  # noqa: E402


# --------------------------------------------------------------- fake transport

class FakeResponse:
    def __init__(self, body: dict):
        self._raw = json.dumps(body).encode("utf-8")

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = {"Retry-After": retry_after} if retry_after is not None else {}
    return urllib.error.HTTPError("https://slack.com/api/x", code, "err", headers, None)


class ScriptedTransport:
    """Replays a queue of responses. Each item is either a dict (200 OK JSON
    body), an Exception instance to raise, or a callable(req) -> response."""

    def __init__(self, items):
        self.items = list(items)
        self.calls = []

    def __call__(self, req, timeout=None):
        self.calls.append(req)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item) and not isinstance(item, dict):
            return item(req)
        return FakeResponse(item)


def install(monkeypatch, items):
    transport = ScriptedTransport(items)
    monkeypatch.setattr(sn.urllib.request, "urlopen", transport)
    monkeypatch.setattr(sn.time, "sleep", lambda *_a, **_kw: None)
    return transport


def last_body(transport, index=-1):
    return json.loads(transport.calls[index].data.decode("utf-8"))


# ------------------------------------------------------------------------ config

class TestConfig:
    def test_load_config_missing_file(self, tmp_path):
        assert sn.load_config(tmp_path) == {}

    def test_load_config_malformed_json(self, tmp_path):
        (tmp_path / "autopilot.json").write_text("{not json", encoding="utf-8")
        assert sn.load_config(tmp_path) == {}

    def test_load_config_missing_slack_key(self, tmp_path):
        (tmp_path / "autopilot.json").write_text(json.dumps({"other": 1}), encoding="utf-8")
        assert sn.load_config(tmp_path) == {}

    def test_load_config_slack_not_a_dict(self, tmp_path):
        (tmp_path / "autopilot.json").write_text(json.dumps({"slack": "nope"}), encoding="utf-8")
        assert sn.load_config(tmp_path) == {}

    def test_load_config_happy_path(self, tmp_path):
        cfg = {"enabled": True, "ops_channel": "COPS", "project_channel": "CPROJ"}
        (tmp_path / "autopilot.json").write_text(json.dumps({"slack": cfg}), encoding="utf-8")
        assert sn.load_config(tmp_path) == cfg

    def test_is_enabled(self):
        assert sn.is_enabled({"enabled": True}) is True
        assert sn.is_enabled({"enabled": False}) is False
        assert sn.is_enabled({}) is False

    def test_get_token_from_env(self, monkeypatch):
        monkeypatch.setenv("DEVTEAM_SLACK_TOKEN", "xoxb-test")
        assert sn.get_token() == "xoxb-test"

    def test_get_token_absent(self, monkeypatch):
        monkeypatch.delenv("DEVTEAM_SLACK_TOKEN", raising=False)
        assert sn.get_token() == ""


# ---------------------------------------------------------------------- routing

class TestRouting:
    CFG = {"ops_channel": "COPS", "project_channel": "CPROJ"}

    def test_p0_to_ops(self):
        assert sn.route_channels("P0", self.CFG) == ["COPS"]

    def test_p1_to_ops(self):
        assert sn.route_channels("P1", self.CFG) == ["COPS"]

    def test_p2_to_project(self):
        assert sn.route_channels("P2", self.CFG) == ["CPROJ"]

    def test_status_to_project(self):
        assert sn.route_channels("status", self.CFG) == ["CPROJ"]

    def test_usage_to_project(self):
        assert sn.route_channels("usage", self.CFG) == ["CPROJ"]

    def test_wave_complete_to_both(self):
        assert sn.route_channels("wave_complete", self.CFG) == ["COPS", "CPROJ"]

    def test_wave_complete_dedupes_same_channel(self):
        cfg = {"ops_channel": "SAME", "project_channel": "SAME"}
        assert sn.route_channels("wave_complete", cfg) == ["SAME"]

    def test_unconfigured_channel_dropped(self):
        cfg = {"ops_channel": "", "project_channel": "CPROJ"}
        assert sn.route_channels("P1", cfg) == []
        assert sn.route_channels("P2", cfg) == ["CPROJ"]

    def test_missing_keys_yield_empty(self):
        assert sn.route_channels("P0", {}) == []
        assert sn.route_channels("wave_complete", {}) == []


# -------------------------------------------------------------------- api_call

class TestApiCall:
    def test_uses_web_api_not_webhook(self, monkeypatch):
        transport = install(monkeypatch, [{"ok": True, "ts": "111.222"}])
        result = sn.api_call("tok", "chat.postMessage", {"channel": "C1", "text": "hi"})
        assert result == {"ok": True, "ts": "111.222"}
        req = transport.calls[0]
        assert req.full_url == "https://slack.com/api/chat.postMessage"
        assert req.get_header("Authorization") == "Bearer tok"
        assert json.loads(req.data.decode())["channel"] == "C1"

    def test_no_token_returns_none_without_network_call(self, monkeypatch):
        transport = install(monkeypatch, [])
        assert sn.api_call("", "chat.postMessage", {"channel": "C1"}) is None
        assert transport.calls == []

    def test_slack_ok_false_returns_none(self, monkeypatch):
        install(monkeypatch, [{"ok": False, "error": "channel_not_found"}])
        assert sn.api_call("tok", "chat.postMessage", {}) is None

    def test_network_exception_fails_open(self, monkeypatch):
        install(monkeypatch, [OSError("network down")])
        assert sn.api_call("tok", "chat.postMessage", {}) is None

    def test_429_with_ok_false_error_ratelimited_retries_then_succeeds(self, monkeypatch):
        transport = install(monkeypatch, [
            {"ok": False, "error": "ratelimited"},
            {"ok": True, "ts": "1.1"},
        ])
        result = sn.api_call("tok", "chat.postMessage", {}, max_retries=3)
        assert result == {"ok": True, "ts": "1.1"}
        assert len(transport.calls) == 2

    def test_429_http_error_retries_then_succeeds(self, monkeypatch):
        transport = install(monkeypatch, [
            http_error(429, retry_after="1"),
            {"ok": True, "ts": "2.2"},
        ])
        result = sn.api_call("tok", "chat.postMessage", {}, max_retries=3)
        assert result == {"ok": True, "ts": "2.2"}
        assert len(transport.calls) == 2

    def test_429_exhausts_retries_and_fails_open(self, monkeypatch):
        transport = install(monkeypatch, [
            http_error(429, retry_after="0"),
            http_error(429, retry_after="0"),
        ])
        result = sn.api_call("tok", "chat.postMessage", {}, max_retries=1)
        assert result is None
        assert len(transport.calls) == 2

    def test_non_429_http_error_fails_open_without_retry(self, monkeypatch):
        transport = install(monkeypatch, [http_error(500)])
        result = sn.api_call("tok", "chat.postMessage", {}, max_retries=3)
        assert result is None
        assert len(transport.calls) == 1

    def test_never_raises_on_malformed_json_response(self, monkeypatch):
        class BadJsonResponse(FakeResponse):
            def read(self):
                return b"not json"
        monkeypatch.setattr(sn.urllib.request, "urlopen", lambda req, timeout=None: BadJsonResponse({}))
        assert sn.api_call("tok", "chat.postMessage", {}) is None


# ----------------------------------------------------------- post/update/react

class TestMessagePrimitives:
    def test_post_message_no_channel_is_noop(self):
        assert sn.post_message("tok", "", "hi") is None

    def test_post_message_includes_blocks_and_thread_ts(self, monkeypatch):
        transport = install(monkeypatch, [{"ok": True, "ts": "9.9"}])
        blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": "x"}}]
        sn.post_message("tok", "C1", "hi", blocks=blocks, thread_ts="1.0")
        body = last_body(transport)
        assert body["blocks"] == blocks
        assert body["thread_ts"] == "1.0"

    def test_update_message_requires_channel_and_ts(self):
        assert sn.update_message("tok", "", "1.0", "x") is None
        assert sn.update_message("tok", "C1", "", "x") is None

    def test_update_message_hits_chat_update(self, monkeypatch):
        transport = install(monkeypatch, [{"ok": True}])
        sn.update_message("tok", "C1", "5.5", "updated text")
        assert transport.calls[0].full_url == "https://slack.com/api/chat.update"
        body = last_body(transport)
        assert body == {"channel": "C1", "ts": "5.5", "text": "updated text"}

    def test_add_reaction_requires_channel_and_ts(self):
        assert sn.add_reaction("tok", "", "1.0") is False
        assert sn.add_reaction("tok", "C1", "") is False

    def test_add_reaction_hits_reactions_add(self, monkeypatch):
        transport = install(monkeypatch, [{"ok": True}])
        ok = sn.add_reaction("tok", "C1", "5.5")
        assert ok is True
        assert transport.calls[0].full_url == "https://slack.com/api/reactions.add"
        body = last_body(transport)
        assert body == {"channel": "C1", "timestamp": "5.5", "name": "white_check_mark"}


# --------------------------------------------------------------- thread store

class TestThreadStore:
    def test_load_threads_missing_file(self, tmp_path):
        assert sn.load_threads(tmp_path) == {}

    def test_load_threads_malformed_json(self, tmp_path):
        p = sn._threads_path(tmp_path)
        p.parent.mkdir(parents=True)
        p.write_text("{broken", encoding="utf-8")
        assert sn.load_threads(tmp_path) == {}

    def test_load_threads_non_dict_json(self, tmp_path):
        p = sn._threads_path(tmp_path)
        p.parent.mkdir(parents=True)
        p.write_text("[1,2,3]", encoding="utf-8")
        assert sn.load_threads(tmp_path) == {}

    def test_save_then_load_round_trip(self, tmp_path):
        sn.save_threads(tmp_path, {"TASK-001": {"channel": "C1", "ts": "1.0"}})
        assert sn.load_threads(tmp_path) == {"TASK-001": {"channel": "C1", "ts": "1.0"}}

    def test_record_and_get_thread(self, tmp_path):
        sn.record_thread(tmp_path, "TASK-002", "C2", "2.0")
        assert sn.get_thread(tmp_path, "TASK-002") == {"channel": "C2", "ts": "2.0"}

    def test_get_thread_unknown_task_is_none(self, tmp_path):
        assert sn.get_thread(tmp_path, "TASK-999") is None

    def test_save_threads_is_atomic_no_tmp_left_behind(self, tmp_path):
        sn.save_threads(tmp_path, {"TASK-001": {"channel": "C1", "ts": "1.0"}})
        assert not sn._threads_path(tmp_path).with_suffix(".json.tmp").exists()
        assert sn._threads_path(tmp_path).exists()


# ------------------------------------------------------- post_or_thread / update

class TestThreadLifecycle:
    def test_first_post_creates_thread(self, tmp_path, monkeypatch):
        transport = install(monkeypatch, [{"ok": True, "ts": "100.0"}])
        result = sn.post_or_thread("tok", tmp_path, "C1", "TASK-010", "hello")
        assert result["ts"] == "100.0"
        assert sn.get_thread(tmp_path, "TASK-010") == {"channel": "C1", "ts": "100.0"}
        assert "thread_ts" not in last_body(transport)

    def test_second_post_replies_in_thread(self, tmp_path, monkeypatch):
        sn.record_thread(tmp_path, "TASK-010", "C1", "100.0")
        transport = install(monkeypatch, [{"ok": True, "ts": "101.0"}])
        sn.post_or_thread("tok", tmp_path, "C1", "TASK-010", "follow-up")
        body = last_body(transport)
        assert body["thread_ts"] == "100.0"
        assert body["channel"] == "C1"
        # anchor unchanged by a reply
        assert sn.get_thread(tmp_path, "TASK-010") == {"channel": "C1", "ts": "100.0"}

    def test_post_or_thread_without_task_id_never_records(self, tmp_path, monkeypatch):
        install(monkeypatch, [{"ok": True, "ts": "1.0"}])
        sn.post_or_thread("tok", tmp_path, "C1", None, "no task here")
        assert sn.load_threads(tmp_path) == {}

    def test_post_or_thread_failure_returns_none(self, tmp_path, monkeypatch):
        install(monkeypatch, [OSError("down")])
        result = sn.post_or_thread("tok", tmp_path, "C1", "TASK-010", "hello")
        assert result is None
        assert sn.get_thread(tmp_path, "TASK-010") is None

    def test_update_decided_updates_the_anchor(self, tmp_path, monkeypatch):
        sn.record_thread(tmp_path, "TASK-010", "C1", "100.0")
        transport = install(monkeypatch, [{"ok": True}])
        ok = sn.update_decided("tok", tmp_path, "TASK-010", "resolved!")
        assert ok is True
        assert transport.calls[0].full_url == "https://slack.com/api/chat.update"
        body = last_body(transport)
        assert body == {"channel": "C1", "ts": "100.0", "text": "resolved!"}

    def test_update_decided_with_no_anchor_is_noop(self, tmp_path, monkeypatch):
        transport = install(monkeypatch, [])
        ok = sn.update_decided("tok", tmp_path, "TASK-999", "resolved!")
        assert ok is False
        assert transport.calls == []

    def test_mark_task_done_adds_reaction_to_anchor(self, tmp_path, monkeypatch):
        sn.record_thread(tmp_path, "TASK-010", "C1", "100.0")
        transport = install(monkeypatch, [{"ok": True}])
        ok = sn.mark_task_done("tok", tmp_path, "TASK-010")
        assert ok is True
        body = last_body(transport)
        assert body == {"channel": "C1", "timestamp": "100.0", "name": "white_check_mark"}

    def test_mark_task_done_with_no_anchor_is_noop(self, tmp_path, monkeypatch):
        transport = install(monkeypatch, [])
        assert sn.mark_task_done("tok", tmp_path, "TASK-999") is False
        assert transport.calls == []

    def test_full_lifecycle_post_reply_update_reaction(self, tmp_path, monkeypatch):
        # post -> thread reply -> update -> reaction, exercised end to end.
        t1 = install(monkeypatch, [{"ok": True, "ts": "1.0"}])
        sn.post_or_thread("tok", tmp_path, "C1", "TASK-777", "blocked!")
        t2 = install(monkeypatch, [{"ok": True, "ts": "1.1"}])
        sn.post_or_thread("tok", tmp_path, "C1", "TASK-777", "still blocked, ping")
        assert last_body(t2)["thread_ts"] == "1.0"
        t3 = install(monkeypatch, [{"ok": True}])
        sn.update_decided("tok", tmp_path, "TASK-777", "answered by Alister")
        assert last_body(t3)["ts"] == "1.0"
        t4 = install(monkeypatch, [{"ok": True}])
        sn.mark_task_done("tok", tmp_path, "TASK-777")
        assert last_body(t4)["timestamp"] == "1.0"


# ------------------------------------------------------------ Block Kit designs

class TestBlockKitDesigns:
    def test_blocked_design_has_reason_and_dossier_tail(self):
        text, blocks = sn.build_blocked_blocks(
            "ORB-JUN-26", "TASK-117", "Candle Vault", "GB (Grok Build)",
            23, "SPEC_AMBIGUITY: no schema file exists", "Attempted to locate schema...",
        )
        rendered = json.dumps(blocks)
        assert "TASK-117" in rendered
        assert "SPEC_AMBIGUITY" in rendered
        assert "Attempted to locate schema" in rendered
        assert "blocked 23 min" in rendered

    def test_blocked_design_has_three_buttons(self):
        _, blocks = sn.build_blocked_blocks("P", "TASK-1", "T", "A", 1, "reason")
        actions = [b for b in blocks if b["type"] == "actions"][0]
        action_ids = {el["action_id"] for el in actions["elements"]}
        assert action_ids == {"slack_approve", "slack_rework", "slack_answer"}

    def test_blocked_design_without_dossier_tail_omits_section(self):
        _, blocks = sn.build_blocked_blocks("P", "TASK-1", "T", "A", 1, "reason")
        rendered = json.dumps(blocks)
        assert "Last dossier entry" not in rendered

    def test_needs_review_design_has_test_counts_and_rework(self):
        text, blocks = sn.build_needs_review_blocks(
            "ORB-JUN-26", "TASK-114", "Server sweep", "CX (Codex AI)", 4, 260, 0,
        )
        rendered = json.dumps(blocks)
        assert "260 passed" in rendered
        assert "Rework count: 0" in rendered
        assert "(first pass)" in rendered

    def test_needs_review_design_rework_count_nonzero_no_first_pass_tag(self):
        _, blocks = sn.build_needs_review_blocks("P", "TASK-1", "T", "A", 1, 10, 2)
        rendered = json.dumps(blocks)
        assert "(first pass)" not in rendered
        assert "Rework count: 2" in rendered

    def test_needs_review_design_has_three_buttons(self):
        _, blocks = sn.build_needs_review_blocks("P", "TASK-1", "T", "A", 1, 10, 0)
        actions = [b for b in blocks if b["type"] == "actions"][0]
        action_ids = {el["action_id"] for el in actions["elements"]}
        assert action_ids == {"slack_open_tower", "slack_approve", "slack_rework"}

    def test_stop_the_line_design_lists_violations(self):
        text, blocks = sn.build_stop_the_line_blocks(
            "ORB-JUN-26", ["TASK-117: Owned_Paths overlaps TASK-119", "Missing Required_Artifacts"],
        )
        rendered = json.dumps(blocks)
        assert "TASK-117: Owned_Paths overlaps TASK-119" in rendered
        assert "Missing Required_Artifacts" in rendered
        assert "2 violation(s)" in rendered

    def test_stop_the_line_design_has_resume_button(self):
        _, blocks = sn.build_stop_the_line_blocks("P", ["v1"])
        actions = [b for b in blocks if b["type"] == "actions"][0]
        assert actions["elements"][0]["action_id"] == "slack_resume"

    def test_wave_complete_design_has_stats_and_instincts(self):
        text, blocks = sn.build_wave_complete_blocks(
            "ORB-JUN-26", 16, 16, "3h 42m", prev_wave_duration="4h 15m",
            builder_stats=["GB: 6 tasks  •  83% first-pass approval"],
            instincts_drafted=3, usage_summary="Claude usage: 5h window 71% · 7d window 44%",
        )
        rendered = json.dumps(blocks)
        assert "16/16 tasks done" in rendered
        assert "4h 15m" in rendered
        assert "83% first-pass approval" in rendered
        assert "Instincts drafted: 3" in rendered
        assert "Claude usage" in rendered

    def test_wave_complete_design_has_three_buttons(self):
        _, blocks = sn.build_wave_complete_blocks("P", 1, 1, "1m")
        actions = [b for b in blocks if b["type"] == "actions"][0]
        action_ids = {el["action_id"] for el in actions["elements"]}
        assert action_ids == {"slack_view_plan", "slack_open_tower_wave", "slack_full_stats"}


# ------------------------------------------------------------- high-level senders

class TestHighLevelSenders:
    CFG = {"ops_channel": "COPS", "project_channel": "CPROJ"}

    def test_notify_blocked_posts_to_project_channel(self, tmp_path, monkeypatch):
        transport = install(monkeypatch, [{"ok": True, "ts": "1.0"}])
        ok = sn.notify_blocked("tok", tmp_path, self.CFG, "P", "TASK-1", "T", "A", 1, "reason")
        assert ok is True
        assert last_body(transport)["channel"] == "CPROJ"

    def test_notify_stop_the_line_posts_to_ops_channel(self, monkeypatch):
        transport = install(monkeypatch, [{"ok": True, "ts": "1.0"}])
        ok = sn.notify_stop_the_line("tok", self.CFG, "P", ["v"])
        assert ok is True
        assert last_body(transport)["channel"] == "COPS"

    def test_notify_wave_complete_posts_to_both_channels(self, monkeypatch):
        transport = install(monkeypatch, [{"ok": True, "ts": "1.0"}, {"ok": True, "ts": "1.1"}])
        ok = sn.notify_wave_complete("tok", self.CFG, "P", tasks_done=1, tasks_total=1, wave_duration="1m")
        assert ok is True
        channels = {json.loads(c.data.decode())["channel"] for c in transport.calls}
        assert channels == {"COPS", "CPROJ"}

    def test_notify_needs_review_ok_false_when_all_channels_fail(self, tmp_path, monkeypatch):
        install(monkeypatch, [OSError("down")])
        ok = sn.notify_needs_review("tok", tmp_path, self.CFG, "P", "TASK-1", "T", "A", 1, 1, 0)
        assert ok is False


# -------------------------------------------------------------------- send_simple

class TestSendSimple:
    CFG = {"ops_channel": "COPS", "project_channel": "CPROJ"}

    def test_p0_routes_to_ops(self, monkeypatch):
        transport = install(monkeypatch, [{"ok": True, "ts": "1.0"}])
        ok = sn.send_simple(Path("."), self.CFG, "tok", "P0", "digest text")
        assert ok is True
        assert last_body(transport)["channel"] == "COPS"

    def test_p2_with_task_id_threads(self, tmp_path, monkeypatch):
        transport = install(monkeypatch, [{"ok": True, "ts": "1.0"}])
        sn.send_simple(tmp_path, self.CFG, "tok", "P2", "TASK-042 blocked: SPEC_AMBIGUITY")
        assert last_body(transport)["channel"] == "CPROJ"
        assert sn.get_thread(tmp_path, "TASK-042") == {"channel": "CPROJ", "ts": "1.0"}

        transport2 = install(monkeypatch, [{"ok": True, "ts": "1.1"}])
        sn.send_simple(tmp_path, self.CFG, "tok", "P2", "TASK-042 still blocked")
        assert last_body(transport2)["thread_ts"] == "1.0"

    def test_p2_without_task_id_posts_plain(self, tmp_path, monkeypatch):
        transport = install(monkeypatch, [{"ok": True, "ts": "1.0"}])
        sn.send_simple(tmp_path, self.CFG, "tok", "P2", "no task id here")
        assert "thread_ts" not in last_body(transport)

    def test_no_configured_channels_is_noop(self, tmp_path, monkeypatch):
        transport = install(monkeypatch, [])
        ok = sn.send_simple(tmp_path, {}, "tok", "P1", "msg")
        assert ok is False
        assert transport.calls == []

    def test_includes_badge_and_message(self, monkeypatch):
        transport = install(monkeypatch, [{"ok": True, "ts": "1.0"}])
        sn.send_simple(Path("."), self.CFG, "tok", "P1", "the message")
        body = last_body(transport)
        assert "🔴 STOP-THE-LINE" in body["text"]
        assert "the message" in body["text"]


# --------------------------------------------------------------------------- CLI

class TestTestSubcommand:
    def test_no_channels_configured(self, tmp_path):
        assert sn.test_channels(tmp_path, {}, "tok") == 1

    def test_no_token(self):
        cfg = {"ops_channel": "COPS"}
        assert sn.test_channels(Path("."), cfg, "") == 1

    def test_all_channels_delivered(self, monkeypatch):
        cfg = {"ops_channel": "COPS", "project_channel": "CPROJ"}
        install(monkeypatch, [{"ok": True, "ts": "1.0"}, {"ok": True, "ts": "1.1"}])
        assert sn.test_channels(Path("."), cfg, "tok") == 0

    def test_partial_failure_returns_1(self, monkeypatch):
        cfg = {"ops_channel": "COPS", "project_channel": "CPROJ"}
        install(monkeypatch, [{"ok": True, "ts": "1.0"}, OSError("down")])
        assert sn.test_channels(Path("."), cfg, "tok") == 1

    def test_main_test_flag_wires_through(self, tmp_path, monkeypatch):
        (tmp_path / "autopilot.json").write_text(
            json.dumps({"slack": {"ops_channel": "COPS"}}), encoding="utf-8",
        )
        monkeypatch.setenv("DEVTEAM_SLACK_TOKEN", "tok")
        install(monkeypatch, [{"ok": True, "ts": "1.0"}])
        rc = sn.main(["--test", "--repo", str(tmp_path)])
        assert rc == 0

    def test_main_without_flags_exits_1_never_2(self):
        assert sn.main([]) == 1
