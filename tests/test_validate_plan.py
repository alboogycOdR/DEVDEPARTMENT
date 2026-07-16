"""Tests for scripts/validate_plan.py — Coordination Protocol enforcement."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from validate_plan import validate, globs_intersect  # noqa: E402

FM = """---
plan_version: 1.0
last_updated: 2026-07-12T10:00:00Z
overall_status: in_progress
---
"""


def task_block(
    tid="TASK-001", status="pending", assignee="GB", prio="high",
    owned="src/auth/**", branch="—", started="—", evidence="—",
    blocked="—", deps="—", upd_by="ORCH", upd_at="2026-07-12T10:00:00Z",
):
    return f"""
### {tid}
**Title:** Test task
**Status:** {status}
**Assigned_To:** {assignee}
**Priority:** {prio}
**Spec_References:** specs/x.md
**Owned_Paths:** {owned}
**Depends_On:** {deps}
**Description:** Do the thing.
**Acceptance_Criteria:**
- [ ] It works
**Branch:** {branch}
**Started_At:** {started}
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** {evidence}
**Review_Findings:** —
**Blocked_Reason:** {blocked}
**Updated_By:** {upd_by}
**Updated_At:** {upd_at}
"""


def test_valid_pending_plan_passes():
    rep = validate(FM + task_block())
    assert rep.ok, rep.errors


def test_missing_frontmatter_fails():
    rep = validate(task_block())
    assert any("FRONTMATTER" in e for e in rep.errors)


def test_illegal_status_fails():
    rep = validate(FM + task_block(status="doing_stuff"))
    assert any("illegal Status" in e for e in rep.errors)


def test_illegal_assignee_fails():
    rep = validate(FM + task_block(assignee="GEMINI"))
    assert any("Assigned_To" in e for e in rep.errors)


def test_blocked_requires_reason():
    rep = validate(FM + task_block(status="blocked", branch="task/TASK-001-gb",
                                   started="2026-07-12T09:00:00Z"))
    assert any("Blocked_Reason is empty" in e for e in rep.errors)


def test_blocked_reason_vocabulary():
    ok = validate(FM + task_block(status="blocked", blocked="SPEC_AMBIGUITY",
                                  branch="task/TASK-001-gb", started="2026-07-12T09:00:00Z"))
    assert ok.ok, ok.errors
    other = validate(FM + task_block(status="blocked", blocked="OTHER: waiting on VPS access",
                                     branch="task/TASK-001-gb", started="2026-07-12T09:00:00Z"))
    assert other.ok, other.errors
    bad = validate(FM + task_block(status="blocked", blocked="just stuck",
                                   branch="task/TASK-001-gb", started="2026-07-12T09:00:00Z"))
    assert any("not in vocabulary" in e for e in bad.errors)


def test_needs_review_requires_evidence():
    rep = validate(FM + task_block(status="needs_review", branch="task/TASK-001-gb",
                                   started="2026-07-12T09:00:00Z"))
    assert any("Test_Evidence is empty" in e for e in rep.errors)
    rep2 = validate(FM + task_block(status="needs_review", branch="task/TASK-001-gb",
                                    started="2026-07-12T09:00:00Z",
                                    evidence="pytest → 14/14 pass"))
    assert rep2.ok, rep2.errors


def test_active_requires_branch_and_start():
    rep = validate(FM + task_block(status="in_progress"))
    assert any("requires Branch" in e for e in rep.errors)
    assert any("requires Started_At" in e for e in rep.errors)


def test_branch_suffix_must_match_assignee():
    rep = validate(FM + task_block(status="in_progress", assignee="CX",
                                   branch="task/TASK-001-gb", started="2026-07-12T09:00:00Z"))
    assert any("should be 'task/TASK-001-cx'" in e for e in rep.errors)


def test_active_tbd_assignment_fails():
    rep = validate(FM + task_block(status="in_progress", assignee="TBD",
                                   branch="task/TASK-001-gb", started="2026-07-12T09:00:00Z"))
    assert any("cannot be Assigned_To TBD" in e for e in rep.errors)


def test_isolation_violation_detected():
    plan = FM + task_block(tid="TASK-001", status="in_progress", assignee="GB",
                           owned="src/auth/**", branch="task/TASK-001-gb",
                           started="2026-07-12T09:00:00Z") \
              + task_block(tid="TASK-002", status="claimed", assignee="CX",
                           owned="src/auth/tokens/**", branch="task/TASK-002-cx",
                           started="2026-07-12T09:30:00Z")
    rep = validate(plan)
    assert any("ISOLATION" in e for e in rep.errors)


def test_disjoint_active_territories_pass():
    plan = FM + task_block(tid="TASK-001", status="in_progress", assignee="GB",
                           owned="src/auth/**", branch="task/TASK-001-gb",
                           started="2026-07-12T09:00:00Z") \
              + task_block(tid="TASK-002", status="in_progress", assignee="CX",
                           owned="src/db/**, migrations/**", branch="task/TASK-002-cx",
                           started="2026-07-12T09:30:00Z")
    rep = validate(plan)
    assert rep.ok, rep.errors


def test_overlap_with_done_task_is_fine():
    plan = FM + task_block(tid="TASK-001", status="done", assignee="GB",
                           owned="src/auth/**", branch="task/TASK-001-gb",
                           started="2026-07-12T09:00:00Z",
                           evidence="pytest pass") \
              + task_block(tid="TASK-002", status="in_progress", assignee="CX",
                           owned="src/auth/refresh/**", branch="task/TASK-002-cx",
                           started="2026-07-12T09:30:00Z")
    rep = validate(plan)
    assert rep.ok, rep.errors


def test_duplicate_ids_fail():
    rep = validate(FM + task_block(tid="TASK-001") + task_block(tid="TASK-001", owned="src/db/**"))
    assert any("duplicate task ID" in e for e in rep.errors)


def test_unknown_dependency_fails():
    rep = validate(FM + task_block(deps="TASK-099"))
    assert any("unknown task 'TASK-099'" in e for e in rep.errors)


def test_bad_timestamp_fails():
    rep = validate(FM + task_block(upd_at="12 July 2026"))
    assert any("not UTC ISO-8601" in e for e in rep.errors)


def test_globs_intersect_logic():
    assert globs_intersect(["src/auth/**"], ["src/auth/tokens/**"])
    assert globs_intersect(["src/**"], ["src/db/**"])
    assert not globs_intersect(["src/auth/**"], ["src/db/**"])
    assert not globs_intersect(["migrations/**"], ["tests/db/**"])
    assert globs_intersect(["**"], ["src/x/**"])  # bare wildcard owns everything


def test_shipped_plan_md_is_legal():
    plan_path = Path(__file__).resolve().parents[1] / "PLAN.md"
    rep = validate(plan_path.read_text(encoding="utf-8"))
    assert rep.ok, rep.errors
