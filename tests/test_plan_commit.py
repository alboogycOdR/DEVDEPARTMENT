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
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PLAN_COMMIT = REPO_ROOT / "scripts" / "plan_commit.sh"
PLAN_COMMIT_PS1 = REPO_ROOT / "scripts" / "plan_commit.ps1"
PLAN_GUARD = REPO_ROOT / "scripts" / "plan_guard.py"
PLAN_STAMP = REPO_ROOT / "scripts" / "plan_stamp.py"
PUSH_POLICY = REPO_ROOT / "scripts" / "push_policy.py"

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
    shutil.copyfile(PUSH_POLICY, r / "scripts" / "push_policy.py")
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


def run_commit_ps1(repo: Path, message: str, env: dict[str, str] | None = None):
    shell = powershell()
    if shell is None:
        pytest.skip("PowerShell is not available")
    return subprocess.run(
        [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
         "scripts/plan_commit.ps1", message],
        cwd=repo, env=env, capture_output=True, text=True, timeout=60,
    )


def files_in_head(repo: Path) -> set[str]:
    out = git(repo, "show", "--name-only", "--pretty=format:", "HEAD").stdout
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def test_plan_commit_respects_batch_policy(repo, tmp_path):
    remote = tmp_path / "remote.git"
    git(repo, "init", "--bare", str(remote))
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    config = repo / "autopilot.json"
    config.write_text('{"git": {"base_branch": "main", "push_policy": "batch", '
                      '"push_batch_minutes": 30}}', encoding="utf-8")
    git(repo, "add", "autopilot.json")
    git(repo, "commit", "-m", "configure batch")
    git(repo, "push")
    remote_before = git(repo, "rev-parse", "origin/main").stdout.strip()
    (repo / "PLAN.md").write_text(plan(status="claimed", by="S5"), encoding="utf-8", newline="\n")
    result = run_commit(repo, "chore(plan): claim TASK-007 [S5]")
    assert result.returncode == 0, result.stderr
    assert "deferred until batch boundary" in result.stdout
    assert git(repo, "rev-parse", "origin/main").stdout.strip() == remote_before
    assert git(repo, "rev-parse", "HEAD").stdout.strip() != remote_before


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
        first_claim = plan(status="claimed", branch="task/TASK-007-s5", by="S5")
        (repo / "PLAN.md").write_text(first_claim, encoding="utf-8", newline="\n")
        assert run_commit(repo, "chore(plan): claim TASK-007 [S5]").returncode == 0
        before = git(repo, "rev-parse", "HEAD").stdout.strip()
        # Model a dispatcher re-flipping/re-stamping an already claimed block,
        # rather than merely invoking plan_commit with a clean working tree.
        duplicate = first_claim.replace("**Status:** claimed", "**Status:** in_progress", 1)
        duplicate = duplicate.replace("**Updated_At:** 2026-08-04T00:00:00Z",
                                      "**Updated_At:** 2026-08-05T00:00:00Z", 1)
        (repo / "PLAN.md").write_text(duplicate, encoding="utf-8", newline="\n")
        result = run_commit(repo, "chore(plan): claim TASK-007 [S5]")
        assert result.returncode == 0
        assert "duplicate claim" in result.stdout.lower()
        assert git(repo, "rev-parse", "HEAD").stdout.strip() == before

    def test_duplicate_claim_of_in_progress_task_is_a_noop(self, repo):
        current = plan(status="in_progress", branch="task/TASK-007-s5", by="S5")
        (repo / "PLAN.md").write_text(current, encoding="utf-8", newline="\n")
        git(repo, "commit", "-q", "-am", "already in progress")
        # A stale claimant flips it back to claimed and re-stamps the block.
        duplicate = current.replace("**Status:** in_progress", "**Status:** claimed", 1)
        duplicate = duplicate.replace("**Updated_At:** 2026-08-04T00:00:00Z",
                                      "**Updated_At:** 2026-08-05T00:00:00Z", 1)
        (repo / "PLAN.md").write_text(duplicate, encoding="utf-8", newline="\n")
        before = git(repo, "rev-parse", "HEAD").stdout.strip()
        result = run_commit(repo, "chore(plan): claim TASK-007 [S5]")
        assert result.returncode == 0
        assert "duplicate claim" in result.stdout.lower()
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

    def test_parallel_processes_same_task_block_second_fails_without_overwrite(self, repo, tmp_path):
        """The later writer must lose loudly when both read the same task block."""
        entered_commit = tmp_path / "first-entered-commit"
        release_commit = tmp_path / "release-first-commit"
        entered_check = tmp_path / "second-entered-cas-check"
        release_check = tmp_path / "release-second-cas-check"
        first_plan = tmp_path / "first-plan.md"
        bash_env = tmp_path / "bash_env_same_block.sh"
        bash_env.write_text(
            "git() {\n"
            "  local args=\"$*\" count_file=\"$SECOND_COUNT_FILE\" count=0\n"
            "  if [[ \"$args\" == *\"commit\"* && \"$args\" == *\"PLAN.md\"* && \"${HOLD_FIRST_COMMIT:-0}\" == 1 && ! -e \"$FIRST_ENTERED\" ]]; then\n"
            "    : > \"$FIRST_ENTERED\"\n"
            "    while [[ ! -e \"$FIRST_RELEASE\" ]]; do sleep 0.05; done\n"
            "    cp \"$FIRST_PLAN\" \"$REPO_ROOT/PLAN.md\"\n"
            "  fi\n"
            "  if [[ \"$args\" == *\"rev-parse HEAD:PLAN.md\"* && \"${HOLD_SECOND_CHECK:-0}\" == 1 ]]; then\n"
            "    count=0; [[ -f \"$count_file\" ]] && count=$(<\"$count_file\")\n"
            "    count=$((count + 1)); printf '%s' \"$count\" > \"$count_file\"\n"
            "    if [[ $count -eq 2 ]]; then\n"
            "      : > \"$SECOND_ENTERED\"\n"
            "      while [[ ! -e \"$SECOND_RELEASE\" ]]; do sleep 0.05; done\n"
            "    fi\n"
            "  fi\n"
            "  command \"$REAL_GIT\" \"$@\"\n"
            "}\n",
            encoding="utf-8", newline="\n",
        )
        env_a = os.environ.copy()
        env_a.update({
            "BASH_ENV": str(bash_env), "REAL_GIT": shutil.which("git") or "git",
            "HOLD_FIRST_COMMIT": "1", "FIRST_ENTERED": str(entered_commit),
            "FIRST_RELEASE": str(release_commit), "FIRST_PLAN": str(first_plan),
            "HOLD_SECOND_CHECK": "0", "SECOND_COUNT_FILE": str(tmp_path / "first-count"),
            "SECOND_ENTERED": str(entered_check), "SECOND_RELEASE": str(release_check),
        })
        env_b = env_a.copy()
        env_b.update({
            "HOLD_FIRST_COMMIT": "0", "HOLD_SECOND_CHECK": "1",
            "SECOND_COUNT_FILE": str(tmp_path / "second-count"),
            "SECOND_ENTERED": str(entered_check), "SECOND_RELEASE": str(release_check),
        })

        desired_a = plan(status="in_progress", branch="task/TASK-007-s5", notes="writer A", by="S5")
        desired_b = plan(status="in_progress", branch="task/TASK-007-s5", notes="writer B", by="S5")
        first_plan.write_text(desired_a, encoding="utf-8", newline="\n")
        (repo / "PLAN.md").write_text(desired_a, encoding="utf-8", newline="\n")
        first = subprocess.Popen(
            [_bash(), "scripts/plan_commit.sh", "chore(plan): progress TASK-007 [S5]"],
            cwd=repo, env=env_a, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        second = None
        try:
            deadline = time.monotonic() + 15
            while not entered_commit.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert entered_commit.exists(), "first process never reached its commit barrier"

            (repo / "PLAN.md").write_text(desired_b, encoding="utf-8", newline="\n")
            second = subprocess.Popen(
                [_bash(), "scripts/plan_commit.sh", "chore(plan): progress TASK-007 [S5]"],
                cwd=repo, env=env_b, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + 15
            while not entered_check.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert entered_check.exists(), "second process never reached its CAS check"

            release_commit.write_text("go", encoding="utf-8")
            out_a, err_a = first.communicate(timeout=30)
            assert first.returncode == 0, f"first process failed: {out_a}\n{err_a}"
            release_check.write_text("go", encoding="utf-8")
            out_b, err_b = second.communicate(timeout=30)
            assert second.returncode != 0, f"second process unexpectedly succeeded: {out_b}\n{err_b}"
            assert "CAS conflict" in err_b or "refusing to overwrite" in err_b
        finally:
            release_commit.write_text("go", encoding="utf-8")
            release_check.write_text("go", encoding="utf-8")
            if first.poll() is None:
                first.communicate(timeout=30)
            if second is not None and second.poll() is None:
                second.communicate(timeout=30)

        final_plan = (repo / "PLAN.md").read_text(encoding="utf-8")
        assert "writer A" in final_plan
        assert "writer B" not in final_plan
        assert git(repo, "log", "--format=%s", "-1").stdout.count("TASK-") == 1


class TestPowerShellCasBytes:
    def test_cas_reapply_preserves_utf8_plan_bytes_and_concurrent_block(self, repo, tmp_path):
        shell = powershell()
        if shell is None:
            pytest.skip("PowerShell is not available")

        _, task_fields = plan().split("### TASK-007", 1)
        other = "### TASK-009" + task_fields.replace("S5", "GB").replace(
            "lib/a", "lib/b").replace("**Updated_By:** ORCH", "**Updated_By:** GB")
        baseline = plan() + "\n" + other
        (repo / "PLAN.md").write_text(baseline, encoding="utf-8", newline="\n")
        git(repo, "commit", "-q", "-am", "seed two blocks")

        entered = tmp_path / "ps-cas-check-entered"
        release = tmp_path / "ps-cas-check-release"
        counter = tmp_path / "ps-cas-check-count"
        hook = tmp_path / "git_hook.py"
        hook.write_text(
            "import os, subprocess, sys, time\n"
            "args = sys.argv[1:]\n"
            "if args[-2:] == ['rev-parse', 'HEAD:PLAN.md']:\n"
            "    count_path = os.environ['CAS_COUNT']\n"
            "    try: count = int(open(count_path, encoding='ascii').read())\n"
            "    except FileNotFoundError: count = 0\n"
            "    count += 1\n"
            "    open(count_path, 'w', encoding='ascii').write(str(count))\n"
            "    if count == 2:\n"
            "        open(os.environ['CAS_ENTERED'], 'w').close()\n"
            "        while not os.path.exists(os.environ['CAS_RELEASE']): time.sleep(0.05)\n"
            "p = subprocess.run([os.environ['REAL_GIT'], *args], stdout=subprocess.PIPE, stderr=subprocess.PIPE)\n"
            "sys.stdout.buffer.write(p.stdout); sys.stderr.buffer.write(p.stderr); sys.exit(p.returncode)\n",
            encoding="utf-8", newline="\n",
        )
        shim_dir = tmp_path / "git-shim"
        shim_dir.mkdir()
        (shim_dir / "git.cmd").write_text(
            '@echo off\r\n"%PYTHON_EXE%" "%GIT_HOOK%" %*\r\nexit /b %ERRORLEVEL%\r\n',
            encoding="ascii", newline="",
        )
        env = os.environ.copy()
        env.update({
            "PATH": f"{shim_dir}{os.pathsep}{env.get('PATH', '')}",
            "REAL_GIT": shutil.which("git") or "git",
            "PYTHON_EXE": sys.executable,
            "GIT_HOOK": str(hook), "CAS_ENTERED": str(entered),
            "CAS_RELEASE": str(release), "CAS_COUNT": str(counter),
        })

        desired = baseline.replace("**Status:** pending", "**Status:** claimed", 1)
        desired = desired.replace("**Branch:** —", "**Branch:** task/TASK-007-s5", 1)
        (repo / "PLAN.md").write_text(desired, encoding="utf-8", newline="\n")
        process = subprocess.Popen(
            [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
             "scripts/plan_commit.ps1", "chore(plan): claim TASK-007 [S5]"],
            cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            deadline = time.monotonic() + 20
            while not entered.exists() and time.monotonic() < deadline:
                time.sleep(0.05)
            assert entered.exists(), "PowerShell process did not reach its CAS check"

            # Land a concurrent change to the other block without touching the
            # pending worktree file that plan_commit is about to replay.
            head = git(repo, "rev-parse", "HEAD").stdout.strip()
            latest = subprocess.run(["git", "show", "HEAD:PLAN.md"], cwd=repo,
                                    capture_output=True, check=True).stdout.decode("utf-8")
            before_other, other_block = latest.split("### TASK-009", 1)
            updated = before_other + "### TASK-009" + other_block.replace(
                "**Progress_Notes:** —", "**Progress_Notes:** concurrent other task", 1)
            blob = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=repo,
                                  input=updated.encode("utf-8"), capture_output=True, check=True).stdout.decode().strip()
            index = tmp_path / "concurrent-index"
            index_env = os.environ.copy()
            index_env["GIT_INDEX_FILE"] = str(index)
            subprocess.run(["git", "read-tree", head], cwd=repo, env=index_env, check=True, capture_output=True)
            subprocess.run(["git", "update-index", "--add", "--cacheinfo", "100644", blob, "PLAN.md"],
                           cwd=repo, env=index_env, check=True, capture_output=True)
            tree = subprocess.run(["git", "write-tree"], cwd=repo, env=index_env,
                                  check=True, capture_output=True, text=True).stdout.strip()
            commit = subprocess.run(["git", "commit-tree", tree, "-p", head, "-m", "parallel TASK-009 update"],
                                    cwd=repo, check=True, capture_output=True, text=True).stdout.strip()
            subprocess.run(["git", "update-ref", "refs/heads/main", commit, head], cwd=repo, check=True,
                           capture_output=True)
        finally:
            release.write_text("go", encoding="ascii")
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, f"stdout={stdout}\nstderr={stderr}"
        final = subprocess.run(["git", "show", "HEAD:PLAN.md"], cwd=repo,
                               capture_output=True, check=True).stdout.decode("utf-8")
        assert "concurrent other task" in final
        assert "**Status:** claimed" in final
        assert "**Depends_On:** —" in final, "UTF-8 em dash must survive the PowerShell CAS snapshot"


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
