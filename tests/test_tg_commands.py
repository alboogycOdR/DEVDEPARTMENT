"""Tests for scripts/tg_commands.py — two-way Telegram command grammar and
PLAN.md micro-transaction editing (Wave A-remainder, completes Pillar 2)."""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import tg_commands as tgc  # noqa: E402


# ------------------------------------------------------------------ fixtures --
def make_plan(*task_blocks: str) -> str:
    fm = (
        "---\n"
        "plan_version: 4.1\n"
        "last_updated: 2026-07-18T00:00:00Z\n"
        "overall_status: in_progress\n"
        "---\n"
        "# Project Plan\n\n## Work Items\n"
    )
    return fm + "\n".join(task_blocks)


def task_block(
    tid="TASK-001", title="Some task", status="in_progress", assignee="GB",
    owned="lib/a/**", progress_notes="—", review_findings="—",
    blocked_reason="—", updated_by="GB", updated_at="2026-07-18T00:00:00Z",
    branch=None, started="2026-07-18T00:00:00Z", evidence="—",
):
    branch = branch or f"task/{tid}-{'gb' if assignee == 'GB' else 'cx'}"
    pn = "**Progress_Notes:** —" if progress_notes == "—" else (
        "**Progress_Notes:**\n" + "\n".join(f"- {ln}" for ln in progress_notes)
    )
    rf = "**Review_Findings:** —" if review_findings == "—" else (
        "**Review_Findings:**\n" + "\n".join(f"- {ln}" for ln in review_findings)
    )
    return f"""
### {tid}
**Title:** {title}
**Status:** {status}
**Assigned_To:** {assignee}
**Priority:** high
**Spec_References:** specs/x.md
**Owned_Paths:** {owned}
**Depends_On:** —
**Description:** some description of {tid}
**Acceptance_Criteria:**
- [ ] criterion
**Branch:** {branch}
**Started_At:** {started}
{pn}
**Artifacts:** —
**Test_Evidence:** {evidence}
{rf}
**Blocked_Reason:** {blocked_reason}
**Updated_By:** {updated_by}
**Updated_At:** {updated_at}
"""


TS = "2026-07-18T12:00:00Z"


# =============================================================== parse_command
class TestParseCommand:
    @pytest.mark.parametrize("cmd", sorted(tgc.COMMANDS))
    def test_all_ten_commands_parse(self, cmd):
        parsed_cmd, args = tgc.parse_command(f"{cmd} some args")
        assert parsed_cmd == cmd
        assert args == "some args"

    def test_bare_command_no_args(self):
        assert tgc.parse_command("/stop") == ("/stop", "")
        assert tgc.parse_command("/status") == ("/status", "")

    def test_botname_suffix_stripped(self):
        assert tgc.parse_command("/status@MyDevBot") == ("/status", "")
        assert tgc.parse_command("/answer@MyDevBot TASK-001 ok") == ("/answer", "TASK-001 ok")

    def test_case_insensitive(self):
        assert tgc.parse_command("/STATUS") == ("/status", "")
        assert tgc.parse_command("/Answer TASK-001 yes") == ("/answer", "TASK-001 yes")

    def test_unknown_command_falls_to_help(self):
        assert tgc.parse_command("/nonexistent foo bar") == ("help", "/nonexistent foo bar")

    def test_non_command_text_falls_to_help(self):
        assert tgc.parse_command("hey what's up") == ("help", "hey what's up")

    def test_empty_and_none_fall_to_help(self):
        assert tgc.parse_command("") == ("help", "")
        assert tgc.parse_command(None) == ("help", "")

    def test_malformed_partial_command_falls_to_help(self):
        # "/" alone, or garbage that starts with / but isn't in the grammar
        assert tgc.parse_command("/") == ("help", "/")
        assert tgc.parse_command("/ans") == ("help", "/ans")

    def test_whitespace_normalised(self):
        assert tgc.parse_command("   /status   ") == ("/status", "")


