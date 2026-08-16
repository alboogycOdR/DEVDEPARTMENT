"""tests/test_plan_commit.py — the PLAN.md coordination-commit tool.

The bug this replaced leaked unreviewed code onto the integration branch
three times across two builder CLIs: `git commit && push . HEAD:<base>` is
correct exactly once (on claim, before any code exists) and silently wrong
every time after, because by then HEAD sits on the builder's own code
commits and the push carries the whole chain.

The fix's load-bearing property is that `git commit -m <msg> -- PLAN.md`
uses a PATHSPEC, which bypasses the index entirely — so it commits only
PLAN.md's working-tree content and cannot pick up code, staged or not.
That property is what these tests pin down; everything else here is
guard rails around it.

bash only: the .ps1 mirror cannot be executed in this environment (no
pwsh), same standing caveat as every other .ps1 in this pack. Its logic is
a 1:1 mirror and is reviewed by reading.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN_COMMIT = REPO_ROOT / "scripts" / "plan_commit.sh"
PLAN_GUARD = REPO_ROOT / "scripts" / "plan_guard.py"

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


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "proj"
    (r / "scripts").mkdir(parents=True)
    (r / "lib").mkdir()
    shutil.copyfile(PLAN_COMMIT, r / "scripts" / "plan_commit.sh")
    (r / "scripts" / "plan_commit.sh").chmod(0o755)
    shutil.copyfile(PLAN_GUARD, r / "scripts" / "plan_guard.py")
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

    On a Windows dev box `bash` is often WSL bash, which cannot read a
    worktree created by Windows git: the linked worktree's .git file holds a
    `C:/...` path that does not exist under WSL, so every git call inside it
    returns empty and the script under test sees no branch at all. Builders
    run Git Bash (or PowerShell), so prefer that; on Linux/CI the two are the
    same binary and this is a no-op.
    """
    if os.name == "nt":
        for cand in (os.path.join("C:", os.sep, "Program Files", "Git", "bin", "bash.exe"),
                     os.path.join("C:", os.sep, "Program Files", "Git", "usr", "bin", "bash.exe")):
            if os.path.exists(cand):
                return cand
    return shutil.which("bash") or "bash"


def run_commit(repo: Path, message: str):
    return subprocess.run(["bash", "scripts/plan_commit.sh", message], cwd=repo,
                          capture_output=True, text=True, timeout=60)


def files_in_head(repo: Path) -> set[str]:
    out = git(repo, "show", "--name-only", "--pretty=format:", "HEAD").stdout
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


class TestCannotCarryCode:
    """The whole point of the pathspec form."""

    def test_dirty_code_is_not_committed(self, repo):
        (repo / "lib" / "feature.py").write_text("print('wip')\n", encoding="utf-8")
        (repo / "PLAN.md").write_text(plan(status="claimed", by="S5"),
                                      encoding="utf-8", newline="\n")
        r = run_commit(repo, "chore(plan): claim TASK-007 [S5]")
        assert r.returncode == 0, r.stderr
        assert files_in_head(repo) == {"PLAN.md"}

    def test_STAGED_code_is_not_committed(self, repo):
        """The exact failure condition: code staged in the index. A plain
        `git commit` would sweep it in; the pathspec form must not."""
        (repo / "lib" / "feature.py").write_text("print('staged')\n", encoding="utf-8")
        git(repo, "add", "lib/feature.py")
        (repo / "PLAN.md").write_text(plan(status="needs_review", by="S5"),
                                      encoding="utf-8", newline="\n")
        r = run_commit(repo, "chore(plan): TASK-007 needs_review [S5]")
        assert r.returncode == 0, r.stderr
        assert files_in_head(repo) == {"PLAN.md"}
        # ...and the staged code is still staged, untouched:
        staged = git(repo, "diff", "--cached", "--name-only").stdout
        assert "lib/feature.py" in staged

    def test_commit_lands_on_the_integration_branch(self, repo):
        (repo / "PLAN.md").write_text(plan(status="claimed", by="S5"),
                                      encoding="utf-8", newline="\n")
        run_commit(repo, "chore(plan): claim TASK-007 [S5]")
        assert git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "main"
        assert "claim TASK-007" in git(repo, "log", "-1", "--format=%s").stdout


