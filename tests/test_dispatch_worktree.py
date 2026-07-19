"""tests/test_dispatch_worktree.py — per-project worktree namespacing fix.

Real bug found via a live diagnostic on the actual deployment target:
dispatch.sh/.ps1 computed builder worktree paths as <project's parent
dir>/wt-grok / wt-codex — a fixed literal name with no project-name
component. Any two DEVDEPARTMENT-onboarded projects sharing a parent
directory (a common, even typical, layout) would compute the identical
worktree path. Worse, the old `if [[ ! -d "$WT" ]]` exists-check only
checked directory presence, not which repo the directory belonged to — a
stale/foreign directory at that path would be silently reused, handing a
builder session a checkout that belongs to a different project entirely.

These tests exercise the real dispatch.sh end-to-end (subprocess, real git
worktrees in temp dirs) rather than mocking anything, since the whole bug
class only exists at the level of "what path does this actually compute
and what does it actually do with a pre-existing directory there."
--dry-run is used wherever it's sufficient: worktree creation/detection is
NOT gated by --dry-run in dispatch.sh (only builder launch and prompt
display are), so a dry run still exercises every line of the fix.
"""
import subprocess
import sys
from pathlib import Path

import pytest

DISPATCH_SH = Path(__file__).resolve().parents[1] / "scripts" / "dispatch.sh"


def make_project(parent: Path, name: str, repo_root: Path) -> Path:
    """Create a minimal DEVDEPARTMENT-onboarded project (copy of scripts/
    the minimum needed for dispatch.sh to run) as parent/name, git-inited."""
    proj = parent / name
    proj.mkdir(parents=True)
    (proj / "scripts").mkdir()
    (proj / "briefings").mkdir()
    (proj / "autopilot.json").write_text('{"control": {"mode": "legacy"}}', encoding="utf-8")
    for fname in ("dispatch.sh", "validate_plan.py", "instincts.py"):
        src = repo_root / "scripts" / fname
        if src.exists():
            (proj / "scripts" / fname).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    (proj / "scripts" / "dispatch.sh").chmod(0o755)
    (proj / "briefings" / "GROK_BUILD_BRIEFING.md").write_text("briefing", encoding="utf-8")
    (proj / "briefings" / "CODEX_BRIEFING.md").write_text("briefing", encoding="utf-8")
    (proj / "PLAN.md").write_text(
        "---\nplan_version: 4.5\nlast_updated: 2026-07-20T00:00:00Z\noverall_status: in_progress\n---\n",
        encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=proj, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=proj, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=proj, check=True)
    subprocess.run(["git", "add", "-A"], cwd=proj, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=proj, check=True)
    return proj


def run_dispatch(proj: Path, builder: str = "grok", dry_run: bool = True):
    args = ["bash", "scripts/dispatch.sh", builder]
    if dry_run:
        args.append("--dry-run")
    return subprocess.run(args, cwd=proj, capture_output=True, text=True, timeout=30)


REPO_ROOT = Path(__file__).resolve().parents[1]


class TestWorktreeNamespacing:
    def test_worktree_path_includes_project_name(self, tmp_path):
        proj = make_project(tmp_path, "projectA", REPO_ROOT)
        result = run_dispatch(proj)
        assert "wt-grok-projectA" in result.stdout, result.stdout + result.stderr

    def test_two_sibling_projects_get_distinct_paths(self, tmp_path):
        proj_a = make_project(tmp_path, "projectA", REPO_ROOT)
        proj_b = make_project(tmp_path, "projectB", REPO_ROOT)
        result_a = run_dispatch(proj_a)
        result_b = run_dispatch(proj_b)
        assert "wt-grok-projectA" in result_a.stdout
        assert "wt-grok-projectB" in result_b.stdout
        assert "wt-grok-projectA" not in result_b.stdout
        assert "wt-grok-projectB" not in result_a.stdout

    def test_codex_builder_also_namespaced(self, tmp_path):
        proj = make_project(tmp_path, "projectA", REPO_ROOT)
        result = run_dispatch(proj, builder="codex")
        assert "wt-codex-projectA" in result.stdout, result.stdout + result.stderr


class TestForeignDirectorySafetyNet:
    def test_foreign_directory_at_expected_path_is_rejected(self, tmp_path):
        proj = make_project(tmp_path, "projectC", REPO_ROOT)
        # A plain directory, NOT created via `git worktree add` — simulates
        # any stray folder that happens to occupy the expected path.
        foreign = tmp_path / "wt-grok-projectC"
        foreign.mkdir()
        (foreign / "not_a_worktree.txt").write_text("stray", encoding="utf-8")

        result = run_dispatch(proj, dry_run=False)
        assert result.returncode == 1
        assert "not a registered worktree" in result.stderr
        # Must not have deleted or modified the foreign directory.
        assert (foreign / "not_a_worktree.txt").exists()

    def test_legitimate_registered_worktree_is_reused_without_error(self, tmp_path):
        proj = make_project(tmp_path, "projectD", REPO_ROOT)
        # First dry-run creates the real worktree (creation isn't gated by --dry-run).
        first = run_dispatch(proj)
        assert first.returncode == 0, first.stderr
        assert (proj.parent / "wt-grok-projectD").is_dir()
        # Second run must reuse it silently — no "Creating worktree" line, no error.
        second = run_dispatch(proj)
        assert second.returncode == 0, second.stderr
        assert "ERROR" not in second.stderr


class TestLegacyWorktreeWarning:
    def test_old_unnamespaced_worktree_warns_but_does_not_block(self, tmp_path):
        proj = make_project(tmp_path, "projectE", REPO_ROOT)
        legacy = tmp_path / "wt-grok"
        legacy.mkdir()
        (legacy / "old_leftover.txt").write_text("leftover", encoding="utf-8")

        result = run_dispatch(proj)
        assert result.returncode == 0, result.stderr
        assert "old-style unnamespaced worktree" in result.stderr
        assert "NOT being used by this dispatch" in result.stderr
        # The new namespaced worktree is still what actually got created.
        assert "wt-grok-projectE" in result.stdout

    def test_no_warning_when_no_legacy_worktree_present(self, tmp_path):
        proj = make_project(tmp_path, "projectF", REPO_ROOT)
        result = run_dispatch(proj)
        assert "old-style unnamespaced worktree" not in result.stderr


class TestDryRunMakesNoUnexpectedWrites:
    def test_dry_run_still_creates_the_real_worktree(self, tmp_path):
        """Documented, deliberate existing behavior (unchanged by this fix):
        worktree creation is NOT gated by --dry-run, only the builder launch
        and prompt display are. This test locks in that this fix didn't
        accidentally change that."""
        proj = make_project(tmp_path, "projectG", REPO_ROOT)
        assert not (tmp_path / "wt-grok-projectG").exists()
        run_dispatch(proj, dry_run=True)
        assert (tmp_path / "wt-grok-projectG").is_dir()
