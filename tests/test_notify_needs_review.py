"""tests/test_notify_needs_review.py — the needs_review Telegram/console hook.

Gap this closes: Telegram was wired only into supervisor.py's P0/P1/P2
escalation contract, which by design never fires on needs_review (the
autopilot handles that status silently via auto-review). A manually-driven
ORCH session — `/devteam-dispatch` + `/devteam-review` without the
autonomous --loop running — therefore had no notification path at all for
"a task just landed needs_review". notify_needs_review.py hooks the one
choke point every plan_commit caller already goes through (manual dispatch
or autopilot alike), so it fires universally.

These tests exercise two layers:
  1. parse_statuses() in isolation — the pure regex parser.
  2. End-to-end through plan_commit.sh with a stub notify.py, so the
     assertion is "the right message reached the notification layer",
     not "the regex matched" — the same standard test_plan_commit.py
     holds itself to for the commit behaviour it guards.

bash only for the end-to-end half — same standing .ps1-cannot-execute-here
caveat as test_plan_commit.py; the .ps1 hook is a 1:1 mirror, reviewed by
reading.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN_COMMIT = REPO_ROOT / "scripts" / "plan_commit.sh"
PLAN_GUARD = REPO_ROOT / "scripts" / "plan_guard.py"
NOTIFY_NEEDS_REVIEW = REPO_ROOT / "scripts" / "notify_needs_review.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))
from notify_needs_review import parse_statuses  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")


PLAN = """---
plan_version: 4.7
last_updated: 2026-08-04T00:00:00Z
overall_status: in_progress
---

