"""Tests for scripts/notify.py's Wave A-remainder amendment: P2 escalations
get an actionable "Reply: /answer TASK-NNN <your decision>" line appended.

Also covers the P1b-1 "slack" channel registration (TASK-015,
specs/DEVDEPARTMENT_SLACK_SPEC.md §5, §9): lazily imported, degrades cleanly
when unconfigured, never raises into the caller."""
import sys
from pathlib import Path
import re

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import notify  # noqa: E402
from notify import CHANNELS, append_reply_hint, send_slack  # noqa: E402
import board_publisher  # noqa: E402
import maintenance  # noqa: E402
import tower_sync  # noqa: E402


class TestAppendReplyHint:
    def test_p2_gets_reply_line(self):
        msg = "TASK-016 blocked: SPEC_AMBIGUITY — human answer needed"
        out = append_reply_hint("P2", msg)
        assert out.startswith(msg)
        assert "Reply: /answer TASK-016 <your decision>" in out

    def test_p1_untouched(self):
        msg = "TASK-016 reached max_rework"
        assert append_reply_hint("P1", msg) == msg

    def test_p0_untouched(self):
        msg = "WAVE COMPLETE — all 5 tasks done."
        assert append_reply_hint("P0", msg) == msg

    def test_p2_without_task_id_untouched(self):
        # Defensive: not every conceivable P2 message references a task.
        msg = "Something went wrong, no task ID here"
        assert append_reply_hint("P2", msg) == msg

    def test_p2_uses_first_task_id_found(self):
        msg = "TASK-005 and TASK-006 both blocked: OWNERSHIP_CONFLICT"
        out = append_reply_hint("P2", msg)
        assert "Reply: /answer TASK-005 <your decision>" in out
        assert "TASK-006" not in out.split("Reply:")[1]

    def test_reply_line_is_appended_not_prepended(self):
        msg = "TASK-009 blocked: repeated OWNERSHIP_CONFLICT"
        out = append_reply_hint("P2", msg)
        lines = out.splitlines()
        assert lines[0] == msg
        assert lines[1].startswith("Reply:")


