"""Unit tests for Tower's two-phase, fail-open inbox consumer."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import inbox  # noqa: E402


def envelope(command="approve", args=None, command_id="cmd-1"):
    return {"id": command_id, "issued_at": "2026-08-26T07:00:00Z", "source": "tower",
            "actor": "alister", "command": command, "args": args if args is not None else {"task_id": "TASK-017"}}


def write(repo, name, value):
    path = repo / ".devteam" / "inbox" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def test_empty_or_missing_inbox_is_a_clean_noop(tmp_path):
    assert inbox.drain_inbox(tmp_path, {}) == []


def test_valid_command_is_normalized_by_the_shared_validator_and_survives_drain(tmp_path):
    path = write(tmp_path, "one.json", envelope("/approve"))
    commands = inbox.drain_inbox(tmp_path, {})
    assert commands == [{"id": "cmd-1", "issued_at": "2026-08-26T07:00:00Z", "source": "tower",
                         "actor": "alister", "command": "approve", "args": {"task_id": "TASK-017"},
                         "_inbox_path": str(path)}]
    assert path.exists(), "draining is not acknowledgement"


def test_ack_is_the_second_phase_and_a_repeated_id_is_rejected(tmp_path):
    path = write(tmp_path, "one.json", envelope())
    command = inbox.drain_inbox(tmp_path, {})[0]
    assert inbox.ack(tmp_path, command) is True
    assert not path.exists()
    duplicate = write(tmp_path, "duplicate.json", envelope(command_id="cmd-1"))
    assert inbox.drain_inbox(tmp_path, {}) == []
    rejected = tmp_path / ".devteam" / "inbox" / "rejected" / duplicate.name
    assert rejected.exists()
    assert "duplicate command id" in rejected.with_name(rejected.name + ".reason").read_text()


def test_two_files_with_the_same_id_reject_the_second(tmp_path):
    write(tmp_path, "a.json", envelope(command_id="same"))
    write(tmp_path, "b.json", envelope(command_id="same"))
    assert [item["id"] for item in inbox.drain_inbox(tmp_path, {})] == ["same"]
    assert (tmp_path / ".devteam" / "inbox" / "rejected" / "b.json").exists()


def test_bad_json_unknown_commands_and_bad_schema_are_rejected_with_reasons(tmp_path):
    bad_json = tmp_path / ".devteam" / "inbox" / "bad.json"
    bad_json.parent.mkdir(parents=True, exist_ok=True)
    bad_json.write_text("{bad", encoding="utf-8")
    write(tmp_path, "unknown.json", envelope("nonsense"))
    write(tmp_path, "schema.json", {"id": "bad"})
    assert inbox.drain_inbox(tmp_path, {}) == []
    rejected = tmp_path / ".devteam" / "inbox" / "rejected"
    assert {p.name for p in rejected.glob("*.json")} == {"bad.json", "unknown.json", "schema.json"}
    assert "unknown command" in (rejected / "unknown.json.reason").read_text()
    assert "missing" in (rejected / "schema.json.reason").read_text()


def test_unexpected_per_file_error_warns_and_does_not_raise(tmp_path, monkeypatch, capsys):
    write(tmp_path, "read-error.json", envelope())
    real_read = Path.read_text

    def boom(self, *args, **kwargs):
        if self.name == "read-error.json":
            raise OSError("disk trouble")
        return real_read(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", boom)
    assert inbox.drain_inbox(tmp_path, {}) == []
    assert "skipping read-error.json" in capsys.readouterr().err


def test_ack_refuses_a_path_outside_the_inbox(tmp_path, capsys):
    assert inbox.ack(tmp_path, {"id": "cmd", "_inbox_path": str(tmp_path / "elsewhere.json")}) is False
    assert "outside the inbox" in capsys.readouterr().err