class TestParseArgs:
    def test_answer_args(self):
        assert tgc.parse_answer_args("TASK-016 use exponential backoff") == (
            "TASK-016", "use exponential backoff")

    def test_answer_args_multiline(self):
        parsed = tgc.parse_answer_args("TASK-016 line one\nline two")
        assert parsed == ("TASK-016", "line one\nline two")

    def test_answer_args_missing_text(self):
        assert tgc.parse_answer_args("TASK-016") is None

    def test_answer_args_missing_task_id(self):
        assert tgc.parse_answer_args("just some text") is None

    def test_rework_args(self):
        assert tgc.parse_rework_args("TASK-005 territory violation, redo") == (
            "TASK-005", "territory violation, redo")

    def test_approve_args_valid(self):
        assert tgc.parse_approve_args("TASK-016") == "TASK-016"
        assert tgc.parse_approve_args("  TASK-016  ") == "TASK-016"

    def test_approve_args_rejects_extra_text(self):
        assert tgc.parse_approve_args("TASK-016 extra") is None

    def test_approve_args_rejects_missing(self):
        assert tgc.parse_approve_args("") is None

    @pytest.mark.parametrize("raw,expected", [
        ("2h", 7200), ("30m", 1800), ("1h", 3600), ("90m", 5400),
        ("2H", 7200), ("  2h  ", 7200),
    ])
    def test_mute_duration_parses(self, raw, expected):
        assert tgc.parse_mute_args(raw) == expected

    @pytest.mark.parametrize("raw", ["", "2", "h2", "-2h", "0h", "two hours", "2d"])
    def test_mute_duration_rejects_garbage(self, raw):
        assert tgc.parse_mute_args(raw) is None


# ================================================================= allowlist
class TestAllowlist:
    def test_default_chat_allowed_when_no_list(self):
        assert tgc.is_allowed("12345", [], "12345") is True

    def test_unlisted_chat_rejected(self):
        assert tgc.is_allowed("99999", [], "12345") is False

    def test_multi_chat_allowlist(self):
        assert tgc.is_allowed("222", ["111", "222", "333"], "999") is True

    def test_owner_never_locked_out_even_if_absent_from_allowlist(self):
        # DEVTEAM_TG_CHAT is always additive so the owner can't accidentally
        # lock themselves out by editing chat_allowlist.
        assert tgc.is_allowed("999", ["111", "222"], "999") is True

    def test_chat_not_in_either_rejected(self):
        assert tgc.is_allowed("444", ["111", "222"], "999") is False

    def test_empty_chat_id_rejected(self):
        assert tgc.is_allowed("", [], "12345") is False
        assert tgc.is_allowed(None, [], "12345") is False


# ============================================================= /answer apply
class TestApplyAnswer:
    def test_answer_on_blocked_task_unblocks(self):
        plan = make_plan(task_block(
            tid="TASK-016", status="blocked", blocked_reason="SPEC_AMBIGUITY",
        ))
        result = tgc.apply_answer(plan, "TASK-016", "use exponential backoff", TS)
        assert result.changed
        assert "**Status:** pending" in result.text
        assert "**Blocked_Reason:** —" in result.text
        assert "[TG-DECISION] use exponential backoff" in result.text
        assert f"**Updated_At:** {TS}" in result.text
        assert "**Updated_By:** ORCH" in result.text

    def test_answer_on_non_blocked_task_appends_note_only_status_unchanged(self):
        plan = make_plan(task_block(tid="TASK-005", status="in_progress"))
        result = tgc.apply_answer(plan, "TASK-005", "go ahead with plan B", TS)
        assert result.changed
        assert "**Status:** in_progress" in result.text
        assert "[TG-DECISION] go ahead with plan B" in result.text

    def test_answer_appends_after_existing_progress_notes(self):
        plan = make_plan(task_block(
            tid="TASK-002", status="in_progress",
            progress_notes=["[2026-07-18T10:00:00Z] [GB] first note"],
        ))
        result = tgc.apply_answer(plan, "TASK-002", "second note via telegram", TS)
        text = result.text
        first_idx = text.index("first note")
        second_idx = text.index("[TG-DECISION] second note")
        assert first_idx < second_idx  # appended AFTER, not replacing

    def test_answer_unknown_task_no_edit(self):
        plan = make_plan(task_block(tid="TASK-001"))
        result = tgc.apply_answer(plan, "TASK-999", "irrelevant", TS)
        assert not result.changed
        assert result.text == plan  # byte-identical, no mutation attempted

    def test_answer_only_touches_target_task_block(self):
        other = task_block(tid="TASK-002", status="pending", assignee="CX")
        target = task_block(tid="TASK-001", status="blocked", blocked_reason="SPEC_AMBIGUITY")
        plan = make_plan(target, other)
        result = tgc.apply_answer(plan, "TASK-001", "decision text", TS)
        # TASK-002's block must be byte-identical before and after.
        assert other in result.text
        assert "TASK-002" in result.text
        # And TASK-001 should have changed.
        assert "**Status:** pending" in result.text

    def test_answer_empty_text_after_sanitisation_no_edit(self):
        plan = make_plan(task_block(tid="TASK-001"))
        result = tgc.apply_answer(plan, "TASK-001", "   \n\n  ", TS)
        assert not result.changed


