"""tests/test_stagnation_signal.py — the I/O layer feeding circuit_breaker.py.

circuit_breaker.py's arithmetic is pure and tested in isolation
(test_circuit_breaker.py); supervisor.decide()'s wiring is tested against
synthetic signals (test_supervisor.py::TestCircuitBreakerIntegration). This
file is the one part of the port that touches real git and a real worktree
layout, so it uses real repos in tmp_path rather than mocking git — the
whole risk in this code is "does the path/branch resolution actually match
what dispatch.sh computes," which a mock cannot catch.

Every test in here proves the FAIL-OPEN contract: a resolution failure must
omit the task_id (or return None/0), never fabricate "stagnant".
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import supervisor as sup  # noqa: E402

FM = ("---\nplan_version: 1.0\nlast_updated: 2026-07-12T10:00:00Z\n"
      "overall_status: in_progress\n---\n")


def task_block(tid="TASK-001", status="in_progress", assignee="GB", owned="lib/a/**"):
    branch = f"task/{tid}-{'gb' if assignee == 'GB' else 'cx' if assignee == 'CX' else 's5'}"
    return f"""
### {tid}
**Title:** T
**Status:** {status}
**Assigned_To:** {assignee}
**Priority:** high
**Spec_References:** specs/x.md
**Owned_Paths:** {owned}
**Depends_On:** —
**Description:** d
**Acceptance_Criteria:**
- [ ] c
**Branch:** {branch}
**Started_At:** 2026-07-12T18:00:00Z
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** ORCH
**Updated_At:** 2026-07-12T19:50:00Z
"""


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args], check=check,
                          capture_output=True, text=True)


def make_repo_with_worktree(tmp_path: Path, unit="GB", task_id="TASK-001",
                            worktree_suffix="grok", branch_suffix="gb") -> tuple[Path, Path, str]:
    """A real project repo plus its unit's worktree, checked out on the
    exact branch dispatch.sh would create: task/<task_id>-<branch_suffix>,
    inside <parent>/wt-<worktree_suffix>-<repo_name> — the identical scheme
    builder_registry.LEGACY_DEFINITIONS + dispatch.sh compute. Returns
    (repo, worktree, branch)."""
    repo = tmp_path / "proj"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")
    (repo / "lib").mkdir()
    (repo / "lib" / "a").mkdir()
    (repo / "lib" / "a" / "x.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")

    branch = f"task/{task_id}-{branch_suffix}"
    worktree = tmp_path / f"wt-{worktree_suffix}-proj"
    _git(repo, "worktree", "add", "-b", branch, str(worktree), "main")
    return repo, worktree, branch


class TestWorktreeAndBranch:
    def test_resolves_legacy_unit(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        result = sup._worktree_and_branch(repo, "TASK-001", "GB")
        assert result == (tmp_path / "wt-grok-proj", "task/TASK-001-gb")

    def test_unregistered_unit_returns_none(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        assert sup._worktree_and_branch(repo, "TASK-001", "NOT_A_UNIT") is None


class TestGitDiffSince:
    def test_missing_worktree_is_none(self, tmp_path):
        assert sup._git_diff_since(tmp_path / "nope", "task/TASK-001-gb", "main", []) is None

    def test_wrong_branch_checked_out_is_none(self, tmp_path):
        # `main` is already checked out in the primary repo, so git refuses
        # to check it out again in the worktree — use a second decoy branch
        # to prove the point instead (still "not the expected task branch").
        repo, worktree, branch = make_repo_with_worktree(tmp_path)
        _git(worktree, "checkout", "-q", "-b", "some-other-branch")
        assert sup._git_diff_since(worktree, branch, "main", []) is None

    def test_no_changes_is_false(self, tmp_path):
        repo, worktree, branch = make_repo_with_worktree(tmp_path)
        assert sup._git_diff_since(worktree, branch, "main", ["lib/a/**"]) is False

    def test_modified_tracked_file_is_true(self, tmp_path):
        repo, worktree, branch = make_repo_with_worktree(tmp_path)
        (worktree / "lib" / "a" / "x.py").write_text("x = 2\n", encoding="utf-8")
        assert sup._git_diff_since(worktree, branch, "main", ["lib/a/**"]) is True

    def test_new_untracked_file_is_true(self, tmp_path):
        """A builder's very first commit-worthy change is often a brand-new
        file, which `git diff` alone (tracked files only) would miss."""
        repo, worktree, branch = make_repo_with_worktree(tmp_path)
        (worktree / "lib" / "a" / "new.py").write_text("y = 1\n", encoding="utf-8")
        assert sup._git_diff_since(worktree, branch, "main", ["lib/a/**"]) is True

    def test_change_outside_owned_paths_is_not_progress(self, tmp_path):
        """A change the task isn't even allowed to make must not read as
        this task's progress — territorial isolation applies here too."""
        repo, worktree, branch = make_repo_with_worktree(tmp_path)
        (worktree / "elsewhere.py").write_text("z = 1\n", encoding="utf-8")
        assert sup._git_diff_since(worktree, branch, "main", ["lib/a/**"]) is False

    def test_committed_change_still_counts(self, tmp_path):
        repo, worktree, branch = make_repo_with_worktree(tmp_path)
        (worktree / "lib" / "a" / "x.py").write_text("x = 3\n", encoding="utf-8")
        _git(worktree, "add", "-A")
        _git(worktree, "commit", "-q", "-m", "wip")
        assert sup._git_diff_since(worktree, branch, "main", ["lib/a/**"]) is True


