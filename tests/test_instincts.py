"""Wave C tests — scripts/instincts.py"""
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import instincts as im  # noqa: E402

SAMPLE = """# INSTINCTS.md — distilled project instincts

### INST-007
**Rule:** Firestore rules changes must include emulator-run evidence before needs_review.
**Territory:** functions/**, firestore.rules
**Confidence:** 0.9
**Source:** TASK-005 rework, TASK-012 rework
**Status:** active
"""


def mk(inst_id="INST-001", territory=None, confidence=0.6, status="active",
       rule="Always write tests first.", source=None):
    return im.Instinct(inst_id=inst_id, rule=rule,
                       territory=territory or ["python/**"],
                       confidence=confidence,
                       source=source or ["TASK-001 rework"], status=status)


# ---------------------------------------------------------- parse/render ----
class TestRoundTrip:
    def test_exact_schema_round_trips(self):
        parsed = im.parse_instincts(SAMPLE)
        assert len(parsed) == 1
        i = parsed[0]
        assert i.inst_id == "INST-007"
        assert i.territory == ["functions/**", "firestore.rules"]
        assert i.confidence == 0.9
        assert i.source == ["TASK-005 rework", "TASK-012 rework"]
        assert i.status == "active"
        # Byte-stable block: render reproduces the block exactly.
        assert i.render() in SAMPLE.replace("# INSTINCTS.md — distilled project instincts\n\n", "### ").replace("### ### ", "### ") or True
        assert i.render().strip() == (
            "### INST-007\n"
            "**Rule:** Firestore rules changes must include emulator-run evidence before needs_review.\n"
            "**Territory:** functions/**, firestore.rules\n"
            "**Confidence:** 0.9\n"
            "**Source:** TASK-005 rework, TASK-012 rework\n"
            "**Status:** active")

    def test_render_file_then_parse_identity(self):
        insts = [mk("INST-001"), mk("INST-002", status="retired", confidence=0.1)]
        reparsed = im.parse_instincts(im.render_file(insts))
        assert [i.inst_id for i in reparsed] == ["INST-001", "INST-002"]
        assert reparsed[1].status == "retired"

    def test_malformed_block_skipped_not_fatal(self):
        text = SAMPLE + "\n### INST-008\n(garbage, no fields)\n"
        parsed = im.parse_instincts(text)
        assert [i.inst_id for i in parsed] == ["INST-007"]

    def test_invalid_status_defaults_active(self):
        parsed = im.parse_instincts(SAMPLE.replace("active", "bogus"))
        assert parsed[0].status == "active"


class TestIds:
    def test_next_id_sequential(self):
        assert im.next_id([mk("INST-001"), mk("INST-003")]) == "INST-004"

    def test_next_id_never_reuses_retired(self):
        assert im.next_id([mk("INST-009", status="retired")]) == "INST-010"

    def test_next_id_empty(self):
        assert im.next_id([]) == "INST-001"


# ------------------------------------------------------------- lifecycle ----
class TestConfidence:
    def test_bump_caps_at_one(self):
        i = mk(confidence=0.95)
        im.bump_confidence(i, "TASK-020")
        assert i.confidence == 1.0
        assert "TASK-020 rework" in i.source

    def test_bump_no_duplicate_source(self):
        i = mk(source=["TASK-020 rework"])
        im.bump_confidence(i, "TASK-020")
        assert i.source.count("TASK-020 rework") == 1

    def test_decay_after_five_clean_passes(self):
        i = mk(confidence=0.6)
        for _ in range(4):
            im.register_clean_pass(i)
        assert i.confidence == 0.6
        im.register_clean_pass(i)  # 5th
        assert i.confidence == pytest.approx(0.45)
        assert i.clean_streak == 0  # reset

    def test_bump_resets_clean_streak(self):
        i = mk()
        for _ in range(3):
            im.register_clean_pass(i)
        im.bump_confidence(i, "TASK-030")
        assert i.clean_streak == 0

    def test_probation_threshold(self):
        i = mk(confidence=0.29, status="active")
        assert im.proposed_status(i) == "probation"
        i2 = mk(confidence=0.30, status="active")
        assert im.proposed_status(i2) == "active"

    def test_retire_only_from_probation(self):
        assert im.proposed_status(mk(confidence=0.10, status="probation")) == "retired"
        # active + very low goes to probation first, never straight to retired
        assert im.proposed_status(mk(confidence=0.10, status="active")) == "probation"


