"""Shared command-validation module (scripts/commands.py) — TASK-013.

Transport-neutral: Slack/Tower/inbox import validate(); Telegram keeps its
help-fallback parse_command on the tg_commands shim. Unknown names are
rejected, never guessed (TOWER §1 P2).
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import commands as cmd  # noqa: E402
import tg_commands as tgc  # noqa: E402


VOCAB = (
    "approve", "rework", "answer", "stop", "resume", "wave",
    "dispatch", "status", "usage", "mute", "digest",
)


def _ok(command, args=None):
    ok, payload = cmd.validate(command, args)
    assert ok is True, payload
    assert isinstance(payload, dict)
    return payload


def _fail(command, args=None):
    ok, reason = cmd.validate(command, args)
    assert ok is False, reason
    assert isinstance(reason, str)
    return reason


class TestVocabulary:
    def test_vocabulary_matches_spec_list(self):
        assert cmd.VOCABULARY == frozenset(VOCAB)

    def test_telegram_commands_keep_historical_slash_set(self):
        # Zero Telegram behaviour change: /board stays, /dispatch is not added.
        assert "/board" in cmd.COMMANDS
        assert "/dispatch" not in cmd.COMMANDS
        assert cmd.COMMANDS == tgc.COMMANDS
        for name in VOCAB:
            if name == "dispatch":
                continue
            assert f"/{name}" in cmd.COMMANDS

    def test_board_is_not_in_shared_vocabulary(self):
        assert "board" not in cmd.VOCABULARY
        reason = _fail("board")
        assert "never guessed" in reason
        reason = _fail("/board")
        assert "never guessed" in reason


class TestValidateAccepts:
    @pytest.mark.parametrize("name", ["stop", "resume", "wave", "status", "usage", "digest"])
    @pytest.mark.parametrize("form", ["bare", "slash", "at"])
    def test_no_arg_commands(self, name, form):
        token = {"bare": name, "slash": f"/{name}", "at": f"/{name}@DevBot"}[form]
        payload = _ok(token, "")
        assert payload == {"command": name, "args": {}}
        payload = _ok(token.upper() if form != "at" else token, None)
        assert payload["command"] == name

    def test_approve_task_string_and_dict(self):
        assert _ok("approve", "TASK-016")["args"] == {"task_id": "TASK-016"}
        assert _ok("/approve", {"task_id": "TASK-114"})["args"] == {"task_id": "TASK-114"}
        assert _ok("approve", "AMEND-003")["args"] == {"amend_id": "AMEND-003"}
        assert _ok("approve", {"task_id": "AMEND-003"})["args"] == {"amend_id": "AMEND-003"}

    def test_answer_and_rework_require_text(self):
        assert _ok("answer", "TASK-016 use exponential backoff")["args"] == {
            "task_id": "TASK-016", "text": "use exponential backoff",
        }
        assert _ok("/rework", {"task_id": "TASK-005", "text": "territory violation"})["args"] == {
            "task_id": "TASK-005", "text": "territory violation",
        }
        assert _ok("rework", "AMEND-002 not a constitutional issue")["args"] == {
            "amend_id": "AMEND-002", "text": "not a constitutional issue",
        }

    def test_mute_duration(self):
        assert _ok("mute", "2h")["args"] == {"duration_s": 7200}
        assert _ok("/mute", "30m")["args"] == {"duration_s": 1800}
        assert _ok("mute", {"text": "1h"})["args"] == {"duration_s": 3600}
        assert _ok("mute", {"duration": "90m"})["args"] == {"duration_s": 5400}
        assert _ok("mute", {"duration_s": 60})["args"] == {"duration_s": 60}

    def test_dispatch_empty_or_task(self):
        assert _ok("dispatch", "") == {"command": "dispatch", "args": {}}
        assert _ok("/dispatch", None)["args"] == {}
        assert _ok("dispatch", "TASK-013")["args"] == {"task_id": "TASK-013"}
        assert _ok("dispatch", {"task_id": "TASK-013", "text": "now"})["args"] == {
            "task_id": "TASK-013", "text": "now",
        }


class TestUnknownNeverGuessed:
    @pytest.mark.parametrize("token", [
        "nonexistent", "/nonexistent", "/ans", "/", "aproved", "help",
        "hey what's up", "", None, 12, "/dispatchx",
    ])
    def test_unknown_tokens_rejected(self, token):
        reason = _fail(token)
        assert "never guessed" in reason
        assert "unknown command" in reason

    def test_typo_is_not_rewritten_to_nearest_command(self):
        reason = _fail("aproved")
        assert "approve" not in reason.split("—")[0]


class TestMalformedRejected:
    @pytest.mark.parametrize("name", ["stop", "resume", "wave", "status", "usage", "digest"])
    def test_no_arg_rejects_extra_text(self, name):
        reason = _fail(name, "please")
        assert "malformed" in reason

    def test_approve_rejects_extra_text_and_missing(self):
        assert "malformed" in _fail("approve", "TASK-016 extra")
        assert "malformed" in _fail("approve", "")
        assert "malformed" in _fail("approve", {"task_id": "TASK-016", "text": "nope"})
        assert "malformed" in _fail("approve", {"task_id": "not-a-task"})

    def test_answer_rejects_missing_text_or_id(self):
        assert "malformed" in _fail("answer", "TASK-016")
        assert "malformed" in _fail("answer", "just some text")
        assert "malformed" in _fail("answer", {"task_id": "TASK-016"})
        assert "malformed" in _fail("answer", {"task_id": "AMEND-001", "text": "no"})

    def test_rework_rejects_empty_text(self):
        assert "malformed" in _fail("rework", "TASK-005")
        assert "malformed" in _fail("rework", {"task_id": "TASK-005", "text": "   "})

    def test_mute_rejects_garbage(self):
        for raw in ("", "2", "h2", "-2h", "0h", "two hours", "2d"):
            assert "malformed" in _fail("mute", raw)

    def test_dispatch_does_not_guess_a_unit_name(self):
        reason = _fail("dispatch", "GB")
        assert "malformed" in reason
        ok, payload = cmd.validate("dispatch", "GB")
        assert ok is False
        assert payload != {"command": "dispatch", "args": {"unit": "GB"}}

    def test_unknown_dict_keys_rejected_never_guessed(self):
        reason = _fail("approve", {"task_id": "TASK-001", "unit": "GB"})
        assert "never guessed" in reason
        assert "malformed" in reason

    def test_args_wrong_type(self):
        reason = _fail("stop", ["nope"])
        assert "malformed" in reason


class TestParsersReexported:
    def test_tg_commands_reexports_parser_functions(self):
        for name in (
            "parse_task_and_text", "parse_answer_args", "parse_rework_args",
            "parse_approve_args", "parse_mute_args", "parse_amend_args",
            "parse_amend_and_text", "validate", "canonical_name", "VOCABULARY",
            "COMMANDS",
        ):
            assert getattr(tgc, name) is getattr(cmd, name)

    def test_parse_helpers_keep_prior_signatures(self):
        assert cmd.parse_approve_args("TASK-016") == "TASK-016"
        assert cmd.parse_approve_args("TASK-016 extra") is None
        assert cmd.parse_answer_args("TASK-016 use exponential backoff") == (
            "TASK-016", "use exponential backoff")
        assert cmd.parse_mute_args("2h") == 7200
        assert cmd.parse_mute_args("0h") is None
        assert cmd.parse_amend_args("AMEND-009") == "AMEND-009"
        assert cmd.parse_amend_and_text("AMEND-009 because") == ("AMEND-009", "because")

    def test_git_helpers_and_renderers_stay_on_tg_commands(self):
        for name in (
            "git_pull", "git_commit_and_push", "git_commit_and_push_detailed",
            "render_status", "render_usage", "render_digest", "render_board_url",
            "apply_answer", "apply_rework", "is_allowed", "send_reply",
            "parse_command", "HELP_TEXT",
        ):
            assert hasattr(tgc, name)
            assert not hasattr(cmd, name), f"{name} leaked into commands.py"


class TestNoTelegramLeak:
    def test_commands_module_does_not_import_telegram(self):
        source = inspect.getsource(cmd)
        tree = ast.parse(source)
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module.split(".")[0])
        assert "tg_commands" not in imported
        assert "tg_listener" not in imported
        banned = [n for n in imported if "telegram" in n.lower() or n.startswith("tg_")]
        assert banned == []

    def test_commands_source_has_no_llm_calls(self):
        source = Path(cmd.__file__).read_text(encoding="utf-8")
        for needle in ("openai", "anthropic", "subprocess", "urllib", "http"):
            assert needle not in source.lower() or needle in ("http",)
        assert "urllib" not in source
        assert "subprocess" not in source
