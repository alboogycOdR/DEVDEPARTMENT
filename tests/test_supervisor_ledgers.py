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

    result = run_once_subprocess(repo)

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

    healthy = run_once_subprocess(repo, ["--dry-run"])

    assert healthy.returncode == 0, healthy.stderr
    assert state_path.read_bytes() == healthy_bytes

    corrupt_bytes = b"not-json\r\n"
    state_path.write_bytes(corrupt_bytes)
    corrupt = run_once_subprocess(repo, ["--dry-run"])

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
    second = subprocess.run(command, cwd=repo, capture_output=True, text=True, timeout=30)
    first.communicate(timeout=30)
    assert runs.read_text(encoding="utf-8").splitlines() == ["run"]
    assert "REVIEW_SKIPPED" in (repo / "AUTOPILOT_LOG.md").read_text(encoding="utf-8")
