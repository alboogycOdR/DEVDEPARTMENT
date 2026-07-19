"""Integration tests for supervisor.py's two-way Telegram wiring
(Wave A-remainder): drain_tg_queue, mute gating on ESCALATE_P2/DIGEST (never
on ESCALATE_P1), and the /approve -> REVIEW_TG scoped-review action.

Uses real temp-directory repos (git-initialised where relevant) and monkeypatches
supervisor.run_shell / tgc.send_reply so nothing here ever shells out to a real
`claude`/`grok`/`codex` binary or hits the network.
"""
import queue
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import supervisor as sup  # noqa: E402
import tg_commands as tgc  # noqa: E402
from supervisor import (  # noqa: E402
    Action, RuntimeState, DEFAULT_CONFIG, execute, is_muted,
    drain_tg_queue, log_line,
)

NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)
CFG = dict(DEFAULT_CONFIG)

PLAN_ONE_TASK = """---
plan_version: 4.1
last_updated: 2026-07-18T00:00:00Z
overall_status: in_progress
---
# Plan

### TASK-016
**Title:** Retry backoff strategy
**Status:** blocked
**Assigned_To:** GB
**Priority:** high
**Spec_References:** specs/x.md
**Owned_Paths:** lib/retry/**
**Depends_On:** —
**Description:** d
**Acceptance_Criteria:**
- [ ] c
**Branch:** task/TASK-016-gb
**Started_At:** 2026-07-18T00:00:00Z
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** SPEC_AMBIGUITY
**Updated_By:** GB
**Updated_At:** 2026-07-18T00:00:00Z
"""


def make_repo(tmp_path: Path, plan_text: str = PLAN_ONE_TASK, git: bool = False) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "PLAN.md").write_text(plan_text, encoding="utf-8")
    if git:
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def tg_item(cmd, args, chat_id="12345"):
    return {"cmd": cmd, "args": args, "chat_id": chat_id, "update_id": 1, "raw": f"{cmd} {args}"}


# ===================================================================== mute
class TestMuteGating:
    def test_p1_never_gated_even_when_muted(self, tmp_path, monkeypatch):
        sent = []
        monkeypatch.setattr(sup, "notify", lambda cfg, prio, msg, repo: sent.append(prio))
        repo = make_repo(tmp_path)
        state = RuntimeState(mute_until="2026-07-19T00:00:00Z")  # muted for the whole test window
        assert is_muted(state, NOW) is True
        execute([Action("ESCALATE_P1", "protocol illegal")], CFG, state, repo, dry_run=False, now=NOW)
        assert sent == ["P1"]

    def test_p2_suppressed_when_muted(self, tmp_path, monkeypatch):
        sent = []
        monkeypatch.setattr(sup, "notify", lambda cfg, prio, msg, repo: sent.append(prio))
        repo = make_repo(tmp_path)
        state = RuntimeState(mute_until="2026-07-19T00:00:00Z")
        execute([Action("ESCALATE_P2", "TASK-016 blocked")], CFG, state, repo, dry_run=False, now=NOW)
        assert sent == []
        log = (repo / "AUTOPILOT_LOG.md").read_text(encoding="utf-8")
        assert "MUTED" in log

    def test_digest_suppressed_when_muted(self, tmp_path, monkeypatch):
        sent = []
        monkeypatch.setattr(sup, "notify", lambda cfg, prio, msg, repo: sent.append(prio))
        repo = make_repo(tmp_path)
        state = RuntimeState(mute_until="2026-07-19T00:00:00Z")
        execute([Action("DIGEST", "wave complete")], CFG, state, repo, dry_run=False, now=NOW)
        assert sent == []

    def test_p2_fires_normally_when_not_muted(self, tmp_path, monkeypatch):
        sent = []
        monkeypatch.setattr(sup, "notify", lambda cfg, prio, msg, repo: sent.append(prio))
        repo = make_repo(tmp_path)
        state = RuntimeState()  # no mute
        execute([Action("ESCALATE_P2", "TASK-016 blocked")], CFG, state, repo, dry_run=False, now=NOW)
        assert sent == ["P2"]

    def test_mute_expires(self, tmp_path, monkeypatch):
        sent = []
        monkeypatch.setattr(sup, "notify", lambda cfg, prio, msg, repo: sent.append(prio))
        repo = make_repo(tmp_path)
        state = RuntimeState(mute_until="2026-07-18T11:00:00Z")  # expired 1h before NOW
        execute([Action("ESCALATE_P2", "TASK-016 blocked")], CFG, state, repo, dry_run=False, now=NOW)
        assert sent == ["P2"]

    def test_is_muted_helper_direct(self):
        assert is_muted(RuntimeState(mute_until=""), NOW) is False
        assert is_muted(RuntimeState(mute_until="2026-07-19T00:00:00Z"), NOW) is True
        assert is_muted(RuntimeState(mute_until="2026-07-01T00:00:00Z"), NOW) is False
        assert is_muted(RuntimeState(mute_until="not-a-date"), NOW) is False


