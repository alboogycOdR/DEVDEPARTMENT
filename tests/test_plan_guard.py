"""tests/test_plan_guard.py — the PLAN.md stray-block guard.

plan_guard is safety-critical: PLAN.md is committed WHOLE (by pathspec, which
is what stops plan_commit carrying code), so it has no merge step and
last-writer-wins is the entire conflict story. The guard is the only thing
standing between a stale-read overwrite and a reverted live claim — which
lets a second builder take an already-owned task, the one outcome the whole
protocol exists to prevent. It shipped verified-by-hand; these lock it in.

Real git repos in tmp_path, real diffs, real subprocess calls to the script —
the guard's whole job is reading `git diff -U0` output correctly, so mocking
git would test nothing that matters.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
GUARD = REPO_ROOT / "scripts" / "plan_guard.py"


PLAN_TEMPLATE = """---
plan_version: 4.7
last_updated: 2026-08-04T00:00:00Z
overall_status: in_progress
orchestrator_notes: "seed"
---

### TASK-011
**Title:** First task
**Status:** {t11_status}
**Assigned_To:** GB
**Priority:** high
**Spec_References:** specs/a.md
**Owned_Paths:** lib/a/**
**Depends_On:** —
**Description:** d
**Acceptance_Criteria:**
- [ ] c
**Branch:** {t11_branch}
**Started_At:** —
**Progress_Notes:** {t11_notes}
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** {t11_by}
**Updated_At:** 2026-08-04T00:00:00Z

### TASK-014
**Title:** Second task
**Status:** {t14_status}
**Assigned_To:** CX
**Priority:** medium
**Spec_References:** specs/b.md
**Owned_Paths:** lib/b/**
**Depends_On:** —
**Description:** d
**Acceptance_Criteria:**
- [ ] c
**Branch:** —
**Started_At:** —
**Progress_Notes:** {t14_notes}
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** {t14_by}
**Updated_At:** 2026-08-04T00:00:00Z
"""


def render(**over) -> str:
    fields = dict(t11_status="pending", t11_branch="—", t11_notes="—", t11_by="ORCH",
                  t14_status="pending", t14_notes="—", t14_by="ORCH")
    fields.update(over)
    return PLAN_TEMPLATE.format(**fields)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    r.mkdir()
    (r / "scripts").mkdir()
    (r / "PLAN.md").write_text(render(), encoding="utf-8", newline="\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=r, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=r, check=True)
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=r, check=True)
    return r


def run_guard(repo: Path, message: str):
    return subprocess.run(
        [sys.executable, str(GUARD), "--message", message, "--repo", str(repo)],
        capture_output=True, text=True, timeout=30)


class TestAllows:
    def test_no_changes_at_all_is_clean(self, repo):
        assert run_guard(repo, "chore(plan): claim TASK-011 [GB]").returncode == 0

    def test_edit_confined_to_the_named_block(self, repo):
        (repo / "PLAN.md").write_text(
            render(t11_status="claimed", t11_branch="task/TASK-011-gb", t11_by="GB"),
            encoding="utf-8", newline="\n")
        r = run_guard(repo, "chore(plan): claim TASK-011 [GB]")
        assert r.returncode == 0, r.stderr

    def test_orch_may_touch_frontmatter(self, repo):
        text = render().replace('orchestrator_notes: "seed"', 'orchestrator_notes: "wave 2"')
        (repo / "PLAN.md").write_text(text, encoding="utf-8", newline="\n")
        assert run_guard(repo, "chore(plan): wave notes [ORCH]").returncode == 0

    def test_orch_may_touch_multiple_blocks(self, repo):
        (repo / "PLAN.md").write_text(
            render(t11_status="done", t14_status="claimed"), encoding="utf-8", newline="\n")
        assert run_guard(repo, "chore(plan): reconcile wave [ORCH]").returncode == 0

    def test_message_naming_two_tasks_may_touch_both(self, repo):
        (repo / "PLAN.md").write_text(
            render(t11_status="done", t14_status="in_progress"), encoding="utf-8", newline="\n")
        r = run_guard(repo, "chore(plan): TASK-011 done, TASK-014 in_progress [GB]")
        assert r.returncode == 0, r.stderr


class TestRefuses:
    def test_stray_block_edit_is_refused(self, repo):
        """The CX-on-2026-08-02 signature: claim written into another task's
        block. Message names TASK-025-ish intent but the diff hits TASK-014."""
        (repo / "PLAN.md").write_text(
            render(t14_status="claimed", t14_by="CX"), encoding="utf-8", newline="\n")
        r = run_guard(repo, "chore(plan): claim TASK-011 [CX]")
        assert r.returncode == 1
        assert "TASK-014" in r.stderr

    def test_reverting_another_units_claim_is_refused(self, repo):
        """The GB-on-2026-08-02 signature and the dangerous one: a stale copy
        reverts a live claim back to pending while its owner is still working."""
        live = render(t11_status="claimed", t11_branch="task/TASK-011-gb", t11_by="GB")
        (repo / "PLAN.md").write_text(live, encoding="utf-8", newline="\n")
        subprocess.run(["git", "commit", "-q", "-am", "GB claims TASK-011"], cwd=repo, check=True)
        # CX writes its own block from a copy read BEFORE GB's claim landed:
        stale = render(t14_status="claimed", t14_by="CX")   # t11 back to pending
        (repo / "PLAN.md").write_text(stale, encoding="utf-8", newline="\n")
        r = run_guard(repo, "chore(plan): claim TASK-014 [CX]")
        assert r.returncode == 1
        assert "TASK-011" in r.stderr

    def test_builder_touching_frontmatter_is_refused(self, repo):
        text = render(t11_status="claimed", t11_by="GB").replace(
            'orchestrator_notes: "seed"', 'orchestrator_notes: "GB was here"')
        (repo / "PLAN.md").write_text(text, encoding="utf-8", newline="\n")
        r = run_guard(repo, "chore(plan): claim TASK-011 [GB]")
        assert r.returncode == 1
        assert "frontmatter" in r.stderr.lower()

    def test_multiple_blocks_with_no_task_named_is_refused(self, repo):
        (repo / "PLAN.md").write_text(
            render(t11_status="claimed", t14_status="claimed"), encoding="utf-8", newline="\n")
        r = run_guard(repo, "chore(plan): sync state [GB]")
        assert r.returncode == 1

    def test_refusal_explains_the_recovery_procedure(self, repo):
        (repo / "PLAN.md").write_text(render(t14_status="claimed"), encoding="utf-8", newline="\n")
        r = run_guard(repo, "chore(plan): claim TASK-011 [CX]")
        assert r.returncode == 1
        assert "checkout -- PLAN.md" in r.stderr   # tells the builder how to recover
        assert "STALE" in r.stderr


class TestFailsOpen:
    """A guard that blocks coordination writes because it could not read a
    diff would strand every builder — worse than the race it prevents."""

    def test_missing_plan_md_allows(self, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        r = run_guard(empty, "chore(plan): claim TASK-011 [GB]")
        assert r.returncode == 0

    def test_not_a_git_repo_allows(self, tmp_path):
        d = tmp_path / "nogit"
        d.mkdir()
        (d / "PLAN.md").write_text(render(), encoding="utf-8", newline="\n")
        r = run_guard(d, "chore(plan): claim TASK-011 [GB]")
        assert r.returncode == 0
        assert "allowing" in r.stderr.lower()

    def test_unparseable_plan_still_allows_when_no_blocks_found(self, repo):
        (repo / "PLAN.md").write_text("not a plan at all\n", encoding="utf-8", newline="\n")
        r = run_guard(repo, "chore(plan): claim TASK-011 [GB]")
        assert r.returncode == 0

    def test_usage_error_without_message(self, repo):
        r = subprocess.run([sys.executable, str(GUARD), "--repo", str(repo)],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 2