class TestGuardRails:
    def test_no_changes_is_a_clean_noop(self, repo):
        before = git(repo, "rev-parse", "HEAD").stdout.strip()
        r = run_commit(repo, "chore(plan): claim TASK-007 [S5]")
        assert r.returncode == 0
        assert "nothing to record" in r.stdout
        assert git(repo, "rev-parse", "HEAD").stdout.strip() == before

    def test_refuses_when_checkout_is_on_the_wrong_branch(self, repo):
        git(repo, "checkout", "-q", "-b", "some-feature")
        (repo / "PLAN.md").write_text(plan(status="claimed", by="S5"),
                                      encoding="utf-8", newline="\n")
        r = run_commit(repo, "chore(plan): claim TASK-007 [S5]")
        assert r.returncode == 1
        assert "expected 'main'" in r.stderr
        assert "do not work around this" in r.stderr.lower()

    def test_respects_a_custom_base_branch(self, repo):
        git(repo, "branch", "-m", "main", "trunk")
        (repo / "autopilot.json").write_text('{"git": {"base_branch": "trunk"}}',
                                             encoding="utf-8", newline="\n")
        (repo / "PLAN.md").write_text(plan(status="claimed", by="S5"),
                                      encoding="utf-8", newline="\n")
        r = run_commit(repo, "chore(plan): claim TASK-007 [S5]")
        assert r.returncode == 0, r.stderr
        assert files_in_head(repo) == {"PLAN.md"}

    def test_missing_autopilot_json_falls_back_to_main(self, repo):
        (repo / "autopilot.json").unlink()
        (repo / "PLAN.md").write_text(plan(status="claimed", by="S5"),
                                      encoding="utf-8", newline="\n")
        r = run_commit(repo, "chore(plan): claim TASK-007 [S5]")
        assert r.returncode == 0, r.stderr

    def test_usage_error_without_a_message(self, repo):
        r = subprocess.run(["bash", "scripts/plan_commit.sh"], cwd=repo,
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 2
        assert "usage:" in r.stderr

    def test_guard_refusal_aborts_the_commit(self, repo):
        """plan_commit must honour plan_guard's veto, not commit anyway."""
        two_blocks = plan(status="claimed", by="S5") + """
### TASK-009
**Title:** Another
**Status:** claimed
**Assigned_To:** GB
**Priority:** low
**Spec_References:** specs/b.md
**Owned_Paths:** lib/b/**
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
**Updated_By:** GB
**Updated_At:** 2026-08-04T00:00:00Z
"""
        (repo / "PLAN.md").write_text(two_blocks, encoding="utf-8", newline="\n")
        git(repo, "commit", "-q", "-am", "seed two blocks")
        # Now edit ONLY TASK-009's block but claim to be committing TASK-007:
        edited = two_blocks.replace("**Status:** claimed\n**Assigned_To:** GB",
                                    "**Status:** done\n**Assigned_To:** GB")
        (repo / "PLAN.md").write_text(edited, encoding="utf-8", newline="\n")
        before = git(repo, "rev-parse", "HEAD").stdout.strip()
        r = run_commit(repo, "chore(plan): TASK-007 progress [S5]")
        assert r.returncode == 1
        assert git(repo, "rev-parse", "HEAD").stdout.strip() == before, "must not have committed"


class TestRunsFromALinkedWorktree:
    """The invocation builders ACTUALLY use — and the one that was broken.

    Builders run `scripts/plan_commit.sh` from their own worktree, exactly as
    the dispatch prompt instructs. A worktree contains every tracked file,
    including this script, so location-based root resolution resolved to the
    WORKTREE — detached HEAD, never the integration branch — and the guard
    refused every such call with "main checkout is on 'HEAD', expected
    'main'". Latent for many waves because a builder using the absolute
    main-checkout path happened to work; CX hit it on 2026-08-16 by following
    the prompt literally and was blocked before it could even claim.
    """

    def _worktree(self, repo: Path) -> Path:
        # core.autocrlf=false so the checked-out shell script keeps LF. The real
        # pack pins this via .gitattributes (*.sh eol=lf); without it, Windows
        # git rewrites the script to CRLF on worktree checkout and bash dies on
        # $'
        # a stray carriage return before reaching anything this test is about.
        git(repo, "config", "core.autocrlf", "false")
        wt = repo.parent / "wt-builder"
        git(repo, "worktree", "add", "--detach", str(wt), "HEAD")
        return wt

    def test_commit_from_worktree_lands_on_the_main_checkout(self, repo):
        wt = self._worktree(repo)
        (repo / "PLAN.md").write_text(plan(status="claimed", by="GB"),
                                      encoding="utf-8", newline="\n")
        r = subprocess.run([_bash(), "scripts/plan_commit.sh", "chore(plan): claim [GB]"],
                           cwd=wt, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"
        # The commit must exist on the MAIN checkout's integration branch...
        log = git(repo, "log", "--oneline", "-1").stdout
        assert "claim [GB]" in log
        assert files_in_head(repo) == {"PLAN.md"}
        # ...and the worktree must still be detached, untouched.
        head = git(wt, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
        assert head == "HEAD", "the builder's worktree must not be moved onto a branch"

    def test_worktree_invocation_does_not_report_the_worktree_as_main(self, repo):
        """The exact symptom: the refusal naming the worktree as the checkout."""
        wt = self._worktree(repo)
        (repo / "PLAN.md").write_text(plan(status="claimed"), encoding="utf-8", newline="\n")
        r = subprocess.run([_bash(), "scripts/plan_commit.sh", "chore(plan): x [GB]"],
                           cwd=wt, capture_output=True, text=True, timeout=60)
        assert "expected" not in r.stderr, f"refused from a worktree: {r.stderr}"
        assert "wt-builder" not in r.stderr