# ========================================================== drain: /stop ===
class TestStopIsUnbreakable:
    def test_stop_creates_file_even_with_corrupted_plan(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tgc, "send_reply", lambda *a, **kw: True)
        repo = make_repo(tmp_path, plan_text="!!! not even close to valid PLAN.md ---{{{")
        q = queue.Queue()
        q.put(tg_item("/stop", ""))
        wave_event = threading.Event()
        state = RuntimeState()
        drain_tg_queue(q, repo, CFG, state, wave_event, NOW, token="tok")
        assert (repo / "STOP").exists()

    def test_stop_works_with_missing_plan_md(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tgc, "send_reply", lambda *a, **kw: True)
        repo = tmp_path / "repo2"
        repo.mkdir()
        # No PLAN.md at all.
        q = queue.Queue()
        q.put(tg_item("/stop", ""))
        drain_tg_queue(q, repo, CFG, RuntimeState(), threading.Event(), NOW, token="tok")
        assert (repo / "STOP").exists()

    def test_resume_clears_stop_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tgc, "send_reply", lambda *a, **kw: True)
        repo = make_repo(tmp_path)
        (repo / "STOP").write_text("halted", encoding="utf-8")
        q = queue.Queue()
        q.put(tg_item("/resume", ""))
        drain_tg_queue(q, repo, CFG, RuntimeState(), threading.Event(), NOW, token="tok")
        assert not (repo / "STOP").exists()

    def test_one_bad_command_does_not_block_a_later_stop_in_same_batch(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tgc, "send_reply", lambda *a, **kw: True)
        repo = make_repo(tmp_path)
        q = queue.Queue()
        # Malformed item missing required keys — must not crash the drain loop
        # or prevent the /stop right after it from executing.
        q.put({"cmd": "/answer", "args": "TASK-016 something", "chat_id": None, "update_id": 1})
        q.put(tg_item("/stop", ""))

        def boom_apply(*a, **kw):
            raise RuntimeError("simulated failure")
        monkeypatch.setattr(tgc, "apply_answer", boom_apply)
        drain_tg_queue(q, repo, CFG, RuntimeState(), threading.Event(), NOW, token="tok")
        assert (repo / "STOP").exists()
        log = (repo / "AUTOPILOT_LOG.md").read_text(encoding="utf-8")
        assert "ERROR" in log


# ========================================================= drain: /wave ====
class TestWave:
    def test_wave_sets_event(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tgc, "send_reply", lambda *a, **kw: True)
        repo = make_repo(tmp_path)
        q = queue.Queue()
        q.put(tg_item("/wave", ""))
        ev = threading.Event()
        assert not ev.is_set()
        drain_tg_queue(q, repo, CFG, RuntimeState(), ev, NOW, token="tok")
        assert ev.is_set()


# ======================================================== drain: /mute =====
class TestMuteCommand:
    def test_mute_sets_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tgc, "send_reply", lambda *a, **kw: True)
        repo = make_repo(tmp_path)
        q = queue.Queue()
        q.put(tg_item("/mute", "2h"))
        state = RuntimeState()
        drain_tg_queue(q, repo, CFG, state, threading.Event(), NOW, token="tok")
        assert state.mute_until != ""
        assert is_muted(state, NOW) is True
        assert is_muted(state, NOW.replace(hour=15)) is False  # 3h later, expired

    def test_bad_duration_no_state_change(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tgc, "send_reply", lambda *a, **kw: True)
        repo = make_repo(tmp_path)
        q = queue.Queue()
        q.put(tg_item("/mute", "not-a-duration"))
        state = RuntimeState()
        drain_tg_queue(q, repo, CFG, state, threading.Event(), NOW, token="tok")
        assert state.mute_until == ""