# ============================================================= /rework apply
class TestApplyRework:
    def test_rework_needs_review_to_in_progress(self):
        plan = make_plan(task_block(
            tid="TASK-005", status="needs_review", evidence="pytest 5/5 pass",
        ))
        result = tgc.apply_rework(plan, "TASK-005", "territory violation, redo", TS)
        assert result.changed
        assert "**Status:** in_progress" in result.text
        assert "[TG-REWORK] territory violation, redo" in result.text

    def test_rework_only_touches_target_task(self):
        other = task_block(tid="TASK-003", status="pending", assignee="CX")
        target = task_block(tid="TASK-002", status="needs_review", evidence="ok")
        plan = make_plan(target, other)
        result = tgc.apply_rework(plan, "TASK-002", "fix the tests", TS)
        assert other in result.text

    def test_rework_unknown_task(self):
        plan = make_plan(task_block(tid="TASK-001"))
        result = tgc.apply_rework(plan, "TASK-999", "reason", TS)
        assert not result.changed
        assert result.text == plan


# =========================================================== injection safety
class TestFreeTextInjectionSafety:
    """Free text is the argument of /answer and /rework only, and must land as
    inert string data — never executed, never interpreted as a path/command,
    never able to inject a fake task header or field into PLAN.md."""

    @pytest.mark.parametrize("payload", [
        "$(rm -rf /)",
        "`rm -rf /`",
        "; rm -rf / #",
        "../../etc/passwd",
        "../../../secrets.env",
        "'; DROP TABLE tasks; --",
        "${DEVTEAM_TG_TOKEN}",
        "%0a%0d",
    ])
    def test_shell_and_path_metacharacters_are_inert(self, payload):
        plan = make_plan(task_block(tid="TASK-001", status="in_progress"))
        result = tgc.apply_answer(plan, "TASK-001", payload, TS)
        assert result.changed
        # The payload must appear verbatim as TEXT inside the bullet line —
        # never having been used to build a shell command or a filesystem
        # path anywhere in this process.
        assert payload in result.text
        # And it must still be a single well-formed PLAN.md: re-parseable,
        # same number of task headers as before.
        assert result.text.count("### TASK-") == plan.count("### TASK-")

    def test_embedded_task_header_cannot_forge_a_new_task_block(self):
        payload = "ok\n### TASK-999\n**Status:** done\n**Title:** forged"
        plan = make_plan(task_block(tid="TASK-001", status="in_progress"))
        result = tgc.apply_answer(plan, "TASK-001", payload, TS)
        # Newlines are collapsed to spaces before writing, so "### TASK-999"
        # can never land at the start of a line and be parsed as a real header.
        assert "\n### TASK-999" not in result.text
        header_lines = [ln for ln in result.text.splitlines() if ln.strip().startswith("### TASK-")]
        assert len(header_lines) == 1
        assert header_lines[0].strip() == "### TASK-001"

    def test_embedded_field_header_cannot_forge_a_new_field(self):
        payload = "ok\n**Status:** done\nmore text"
        plan = make_plan(task_block(tid="TASK-001", status="in_progress"))
        result = tgc.apply_answer(plan, "TASK-001", payload, TS)
        # The payload's "**Status:** done" must never land at the START of its
        # own line — only the one real Status field line may do that.
        status_header_lines = [ln for ln in result.text.splitlines() if ln.strip().startswith("**Status:**")]
        assert len(status_header_lines) == 1
        assert status_header_lines[0].strip() == "**Status:** in_progress"  # untouched, not "done"

    def test_very_long_payload_is_truncated(self):
        payload = "A" * 5000
        plan = make_plan(task_block(tid="TASK-001", status="in_progress"))
        result = tgc.apply_answer(plan, "TASK-001", payload, TS)
        assert result.changed
        assert "[truncated]" in result.text
        # never write more than MAX_FREE_TEXT_LEN + a small suffix
        note_line = [ln for ln in result.text.splitlines() if "[TG-DECISION]" in ln][0]
        assert len(note_line) < tgc.MAX_FREE_TEXT_LEN + 100

    def test_control_characters_stripped(self):
        payload = "hello\x00\x01\x02world"
        plan = make_plan(task_block(tid="TASK-001", status="in_progress"))
        result = tgc.apply_answer(plan, "TASK-001", payload, TS)
        note_line = [ln for ln in result.text.splitlines() if "[TG-DECISION]" in ln][0]
        assert "\x00" not in note_line and "\x01" not in note_line


