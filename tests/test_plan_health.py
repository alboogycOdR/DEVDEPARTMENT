"""Regression tests for frontmatter freshness and untracked-work reporting."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import plan_health  # noqa: E402


ROOT = Path(__file__).resolve().parents[1]


def _run(args: list[str], cwd: Path, *, env=None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, check=True)


def make_repo(tmp_path: Path, *, last_updated="2026-09-01T00:00:00Z",
              task_updated="2026-09-02T00:00:00Z") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "scripts").mkdir()
    (repo / "autopilot.json").write_text(
        json.dumps({"git": {"base_branch": "master"}}), encoding="utf-8"
    )
    (repo / "PLAN.md").write_text(f"""---
plan_version: 1.0
last_updated: {last_updated}
overall_status: in_progress
orchestrator_notes: "Current checkpoint"
---

### TASK-123
**Status:** done
**Updated_At:** {task_updated}
""", encoding="utf-8")
    _run(["git", "init", "-q", "-b", "master"], repo)
    _run(["git", "config", "user.email", "test@example.com"], repo)
    _run(["git", "config", "user.name", "Plan Health Test"], repo)
    (repo / "tracked.txt").write_text("initial\n", encoding="utf-8")
    _run(["git", "add", "-A"], repo)
    _run(["git", "commit", "-q", "-m", "init [TASK-123]"], repo)
    return repo


def commit(repo: Path, message: str, when: datetime) -> None:
    (repo / "tracked.txt").write_text(message + "\n", encoding="utf-8")
    env = os.environ.copy()
    stamp = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    env.update({"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
    _run(["git", "add", "tracked.txt"], repo)
    _run(["git", "commit", "-q", "-m", message], repo, env=env)


def test_freshness_flags_old_frontmatter_against_task_and_tagged_git(tmp_path):
    repo = make_repo(tmp_path)
    latest = datetime.now(timezone.utc) - timedelta(days=1)
    commit(repo, "implementation [TASK-124]", latest)

    result = plan_health.freshness_report(repo)

    assert result.stale is True
    assert result.frontmatter_time == datetime(2026, 9, 1, tzinfo=timezone.utc)
    assert result.newest_source >= latest
    assert "last_updated" in result.message
    assert "overall_status" in result.message
    assert "orchestrator_notes" in result.message


def test_freshness_is_current_when_frontmatter_covers_task_and_git(tmp_path):
    repo = make_repo(
        tmp_path,
        last_updated="2099-01-01T00:00:00Z",
        task_updated="2098-01-01T00:00:00Z",
    )
    result = plan_health.freshness_report(repo)
    assert result.stale is False


def test_untracked_report_counts_only_recent_untagged_base_commits(tmp_path):
    repo = make_repo(tmp_path)
    now = datetime.now(timezone.utc)
    commit(repo, "work outside plan", now - timedelta(days=2))
    commit(repo, "review result [ORCH]", now - timedelta(days=1))
    commit(repo, "old untracked work", now - timedelta(days=20))

    result = plan_health.untracked_report(repo, now=now)

    assert result.total_commits == 3  # includes the tagged base fixture commit
    assert result.untagged_commits == 1
    assert "1/3" in result.message


def test_status_command_invokes_plan_health():
    command = (ROOT / ".claude" / "commands" / "devteam-status.md").read_text(encoding="utf-8")
    assert "python scripts/plan_health.py --repo ." in command


def test_review_cmd_only_points_at_review_document_and_document_uses_clock_and_model():
    config = json.loads((ROOT / "autopilot.json").read_text(encoding="utf-8"))
    review_cmd = config["review_cmd"]
    assert ".claude/commands/devteam-review.md" in review_cmd
    assert "You are ORCH" not in review_cmd
    review_doc = (ROOT / ".claude" / "commands" / "devteam-review.md").read_text(encoding="utf-8")
    assert 'date -u +"%Y-%m-%dT%H:%M:%SZ"' in review_doc
    assert "[DateTime]::UtcNow" in review_doc
    assert "reviewer model" in review_doc


def test_decompose_command_uses_owner_selected_model():
    command = (ROOT / ".claude" / "commands" / "devteam-decompose.md").read_text(encoding="utf-8")
    assert "claude-opus-5-5" in command
    assert "claude-fable-5" not in command


def test_session_start_reports_stale_frontmatter_from_fixture_repo(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "hooks").mkdir()
    shutil.copy2(ROOT / "hooks" / "session-start.js", repo / "hooks" / "session-start.js")
    shutil.copy2(ROOT / "hooks" / "lib.js", repo / "hooks" / "lib.js")
    shutil.copy2(ROOT / "scripts" / "plan_health.py", repo / "scripts" / "plan_health.py")
    env = os.environ.copy()
    env["CLAUDE_PROJECT_DIR"] = str(repo)
    env["DEVTEAM_UNIT"] = "CX"

    result = _run(["node", str(repo / "hooks" / "session-start.js")], repo, env=env)

    assert "[plan_health] STALE frontmatter" in result.stdout
    assert "overall_status" in result.stdout

