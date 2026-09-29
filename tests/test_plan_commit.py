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
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN_COMMIT = REPO_ROOT / "scripts" / "plan_commit.sh"
PLAN_COMMIT_PS1 = REPO_ROOT / "scripts" / "plan_commit.ps1"
PLAN_GUARD = REPO_ROOT / "scripts" / "plan_guard.py"
PLAN_STAMP = REPO_ROOT / "scripts" / "plan_stamp.py"

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
    shutil.copyfile(PLAN_COMMIT_PS1, r / "scripts" / "plan_commit.ps1")
    (r / "scripts" / "plan_commit.sh").chmod(0o755)
    shutil.copyfile(PLAN_GUARD, r / "scripts" / "plan_guard.py")
    shutil.copyfile(PLAN_STAMP, r / "scripts" / "plan_stamp.py")
    (r / "PLAN.md").write_text(plan(), encoding="utf-8", newline="\n")
    (r / "autopilot.json").write_text('{"git": {"base_branch": "main"}}',
                                      encoding="utf-8", newline="\n")
    git(r, "init", "-q", "-b", "main")
    git(r, "config", "user.email", "t@example.com")
    git(r, "config", "user.name", "T")
    git(r, "config", "core.autocrlf", "false")
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


def powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


def run_commit_ps1(repo: Path, message: str):
    shell = powershell()
    if shell is None:
        pytest.skip("PowerShell is not available")
    return subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         "scripts/plan_commit.ps1", message],
        cwd=repo, capture_output=True, text=True, timeout=60,
    )


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

    def test_duplicate_claim_is_a_noop_without_a_commit(self, repo):
        (repo / "PLAN.md").write_text(plan(status="claimed", by="S5"), encoding="utf-8", newline="\n")
        assert run_commit(repo, "chore(plan): claim TASK-007 [S5]").returncode == 0
        before = git(repo, "rev-parse", "HEAD").stdout.strip()
        result = run_commit(repo, "chore(plan): claim TASK-007 [S5]")
        assert result.returncode == 0
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

    def test_parallel_processes_commit_different_task_blocks(self, repo, tmp_path):
        """Two real plan_commit processes preserve both independent edits."""
        second = plan().replace("TASK-007", "TASK-009").replace("S5", "GB").replace(
            "lib/a", "lib/b").replace("**Updated_By:** ORCH", "**Updated_By:** GB")
        baseline = plan() + "\n" + second
        (repo / "PLAN.md").write_text(baseline, encoding="utf-8", newline="\n")
        git(repo, "commit", "-q", "-am", "seed two tasks")

        entered = tmp_path / "first-entered-commit"
        release = tmp_path / "release-first-commit"
        bash_env = tmp_path / "bash_env.sh"
        bash_env.write_text(
            "git() {\n"
            "  local is_commit=0 has_plan=0 arg\n"
            "  for arg in \"$@\"; do\n"
            "    [ \"$arg\" = commit ] && is_commit=1\n"
            "    [ \"$arg\" = PLAN.md ] && has_plan=1\n"
            "  done\n"
            "  if [ \"$is_commit\" = 1 ] && [ \"$has_plan\" = 1 ] && [ \"${HOLD_FIRST_COMMIT:-0}\" = 1 ] && [ ! -e \"$HOLD_ENTERED\" ]; then\n"
            "    : > \"$HOLD_ENTERED\"\n"
            "    while [ ! -e \"$HOLD_RELEASE\" ]; do sleep 0.05; done\n"
            "  fi\n"
            "  command \"$REAL_GIT\" \"$@\"\n"
            "}\n",
            encoding="utf-8", newline="\n",
        )
        env_a = os.environ.copy()
        env_a["BASH_ENV"] = str(bash_env)
        env_a["REAL_GIT"] = shutil.which("git") or "git"
        env_a["HOLD_FIRST_COMMIT"] = "1"
        env_a["HOLD_ENTERED"] = str(entered)
        env_a["HOLD_RELEASE"] = str(release)
        env_b = env_a.copy()
        env_b["HOLD_FIRST_COMMIT"] = "0"

        desired_a = baseline.replace(
            "**Progress_Notes:** —", "**Progress_Notes:**\n- [2026-09-29T11:00:00Z] [S5] concurrent A", 1)
        (repo / "PLAN.md").write_text(desired_a, encoding="utf-8", newline="\n")
        first = subprocess.Popen(
            [_bash(), "scripts/plan_commit.sh", "chore(plan): progress TASK-007 [S5]"],
            cwd=repo, env=env_a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 15
            while not entered.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            if not entered.exists():
                release.write_text("abort", encoding="utf-8")
                out, err = first.communicate(timeout=10)
                pytest.fail(f"first process never reached the delayed commit: {out}\n{err}")

            task9 = baseline.split("### TASK-009", 1)
            desired_b = task9[0] + "### TASK-009" + task9[1].replace(
                "**Status:** pending", "**Status:** claimed", 1).replace(
                "**Branch:** —", "**Branch:** task/TASK-009-gb", 1).replace(
                "**Started_At:** —", "**Started_At:** 2026-09-29T11:00:00Z", 1)
            (repo / "PLAN.md").write_text(desired_b, encoding="utf-8", newline="\n")
            second_result = subprocess.run(
                [_bash(), "scripts/plan_commit.sh", "chore(plan): claim TASK-009 [GB]"],
                cwd=repo, env=env_b, capture_output=True, text=True, timeout=30)
            assert second_result.returncode == 0, second_result.stderr
        finally:
            release.write_text("go", encoding="utf-8")
        out, err = first.communicate(timeout=30)
        assert first.returncode == 0, f"stdout={out} stderr={err}"
        final_plan = (repo / "PLAN.md").read_text(encoding="utf-8")
        assert "concurrent A" in final_plan
        assert "**Branch:** task/TASK-009-gb" in final_plan
        assert git(repo, "log", "--format=%s", "-2").stdout.count("TASK-") == 2


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
        r = subprocess.run([_bash(), "scripts/plan_commit.sh", "chore(plan): claim TASK-007 [S5]"],
                           cwd=wt, capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f"stdout={r.stdout} stderr={r.stderr}"
        # The commit must exist on the MAIN checkout's integration branch...
        log = git(repo, "log", "--oneline", "-1").stdout
        assert "claim TASK-007 [S5]" in log
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


class TestClockStampedUpdatedAt:
    """Both platform mirrors replace only unsafe timestamps in changed blocks."""

    @staticmethod
    def _two_blocks(timestamp: str | None) -> str:
        changed = plan(status="claimed", by="S5")
        if timestamp is None:
            changed = changed.replace("**Updated_At:** 2026-08-04T00:00:00Z\n", "")
        else:
            changed = changed.replace("**Updated_At:** 2026-08-04T00:00:00Z", f"**Updated_At:** {timestamp}")
        return changed + """
### TASK-009
**Title:** Untouched task
**Status:** pending
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
**Updated_At:** 2099-01-01T00:00:00Z
"""

    @staticmethod
    def _assert_stamped(repo: Path, before: datetime):
        content = (repo / "PLAN.md").read_text(encoding="utf-8")
        changed, untouched = content.split("### TASK-009", 1)
        value = next(line for line in changed.splitlines() if line.startswith("**Updated_At:**"))
        stamped = datetime.strptime(value.removeprefix("**Updated_At:** "), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        assert before <= stamped <= datetime.now(timezone.utc)
        assert "**Updated_At:** 2099-01-01T00:00:00Z" in untouched

    @pytest.mark.parametrize("unsafe", [None, "not-a-timestamp", "9999-01-01T00:00:00Z", "2000-01-01T00:00:00Z"])
    def test_shell_stamps_missing_invalid_future_and_stale_values(self, repo, unsafe):
        baseline = self._two_blocks("2026-08-04T00:00:00Z").replace("**Status:** claimed", "**Status:** pending", 1)
        (repo / "PLAN.md").write_text(baseline, encoding="utf-8", newline="\n")
        git(repo, "commit", "-q", "-am", "add untouched block")
        (repo / "PLAN.md").write_text(self._two_blocks(unsafe), encoding="utf-8", newline="\n")
        before = datetime.now(timezone.utc).replace(microsecond=0)
        result = run_commit(repo, "chore(plan): claim TASK-007 [S5]")
        assert result.returncode == 0, result.stderr
        self._assert_stamped(repo, before)

    @pytest.mark.parametrize("unsafe", [None, "not-a-timestamp", "9999-01-01T00:00:00Z", "2000-01-01T00:00:00Z"])
    def test_powershell_stamps_missing_invalid_future_and_stale_values(self, repo, unsafe):
        baseline = self._two_blocks("2026-08-04T00:00:00Z").replace("**Status:** claimed", "**Status:** pending", 1)
        (repo / "PLAN.md").write_text(baseline, encoding="utf-8", newline="\n")
        git(repo, "commit", "-q", "-am", "add untouched block")
        (repo / "PLAN.md").write_text(self._two_blocks(unsafe), encoding="utf-8", newline="\n")
        before = datetime.now(timezone.utc).replace(microsecond=0)
        result = run_commit_ps1(repo, "chore(plan): claim TASK-007 [S5]")
        assert result.returncode == 0, result.stderr
        self._assert_stamped(repo, before)

    @pytest.mark.parametrize("runner", [run_commit, run_commit_ps1])
    def test_untouched_block_is_not_stamped(self, repo, runner):
        """Both mirrors stamp blocks selected from the diff, not the task roster."""
        baseline = self._two_blocks("2026-08-04T00:00:00Z").replace("**Status:** claimed", "**Status:** pending", 1)
        (repo / "PLAN.md").write_text(baseline, encoding="utf-8", newline="\n")
        git(repo, "commit", "-q", "-am", "add untouched block")
        # Only TASK-007 changes in this diff; TASK-009 remains untouched.
        (repo / "PLAN.md").write_text(self._two_blocks("2026-08-04T00:00:00Z"), encoding="utf-8", newline="\n")
        result = runner(repo, "chore(plan): claim TASK-007 [S5]")
        assert result.returncode == 0, result.stderr
        content = (repo / "PLAN.md").read_text(encoding="utf-8")
        assert "**Updated_At:** 2099-01-01T00:00:00Z" in content  # TASK-009's timestamp is preserved

    def test_fixture_suite_never_mutates_the_live_checkout(self, repo):
        """Fixtures copy the tools; no invocation points at the source checkout."""
        live_head = git(REPO_ROOT, "rev-parse", "HEAD").stdout.strip()
        live_mtime = (REPO_ROOT / "PLAN.md").stat().st_mtime_ns
        (repo / "PLAN.md").write_text(plan(status="claimed", by="S5"), encoding="utf-8", newline="\n")
        result = run_commit(repo, "chore(plan): claim TASK-007 [S5]")
        assert result.returncode == 0, result.stderr
        assert git(REPO_ROOT, "rev-parse", "HEAD").stdout.strip() == live_head
        assert (REPO_ROOT / "PLAN.md").stat().st_mtime_ns == live_mtime