class TestGateguardDenialCounter:
    def test_missing_file_is_zero(self, tmp_path):
        assert sup._gateguard_denials(tmp_path, "GB") == 0

    def test_corrupt_file_is_zero(self, tmp_path):
        p = tmp_path / ".devteam" / "gateguard" / "denials"
        p.mkdir(parents=True)
        (p / "GB.json").write_text("not json", encoding="utf-8")
        assert sup._gateguard_denials(tmp_path, "GB") == 0

    def test_reads_the_written_count(self, tmp_path):
        p = tmp_path / ".devteam" / "gateguard" / "denials"
        p.mkdir(parents=True)
        (p / "GB.json").write_text(json.dumps({"count": 4}), encoding="utf-8")
        assert sup._gateguard_denials(tmp_path, "GB") == 4

    def test_reset_zeroes_an_existing_counter(self, tmp_path):
        p = tmp_path / ".devteam" / "gateguard" / "denials"
        p.mkdir(parents=True)
        (p / "GB.json").write_text(json.dumps({"count": 7}), encoding="utf-8")
        sup._reset_gateguard_denials(tmp_path, "GB")
        assert sup._gateguard_denials(tmp_path, "GB") == 0

    def test_reset_on_a_never_denied_unit_does_not_raise(self, tmp_path):
        sup._reset_gateguard_denials(tmp_path, "GB")  # must not throw
        assert sup._gateguard_denials(tmp_path, "GB") == 0


class TestStagnationSignalEndToEnd:
    def test_stagnant_task_reports_changed_false_with_denials(self, tmp_path):
        repo, worktree, branch = make_repo_with_worktree(tmp_path)
        p = repo / ".devteam" / "gateguard" / "denials"
        p.mkdir(parents=True)
        (p / "GB.json").write_text(json.dumps({"count": 3}), encoding="utf-8")

        sig = sup._stagnation_signal(repo, FM + task_block(), sup.DEFAULT_CONFIG)
        assert sig == {"TASK-001": {"changed": False, "denials": 3}}

    def test_progress_resets_the_denial_counter_on_disk(self, tmp_path):
        repo, worktree, branch = make_repo_with_worktree(tmp_path)
        p = repo / ".devteam" / "gateguard" / "denials"
        p.mkdir(parents=True)
        (p / "GB.json").write_text(json.dumps({"count": 5}), encoding="utf-8")
        (worktree / "lib" / "a" / "x.py").write_text("x = 9\n", encoding="utf-8")

        sig = sup._stagnation_signal(repo, FM + task_block(), sup.DEFAULT_CONFIG)
        assert sig == {"TASK-001": {"changed": True, "denials": 5}}
        # supervisor resets the counter itself the instant progress is seen —
        # the NEXT tick must not still see the pre-progress denial count.
        assert sup._gateguard_denials(repo, "GB") == 0

    def test_unresolvable_worktree_omits_the_task_entirely(self, tmp_path):
        """No worktree exists at all yet (unit just claimed, dispatch hasn't
        run) — must be absent from the dict, not present with a guessed
        value either way."""
        repo = tmp_path / "proj"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        sig = sup._stagnation_signal(repo, FM + task_block(), sup.DEFAULT_CONFIG)
        assert sig == {}

    def test_pending_and_done_tasks_are_never_signaled(self, tmp_path):
        repo, worktree, branch = make_repo_with_worktree(tmp_path)
        plan = FM + task_block(status="pending") + task_block(tid="TASK-002", status="done")
        sig = sup._stagnation_signal(repo, plan, sup.DEFAULT_CONFIG)
        assert sig == {}

    def test_malformed_plan_text_fails_open_to_empty(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        sig = sup._stagnation_signal(repo, "not a plan at all {{{", sup.DEFAULT_CONFIG)
        assert sig == {}

    def test_two_units_resolved_independently(self, tmp_path):
        repo = tmp_path / "proj"
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        _git(repo, "config", "user.email", "t@t.com")
        _git(repo, "config", "user.name", "t")
        (repo / "lib").mkdir()
        (repo / "lib" / "a").mkdir()
        (repo / "lib" / "b").mkdir()
        (repo / "lib" / "a" / "x.py").write_text("1\n", encoding="utf-8")
        (repo / "lib" / "b" / "y.py").write_text("1\n", encoding="utf-8")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "init")

        wt_gb = tmp_path / "wt-grok-proj"
        wt_cx = tmp_path / "wt-codex-proj"
        _git(repo, "worktree", "add", "-b", "task/TASK-001-gb", str(wt_gb), "main")
        _git(repo, "worktree", "add", "-b", "task/TASK-002-cx", str(wt_cx), "main")
        (wt_cx / "lib" / "b" / "y.py").write_text("2\n", encoding="utf-8")  # CX made progress

        plan = (FM + task_block(tid="TASK-001", assignee="GB", owned="lib/a/**")
                + task_block(tid="TASK-002", assignee="CX", owned="lib/b/**"))
        sig = sup._stagnation_signal(repo, plan, sup.DEFAULT_CONFIG)
        assert sig["TASK-001"]["changed"] is False
        assert sig["TASK-002"]["changed"] is True