# ============================================================== round-trip --
class TestRoundTripParseable:
    def test_answer_output_is_still_parseable_by_validate_plan(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from validate_plan import parse_tasks, Report
        plan = make_plan(task_block(
            tid="TASK-016", status="blocked", blocked_reason="SPEC_AMBIGUITY",
        ))
        result = tgc.apply_answer(plan, "TASK-016", "use exponential backoff", TS)
        tasks = parse_tasks(result.text, Report())
        assert len(tasks) == 1
        assert tasks[0].task_id == "TASK-016"
        assert tasks[0].get("Status") == "pending"

    def test_multi_task_plan_all_tasks_still_parse_after_edit(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
        from validate_plan import parse_tasks, Report
        plan = make_plan(
            task_block(tid="TASK-001", status="needs_review", evidence="ok"),
            task_block(tid="TASK-002", status="pending", assignee="CX"),
            task_block(tid="TASK-003", status="blocked", blocked_reason="TOOLING_FAILURE"),
        )
        result = tgc.apply_rework(plan, "TASK-001", "add more tests", TS)
        tasks = parse_tasks(result.text, Report())
        assert {t.task_id for t in tasks} == {"TASK-001", "TASK-002", "TASK-003"}
        by_id = {t.task_id: t for t in tasks}
        assert by_id["TASK-001"].get("Status") == "in_progress"
        assert by_id["TASK-002"].get("Status") == "pending"          # untouched
        assert by_id["TASK-003"].get("Status") == "blocked"          # untouched


# =================================================================== render
class TestRenderStatus:
    def test_render_status_basic(self):
        board = {
            "project": "orb-terminal",
            "burndown": {"done": 3, "total": 10, "pct": 30},
            "columns": {
                "blocked": [{"id": "TASK-009", "assignee": "GB", "blocked_reason": "SPEC_AMBIGUITY"}],
                "needs_review": [], "in_progress": [{"id": "TASK-001", "assignee": "CX"}],
                "claimed": [], "pending": [{"id": "TASK-003", "assignee": "GB"}],
                "done": [{"id": "x"}, {"id": "y"}, {"id": "z"}],
            },
            "autopilot": {"stop_file": False},
        }
        text = tgc.render_status(board)
        assert "orb-terminal" in text
        assert "3/10" in text
        assert "TASK-009" in text
        assert "TASK-001" in text
        assert "done: 3" in text

    def test_render_status_shows_stop_file(self):
        board = {"project": "p", "burndown": {}, "columns": {}, "autopilot": {"stop_file": True}}
        assert "STOP" in tgc.render_status(board)

    def test_render_status_caps_long_columns(self):
        items = [{"id": f"TASK-{i:03d}", "assignee": "GB"} for i in range(20)]
        board = {"project": "p", "burndown": {}, "columns": {"pending": items}, "autopilot": {}}
        text = tgc.render_status(board)
        assert "+12 more" in text


class TestRenderBoardUrl:
    def test_url_present(self):
        cfg = {"board": {"url": "https://boards.example.com/orb"}}
        assert tgc.render_board_url(cfg) == "https://boards.example.com/orb"

    def test_url_absent(self):
        cfg = {"board": {}}
        assert "No board URL" in tgc.render_board_url(cfg)

    def test_missing_board_key_entirely(self):
        assert "No board URL" in tgc.render_board_url({})


class TestRenderDigest:
    def test_digest_with_escalations(self):
        board = {
            "project": "orb-terminal",
            "burndown": {"done": 5, "total": 12, "pct": 42},
            "escalations_open": [{"task": "TASK-009", "question": "SPEC_AMBIGUITY"}],
            "team": {"GB": {"reviews": 10, "first_pass_rate": 0.8}, "CX": {"reviews": 0}},
        }
        text = tgc.render_digest(board)
        assert "5/12" in text
        assert "TASK-009" in text
        assert "GB: 10 reviews" in text

    def test_digest_no_escalations(self):
        board = {"project": "p", "burndown": {"done": 1, "total": 1, "pct": 100},
                  "escalations_open": [], "team": {}}
        assert "No open escalations" in tgc.render_digest(board)


class TestHelpText:
    def test_help_lists_all_commands(self):
        for cmd in tgc.COMMANDS:
            assert cmd in tgc.HELP_TEXT


# ============================================================ git plumbing --
class TestGitPlumbing:
    def _init_repo(self, tmp_path: Path) -> Path:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
        (tmp_path / "PLAN.md").write_text(make_plan(task_block()), encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)
        return tmp_path

    def test_git_pull_non_repo_fails_gracefully(self, tmp_path):
        # No .git here at all — must return False, never raise.
        assert tgc.git_pull(tmp_path) is False

    def test_git_commit_and_push_non_repo_fails_gracefully(self, tmp_path):
        (tmp_path / "PLAN.md").write_text("x", encoding="utf-8")
        assert tgc.git_commit_and_push(tmp_path, "test") is False

    def test_git_commit_succeeds_in_real_repo_no_remote(self, tmp_path):
        repo = self._init_repo(tmp_path)
        (repo / "PLAN.md").write_text(make_plan(task_block(status="pending")), encoding="utf-8")
        # No remote configured -> push will fail, but commit itself must succeed;
        # git_commit_and_push should report False overall since push failed, but
        # must not raise and must not corrupt the working tree.
        ok = tgc.git_commit_and_push(repo, "chore(plan): test [TG]")
        assert ok is False  # push fails (no remote) — reported honestly, no crash
        log = subprocess.run(["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True)
        assert "chore(plan): test [TG]" in log.stdout

    def test_git_commit_only_touches_plan_md(self, tmp_path):
        repo = self._init_repo(tmp_path)
        (repo / "other.txt").write_text("should not be staged", encoding="utf-8")
        (repo / "PLAN.md").write_text(make_plan(task_block(status="pending")), encoding="utf-8")
        tgc.git_commit_and_push(repo, "chore(plan): test [TG]")
        show = subprocess.run(["git", "show", "--stat", "HEAD"], cwd=repo, capture_output=True, text=True)
        assert "PLAN.md" in show.stdout
        assert "other.txt" not in show.stdout
        status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True)
        assert "other.txt" in status.stdout  # still untracked/uncommitted


# =============================================================== send_reply
class TestSendReply:
    def test_no_token_no_op(self):
        assert tgc.send_reply("", "123", "hello") is False

    def test_no_chat_id_no_op(self):
        assert tgc.send_reply("tok", "", "hello") is False

    def test_no_text_no_op(self):
        assert tgc.send_reply("tok", "123", "") is False

    def test_network_failure_swallowed(self, monkeypatch):
        def boom(*a, **kw):
            raise OSError("network down")
        monkeypatch.setattr(tgc.urllib.request, "urlopen", boom)
        # Must not raise.
        assert tgc.send_reply("tok", "123", "hello") is False
