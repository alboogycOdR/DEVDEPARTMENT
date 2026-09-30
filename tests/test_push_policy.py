"""Multi-process push-policy regressions with a durable fake clock."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import push_policy


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def fixture(tmp_path: Path, policy: str | None) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare", "-q")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    cfg = {"git": {"base_branch": "main"}}
    if policy is not None:
        cfg["git"].update(push_policy=policy, push_batch_minutes=30)
    (repo / "autopilot.json").write_text(json.dumps(cfg), encoding="utf-8")
    (repo / "PLAN.md").write_text("seed\n", encoding="utf-8")
    git(repo, "add", "PLAN.md", "autopilot.json")
    git(repo, "commit", "-qm", "seed")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-qu", "origin", "main")
    return repo, remote


def remote_head(remote: Path) -> str:
    return git(remote, "rev-parse", "refs/heads/main")


def policy_process(repo: Path, event: str, at: datetime) -> tuple[bool, str]:
    # A new interpreter for every event proves the window survives --once.
    program = ("import sys, json; from pathlib import Path; from datetime import datetime; "
               "sys.path.insert(0, sys.argv[1]); import push_policy; "
               "print(json.dumps(push_policy.maybe_push(Path(sys.argv[2]), sys.argv[3], "
               "now=datetime.fromisoformat(sys.argv[4]))))")
    result = subprocess.run([sys.executable, "-c", program, str(ROOT / "scripts"),
                             str(repo), event, at.isoformat()], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return tuple(json.loads(result.stdout.strip()))


def test_batch_twenty_commits_then_one_push_at_boundary(tmp_path):
    repo, remote = fixture(tmp_path, "batch")
    initial = remote_head(remote)
    start = datetime(2026, 9, 30, tzinfo=timezone.utc)
    for i in range(20):
        (repo / "PLAN.md").write_text(f"edit {i}\n", encoding="utf-8")
        git(repo, "commit", "-qm", f"chore(plan): edit {i}", "--", "PLAN.md")
        pushed, _ = policy_process(repo, "bookkeeping", start + timedelta(seconds=i * 30))
        assert not pushed
        assert remote_head(remote) == initial
    # The scheduled supervisor tick flushes at the boundary, with no new commit.
    pushed, _ = policy_process(repo, "bookkeeping", start + timedelta(minutes=30))
    assert pushed
    assert remote_head(remote) == git(repo, "rev-parse", "HEAD")


def test_merge_and_park_flush_merge_only(tmp_path):
    repo, remote = fixture(tmp_path, "merge_only")
    start = datetime(2026, 9, 30, tzinfo=timezone.utc)
    initial = remote_head(remote)
    (repo / "PLAN.md").write_text("edit\n", encoding="utf-8")
    git(repo, "commit", "-qm", "chore(plan): edit", "--", "PLAN.md")
    assert not policy_process(repo, "bookkeeping", start)[0]
    assert remote_head(remote) == initial
    assert policy_process(repo, "merge", start + timedelta(minutes=1))[0]
    assert remote_head(remote) == git(repo, "rev-parse", "HEAD")
    (repo / "PLAN.md").write_text("second\n", encoding="utf-8")
    git(repo, "commit", "-qm", "chore(plan): second", "--", "PLAN.md")
    assert policy_process(repo, "park", start + timedelta(minutes=2))[0]
    assert remote_head(remote) == git(repo, "rev-parse", "HEAD")


def test_missing_policy_is_every_and_unchanged_plan_creates_no_commit(tmp_path):
    repo, remote = fixture(tmp_path, None)
    before = git(repo, "rev-parse", "HEAD")
    committed, pushed, note = push_policy.commit_plan(repo, "chore(plan): no-op")
    assert committed and not pushed and "nothing to commit" in note
    assert git(repo, "rev-parse", "HEAD") == before
    (repo / "PLAN.md").write_text("edit\n", encoding="utf-8")
    committed, pushed, _ = push_policy.commit_plan(repo, "chore(plan): edit")
    assert committed and pushed
    assert remote_head(remote) == git(repo, "rev-parse", "HEAD")


def test_benign_scheduler_outcomes_exit_successfully(tmp_path):
    repo, _ = fixture(tmp_path, None)
    script = ROOT / "scripts" / "push_policy.py"
    for args in (("--only-if-configured",), ()):
        result = subprocess.run([sys.executable, str(script), "--repo", str(repo), *args],
                                capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert "push failed" not in result.stderr
