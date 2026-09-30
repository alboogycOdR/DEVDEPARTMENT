"""Wave E exit contract over ten actual processes and a 12-hour virtual loop."""
from __future__ import annotations

from collections import Counter
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

import tick_harness as harness
from test_supervisor import FM, task
import supervisor as sup

DRIVER = Path(__file__).parent / "fixtures" / "wave_e_exit"
ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.slow


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True,
                          capture_output=True, text=True, timeout=30)


@pytest.fixture(scope="module")
def scenario(tmp_path_factory):
    root = tmp_path_factory.mktemp("wave-e-exit")
    repo = root / "project"
    repo.mkdir()
    plan = FM + task(tid="TASK-901", status="needs_review", owned="review/**")
    plan += task(tid="TASK-902", status="needs_review", owned="frozen/**")
    for number in range(903, 906):
        plan += task(tid=f"TASK-{number}", status="blocked", owned=f"area{number}/**",
                     blocked="SPEC_AMBIGUITY: choose the supported default")
    harness.make_git_fixture_repo(repo, plan)
    remote = root / "remote.git"
    git(root, "init", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    config = {"builders": [], "notify_channels": [], "interval_seconds": 300,
              "git": {"base_branch": "main", "push_policy": "batch",
                      "push_batch_minutes": 30},
              "usage": {"enabled": False}, "board": {"enabled": False},
              "tower": {"enabled": False}, "learning": {"enabled": False}}
    (repo / "autopilot.json").write_text(json.dumps(config), encoding="utf-8")
    sup.RuntimeState(rework_counts={"TASK-902": sup.DEFAULT_CONFIG["max_rework"]}).save(
        repo / ".autopilot_state.json")
    env = {"WAVE_E_SCRIPTS": str(ROOT / "scripts"),
           "WAVE_E_TESTS": str(ROOT / "tests")}
    with pytest.MonkeyPatch.context() as patch:
        # Reuse the shared real-process launcher with a test-only clock adapter.
        patch.setattr(harness, "SCRIPTS_DIR", DRIVER)
        for index in range(10):
            result = harness.run_once_subprocess(
                repo, env={**env, "WAVE_E_MINUTE": str(index * 5)}, timeout=120)
            (root / f"once-{index}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            assert result.returncode == 0, result.stdout + result.stderr
        full_env = {**os.environ, **env, "WAVE_E_MINUTE": "50"}
        result = subprocess.run([sys.executable, str(DRIVER / "supervisor.py"),
                                 "--loop", "--repo", str(repo), "--max-ticks", "145"],
                                env=full_env, cwd=repo, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=240)
        (root / "loop.log").write_text(result.stdout + result.stderr, encoding="utf-8")
        assert result.returncode == 0, result.stdout + result.stderr
    events = [json.loads(line) for line in (repo / "events.jsonl").read_text().splitlines()]
    log = (repo / "AUTOPILOT_LOG.md").read_text(encoding="utf-8")
    text = (repo / "PLAN.md").read_text(encoding="utf-8")
    notices = Counter((row["priority"], row["message"]) for row in events
                      if row["kind"] == "notification" and row["priority"] in ("P1", "P2"))
    report = {"once_processes": sum(e.get("mode") == "once" for e in events),
              "loop_ticks": result.stdout.count("===== TICK"),
              "review_launches": log.count("REVIEW_START task=TASK-901 "),
              "unique_P1": sum(key[0] == "P1" for key in notices),
              "unique_P2": sum(key[0] == "P2" for key in notices),
              "max_sends_per_condition": max(notices.values(), default=0),
              "answer_applications": text.count("[TG-DECISION] use the documented default"),
              "plan_bytes": len(text.encode("utf-8")),
              "pushes": sum(e["kind"] == "push" for e in events)}
    (root / "evidence.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("\nWave E exit evidence: " + json.dumps(report, sort_keys=True))
    print(f"Fixture logs: {root}")
    return report, repo, events


def test_scenario_exercises_ten_processes_and_twelve_hour_loop(scenario):
    report, _, events = scenario
    assert report["once_processes"] == 10
    assert report["loop_ticks"] == 145  # inclusive endpoints, 144 x 5 min = 12 h
    assert len({e["pid"] for e in events if e["kind"] == "process"}) == 11


def test_review_launch_ceiling(scenario):
    assert 0 < scenario[0]["review_launches"] <= 5


def test_escalation_conditions_and_reminder_ceiling(scenario):
    report = scenario[0]
    assert report["unique_P2"] == 3
    assert report["unique_P1"] == 1
    assert report["max_sends_per_condition"] <= 2, report


def test_answer_applied_exactly_once(scenario):
    report, repo, events = scenario
    assert report["answer_applications"] == 1
    assert sum(e["kind"] == "telegram_delivery" for e in events) == 1
    consumed = json.loads((repo / ".devteam/inbox/.consumed_ids.json").read_text())
    assert consumed == ["telegram-43"]
    assert (repo / ".devteam/tg_offset.txt").read_text() == "44"


def test_plan_size_and_real_batch_push_bound(scenario):
    report, _, events = scenario
    assert report["plan_bytes"] < 60 * 1024
    assert 0 < report["pushes"] <= 12 * 2
    assert all(e["exit_code"] == 0 for e in events if e["kind"] == "push")
