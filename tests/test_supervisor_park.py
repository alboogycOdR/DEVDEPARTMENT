"""E-C durable park and on-disk inflight regression tests."""
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import supervisor as sup  # noqa: E402
from test_supervisor import CFG, FM, task, kinds  # noqa: E402
from tick_harness import make_git_fixture_repo, run_once_subprocess  # noqa: E402

NOW = datetime(2026, 7, 12, 20, tzinfo=timezone.utc)


def test_p1_parks_across_ten_saved_once_states_and_notifies_once(tmp_path, monkeypatch):
    """E-C acceptance: each simulated --once run loads/saves durable state."""
    notices = []
    monkeypatch.setattr(sup, "notify", lambda *args: notices.append(args[1:3]))
    plan = FM + task(status="needs_review")
    state_path = tmp_path / ".autopilot_state.json"
    for n in range(10):
        now = NOW + timedelta(minutes=n)
        state = sup.RuntimeState.load(state_path)
        if n == 0:
            state.rework_counts["TASK-001"] = 2
        actions = sup.decide(plan, state, CFG, now)
        sup.execute(actions, CFG, state, tmp_path, dry_run=False, now=now)
        state.save(state_path)
        assert "ESCALATE_P1" in kinds(actions) if n == 0 else kinds(actions) == ["IDLE"]
    assert len(notices) == 1
    assert sup.RuntimeState.load(state_path).parked["kind"] == "P1"


def test_durable_inflight_written_by_launch_then_reaped_by_fresh_state(tmp_path, monkeypatch):
    """E-C acceptance: use execute's real DISPATCH launch path, then reload."""
    notices = []
    monkeypatch.setattr(sup, "notify", lambda *args: notices.append(args[1:3]))
    class DeadLaunch:
        pid = 99999999

    monkeypatch.setattr(sup, "launch_shell_bg", lambda command, repo: DeadLaunch())
    state_path = tmp_path / ".autopilot_state.json"
    state = sup.RuntimeState()
    sup.execute([sup.Action("DISPATCH", "fixture", unit="GB", task_id="TASK-001")],
                CFG, state, tmp_path, dry_run=False, now=NOW)
    state.save(state_path)
    record = tmp_path / ".devteam" / "inflight" / "GB.json"
    assert record.exists()
    fresh = sup.RuntimeState.load(state_path)
    sup._reap_durable_inflight(CFG, fresh, tmp_path, NOW + timedelta(minutes=1))
    assert not record.exists()
    assert fresh.dispatch_failures["GB"] == 1
    assert notices


def test_recent_task_branch_commit_prevents_stale_redispatch(tmp_path):
    """E-C heartbeat uses the newest of Updated_At, branch commit and dossier."""
    old = "2026-07-09T20:00:00Z"
    plan = FM + task(status="in_progress", upd_at=old).replace("**Branch:** task/TASK-001-gb", "**Branch:** task/TASK-001-gb")
    repo = make_git_fixture_repo(tmp_path, plan, {"task/TASK-001-gb": "fresh"})
    # The fixture branch commit is necessarily recent relative to its real
    # commit clock; read it and use it as the deterministic decision clock.
    branch_ts = sup._task_heartbeats(repo, plan)["TASK-001"]
    actions = sup.decide(plan, sup.RuntimeState(), CFG, branch_ts + timedelta(minutes=5),
                         dossier_heartbeats=sup._task_heartbeats(repo, plan))
    assert "REDISPATCH_STALE" not in kinds(actions)


def test_resume_and_plan_changes_unpark_durable_state(tmp_path, monkeypatch):
    state = sup.RuntimeState(parked={"kind": "P1", "reason": "TASK-001 frozen", "since": "x"})
    monkeypatch.setattr(sup.tgc, "send_reply", lambda *args: None)
    sup._process_tg_command({"cmd": "/resume", "args": "", "chat_id": "1"}, tmp_path, CFG,
                            state, threading.Event(), NOW, "")
    assert state.parked == {}
    frozen = sup.RuntimeState(parked={"kind": "P1", "reason": "TASK-001 frozen", "since": "x"})
    assert frozen.parked
    sup.decide(FM + task(status="in_progress"), frozen, CFG, NOW)
    assert frozen.parked == {}
    done_wave = sup.RuntimeState(parked={"kind": "WAVE_DONE", "reason": "done", "since": "x"})
    sup.decide(FM + task(status="pending"), done_wave, CFG, NOW)
    assert done_wave.parked == {}


def test_stop_once_exits_three_and_pm2_marks_it_intentional(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
    (repo / "STOP").write_text("operator stop\n", encoding="utf-8")
    result = run_once_subprocess(repo, timeout=30)
    assert result.returncode == 3
    ecosystem = (Path(__file__).resolve().parents[1] / "deploy" / "ecosystem.config.js").read_text(encoding="utf-8")
    assert "stop_exit_codes: [3]" in ecosystem
