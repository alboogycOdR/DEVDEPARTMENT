"""Tests for scripts/maintenance.py — nightly self-audit routine (Wave B).

Every subprocess-shaped step (_step_harness_audit, _step_pytest, etc.) is
individually monkeypatched in these tests — matching this codebase's existing
convention (see tests/test_supervisor.py::test_triage_unblock_uses_sonnet5)
of substituting the module-level function rather than mocking subprocess
internals. Real subprocess calls are only exercised in the small number of
tests that specifically need a real git repo (backup, hygiene branch
pruning) — those use tmp_path + a real `git init`.
"""
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import maintenance as maint  # noqa: E402
from maintenance import StepResult  # noqa: E402

NOW = datetime(2026, 7, 19, 2, 30, 0, tzinfo=timezone.utc)  # after default hour_utc=2


def init_git_repo(repo: Path, with_plan: bool = True) -> Path:
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=repo, check=True)
    if with_plan:
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
    else:
        (repo / "README.md").write_text("x", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


SIMPLE_PLAN = """---
plan_version: 4.2
last_updated: 2026-07-19T00:00:00Z
overall_status: in_progress
---
# Plan

### TASK-001
**Title:** A task
**Status:** pending
**Assigned_To:** GB
**Priority:** high
**Spec_References:** specs/x.md
**Owned_Paths:** lib/a/**
**Depends_On:** —
**Description:** d
**Acceptance_Criteria:**
- [ ] c
**Branch:** —
**Started_At:** —
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** ORCH
**Updated_At:** 2026-07-19T00:00:00Z
"""


def all_pass(monkeypatch):
    """Patch every step to a trivially-passing StepResult."""
    for name in maint._ORDERED_STEPS:
        monkeypatch.setattr(maint, name, _make_step(name, True))


def _make_step(name, passed, detail="ok"):
    short = name.replace("_step_", "")
    if name == "_step_backup":
        return lambda repo, retain_days: StepResult(short, passed, detail)
    return lambda repo: StepResult(short, passed, detail)


# =============================================================== oneline ====
class TestOneline:
    def test_collapses_newlines(self):
        assert maint._oneline("a\nb\nc") == "a b c"

    def test_collapses_whitespace(self):
        assert maint._oneline("a    b\t\tc") == "a b c"

    def test_truncates_long_text(self):
        text = "x" * 1000
        out = maint._oneline(text, max_len=50)
        assert len(out) < 70
        assert "truncated" in out

    def test_empty_and_none(self):
        assert maint._oneline("") == ""
        assert maint._oneline(None) == ""


# ======================================================= idempotency + gate =
class TestScheduleGate:
    def test_not_due_returns_ran_false(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        early = NOW.replace(hour=1)  # before default hour_utc=2
        result = maint.run_nightly_audit(repo, {}, now=early)
        assert result.ran is False
        assert result.passed is True  # not-due is not a failure

    def test_force_ignores_schedule_gate(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        early = NOW.replace(hour=1)
        result = maint.run_nightly_audit(repo, {}, now=early, force=True)
        assert result.ran is True

    def test_running_twice_same_day_is_noop_second_time(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        first = maint.run_nightly_audit(repo, {}, now=NOW)
        assert first.ran is True
        second = maint.run_nightly_audit(repo, {}, now=NOW + timedelta(hours=2))
        assert second.ran is False

    def test_restart_mid_day_does_not_rerun(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        maint.run_nightly_audit(repo, {}, now=NOW)
        again = maint.run_nightly_audit(repo, {}, now=NOW + timedelta(minutes=90))
        assert again.ran is False

    def test_next_day_runs_again(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        maint.run_nightly_audit(repo, {}, now=NOW)
        tomorrow = maint.run_nightly_audit(repo, {}, now=NOW + timedelta(days=1))
        assert tomorrow.ran is True

    def test_marker_written_after_run(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        maint.run_nightly_audit(repo, {}, now=NOW)
        marker = repo / ".devteam" / "last_audit_date.txt"
        assert marker.exists()
        assert marker.read_text(encoding="utf-8").strip() == NOW.strftime("%Y-%m-%d")

    def test_custom_hour_utc_respected(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        cfg = {"maintenance": {"hour_utc": 20, "backup_retain_days": 7}}
        result = maint.run_nightly_audit(repo, cfg, now=NOW.replace(hour=10))
        assert result.ran is False  # 10 < 20
        result2 = maint.run_nightly_audit(repo, cfg, now=NOW.replace(hour=21))
        assert result2.ran is True


# ============================================================ all-pass flow
class TestAllStepsPass:
    def test_passed_true_no_task_filed(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        result = maint.run_nightly_audit(repo, {}, now=NOW)
        assert result.passed is True
        assert result.task_id is None
        assert result.digest_line == "Self-audit: PASS"
        assert len(result.steps) == 6

    def test_plan_md_unchanged_when_all_pass(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        maint.run_nightly_audit(repo, {}, now=NOW)
        assert (repo / "PLAN.md").read_text(encoding="utf-8") == SIMPLE_PLAN


# ======================================================= individual failures
class TestIndividualStepFailures:
    """Spec: 'Maintenance step failures individually simulated ... confirm
    each produces a correctly-formed TASK-MAINT block, and that one failure
    doesn't prevent the other steps from running.'"""

    def test_harness_audit_failure_alone(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        monkeypatch.setattr(maint, "_step_harness_audit",
                            lambda repo: StepResult("harness_audit", False, "AgentShield found a critical issue"))
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        result = maint.run_nightly_audit(repo, {}, now=NOW)
        assert result.passed is False
        assert len(result.steps) == 6  # all 6 still ran
        assert result.task_id == f"TASK-MAINT-{NOW.strftime('%Y-%m-%d')}"
        plan = (repo / "PLAN.md").read_text(encoding="utf-8")
        assert result.task_id in plan
        assert "harness_audit" in plan

    def test_validator_failure_alone(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        monkeypatch.setattr(maint, "_step_validate_plan",
                            lambda repo: StepResult("validate_plan", False, "PLAN.md illegal: bad frontmatter"))
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        result = maint.run_nightly_audit(repo, {}, now=NOW)
        assert result.passed is False
        _, block = maint._compose_maint_task(repo, [StepResult("validate_plan", False, "x")], NOW)
        assert "PLAN.md" in block

    def test_pytest_failure_alone(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        monkeypatch.setattr(maint, "_step_pytest",
                            lambda repo: StepResult("pytest", False, "3 failed, 40 passed"))
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        result = maint.run_nightly_audit(repo, {}, now=NOW)
        assert result.passed is False
        plan = (repo / "PLAN.md").read_text(encoding="utf-8")
        assert "3 failed, 40 passed" in plan

    def test_node_tests_failure_alone(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        monkeypatch.setattr(maint, "_step_node_tests",
                            lambda repo: StepResult("node_tests", False, "2 hook tests failed"))
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        result = maint.run_nightly_audit(repo, {}, now=NOW)
        assert result.passed is False
        assert result.task_id is not None

    def test_hygiene_failure_alone(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        monkeypatch.setattr(maint, "_step_hygiene",
                            lambda repo: StepResult("hygiene", False, "checkpoint cleanup crashed: permission denied"))
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        result = maint.run_nightly_audit(repo, {}, now=NOW)
        assert result.passed is False

    def test_backup_failure_alone(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        monkeypatch.setattr(maint, "_step_backup",
                            lambda repo, retain_days: StepResult("backup", False, "git bundle create failed: no commits"))
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        result = maint.run_nightly_audit(repo, {}, now=NOW)
        assert result.passed is False
        _, block = maint._compose_maint_task(repo, [StepResult("backup", False, "x")], NOW)
        assert "backups/**" in block

    def test_multiple_failures_all_filed_in_one_task(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        monkeypatch.setattr(maint, "_step_pytest", lambda repo: StepResult("pytest", False, "tests red"))
        monkeypatch.setattr(maint, "_step_backup",
                            lambda repo, retain_days: StepResult("backup", False, "bundle failed"))
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        result = maint.run_nightly_audit(repo, {}, now=NOW)
        assert result.passed is False
        plan = (repo / "PLAN.md").read_text(encoding="utf-8")
        assert plan.count("### TASK-MAINT-") == 1
        assert "pytest" in plan and "backup" in plan

    def test_one_failure_does_not_block_later_steps_from_running(self, tmp_path, monkeypatch):
        calls = []

        def failing_harness(repo):
            calls.append("harness_audit")
            return StepResult("harness_audit", False, "boom")

        def ok_step(name):
            def _inner(repo):
                calls.append(name)
                return StepResult(name, True, "ok")
            return _inner

        monkeypatch.setattr(maint, "_step_harness_audit", failing_harness)
        monkeypatch.setattr(maint, "_step_validate_plan", ok_step("validate_plan"))
        monkeypatch.setattr(maint, "_step_pytest", ok_step("pytest"))
        monkeypatch.setattr(maint, "_step_node_tests", ok_step("node_tests"))
        monkeypatch.setattr(maint, "_step_hygiene", ok_step("hygiene"))
        monkeypatch.setattr(maint, "_step_backup", lambda repo, retain_days: StepResult("backup", True, "ok"))

        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        maint.run_nightly_audit(repo, {}, now=NOW)
        assert calls == ["harness_audit", "validate_plan", "pytest", "node_tests", "hygiene"]

    def test_step_that_raises_is_caught_and_recorded_as_failed(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)

        def boom(repo):
            raise RuntimeError("unexpected crash")
        monkeypatch.setattr(maint, "_step_validate_plan", boom)
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        result = maint.run_nightly_audit(repo, {}, now=NOW)
        assert result.passed is False
        assert any("crashed" in s.detail for s in result.steps if not s.passed)
        assert (repo / ".devteam" / "last_audit_date.txt").exists()


# ==================================================== TASK-MAINT composition
class TestComposeMaintTask:
    def test_block_has_required_fields(self, tmp_path):
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        failed = [StepResult("pytest", False, "3 failed")]
        task_id, block = maint._compose_maint_task(repo, failed, NOW)
        assert task_id == f"TASK-MAINT-{NOW.strftime('%Y-%m-%d')}"
        for field in ("Title", "Status", "Assigned_To", "Priority", "Spec_References",
                      "Owned_Paths", "Description", "Acceptance_Criteria", "Updated_By", "Updated_At"):
            assert f"**{field}:**" in block

    def test_status_pending_priority_high(self, tmp_path):
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        _, block = maint._compose_maint_task(repo, [StepResult("pytest", False, "x")], NOW)
        assert "**Status:** pending" in block
        assert "**Priority:** high" in block

    def test_spec_references_self_generated(self, tmp_path):
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        _, block = maint._compose_maint_task(repo, [StepResult("pytest", False, "x")], NOW)
        assert "self-generated" in block

    def test_owned_paths_scoped_to_failed_steps(self, tmp_path):
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        _, block = maint._compose_maint_task(
            repo, [StepResult("validate_plan", False, "x"), StepResult("backup", False, "y")], NOW)
        owned_line = [ln for ln in block.splitlines() if ln.startswith("**Owned_Paths:**")][0]
        assert "PLAN.md" in owned_line
        assert "backups/**" in owned_line
        assert "hooks/**" not in owned_line

    def test_default_assignee_gb_when_no_review_md(self, tmp_path):
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        assignee = maint._pick_assignee(repo)
        assert assignee == "GB"

    def test_assignee_follows_team_stats_hint(self, tmp_path):
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        review = "\n".join(
            f"| TASK-{i:03d} | CX | approved | clean | yes | 2026-07-{10 + i:02d}T00:00:00Z |"
            for i in range(11)
        )
        (repo / "REVIEW.md").write_text(review, encoding="utf-8")
        assignee = maint._pick_assignee(repo)
        assert assignee in ("CX", "GB")

    def test_description_is_single_line_even_with_multiline_detail(self, tmp_path):
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        failed = [StepResult("pytest", False, "line one\nline two\nline three")]
        _, block = maint._compose_maint_task(repo, failed, NOW)
        desc_lines = [ln for ln in block.splitlines() if ln.startswith("**Description:**")]
        assert len(desc_lines) == 1
        assert not any(ln.strip() == "line two" for ln in block.splitlines())


# ============================================================ parseability ==
class TestFiledTaskIsParseable:
    def test_task_md_round_trips_through_validate_plan(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        monkeypatch.setattr(maint, "_step_pytest", lambda repo: StepResult("pytest", False, "3 failed"))
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        result = maint.run_nightly_audit(repo, {}, now=NOW)

        from validate_plan import parse_tasks, Report
        plan_text = (repo / "PLAN.md").read_text(encoding="utf-8")
        tasks = parse_tasks(plan_text, Report())
        ids = {t.task_id for t in tasks}
        assert "TASK-001" in ids
        assert result.task_id in ids
        maint_task = [t for t in tasks if t.task_id == result.task_id][0]
        assert maint_task.get("Status") == "pending"
        assert maint_task.get("Priority") == "high"

    def test_original_task_block_untouched(self, tmp_path, monkeypatch):
        all_pass(monkeypatch)
        monkeypatch.setattr(maint, "_step_pytest", lambda repo: StepResult("pytest", False, "3 failed"))
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        maint.run_nightly_audit(repo, {}, now=NOW)
        plan_text = (repo / "PLAN.md").read_text(encoding="utf-8")
        assert SIMPLE_PLAN.strip() in plan_text


# ==================================================== real subprocess steps
class TestRealSteps:
    """A handful of tests exercise the real (non-monkeypatched) step
    implementations against a throwaway git repo, to prove the subprocess
    wiring itself is correct — not just the orchestration around it."""

    def test_step_validate_plan_real_pass(self, tmp_path):
        repo = init_git_repo(tmp_path)
        result = maint._step_validate_plan(repo)
        assert result.passed is True

    def test_step_validate_plan_real_fail(self, tmp_path):
        repo = init_git_repo(tmp_path)
        (repo / "PLAN.md").write_text("not a valid plan at all", encoding="utf-8")
        result = maint._step_validate_plan(repo)
        assert result.passed is False

    def test_step_validate_plan_missing_file(self, tmp_path):
        repo = tmp_path
        result = maint._step_validate_plan(repo)
        assert result.passed is False
        assert "not found" in result.detail

    def test_step_backup_creates_bundle(self, tmp_path):
        repo = init_git_repo(tmp_path)
        result = maint._step_backup(repo, retain_days=7)
        assert result.passed is True
        bundles = list((repo / "backups").glob("*.bundle"))
        assert len(bundles) == 1

    def test_step_backup_prunes_old_bundles(self, tmp_path):
        repo = init_git_repo(tmp_path)
        backups_dir = repo / "backups"
        backups_dir.mkdir()
        old_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        recent_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
        (backups_dir / f"{old_date}.bundle").write_text("old", encoding="utf-8")
        (backups_dir / f"{recent_date}.bundle").write_text("recent", encoding="utf-8")
        maint._step_backup(repo, retain_days=7)
        remaining = {f.name for f in backups_dir.glob("*.bundle")}
        assert f"{old_date}.bundle" not in remaining
        assert f"{recent_date}.bundle" in remaining

    def test_step_backup_keeps_bundles_within_retain_window(self, tmp_path):
        repo = init_git_repo(tmp_path)
        backups_dir = repo / "backups"
        backups_dir.mkdir()
        recent_date = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
        (backups_dir / f"{recent_date}.bundle").write_text("recent", encoding="utf-8")
        maint._step_backup(repo, retain_days=7)
        assert (backups_dir / f"{recent_date}.bundle").exists()

    def test_step_hygiene_no_op_is_not_a_failure(self, tmp_path):
        repo = init_git_repo(tmp_path)
        result = maint._step_hygiene(repo)
        assert result.passed is True

    def test_step_hygiene_deletes_merged_done_task_branch(self, tmp_path):
        repo = init_git_repo(tmp_path)
        plan_done = SIMPLE_PLAN.replace("**Status:** pending", "**Status:** done")
        (repo / "PLAN.md").write_text(plan_done, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "mark done"], cwd=repo, check=True)
        subprocess.run(["git", "branch", "task/TASK-001-gb"], cwd=repo, check=True)
        current = subprocess.run(["git", "branch", "--show-current"], cwd=repo,
                                 capture_output=True, text=True).stdout.strip()
        if current != "main":
            subprocess.run(["git", "branch", "-m", current, "main"], cwd=repo, check=True)
        result = maint._step_hygiene(repo)
        assert result.passed is True
        branches = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout
        assert "task/TASK-001-gb" not in branches

    def test_step_hygiene_does_not_delete_branch_for_non_done_task(self, tmp_path):
        repo = init_git_repo(tmp_path)  # TASK-001 is "pending", not done
        subprocess.run(["git", "branch", "task/TASK-001-gb"], cwd=repo, check=True)
        current = subprocess.run(["git", "branch", "--show-current"], cwd=repo,
                                 capture_output=True, text=True).stdout.strip()
        if current != "main":
            subprocess.run(["git", "branch", "-m", current, "main"], cwd=repo, check=True)
        maint._step_hygiene(repo)
        branches = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True).stdout
        assert "task/TASK-001-gb" in branches

    def test_step_hygiene_rotates_oversized_log(self, tmp_path):
        repo = init_git_repo(tmp_path)
        big = "x" * (1_000_001)
        (repo / "AUTOPILOT_LOG.md").write_text(big, encoding="utf-8")
        result = maint._step_hygiene(repo)
        assert result.passed is True
        assert "rotated" in result.detail
        archives = list(repo.glob("AUTOPILOT_LOG.*.md"))
        assert len(archives) == 1
        assert (repo / "AUTOPILOT_LOG.md").read_text(encoding="utf-8") == ""

    def test_step_hygiene_does_not_rotate_small_log(self, tmp_path):
        repo = init_git_repo(tmp_path)
        (repo / "AUTOPILOT_LOG.md").write_text("small log", encoding="utf-8")
        result = maint._step_hygiene(repo)
        assert "rotated" not in result.detail
        assert (repo / "AUTOPILOT_LOG.md").read_text(encoding="utf-8") == "small log"

    def test_step_hygiene_removes_stale_checkpoint_for_done_task(self, tmp_path):
        repo = init_git_repo(tmp_path)
        plan_done = SIMPLE_PLAN.replace("**Status:** pending", "**Status:** done")
        (repo / "PLAN.md").write_text(plan_done, encoding="utf-8")
        checkpoint_dir = repo / ".devteam"
        checkpoint_dir.mkdir()
        (checkpoint_dir / "CHECKPOINT.md").write_text("Resuming TASK-001 at step 3", encoding="utf-8")
        result = maint._step_hygiene(repo)
        assert result.passed is True
        assert not (checkpoint_dir / "CHECKPOINT.md").exists()

    def test_step_hygiene_keeps_checkpoint_for_active_task(self, tmp_path):
        repo = init_git_repo(tmp_path)  # TASK-001 stays "pending"
        checkpoint_dir = repo / ".devteam"
        checkpoint_dir.mkdir()
        (checkpoint_dir / "CHECKPOINT.md").write_text("Resuming TASK-001 at step 3", encoding="utf-8")
        maint._step_hygiene(repo)
        assert (checkpoint_dir / "CHECKPOINT.md").exists()

    def test_step_harness_audit_missing_script(self, tmp_path):
        repo = tmp_path
        result = maint._step_harness_audit(repo)
        assert result.passed is False
        assert "not found" in result.detail

    def test_step_pytest_no_tests_dir_is_pass(self, tmp_path):
        repo = tmp_path
        result = maint._step_pytest(repo)
        assert result.passed is True
        assert "skipped" in result.detail

    def test_step_node_tests_no_runner_is_pass(self, tmp_path):
        repo = tmp_path
        result = maint._step_node_tests(repo)
        assert result.passed is True
        assert "skipped" in result.detail


# ============================================================== CLI =========
class TestCLI:
    def test_check_only_prints_decision(self, tmp_path, capsys):
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        rc = maint.main(["--repo", str(repo), "--check-only"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "Would run now" in out

    def test_run_without_force_does_not_crash(self, tmp_path):
        repo = tmp_path
        (repo / "PLAN.md").write_text(SIMPLE_PLAN, encoding="utf-8")
        (repo / "autopilot.json").write_text('{"maintenance": {"hour_utc": 23}}', encoding="utf-8")
        rc = maint.main(["--repo", str(repo)])
        assert rc in (0, 1)  # must not crash regardless of real current UTC hour
