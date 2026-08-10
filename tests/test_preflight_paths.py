"""tests/test_preflight_paths.py — the Owned_Paths pre-flight check.

This script exists because the c8b9872 filesystem-check requirement was
missed three times by the same unit across three sessions — the signature
of a mechanism problem, not a discipline one. A prose attestation is
unfalsifiable; generated output is evidence. Its value therefore depends
entirely on the output being ACCURATE about what is on disk, especially
the surprises (a path assumed to exist that does not, a glob matching
nothing). That accuracy is what these tests pin down.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = REPO_ROOT / "scripts" / "preflight_paths.py"


def plan_with(owned: str, task_id: str = "TASK-019") -> str:
    return f"""---
plan_version: 4.7
last_updated: 2026-08-04T00:00:00Z
overall_status: in_progress
---

### {task_id}
**Title:** T
**Status:** pending
**Assigned_To:** GB
**Priority:** high
**Spec_References:** specs/a.md
**Owned_Paths:** {owned}
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
**Updated_At:** 2026-08-04T00:00:00Z
"""


@pytest.fixture()
def proj(tmp_path: Path) -> Path:
    (tmp_path / "lib" / "auth").mkdir(parents=True)
    (tmp_path / "lib" / "auth" / "jwt.dart").write_text("a\nb\nc\n", encoding="utf-8")
    (tmp_path / "lib" / "auth" / "refresh.dart").write_text("x\n", encoding="utf-8")
    (tmp_path / "test").mkdir()
    return tmp_path


def run(proj: Path, task="TASK-019"):
    return subprocess.run([sys.executable, str(PREFLIGHT), task, "--repo", str(proj)],
                          capture_output=True, text=True, timeout=30)


class TestClassification:
    def test_existing_file_reported_with_size(self, proj):
        (proj / "PLAN.md").write_text(plan_with("lib/auth/jwt.dart"), encoding="utf-8")
        r = run(proj)
        assert r.returncode == 0, r.stderr
        assert "FILE" in r.stdout and "3 line(s)" in r.stdout

    def test_existing_directory_lists_children(self, proj):
        (proj / "PLAN.md").write_text(plan_with("lib/auth"), encoding="utf-8")
        r = run(proj)
        assert "DIR" in r.stdout
        assert "jwt.dart" in r.stdout and "refresh.dart" in r.stdout

    def test_glob_reports_matched_files(self, proj):
        (proj / "PLAN.md").write_text(plan_with("lib/auth/*.dart"), encoding="utf-8")
        r = run(proj)
        assert "GLOB" in r.stdout and "2 file(s)" in r.stdout

    def test_glob_matching_nothing_is_flagged_as_new_territory(self, proj):
        """The surprise this exists to surface: a builder assumed a test file
        existed. It must say so plainly rather than silently listing zero."""
        (proj / "PLAN.md").write_text(plan_with("test/auth/*.dart"), encoding="utf-8")
        r = run(proj)
        assert "matches nothing yet" in r.stdout

    def test_missing_path_with_existing_parent(self, proj):
        (proj / "PLAN.md").write_text(plan_with("lib/auth/new_file.dart"), encoding="utf-8")
        r = run(proj)
        assert "NEW" in r.stdout and "parent lib/auth/ exists" in r.stdout

    def test_missing_path_with_missing_parent(self, proj):
        (proj / "PLAN.md").write_text(plan_with("lib/nope/deeper/x.dart"), encoding="utf-8")
        r = run(proj)
        assert "NEW" in r.stdout
        assert "does NOT exist either" in r.stdout

    def test_multiple_entries_all_reported(self, proj):
        (proj / "PLAN.md").write_text(
            plan_with("lib/auth/jwt.dart, test/auth/**, lib/missing.dart"), encoding="utf-8")
        r = run(proj)
        assert "3 entr" in r.stdout
        assert "FILE" in r.stdout and "NEW" in r.stdout

    def test_output_paths_use_forward_slashes(self, proj):
        """The evidence is pasted into PLAN.md; a backslash path has already
        survived a round trip as a literal tab (TASK-024). This test only
        proves anything on Windows (or with a Windows-shaped tmp_path) --
        v4.8 fix found live: the HEADER line ("Owned_Paths inspected in
        {root}") interpolated a raw Path object without .as_posix(), leaking
        backslashes on Windows even though every per-entry describe() line
        was already correctly normalized. Assert on both, explicitly."""
        (proj / "PLAN.md").write_text(plan_with("lib/nope/deeper/x.dart"), encoding="utf-8")
        r = run(proj)
        assert "\\" not in r.stdout, r.stdout

class TestContract:
    def test_prints_the_paste_instruction(self, proj):
        (proj / "PLAN.md").write_text(plan_with("lib/auth/jwt.dart"), encoding="utf-8")
        assert "c8b9872" in run(proj).stdout

    def test_bare_number_is_normalised_to_task_id(self, proj):
        (proj / "PLAN.md").write_text(plan_with("lib/auth/jwt.dart"), encoding="utf-8")
        r = subprocess.run([sys.executable, str(PREFLIGHT), "019", "--repo", str(proj)],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode == 0
        assert "TASK-019" in r.stdout

    def test_unknown_task_exits_nonzero_with_a_clear_message(self, proj):
        (proj / "PLAN.md").write_text(plan_with("lib/auth/jwt.dart"), encoding="utf-8")
        r = run(proj, task="TASK-999")
        assert r.returncode != 0
        assert "not found" in (r.stderr + r.stdout)

    def test_missing_plan_md_exits_nonzero(self, tmp_path):
        r = subprocess.run([sys.executable, str(PREFLIGHT), "TASK-019", "--repo", str(tmp_path)],
                           capture_output=True, text=True, timeout=30)
        assert r.returncode != 0
        assert "no PLAN.md" in (r.stderr + r.stdout)

    def test_em_dash_owned_paths_yields_zero_entries(self, proj):
        (proj / "PLAN.md").write_text(plan_with("—"), encoding="utf-8")
        r = run(proj)
        assert r.returncode == 0
        assert "0 entr" in r.stdout
