"""Multi-tick and cross-process regression coverage for TASK-028."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import supervisor as sup  # noqa: E402
from test_supervisor import FM, kinds, task  # noqa: E402
from tick_harness import (FakeClock, bump_branch, git_branch_head, make_fixture_repo,
                          make_git_fixture_repo, review_launch_times, run_once_subprocess,
                          run_ticks)  # noqa: E402


NOW = datetime(2026, 9, 27, 12, tzinfo=timezone.utc)
CFG = dict(sup.DEFAULT_CONFIG)


def test_review_backoff_schedule_is_not_every_tick(tmp_path, monkeypatch):
    plan = FM + task(status="needs_review")
    repo = make_fixture_repo(tmp_path, plan)
    monkeypatch.setattr(sup, "run_shell", lambda *_: 1)
    clock = FakeClock(NOW)
    results = run_ticks(repo, CFG, sup.RuntimeState(), clock, 30,
                        interval_minutes=CFG["interval_seconds"] / 60, plan_text=plan)
    offsets = [(instant - NOW).total_seconds() / 60 for instant in review_launch_times(results)]
    assert offsets[:4] == [0, 5, 15, 35]
    assert len(offsets) < 10


def test_new_branch_head_bypasses_existing_backoff(tmp_path, monkeypatch):
    plan = FM + task(status="needs_review", branch="task/TASK-001-gb",
                     started="2026-09-27T10:00:00Z")
    repo = make_git_fixture_repo(tmp_path, plan, {"task/TASK-001-gb": "first"})
    monkeypatch.setattr(sup, "run_shell", lambda *_: 1)
    state = sup.RuntimeState()
    sup.execute(sup.decide(plan, state, CFG, NOW, head_shas=sup._review_head_shas(repo, plan)),
                CFG, state, repo, False, NOW)
    old_head = git_branch_head(repo, "task/TASK-001-gb")
    assert state.review_ledger["TASK-001"]["key"] == old_head
    moved_head = bump_branch(repo, "task/TASK-001-gb", "second")
    assert moved_head != old_head
    assert "REVIEW" in kinds(sup.decide(plan, state, CFG, NOW, head_shas={"TASK-001": moved_head}))


def test_markers_and_atomic_corrupt_state_handling(tmp_path, monkeypatch):
    plan = FM + task(status="needs_review")
    repo = make_fixture_repo(tmp_path, plan)
    monkeypatch.setattr(sup, "run_shell", lambda *_: 1)
    state = sup.RuntimeState()
    sup.execute(sup.decide(plan, state, CFG, NOW), CFG, state, repo, False, NOW)
    log = (repo / "AUTOPILOT_LOG.md").read_text(encoding="utf-8")
    assert "REVIEW_START task=TASK-001 sha=- session=" in log
    assert "REVIEW_END task=TASK-001 verdict=none duration=" in log
    path = repo / ".autopilot_state.json"
    path.write_text("not-json", encoding="utf-8")
    recovered = sup.RuntimeState.load(path, quarantine=True)
    assert recovered._corrupt_note and not path.exists()
    assert len(list(repo.glob(".autopilot_state.corrupt-*.json"))) == 1
    recovered.save(path)
    assert json.loads(path.read_text(encoding="utf-8"))
    assert not list(repo.glob("*.tmp"))


def test_once_quarantines_corrupt_state_and_sends_one_p2(tmp_path):
    """The real --once entry point reports corrupt durable state exactly once."""
    repo = make_git_fixture_repo(tmp_path, FM + task())
    (repo / "autopilot.json").write_text(json.dumps({"notify_channels": []}), encoding="utf-8")
    (repo / ".autopilot_state.json").write_text("not-json", encoding="utf-8")

    result = run_once_subprocess(repo, timeout=120)

    assert result.returncode == 0, result.stderr
    assert result.stdout.count("[P2] supervisor state file was corrupt:") == 1
    log = (repo / "AUTOPILOT_LOG.md").read_text(encoding="utf-8")
    assert log.count("STATE_CORRUPT:") == 1
    assert len(list(repo.glob(".autopilot_state.corrupt-*.json"))) == 1
    assert json.loads((repo / ".autopilot_state.json").read_text(encoding="utf-8"))


def test_dry_run_preserves_state_bytes_and_never_quarantines_corruption(tmp_path):
    """--dry-run must not modify either healthy or corrupt on-disk state."""
    repo = make_git_fixture_repo(tmp_path, FM + task())
    (repo / "autopilot.json").write_text(json.dumps({"notify_channels": []}), encoding="utf-8")
    state_path = repo / ".autopilot_state.json"
    state_path.write_bytes(b'{\n  "last_digest_ts": "kept-exactly"\n}\n')
    healthy_bytes = state_path.read_bytes()

    healthy = run_once_subprocess(repo, ["--dry-run"], timeout=120)

    assert healthy.returncode == 0, healthy.stderr
    assert state_path.read_bytes() == healthy_bytes

    corrupt_bytes = b"not-json\r\n"
    state_path.write_bytes(corrupt_bytes)
    corrupt = run_once_subprocess(repo, ["--dry-run"], timeout=120)

    assert corrupt.returncode == 0, corrupt.stderr
    assert state_path.read_bytes() == corrupt_bytes
    assert not list(repo.glob(".autopilot_state.corrupt-*.json"))


def test_digest_includes_behind_pack_warning(tmp_path, monkeypatch):
    import status_digest
    repo = make_fixture_repo(tmp_path, FM + task())
    monkeypatch.setattr(status_digest, "_behind_pack_line", lambda _: "framework v1 behind pack v2")
    assert "framework v1 behind pack v2" in status_digest.build(repo, NOW)


def test_two_once_processes_share_an_exclusive_review_lock(tmp_path):
    plan = FM + task(status="needs_review")
    repo = make_git_fixture_repo(tmp_path, plan)
    runs = repo / "runs.txt"
    script = repo / "review.py"
    script.write_text(
        "import pathlib, time\n"
        f"pathlib.Path(r'{runs}').open('a').write('run\\n')\n"
        "time.sleep(6)\nraise SystemExit(1)\n", encoding="utf-8")
    (repo / "autopilot.json").write_text(json.dumps({"review_cmd": f"{sys.executable} {script}", "notify_channels": []}), encoding="utf-8")
    command = [sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "supervisor.py"),
               "--once", "--repo", str(repo)]
    first = subprocess.Popen(command, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    time.sleep(0.5)
    second = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=120)
    first.communicate(timeout=120)
    assert runs.read_text(encoding="utf-8").splitlines() == ["run"]
    assert "REVIEW_SKIPPED" in (repo / "AUTOPILOT_LOG.md").read_text(encoding="utf-8")


def test_escalation_ledger_holds_p2s_and_renotifies_after_four_hours(tmp_path, monkeypatch):
    plan = FM + task(tid="TASK-001", status="blocked", blocked="SPEC_AMBIGUITY: retry policy is unclear",
                     branch="task/TASK-001-gb", started="2026-09-27T10:00:00Z") \
           + task(tid="TASK-002", status="blocked", blocked="SPEC_AMBIGUITY: acceptance is unclear",
                  branch="task/TASK-002-cx", started="2026-09-27T10:00:00Z", owned="lib/b/**") \
           + task(tid="TASK-003", status="blocked", blocked="SPEC_AMBIGUITY: dependency choice is unclear",
                  branch="task/TASK-003-s5", started="2026-09-27T10:00:00Z", owned="lib/c/**")
    sent = []
    monkeypatch.setattr(sup, "notify", lambda _cfg, priority, message, _repo: sent.append((priority, message)))
    state, clock = sup.RuntimeState(), FakeClock(NOW)
    results = run_ticks(make_fixture_repo(tmp_path, plan), CFG, state, clock, 30,
                        interval_minutes=5, plan_text=plan)
    assert len([x for x in sent if x[0] == "P2"]) == 3
    assert sum(a.kind == "ESCALATION_HELD" for r in results for a in r.actions) == 3

    later = NOW.replace(hour=16)
    actions = sup.decide(plan, state, CFG, later)
    assert len([a for a in actions if a.kind == "ESCALATE_P2"]) == 3


def test_escalation_timer_resend_is_capped_persisted_and_resets_on_key_change(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(sup, "notify", lambda _cfg, priority, message, _repo: sent.append((priority, message)))
    repo = make_fixture_repo(tmp_path, FM)
    state_path = tmp_path / ".autopilot_state.json"
    cfg = {**CFG, "escalation": {**CFG["escalation"], "max_timer_resends": 1}}
    original = sup.Action("ESCALATE_P1", "frozen condition A", task_id="TASK-001")

    # First sight, then exactly one timer resend at +1 h. Reload from disk on
    # each tick to prove the ceiling survives scheduled --once processes.
    for offset in (0, 60, *range(120, 13 * 60, 60)):
        now = NOW + __import__("datetime").timedelta(minutes=offset)
        state = sup.RuntimeState.load(state_path) if state_path.exists() else sup.RuntimeState()
        actions = sup._dedupe_escalations([original], state, cfg, now)
        sup.execute(actions, cfg, state, repo, False, now)
        state.save(state_path)
    assert [priority for priority, _ in sent] == ["P1", "P1"]
    persisted = sup.RuntimeState.load(state_path)
    key = sup.escalation_key(original)
    assert persisted.escalation_timer_resends[key] == 1

    # A changed condition has a new key and is immediately actionable.
    changed = sup.Action("ESCALATE_P1", "frozen condition B", task_id="TASK-001")
    actions = sup._dedupe_escalations([changed], persisted, cfg, NOW + __import__("datetime").timedelta(hours=13))
    sup.execute(actions, cfg, persisted, repo, False, NOW + __import__("datetime").timedelta(hours=13))
    assert [priority for priority, _ in sent] == ["P1", "P1", "P1"]
    assert key not in persisted.escalation_timer_resends


def test_parked_frozen_p1_uses_the_same_timer_resend_cap(tmp_path, monkeypatch):
    plan = FM + task(status="needs_review")
    sent = []
    monkeypatch.setattr(sup, "notify", lambda _cfg, priority, message, _repo: sent.append(priority))
    cfg = {**CFG, "escalation": {**CFG["escalation"], "max_timer_resends": 1}}
    detail = f"TASK-001 reached max_rework={cfg['max_rework']} — frozen for human review"
    action = sup.Action("ESCALATE_P1", detail, task_id="TASK-001")
    state = sup.RuntimeState(rework_counts={"TASK-001": cfg["max_rework"]},
                             parked={"kind": "P1", "reason": detail, "since": NOW.strftime(sup.UTC_FMT)},
                             escalated={sup.escalation_key(action): NOW.strftime(sup.UTC_FMT)})
    repo = make_fixture_repo(tmp_path, plan)

    resend_at = NOW + __import__("datetime").timedelta(hours=1)
    actions = sup.decide(plan, state, cfg, resend_at)
    assert [a.kind for a in actions] == ["ESCALATE_P1"]
    sup.execute(actions, cfg, state, repo, False, resend_at)

    after_cap = NOW + __import__("datetime").timedelta(hours=2)
    actions = sup.decide(plan, state, cfg, after_cap)
    assert "ESCALATE_P1" not in [a.kind for a in actions]
    assert sent == ["P1"]


def test_parked_p1_reminder_preserves_other_live_escalation_ledgers(tmp_path, monkeypatch):
    p2 = sup.Action("ESCALATE_P2", "TASK-101 blocked: SPEC_AMBIGUITY — human answer needed",
                    task_id="TASK-101")
    p1 = sup.Action("ESCALATE_P1", "TASK-102 reached max_rework=2 — frozen for human review",
                    task_id="TASK-102")
    p2_key, p1_key = sup.escalation_key(p2), sup.escalation_key(p1)
    p2_last, p2_held = NOW.strftime(sup.UTC_FMT), (NOW - __import__("datetime").timedelta(hours=5)).strftime(sup.UTC_FMT)
    plan = (FM
            + task(tid="TASK-101", status="blocked", blocked="SPEC_AMBIGUITY: awaiting a decision",
                   owned="lib/a/**")
            + task(tid="TASK-102", status="needs_review", owned="lib/b/**"))
    repo = make_fixture_repo(tmp_path, plan)
    sent = []
    monkeypatch.setattr(sup, "notify", lambda _cfg, priority, message, _repo: sent.append((priority, message)))
    state = sup.RuntimeState(
        rework_counts={"TASK-102": CFG["max_rework"]},
        parked={"kind": "P1", "reason": p1.detail, "since": NOW.strftime(sup.UTC_FMT)},
        escalated={p1_key: NOW.strftime(sup.UTC_FMT), p2_key: p2_last},
        escalation_held={p2_key: p2_held},
        escalation_timer_resends={p1_key: 0, p2_key: 1},
    )

    # A due parked P1 must touch only its own ledger entry.
    reminder_at = NOW + __import__("datetime").timedelta(minutes=61)
    reminder_actions = sup.decide(plan, state, CFG, reminder_at)
    assert [action.kind for action in reminder_actions] == ["ESCALATE_P1"]
    sup.execute(reminder_actions, CFG, state, repo, False, reminder_at)
    assert state.escalated[p2_key] == p2_last
    assert state.escalation_held[p2_key] == p2_held
    assert state.escalation_timer_resends[p2_key] == 1

    # Once unparked, the still-live P2 remains throttled by its original
    # timestamp and exhausted resend count rather than looking newly raised.
    state.parked = {}
    unparked_actions = sup.decide(plan, state, CFG, reminder_at + __import__("datetime").timedelta(minutes=1))
    assert "ESCALATE_P2" not in [action.kind for action in unparked_actions]
    assert any(action.kind == "ESCALATION_HELD" and p2_key in action.detail
               for action in unparked_actions)
    assert [priority for priority, _ in sent] == ["P1"]


def test_tooling_failure_triage_is_durable_and_attempt_is_real(tmp_path, monkeypatch):
    plan = FM + task(status="blocked", blocked="TOOLING_FAILURE: runner exited unexpectedly",
                     branch="task/TASK-001-gb", started="2026-09-27T10:00:00Z")
    repo = make_fixture_repo(tmp_path, plan)
    calls = []
    monkeypatch.setattr(sup, "run_shell", lambda cmd, _repo: calls.append(cmd) or 0)
    state = sup.RuntimeState()
    first = sup.decide(plan, state, CFG, NOW)
    assert first[0].kind == "TRIAGE_UNBLOCK" and "attempt 1" in first[0].detail
    sup.execute(first, CFG, state, repo, False, NOW)
    second = sup.decide(plan, state, CFG, NOW.replace(minute=5))
    assert [a.kind for a in second] == ["ESCALATE_P2"]
    assert state.triage_counts == {"TASK-001": {"TOOLING_FAILURE": 1}}
    assert len(calls) == 1


def test_stop_mtime_logs_once_until_file_changes(tmp_path):
    repo = make_fixture_repo(tmp_path, FM + task())
    state = sup.RuntimeState()
    first = sup.decide(FM + task(), state, CFG, NOW, stop_file_exists=True, stop_file_mtime="one")
    second = sup.decide(FM + task(), state, CFG, NOW + __import__("datetime").timedelta(hours=3), stop_file_exists=True, stop_file_mtime="one")
    third = sup.decide(FM + task(), state, CFG, NOW + __import__("datetime").timedelta(hours=6), stop_file_exists=True, stop_file_mtime="two")
    sup.execute(first, CFG, state, repo, False, NOW)
    sup.execute(second, CFG, state, repo, False, NOW)
    sup.execute(third, CFG, state, repo, False, NOW)
    assert (repo / "AUTOPILOT_LOG.md").read_text(encoding="utf-8").count("HALT:") == 2


def test_status_digest_send_uses_durable_content_ledger(tmp_path, monkeypatch):
    repo = make_fixture_repo(tmp_path, FM + task())
    sent = []
    monkeypatch.setattr(sup, "notify", lambda _cfg, priority, message, _repo: sent.append((priority, message)))
    cfg = {**CFG, "status_digest_minutes": 0, "status_digest": {"send": True}}
    state = sup.RuntimeState()
    sup.maybe_status_digest(repo, cfg, state, NOW)
    sup.maybe_status_digest(repo, cfg, state, NOW.replace(minute=5))
    assert [priority for priority, _ in sent] == ["P0"]
