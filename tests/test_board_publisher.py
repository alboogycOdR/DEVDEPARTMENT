"""Tests for scripts/board_publisher.py — Mission Control projection of PLAN.md."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from board_publisher import build_board, merge_index, write_outputs, DEFAULT_BOARD_CFG  # noqa: E402

NOW = datetime(2026, 7, 16, 18, 0, 0, tzinfo=timezone.utc)

PLAN = """---
plan_version: 4.0
last_updated: 2026-07-16T17:00:00Z
overall_status: in_progress
orchestrator_notes: "Wave 2 running."
---
# Plan

### TASK-001
**Title:** Auth endpoints
**Status:** in_progress
**Assigned_To:** GB
**Priority:** high
**Spec_References:** specs/a.md
**Owned_Paths:** lib/auth/**
**Depends_On:** —
**Description:** d
**Acceptance_Criteria:**
- [ ] c
**Branch:** task/TASK-001-gb
**Started_At:** 2026-07-16T16:30:00Z
**Progress_Notes:**
- [2026-07-16T17:40:00Z] [GB] JWT flow done; next: refresh rotation.
**Artifacts:** lib/auth/jwt.dart
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** GB
**Updated_At:** 2026-07-16T17:40:00Z

### TASK-002
**Title:** Data layer
**Status:** done
**Assigned_To:** CX
**Priority:** critical
**Spec_References:** specs/b.md
**Owned_Paths:** lib/db/**
**Depends_On:** —
**Description:** d
**Acceptance_Criteria:**
- [x] c
**Branch:** task/TASK-002-cx
**Started_At:** 2026-07-16T12:00:00Z
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** pytest pass
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** ORCH
**Updated_At:** 2026-07-16T15:00:00Z

### TASK-003
**Title:** EXAMPLE — should be excluded
**Status:** pending
**Assigned_To:** TBD
**Priority:** low
**Spec_References:** specs/x.md
**Owned_Paths:** x/**
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
**Updated_At:** 2026-07-16T10:00:00Z

### TASK-004
**Title:** Stakeholder sign-off gate
**Status:** blocked
**Assigned_To:** GB
**Priority:** medium
**Spec_References:** specs/c.md
**Owned_Paths:** docs2/**
**Depends_On:** —
**Description:** d
**Acceptance_Criteria:**
- [ ] c
**Branch:** task/TASK-004-gb
**Started_At:** 2026-07-16T13:00:00Z
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** OTHER: awaiting sign-off
**Updated_By:** GB
**Updated_At:** 2026-07-16T13:30:00Z
"""


def make_repo(tmp_path: Path) -> Path:
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    (tmp_path / "AUTOPILOT_LOG.md").write_text(
        "\n".join(f"- [ts] line {i}" for i in range(30)), encoding="utf-8")
    return tmp_path


def test_columns_and_example_exclusion(tmp_path):
    b = build_board(make_repo(tmp_path), DEFAULT_BOARD_CFG, NOW)
    ids = {c["id"] for col in b["columns"].values() for c in col}
    assert ids == {"TASK-001", "TASK-002", "TASK-004"}  # EXAMPLE excluded
    assert [c["id"] for c in b["columns"]["in_progress"]] == ["TASK-001"]
    assert [c["id"] for c in b["columns"]["done"]] == ["TASK-002"]
    assert [c["id"] for c in b["columns"]["blocked"]] == ["TASK-004"]


def test_burndown_and_flags(tmp_path):
    b = build_board(make_repo(tmp_path), DEFAULT_BOARD_CFG, NOW)
    assert b["burndown"] == {"total": 3, "done": 1, "pct": 33}
    assert b["plan_version"] == "4.0"
    assert b["orchestrator_notes"].startswith("Wave 2")
    assert b["escalations_open"][0]["task"] == "TASK-004"


def test_heartbeat_and_active_clock(tmp_path):
    b = build_board(make_repo(tmp_path), DEFAULT_BOARD_CFG, NOW)
    card = b["columns"]["in_progress"][0]
    assert card["heartbeat_age_min"] == 20.0        # 17:40 -> 18:00
    assert card["active_min"] == 90.0               # started 16:30
    assert "refresh rotation" in card["last_note"]
    done = b["columns"]["done"][0]
    assert done["heartbeat_age_min"] is None        # only active tasks track heartbeat


def test_log_tail_capped_at_20(tmp_path):
    b = build_board(make_repo(tmp_path), DEFAULT_BOARD_CFG, NOW)
    assert len(b["log_tail"]) == 20
    assert b["log_tail"][-1].endswith("line 29")


def test_stop_file_reflected(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "STOP").write_text("")
    b = build_board(repo, DEFAULT_BOARD_CFG, NOW)
    assert b["autopilot"]["stop_file"] is True


def test_merge_index_multi_project(tmp_path):
    repo = make_repo(tmp_path)
    cfg1 = {**DEFAULT_BOARD_CFG, "project_name": "orb-terminal", "host_label": "windows"}
    cfg2 = {**DEFAULT_BOARD_CFG, "project_name": "skul", "host_label": "macbook"}
    out = tmp_path / "central"
    write_outputs(out, build_board(repo, cfg1, NOW))
    write_outputs(out, build_board(repo, cfg2, NOW))
    idx = json.loads((out / "projects.json").read_text())
    names = [p["project"] for p in idx["projects"]]
    assert names == ["orb-terminal", "skul"]                    # both machines listed
    assert (out / "orb-terminal.json").exists() and (out / "skul.json").exists()
    # re-publishing same project replaces, not duplicates
    write_outputs(out, build_board(repo, cfg1, NOW))
    idx = json.loads((out / "projects.json").read_text())
    assert [p["project"] for p in idx["projects"]] == ["orb-terminal", "skul"]
    entry = [p for p in idx["projects"] if p["project"] == "orb-terminal"][0]
    assert entry["host"] == "windows" and entry["blocked"] == 1 and entry["in_flight"] == 1


def test_frontend_shipped_alongside_data(tmp_path):
    repo = make_repo(tmp_path)
    out = tmp_path / "central"
    write_outputs(out, build_board(repo, DEFAULT_BOARD_CFG, NOW))
    pack_html = Path(__file__).resolve().parents[1] / "board" / "index.html"
    if pack_html.exists():
        assert (out / "index.html").exists()


def test_empty_plan_is_safe(tmp_path):
    (tmp_path / "PLAN.md").write_text("---\nplan_version: 0.1\nlast_updated: 2026-07-16T00:00:00Z\noverall_status: not_started\n---\n", encoding="utf-8")
    b = build_board(tmp_path, DEFAULT_BOARD_CFG, NOW)
    assert b["burndown"] == {"total": 0, "done": 0, "pct": 0}
    assert all(v == [] for v in b["columns"].values())


def test_learning_key_counts_instincts_and_pending_amendments(tmp_path):
    """Wave C: board surfaces active/probation instinct counts and pending
    AMEND proposals without ever invoking distiller.py."""
    repo = make_repo(tmp_path)
    (repo / "INSTINCTS.md").write_text(
        "### INST-001\n**Rule:** r1\n**Territory:** a/**\n**Confidence:** 0.7\n"
        "**Source:** TASK-001\n**Status:** active\n\n"
        "### INST-002\n**Rule:** r2\n**Territory:** b/**\n**Confidence:** 0.2\n"
        "**Source:** TASK-002\n**Status:** probation\n\n"
        "### INST-003\n**Rule:** r3\n**Territory:** c/**\n**Confidence:** 0.1\n"
        "**Source:** TASK-003\n**Status:** retired\n",
        encoding="utf-8")
    amend_dir = repo / ".devteam" / "pending_amendments"
    amend_dir.mkdir(parents=True)
    (amend_dir / "AMEND-001.md").write_text(
        "# AMEND-001\n**Proposed:** 2026-07-16T00:00:00Z\n**Status:** pending\n\nbody\n",
        encoding="utf-8")
    (amend_dir / "AMEND-002.md").write_text(
        "# AMEND-002\n**Proposed:** 2026-07-15T00:00:00Z\n**Status:** approved\n\nbody\n",
        encoding="utf-8")
    b = build_board(repo, DEFAULT_BOARD_CFG, NOW)
    assert b["learning"] == {
        "active_instincts": 1,
        "probation_instincts": 1,
        "pending_amendments": 1,
    }


def test_learning_key_empty_when_no_instincts_file(tmp_path):
    repo = make_repo(tmp_path)
    b = build_board(repo, DEFAULT_BOARD_CFG, NOW)
    assert b["learning"] == {"active_instincts": 0, "probation_instincts": 0, "pending_amendments": 0}