# ====================================================== drain: /approve ====
class TestApprove:
    def test_approve_returns_review_tg_action(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tgc, "send_reply", lambda *a, **kw: True)
        repo = make_repo(tmp_path)
        q = queue.Queue()
        q.put(tg_item("/approve", "TASK-016"))
        actions = drain_tg_queue(q, repo, CFG, RuntimeState(), threading.Event(), NOW, token="tok")
        assert len(actions) == 1
        assert actions[0].kind == "REVIEW_TG"
        assert actions[0].task_id == "TASK-016"

    def test_review_tg_execute_uses_scoped_prompt_and_sonnet5(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(sup, "run_shell", lambda cmd, repo: calls.append(cmd) or 1)
        repo = make_repo(tmp_path)
        execute([Action("REVIEW_TG", "TG /approve TASK-016", task_id="TASK-016")],
                CFG, RuntimeState(), repo, dry_run=False, now=NOW)
        assert len(calls) == 1
        assert "claude-opus-4-8" in calls[0]  # judgment_model, never the S5 builder's model
        assert "TASK-016" in calls[0]
        assert "/devteam-review" in calls[0]

    def test_approve_bad_args_no_action(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tgc, "send_reply", lambda *a, **kw: True)
        repo = make_repo(tmp_path)
        q = queue.Queue()
        q.put(tg_item("/approve", ""))
        actions = drain_tg_queue(q, repo, CFG, RuntimeState(), threading.Event(), NOW, token="tok")
        assert actions == []


# ===================================================== drain: /answer e2e ==
class TestAnswerEndToEnd:
    def test_answer_updates_plan_and_logs(self, tmp_path, monkeypatch):
        monkeypatch.setattr(tgc, "send_reply", lambda *a, **kw: True)
        repo = make_repo(tmp_path, git=True)
        q = queue.Queue()
        q.put(tg_item("/answer", "TASK-016 use exponential backoff"))
        drain_tg_queue(q, repo, CFG, RuntimeState(), threading.Event(), NOW, token="tok")

        plan_text = (repo / "PLAN.md").read_text(encoding="utf-8")
        assert "**Status:** pending" in plan_text
        assert "[TG-DECISION] use exponential backoff" in plan_text

        log = (repo / "AUTOPILOT_LOG.md").read_text(encoding="utf-8")
        assert "TG_COMMAND unit=TG cmd=/answer task=TASK-016" in log

        # Committed with the [TG] tag (no remote configured, so push fails, but
        # the local commit must still have happened).
        gitlog = subprocess.run(["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True).stdout
        assert "[TG]" in gitlog

    def test_answer_unknown_task_replies_warning_no_commit(self, tmp_path, monkeypatch):
        replies = []
        monkeypatch.setattr(tgc, "send_reply", lambda token, chat, text: replies.append(text))
        repo = make_repo(tmp_path, git=True)
        q = queue.Queue()
        q.put(tg_item("/answer", "TASK-999 nope"))
        drain_tg_queue(q, repo, CFG, RuntimeState(), threading.Event(), NOW, token="tok")
        assert any("unknown task" in r.lower() for r in replies)
        gitlog = subprocess.run(["git", "log", "--oneline"], cwd=repo, capture_output=True, text=True).stdout
        assert gitlog.count("\n") == 1  # only the init commit, nothing added


# ======================================================== drain: help ======
class TestUnknownCommandReply:
    def test_unrecognised_text_gets_help_reply(self, tmp_path, monkeypatch):
        replies = []
        monkeypatch.setattr(tgc, "send_reply", lambda token, chat, text: replies.append(text))
        repo = make_repo(tmp_path)
        q = queue.Queue()
        q.put({"cmd": "help", "args": "hello there", "chat_id": "12345", "update_id": 1})
        actions = drain_tg_queue(q, repo, CFG, RuntimeState(), threading.Event(), NOW, token="tok")
        assert actions == []
        assert replies == [tgc.HELP_TEXT]


# ===================================================== config load defaults
class TestConfigDefaults:
    def test_load_config_merges_telegram_defaults(self, tmp_path):
        repo = tmp_path / "repo3"
        repo.mkdir()
        (repo / "PLAN.md").write_text(PLAN_ONE_TASK, encoding="utf-8")
        cfg = sup.load_config(repo)
        assert "telegram" in cfg
        assert "chat_allowlist" in cfg["telegram"]
        assert "poll_interval_seconds" in cfg["telegram"]

    def test_load_config_preserves_custom_telegram_section(self, tmp_path):
        import json
        repo = tmp_path / "repo4"
        repo.mkdir()
        (repo / "PLAN.md").write_text(PLAN_ONE_TASK, encoding="utf-8")
        custom = dict(DEFAULT_CONFIG)
        custom["telegram"] = {"chat_allowlist": ["111", "222"], "poll_interval_seconds": 5}
        (repo / "autopilot.json").write_text(json.dumps(custom), encoding="utf-8")
        cfg = sup.load_config(repo)
        assert cfg["telegram"]["chat_allowlist"] == ["111", "222"]
        assert cfg["telegram"]["poll_interval_seconds"] == 5
