"""Reusable multi-tick / multi-process supervisor test harness (TASK-028).

The fake-clock path drives deterministic multi-tick tests; the subprocess path
is reserved for filesystem/process-boundary behavior such as review locks.
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

import supervisor as sup  # noqa: E402


@dataclass
class FakeClock:
    now: datetime

    def advance(self, minutes: float = 0.0, seconds: float = 0.0) -> datetime:
        self.now += timedelta(minutes=minutes, seconds=seconds)
        return self.now


@dataclass
class TickResult:
    tick: int
    now: datetime
    actions: list
    kept_going: bool


def make_fixture_repo(tmp_path: Path, plan_text: str, autopilot_log: str = "") -> Path:
    (tmp_path / "PLAN.md").write_text(plan_text, encoding="utf-8")
    (tmp_path / "AUTOPILOT_LOG.md").write_text(autopilot_log, encoding="utf-8")
    return tmp_path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=30, check=True)


def make_git_fixture_repo(tmp_path: Path, plan_text: str,
                          branches: dict[str, str] | None = None) -> Path:
    make_fixture_repo(tmp_path, plan_text)
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "harness@example.com")
    _git(tmp_path, "config", "user.name", "tick-harness")
    _git(tmp_path, "add", "PLAN.md", "AUTOPILOT_LOG.md")
    _git(tmp_path, "commit", "-q", "-m", "init")
    for branch, marker in (branches or {}).items():
        _git(tmp_path, "checkout", "-q", "-b", branch)
        marker_path = tmp_path / ".harness-marker"
        marker_path.write_text(marker, encoding="utf-8")
        _git(tmp_path, "add", marker_path.name)
        _git(tmp_path, "commit", "-q", "--no-verify", "-m", f"marker for {branch}")
        _git(tmp_path, "checkout", "-q", "main")
    return tmp_path


def git_branch_head(repo: Path, branch: str) -> str:
    return sup._git_head_sha(repo, branch)


def bump_branch(repo: Path, branch: str, marker: str) -> str:
    _git(repo, "checkout", "-q", branch)
    (repo / ".harness-marker").write_text(marker, encoding="utf-8")
    _git(repo, "add", ".harness-marker")
    _git(repo, "commit", "-q", "--no-verify", "-m", f"bump {branch}")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", "main")
    return sha


def run_ticks(repo: Path, cfg: dict, state: "sup.RuntimeState", clock: FakeClock,
              n_ticks: int, interval_minutes: float = 1.0, dry_run: bool = False,
              plan_text: str | None = None, **decide_kwargs) -> list[TickResult]:
    results = []
    for tick in range(1, n_ticks + 1):
        text = plan_text if plan_text is not None else (repo / "PLAN.md").read_text(encoding="utf-8")
        actions = sup.decide(text, state, cfg, now=clock.now, **decide_kwargs)
        kept_going = sup.execute(actions, cfg, state, repo, dry_run, now=clock.now)
        results.append(TickResult(tick, clock.now, actions, kept_going))
        if tick < n_ticks:
            clock.advance(minutes=interval_minutes)
    return results


def review_launch_times(results: list[TickResult]) -> list[datetime]:
    return [r.now for r in results if any(a.kind == "REVIEW" for a in r.actions)]


def run_once_subprocess(repo: Path, extra_args: list[str] | None = None,
                        env: dict | None = None, timeout: float = 60.0) -> subprocess.CompletedProcess:
    full_env = dict(os.environ)
    full_env["DEVTEAM_DELEGATED"] = "1"
    if env:
        full_env.update(env)
    command = [sys.executable, str(SCRIPTS_DIR / "supervisor.py"), "--once", "--repo", str(repo)]
    return subprocess.run(command + (extra_args or []), cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=full_env)
