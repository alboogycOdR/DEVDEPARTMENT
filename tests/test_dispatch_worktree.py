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
import os
import subprocess
import sys
import time
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
    (proj / "autopilot.json").write_text('{"control": {"mode": "legacy"}}', encoding="utf-8", newline="\n")
    for fname in ("dispatch.sh", "dispatch.ps1", "validate_plan.py", "instincts.py", "builder_registry.py"):
        src = repo_root / "scripts" / fname
        if src.exists():
            # Byte-exact copy, NOT read_text()/write_text(). write_text()'s
            # default newline handling translates "\n" to the OS's native
            # line ending on write (CRLF on Windows) even when the source
            # bytes and the read were pure LF — which corrupts dispatch.sh's
            # `set -euo pipefail` line and breaks every test that shells out
            # to the copy. Confirmed via a live diagnostic during real
            # onboarding: the shipped scripts/dispatch.sh itself is clean
            # LF-only and works correctly when run directly; only this
            # fixture's copy step reintroduced the corruption.
            (proj / "scripts" / fname).write_bytes(src.read_bytes())
    (proj / "scripts" / "dispatch.sh").chmod(0o755)
    (proj / "briefings" / "GROK_BUILD_BRIEFING.md").write_text("briefing", encoding="utf-8", newline="\n")
    (proj / "briefings" / "CODEX_BRIEFING.md").write_text("briefing", encoding="utf-8", newline="\n")
    (proj / "PLAN.md").write_text(
        "---\nplan_version: 4.5\nlast_updated: 2026-07-20T00:00:00Z\noverall_status: in_progress\n---\n",
        encoding="utf-8", newline="\n")
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


def run_dispatch_ps1(proj: Path, builder: str = "grok", dry_run: bool = True):
    args = [
        "powershell",
        "-NoProfile",
        "-ExecutionPolicy", "Bypass",
        "-File", str(proj / "scripts" / "dispatch.ps1"),
        "-Builder", builder,
    ]
    if dry_run:
        args.append("-DryRun")
    return subprocess.run(args, cwd=proj, capture_output=True, text=True, timeout=60)


def _combined(result):
    return (result.stdout or "") + (result.stderr or "")


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
        (foreign / "not_a_worktree.txt").write_text("stray", encoding="utf-8", newline="\n")

        result = run_dispatch(proj, dry_run=False)
        assert result.returncode == 1
        assert "not a registered worktree" in result.stderr
        assert "Inspect it manually" in result.stderr
        assert "git worktree list" in result.stderr
        # Must not have deleted or modified the foreign directory.
        assert (foreign / "not_a_worktree.txt").exists()
        assert "Reclaimed" not in _combined(result)

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