### TASK-007
**Title:** A task
**Status:** {status}
**Assigned_To:** S5
**Priority:** high
**Spec_References:** specs/a.md
**Owned_Paths:** lib/a/**
**Depends_On:** —
**Description:** d
**Acceptance_Criteria:**
- [ ] c
**Branch:** {branch}
**Started_At:** —
**Progress_Notes:** {notes}
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** {by}
**Updated_At:** 2026-08-04T00:00:00Z
"""


def plan(status="pending", branch="—", notes="—", by="ORCH") -> str:
    return PLAN.format(status=status, branch=branch, notes=notes, by=by)


def git(repo: Path, *args, check=True):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, check=check)


STUB_NOTIFY = """#!/usr/bin/env python3
# Test double: records every invocation instead of calling the real Telegram
# API, so the end-to-end tests assert on "the right message was sent", not
# on network access.
import sys
from pathlib import Path

log = Path(__file__).parent / "notify_calls.log"
with log.open("a", encoding="utf-8") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
"""


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    (r / "scripts").mkdir(parents=True)
    (r / "lib").mkdir()
    shutil.copyfile(PLAN_COMMIT, r / "scripts" / "plan_commit.sh")
    (r / "scripts" / "plan_commit.sh").chmod(0o755)
    shutil.copyfile(PLAN_GUARD, r / "scripts" / "plan_guard.py")
    shutil.copyfile(NOTIFY_NEEDS_REVIEW, r / "scripts" / "notify_needs_review.py")
    (r / "scripts" / "notify.py").write_text(STUB_NOTIFY, encoding="utf-8", newline="\n")
    (r / "PLAN.md").write_text(plan(), encoding="utf-8", newline="\n")
    (r / "autopilot.json").write_text('{"git": {"base_branch": "main"}}',
                                      encoding="utf-8", newline="\n")
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "T")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "seed")
    return r


def _bash() -> str:
    """The bash the PACK targets, not whatever `bash` resolves to first.

    Same rationale as test_plan_commit.py's identical helper: on a Windows
    dev box `bash` often resolves to WSL bash, which cannot read a worktree
    created by Windows git.
    """
    if os.name == "nt":
        for cand in (os.path.join("C:", os.sep, "Program Files", "Git", "bin", "bash.exe"),
                     os.path.join("C:", os.sep, "Program Files", "Git", "usr", "bin", "bash.exe")):
            if os.path.exists(cand):
                return cand
    return shutil.which("bash") or "bash"


def run_commit(repo: Path, message: str):
    return subprocess.run([_bash(), "scripts/plan_commit.sh", message], cwd=repo,
                          capture_output=True, text=True, timeout=60)


def notify_calls(repo: Path) -> list[str]:
    log = repo / "scripts" / "notify_calls.log"
    if not log.exists():
        return []
    return [ln for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


class TestParseStatuses:
    """The pure regex parser, in isolation."""

    def test_reads_status_and_title(self):
        text = plan(status="needs_review")
        statuses = parse_statuses(text)
        assert statuses == {"TASK-007": ("A task", "needs_review")}

    def test_multiple_tasks(self):
        second_block = plan(status="needs_review", by="GB").replace(
            "### TASK-007", "### TASK-008", 1,
        )
        text = plan(status="done") + "\n" + second_block
        statuses = parse_statuses(text)
        assert statuses["TASK-007"][1] == "done"
        assert statuses["TASK-008"][1] == "needs_review"

    def test_no_tasks_returns_empty(self):
        assert parse_statuses("---\nplan_version: 1\n---\n") == {}


class TestEndToEndViaPlanCommit:
    """The real trigger path: a builder calls plan_commit.sh, which calls
    notify_needs_review.py, which should call notify.py exactly when a task
    transitions INTO needs_review — and never on an unrelated commit."""

    def test_fresh_transition_notifies(self, repo):
        (repo / "PLAN.md").write_text(plan(status="in_progress", by="S5"),
                                      encoding="utf-8", newline="\n")
        run_commit(repo, "chore(plan): TASK-007 in_progress [S5]")

        (repo / "PLAN.md").write_text(plan(status="needs_review", by="S5"),
                                      encoding="utf-8", newline="\n")
        r = run_commit(repo, "chore(plan): TASK-007 -> needs_review [S5]")
        assert r.returncode == 0, r.stderr

        calls = notify_calls(repo)
        assert len(calls) == 1
        assert "--priority P0" in calls[0]
        assert "TASK-007 -> needs_review: A task" in calls[0]
        assert "--channels telegram,console" in calls[0]

    def test_claim_does_not_notify(self, repo):
        (repo / "PLAN.md").write_text(plan(status="claimed", by="S5"),
                                      encoding="utf-8", newline="\n")
        r = run_commit(repo, "chore(plan): claim TASK-007 [S5]")
        assert r.returncode == 0, r.stderr
        assert notify_calls(repo) == []

    def test_touching_an_already_needs_review_task_does_not_renotify(self, repo):
        (repo / "PLAN.md").write_text(plan(status="needs_review", by="S5"),
                                      encoding="utf-8", newline="\n")
        run_commit(repo, "chore(plan): TASK-007 -> needs_review [S5]")
        assert len(notify_calls(repo)) == 1

        # A later, unrelated Progress_Note edit while STILL needs_review
        # must not fire a second ping for the same transition.
        (repo / "PLAN.md").write_text(
            plan(status="needs_review", by="S5", notes="- a late note"),
            encoding="utf-8", newline="\n",
        )
        run_commit(repo, "chore(plan): TASK-007 note [S5]")
        assert len(notify_calls(repo)) == 1

    def test_transition_to_done_does_not_notify(self, repo):
        (repo / "PLAN.md").write_text(plan(status="needs_review", by="S5"),
                                      encoding="utf-8", newline="\n")
        run_commit(repo, "chore(plan): TASK-007 -> needs_review [S5]")
        assert len(notify_calls(repo)) == 1

        (repo / "PLAN.md").write_text(plan(status="done", by="ORCH"),
                                      encoding="utf-8", newline="\n")
        run_commit(repo, "chore(review): TASK-007 approved [ORCH]")
        assert len(notify_calls(repo)) == 1  # unchanged — done is not a re-fire

    def test_missing_notify_py_does_not_break_plan_commit(self, repo):
        (repo / "scripts" / "notify.py").unlink()
        (repo / "PLAN.md").write_text(plan(status="needs_review", by="S5"),
                                      encoding="utf-8", newline="\n")
        r = run_commit(repo, "chore(plan): TASK-007 -> needs_review [S5]")
        assert r.returncode == 0, r.stderr
        # No crash, no notify — the coordination commit is what matters.