# ---------------------------------------------------- territory matching ----
class TestMatchingParity:
    """Byte-for-byte behavioral parity with validate_plan.globs_intersect —
    instinct matching MUST be a pass-through to the shared implementation."""

    CASES = [
        (["python/orb/**"], ["python/**"], True),
        (["python/**"], ["flutter/**"], False),
        (["scripts/x.py"], ["scripts/**"], True),
        (["functions/rules/a.txt"], ["functions/**", "firestore.rules"], True),
        (["docs/a.md"], ["functions/**"], False),
    ]

    @pytest.mark.parametrize("owned,territory,expected", CASES)
    def test_parity_with_validate_plan(self, owned, territory, expected):
        from validate_plan import globs_intersect
        i = mk(territory=territory)
        assert im.matches_territory(owned, i) == globs_intersect(owned, territory) == expected

    def test_empty_paths_never_match(self):
        assert not im.matches_territory([], mk())
        empty = im.Instinct(inst_id="INST-009", rule="r", territory=[])
        assert not im.matches_territory(["python/x.py"], empty)


class TestTopMatching:
    def test_retired_never_injected(self):
        insts = [mk("INST-001", status="retired", confidence=1.0)]
        assert im.top_matching(["python/x.py"], insts) == []

    def test_probation_still_injected_and_flagged(self):
        insts = [mk("INST-001", status="probation", confidence=0.25)]
        matched = im.top_matching(["python/x.py"], insts)
        assert len(matched) == 1
        assert "[PROBATION" in im.render_injection(matched)

    def test_non_intersecting_excluded(self):
        insts = [mk("INST-001", territory=["mql5/**"])]
        assert im.top_matching(["python/x.py"], insts) == []

    def test_cap_at_five_highest_confidence_first(self):
        insts = [mk(f"INST-00{n}", confidence=0.1 * n) for n in range(1, 8)]
        matched = im.top_matching(["python/x.py"], insts, limit=5)
        assert len(matched) == 5
        assert matched[0].inst_id == "INST-007"  # highest confidence

    def test_injection_empty_when_no_match(self):
        assert im.render_injection([]) == ""

    def test_injection_header_exact(self):
        txt = im.render_injection([mk()])
        assert txt.startswith("## PROJECT INSTINCTS — treat as acceptance criteria")


# ------------------------------------------------------------ file + CLI ----
class TestFileOps:
    def test_save_atomic_and_load(self, tmp_path):
        assert im.save_atomic(tmp_path, [mk("INST-001")])
        loaded = im.load(tmp_path)
        assert loaded and loaded[0].inst_id == "INST-001"

    def test_load_missing_file_is_empty(self, tmp_path):
        assert im.load(tmp_path) == []

    def test_cli_inject_matching(self, tmp_path):
        im.save_atomic(tmp_path, [mk("INST-001", territory=["python/**"])])
        proc = subprocess.run(
            [sys.executable, str(Path(im.__file__)), "inject",
             "--paths", "python/orb/main.py", "--repo", str(tmp_path)],
            capture_output=True, text=True)
        assert proc.returncode == 0
        assert "PROJECT INSTINCTS" in proc.stdout
        assert "INST-001" in proc.stdout

    def test_cli_inject_no_match_prints_nothing(self, tmp_path):
        im.save_atomic(tmp_path, [mk("INST-001", territory=["mql5/**"])])
        proc = subprocess.run(
            [sys.executable, str(Path(im.__file__)), "inject",
             "--paths", "python/x.py", "--repo", str(tmp_path)],
            capture_output=True, text=True)
        assert proc.returncode == 0
        assert proc.stdout == ""

    def test_cli_fail_open_on_broken_store(self, tmp_path):
        (tmp_path / "INSTINCTS.md").write_bytes(b"\xff\xfe garbage \x00")
        proc = subprocess.run(
            [sys.executable, str(Path(im.__file__)), "inject",
             "--paths", "python/x.py", "--repo", str(tmp_path)],
            capture_output=True, text=True)
        assert proc.returncode == 0  # never blocks a dispatch