class TestEmptyHuskReclaim:
    """R-A (specs/L2_DISPATCH_RESILIENCE.md §2): an empty unregistered
    directory at the worktree path is a leftover husk, not someone's work.
    These cases fail against pre-fix dispatch (it refuses the empty dir)."""

    REFUSAL_MARKERS = (
        "not a registered worktree",
        "Inspect it manually",
        "git worktree list",
    )

    def test_empty_unregistered_directory_is_reclaimed(self, tmp_path):
        proj = make_project(tmp_path, "projectHusk", REPO_ROOT)
        husk = tmp_path / "wt-grok-projectHusk"
        husk.mkdir()
        assert list(husk.iterdir()) == []

        result = run_dispatch(proj)
        combined = _combined(result)
        assert result.returncode == 0, combined
        assert combined.count("Reclaimed empty unregistered directory") == 1
        assert "leftover husk" in combined
        assert "Creating worktree" in combined
        assert "ERROR" not in result.stderr
        # git worktree add turned the husk into a real linked worktree.
        assert (husk / ".git").exists()

    def test_directory_with_one_file_keeps_verbatim_refusal(self, tmp_path):
        proj = make_project(tmp_path, "projectFile", REPO_ROOT)
        foreign = tmp_path / "wt-grok-projectFile"
        foreign.mkdir()
        (foreign / "notes.txt").write_text("keep me", encoding="utf-8", newline="\n")

        result = run_dispatch(proj)
        combined = _combined(result)
        assert result.returncode == 1, combined
        for marker in self.REFUSAL_MARKERS:
            assert marker in result.stderr
        assert "Reclaimed" not in combined
        assert (foreign / "notes.txt").read_text(encoding="utf-8") == "keep me"

    def test_dotfile_only_directory_counts_as_nonempty_and_is_refused(self, tmp_path):
        proj = make_project(tmp_path, "projectDot", REPO_ROOT)
        foreign = tmp_path / "wt-grok-projectDot"
        foreign.mkdir()
        (foreign / ".keep").write_text("hidden work", encoding="utf-8", newline="\n")

        result = run_dispatch(proj)
        combined = _combined(result)
        assert result.returncode == 1, combined
        for marker in self.REFUSAL_MARKERS:
            assert marker in result.stderr
        assert "Reclaimed" not in combined
        assert (foreign / ".keep").read_text(encoding="utf-8") == "hidden work"

    def test_failed_removal_refuses_cleanly_without_partial_state(self, tmp_path):
        """§6: show the lock/removal-failure actually occurring, not just
        the happy path. Windows: a live cwd handle. POSIX: parent not writable."""
        proj = make_project(tmp_path, "projectLock", REPO_ROOT)
        husk = tmp_path / "wt-grok-projectLock"
        husk.mkdir()

        holder = None
        parent_mode = None
        try:
            if os.name == "nt":
                holder = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(120)"],
                    cwd=str(husk),
                )
                time.sleep(0.3)
            else:
                parent_mode = tmp_path.stat().st_mode
                tmp_path.chmod(0o555)

            result = run_dispatch(proj)
            combined = _combined(result)
            if result.returncode == 0 and "Reclaimed" in combined:
                pytest.skip(
                    "platform allowed rmdir of a held/empty directory; "
                    "lock-refusal path not exercisable here"
                )
            assert result.returncode == 1, combined
            for marker in self.REFUSAL_MARKERS:
                assert marker in result.stderr
            assert "Reclaimed" not in combined
            assert husk.is_dir()
            assert list(husk.iterdir()) == []
        finally:
            if parent_mode is not None:
                tmp_path.chmod(parent_mode)
            if holder is not None:
                holder.terminate()
                try:
                    holder.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    holder.kill()
                    holder.wait(timeout=5)

    def test_empty_husk_reclaim_is_identical_on_dispatch_ps1(self, tmp_path):
        proj = make_project(tmp_path, "projectHuskPs", REPO_ROOT)
        husk = tmp_path / "wt-grok-projectHuskPs"
        husk.mkdir()

        result = run_dispatch_ps1(proj)
        combined = _combined(result)
        assert result.returncode == 0, combined
        assert combined.count("Reclaimed empty unregistered directory") == 1
        assert "leftover husk" in combined
        assert "Creating worktree" in combined
        assert (husk / ".git").exists()

    def test_dotfile_only_directory_is_refused_by_dispatch_ps1(self, tmp_path):
        proj = make_project(tmp_path, "projectDotPs", REPO_ROOT)
        foreign = tmp_path / "wt-grok-projectDotPs"
        foreign.mkdir()
        (foreign / ".keep").write_text("hidden work", encoding="utf-8", newline="\n")

        result = run_dispatch_ps1(proj)
        combined = _combined(result)
        assert result.returncode != 0, combined
        assert "not a registered worktree" in combined
        assert "Reclaimed" not in combined
        assert (foreign / ".keep").read_text(encoding="utf-8") == "hidden work"

    def test_dispatch_scripts_parse(self):
        sh = subprocess.run(
            ["bash", "-n", "scripts/dispatch.sh"],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=15,
        )
        assert sh.returncode == 0, sh.stderr
        ps = subprocess.run(
            [
                "powershell", "-NoProfile", "-Command",
                "$errs = $null; "
                "[void][System.Management.Automation.PSParser]::Tokenize("
                "(Get-Content -Raw -LiteralPath '"
                + str(REPO_ROOT / "scripts" / "dispatch.ps1").replace("'", "''")
                + "'), [ref]$errs); "
                "if ($errs -and $errs.Count) { $errs | ForEach-Object { $_.Message }; exit 1 }",
            ],
            capture_output=True, text=True, timeout=30,
        )
        assert ps.returncode == 0, ps.stdout + ps.stderr


class TestLegacyWorktreeWarning:
    def test_old_unnamespaced_worktree_warns_but_does_not_block(self, tmp_path):
        proj = make_project(tmp_path, "projectE", REPO_ROOT)
        legacy = tmp_path / "wt-grok"
        legacy.mkdir()
        (legacy / "old_leftover.txt").write_text("leftover", encoding="utf-8", newline="\n")

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


S5B_REGISTRY = {
    "builders": {
        "active": ["GB", "CX", "S5", "S5B"],
        "defined": {
            "GB": {"cli": "grok", "worktree_suffix": "grok", "branch_suffix": "gb",
                    "briefing": "briefings/GROK_BUILD_BRIEFING.md"},
            "CX": {"cli": "codex", "model": "gpt-5.6-sol", "worktree_suffix": "codex",
                    "branch_suffix": "cx", "briefing": "briefings/CODEX_BRIEFING.md",
                    "usage_provider": "codex"},
            "S5": {"cli": "claude", "model": "claude-sonnet-5", "worktree_suffix": "s5",
                    "branch_suffix": "s5", "briefing": "briefings/S5_BUILD_BRIEFING.md",
                    "auto_loads_ambient_context": True, "usage_provider": "claude"},
            "S5B": {"cli": "claude", "model": "claude-sonnet-5",
                     "auth": {"mode": "config_dir", "value": "~/.claude-s5b"},
                     "worktree_suffix": "s5b", "branch_suffix": "s5b",
                     "briefing": "briefings/S5_BUILD_BRIEFING.md",
                     "auto_loads_ambient_context": True, "usage_provider": "claude:s5b"},
        },
    },
    "control": {"mode": "legacy"},
}


