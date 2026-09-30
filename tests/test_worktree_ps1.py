"""Windows runner lifecycle coverage for scripts/worktree.ps1."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh") or "powershell"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)


def _quote_ps(value: Path) -> str:
    return str(value).replace("'", "''")


@pytest.mark.skipif(os.name != "nt", reason="Windows process/worktree lifecycle")
def test_remove_stops_recorded_runner_before_removing_worktree(tmp_path):
    repo = tmp_path / "fixture"
    scripts = repo / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(ROOT / "scripts" / "worktree.ps1", scripts / "worktree.ps1")
    (repo / "autopilot.json").write_text(
        json.dumps({"git": {"base_branch": "master"}}), encoding="utf-8")
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")

    worktree = tmp_path / "wt-grok-fixture"
    _git(repo, "worktree", "add", "--detach", str(worktree), "master")
    launch = repo / ".devteam" / "launch"
    launch.mkdir(parents=True)
    runner = launch / "dummy.run.ps1"
    runner.write_text(
        f"Set-Location -LiteralPath '{_quote_ps(worktree)}'\n"
        "while ($true) { Start-Sleep -Seconds 1 }\n",
        encoding="utf-8", newline="\n")
    pid_path = launch / "grok.pid"
    process = subprocess.Popen(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(runner)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        record_script = tmp_path / "record_runner.ps1"
        record_script.write_text(
            "$p = Get-Process -Id " + str(process.pid) + "\n"
            "[ordered]@{ pid = $p.Id; runner = '" + _quote_ps(runner) + "'; worktree = '" + _quote_ps(worktree) + "'; "
            "started_utc_ticks = $p.StartTime.ToUniversalTime().Ticks } | "
            "ConvertTo-Json -Compress | Set-Content -LiteralPath '" + _quote_ps(pid_path) + "'\n",
            encoding="utf-8", newline="\n")
        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(record_script)],
            capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        record = json.loads(pid_path.read_text(encoding="utf-8-sig"))
        assert record["pid"] == process.pid

        result = subprocess.run(
            [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             str(scripts / "worktree.ps1"), "-Action", "remove", "-Builder", "grok"],
            cwd=repo, capture_output=True, text=True, timeout=45)

        assert result.returncode == 0, result.stdout + result.stderr
        assert not worktree.exists()
        assert process.poll() is not None
        assert not pid_path.exists()
        assert str(worktree).lower() not in _git(repo, "worktree", "list", "--porcelain").stdout.lower()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


def test_dispatch_detached_runner_records_pid_and_closes_on_completion():
    source = (ROOT / "scripts" / "dispatch.ps1").read_text(encoding="utf-8")
    assert '"-NoExit"' not in source
    assert "-PassThru" in source
    assert '"$Id.pid"' in source
    assert "started_utc_ticks" in source