class TestFileChannelLogGrammar:
    def test_send_file_writes_parseable_alert_kind_with_utc_timestamp(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        notify.send_file("P1", "stop the line")
        [line] = (tmp_path / "AUTOPILOT_LOG.md").read_text(encoding="utf-8").splitlines()
        assert re.match(r"^- \[\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\] [A-Z][A-Z0-9_]*: ", line)
        event = tower_sync._parse_log_line(line)
        assert event is not None
        assert event["kind"] == "ALERT_P1"
        assert event["text"] == "stop the line"

    def test_alert_lines_do_not_disturb_maintenance_summary_or_log_rotation(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "AUTOPILOT_LOG.md").write_text(
            "- [2026-08-28T16:00:00Z] MAINTENANCE: Self-audit: PASS\n",
            encoding="utf-8",
        )
        notify.send_file("P0", "digest")
        assert board_publisher.read_maintenance_summary(tmp_path)["status"] == "Self-audit: PASS"

        # Rotation is content-agnostic: an alert at the end of an oversized
        # log is preserved in its archive and the live log is reset.
        (tmp_path / "AUTOPILOT_LOG.md").write_text("x" * 1_000_001, encoding="utf-8")
        notify.send_file("P2", "TASK-021 blocked")
        result = maintenance._step_hygiene(tmp_path)
        archive = next(tmp_path.glob("AUTOPILOT_LOG.*.md"))
        assert result.passed is True
        assert "ALERT_P2: TASK-021 blocked" in archive.read_text(encoding="utf-8")
        assert (tmp_path / "AUTOPILOT_LOG.md").read_text(encoding="utf-8") == ""


# ---------------------------------------------------------- slack channel (P1b-1)

class TestSlackChannelRegistration:
    def test_slack_registered_in_channels_dict(self):
        assert "slack" in CHANNELS
        assert CHANNELS["slack"] is send_slack

    def test_existing_channels_untouched(self):
        assert set(CHANNELS) == {"console", "file", "telegram", "slack"}


class TestSendSlack:
    def test_no_token_warns_and_returns_none(self, monkeypatch, capsys):
        monkeypatch.delenv("DEVTEAM_SLACK_TOKEN", raising=False)
        result = send_slack("P1", "something happened")
        assert result is None
        err = capsys.readouterr().err
        assert "DEVTEAM_SLACK_TOKEN is not set" in err

    def test_no_channels_configured_warns_and_returns_none(self, monkeypatch, capsys):
        monkeypatch.setenv("DEVTEAM_SLACK_TOKEN", "xoxb-test")
        # notify.py resolves the repo root itself; point slack_notify's config
        # lookup at an empty config directly rather than fixturing a real tree.
        import slack_notify
        monkeypatch.setattr(slack_notify, "load_config", lambda repo: {})
        result = send_slack("P2", "TASK-001 blocked: SPEC_AMBIGUITY")
        assert result is None
        err = capsys.readouterr().err
        assert "no channels configured" in err

    def test_degrades_cleanly_when_slack_notify_import_fails(self, monkeypatch, capsys):
        import builtins
        monkeypatch.setenv("DEVTEAM_SLACK_TOKEN", "xoxb-test")
        real_import = builtins.__import__

        def boom_import(name, *a, **kw):
            if name == "slack_notify":
                raise ImportError("simulated missing module")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", boom_import)
        result = send_slack("P1", "stop the line")
        assert result is None
        err = capsys.readouterr().err
        assert "slack_notify.py is unavailable" in err

    def test_send_slack_delegates_to_slack_notify_send_simple(self, monkeypatch):
        monkeypatch.setenv("DEVTEAM_SLACK_TOKEN", "xoxb-test")
        import slack_notify
        monkeypatch.setattr(slack_notify, "load_config", lambda repo: {"ops_channel": "COPS"})
        calls = []
        monkeypatch.setattr(
            slack_notify, "send_simple",
            lambda repo, cfg, token, priority, message: calls.append((priority, message, token)) or True,
        )
        send_slack("P1", "line down")
        assert calls == [("P1", "line down", "xoxb-test")]

    def test_send_slack_never_raises_on_send_failure_and_falls_back_to_file(self, monkeypatch, tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEVTEAM_SLACK_TOKEN", "xoxb-test")
        import slack_notify
        monkeypatch.setattr(slack_notify, "load_config", lambda repo: {"ops_channel": "COPS"})

        def boom(*a, **kw):
            raise RuntimeError("network exploded")

        monkeypatch.setattr(slack_notify, "send_simple", boom)
        # Must not raise, and §5's "fail-open to the file channel" must fire.
        send_slack("P0", "digest")
        err = capsys.readouterr().err
        assert "falling back to file channel" in err
        log = (tmp_path / "AUTOPILOT_LOG.md").read_text(encoding="utf-8")
        assert "digest" in log

    def test_send_slack_delivery_returns_false_falls_back_to_file(self, monkeypatch, tmp_path, capsys):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEVTEAM_SLACK_TOKEN", "xoxb-test")
        import slack_notify
        monkeypatch.setattr(slack_notify, "load_config", lambda repo: {"ops_channel": "COPS"})
        monkeypatch.setattr(slack_notify, "send_simple", lambda *a, **kw: False)
        send_slack("P1", "unreachable case")
        err = capsys.readouterr().err
        assert "falling back to file channel" in err
        log = (tmp_path / "AUTOPILOT_LOG.md").read_text(encoding="utf-8")
        assert "unreachable case" in log

    def test_send_slack_successful_delivery_does_not_touch_file(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("DEVTEAM_SLACK_TOKEN", "xoxb-test")
        import slack_notify
        monkeypatch.setattr(slack_notify, "load_config", lambda repo: {"ops_channel": "COPS"})
        monkeypatch.setattr(slack_notify, "send_simple", lambda *a, **kw: True)
        send_slack("P1", "delivered fine")
        assert not (tmp_path / "AUTOPILOT_LOG.md").exists()

    def test_main_routes_priority_message_through_slack_channel(self, monkeypatch, capsys):
        monkeypatch.delenv("DEVTEAM_SLACK_TOKEN", raising=False)
        rc = notify.main(["--priority", "P2", "--message", "TASK-777 blocked", "--channels", "slack"])
        assert rc == 0
        err = capsys.readouterr().err
        assert "DEVTEAM_SLACK_TOKEN is not set" in err