class TestRegistryDrivenDispatch:
    """v4.7: the same-cli-different-unit scenario the registry redesign
    exists to support — S5 and S5B, both cli=claude, must resolve to
    distinct worktrees and distinct auth without any script edits."""

    def _registry_project(self, tmp_path, name):
        import json
        proj = make_project(tmp_path, name, REPO_ROOT)
        (proj / "autopilot.json").write_text(json.dumps(S5B_REGISTRY),
                                             encoding="utf-8", newline="\n")
        return proj

    def test_argv_unit_id_s5b_gets_own_worktree(self, tmp_path):
        proj = self._registry_project(tmp_path, "projectR")
        result = run_dispatch(proj, builder="S5B")
        assert "wt-s5b-projectR" in result.stdout, result.stdout + result.stderr
        assert "wt-s5-projectR" not in result.stdout

    def test_argv_unit_id_s5_distinct_from_s5b(self, tmp_path):
        proj = self._registry_project(tmp_path, "projectR")
        r_s5 = run_dispatch(proj, builder="S5")
        r_s5b = run_dispatch(proj, builder="S5B")
        assert "wt-s5-projectR" in r_s5.stdout
        assert "wt-s5b-projectR" in r_s5b.stdout

    def test_legacy_cli_argv_shim_still_resolves_to_s5(self, tmp_path):
        """`dispatch.sh claude` keeps meaning S5 (first active claude unit) —
        every pre-v4.7 caller keeps working."""
        proj = self._registry_project(tmp_path, "projectR")
        result = run_dispatch(proj, builder="claude")
        assert "wt-s5-projectR" in result.stdout, result.stdout + result.stderr

    def test_s5b_launch_line_scopes_claude_config_dir(self, tmp_path):
        proj = self._registry_project(tmp_path, "projectR")
        result = run_dispatch(proj, builder="S5B")
        assert "CLAUDE_CONFIG_DIR=" in result.stdout
        assert "scoped to this launch" in result.stdout
        # And S5 (auth mode default) must NOT get the env override:
        r_s5 = run_dispatch(proj, builder="S5")
        assert "CLAUDE_CONFIG_DIR=" not in r_s5.stdout

    def test_unknown_unit_fails_closed(self, tmp_path):
        proj = self._registry_project(tmp_path, "projectR")
        result = run_dispatch(proj, builder="ZZ")
        assert result.returncode == 1
        assert "refusing to dispatch" in result.stderr

    def test_flat_array_project_still_dispatches_gb(self, tmp_path):
        """A pre-v4.7 project (flat builders array in the fixture's default
        autopilot.json) keeps working unchanged — the dual-shape guarantee."""
        proj = make_project(tmp_path, "projectL", REPO_ROOT)
        result = run_dispatch(proj)  # grok, legacy argv, flat-array config
        assert "wt-grok-projectL" in result.stdout, result.stdout + result.stderr


class TestBuilderIdentity:
    """v4.8: identity=agent replaces the injection-shaped IDENTITY OVERRIDE
    preamble with a real agent definition via `--agent`. Default stays
    `preamble` (byte-identical to v4.7) until --agent is live-verified."""

    def _proj(self, tmp_path, name, identity=None):
        import json, copy
        reg = copy.deepcopy(S5B_REGISTRY)
        if identity:
            reg["builders"]["defined"]["S5"]["identity"] = identity
        proj = make_project(tmp_path, name, REPO_ROOT)
        (proj / "autopilot.json").write_text(json.dumps(reg), encoding="utf-8", newline="\n")
        return proj

    def test_default_is_preamble_and_unchanged(self, tmp_path):
        proj = self._proj(tmp_path, "projectI")
        r = run_dispatch(proj, builder="S5")
        assert "IDENTITY OVERRIDE" in r.stdout, r.stdout + r.stderr
        assert "--agent" not in r.stdout

    def test_agent_mode_uses_agent_flag_and_drops_the_preamble(self, tmp_path):
        proj = self._proj(tmp_path, "projectI", identity="agent")
        r = run_dispatch(proj, builder="S5")
        assert "--agent devteam-builder" in r.stdout, r.stdout + r.stderr
        assert "IDENTITY OVERRIDE" not in r.stdout, "the whole point: no injection-shaped preamble"

    def test_unknown_identity_value_falls_back_to_preamble(self, tmp_path):
        proj = self._proj(tmp_path, "projectI", identity="nonsense")
        r = run_dispatch(proj, builder="S5")
        assert "IDENTITY OVERRIDE" in r.stdout
        assert "--agent" not in r.stdout

    def test_non_claude_units_are_unaffected_by_agent_mode(self, tmp_path):
        """GB/CX don't auto-load CLAUDE.md, so identity is moot for them and
        --agent (a claude-only flag) must never leak onto their command."""
        import json, copy
        reg = copy.deepcopy(S5B_REGISTRY)
        reg["builders"]["defined"]["GB"]["identity"] = "agent"
        proj = make_project(tmp_path, "projectI", REPO_ROOT)
        (proj / "autopilot.json").write_text(json.dumps(reg), encoding="utf-8", newline="\n")
        r = run_dispatch(proj, builder="GB")
        assert "--agent" not in r.stdout
        assert "IDENTITY OVERRIDE" not in r.stdout
