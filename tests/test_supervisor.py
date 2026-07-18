"""Tests for the autopilot decision engine (supervisor.decide) and team_stats."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from supervisor import decide, RuntimeState, DEFAULT_CONFIG  # noqa: E402
from team_stats import compute  # noqa: E402

NOW = datetime(2026, 7, 12, 20, 0, 0, tzinfo=timezone.utc)
CFG = dict(DEFAULT_CONFIG)

FM = """---
plan_version: 1.0
last_updated: 2026-07-12T10:00:00Z
overall_status: in_progress
---
"""


def task(tid="TASK-001", status="pending", assignee="GB", prio="high",
         owned="lib/a/**", branch="—", started="—", evidence="—",
         blocked="—", deps="—", upd_at="2026-07-12T19:50:00Z"):
    if status in ("claimed", "in_progress", "needs_review") and branch == "—":
        suffix = {"GB": "gb", "CX": "cx"}.get(assignee, "gb")
        branch = f"task/{tid}-{suffix}"
        started = started if started != "—" else "2026-07-12T18:00:00Z"
    if status == "needs_review" and evidence == "—":
        evidence = "pytest 10/10 pass"
    return f"""
### {tid}
**Title:** T {tid}
**Status:** {status}
**Assigned_To:** {assignee}
**Priority:** {prio}
**Spec_References:** specs/x.md
**Owned_Paths:** {owned}
**Depends_On:** {deps}
**Description:** d
**Acceptance_Criteria:**
- [ ] c
**Branch:** {branch}
**Started_At:** {started}
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** {evidence}
**Review_Findings:** —
**Blocked_Reason:** {blocked}
**Updated_By:** ORCH
**Updated_At:** {upd_at}
"""


def kinds(actions):
    return [a.kind for a in actions]


def test_stop_file_halts():
    acts = decide(FM + task(), RuntimeState(), CFG, NOW, stop_file_exists=True)
    assert kinds(acts) == ["HALT"]


def test_illegal_plan_escalates_p1():
    plan = FM + task(status="needs_review", evidence="—").replace("pytest 10/10 pass", "—")
    acts = decide(plan, RuntimeState(), CFG, NOW)
    assert kinds(acts) == ["ESCALATE_P1"]


def test_needs_review_triggers_review():
    acts = decide(FM + task(status="needs_review"), RuntimeState(), CFG, NOW)
    assert "REVIEW" in kinds(acts)


def test_max_rework_freezes_and_escalates():
    st = RuntimeState(rework_counts={"TASK-001": 2})
    acts = decide(FM + task(status="needs_review"), st, CFG, NOW)
    assert "ESCALATE_P1" in kinds(acts)
    assert "REVIEW" not in kinds(acts)


def test_spec_ambiguity_escalates_p2():
    acts = decide(FM + task(status="blocked", blocked="SPEC_AMBIGUITY",
                            branch="task/TASK-001-gb", started="2026-07-12T18:00:00Z"),
                  RuntimeState(), CFG, NOW)
    assert "ESCALATE_P2" in kinds(acts)


def test_first_ownership_conflict_self_heals_second_escalates():
    plan = FM + task(status="blocked", blocked="OWNERSHIP_CONFLICT",
                     branch="task/TASK-001-gb", started="2026-07-12T18:00:00Z")
    first = decide(plan, RuntimeState(), CFG, NOW)
    assert "TRIAGE_UNBLOCK" in kinds(first)
    second = decide(plan, RuntimeState(conflict_counts={"TASK-001": 1}), CFG, NOW)
    assert "ESCALATE_P2" in kinds(second)


def test_stale_heartbeat_redispatches_then_escalates():
    stale = task(status="in_progress", upd_at="2026-07-12T17:00:00Z")  # 3h old > 90m
    first = decide(FM + stale, RuntimeState(), CFG, NOW)
    rd = [a for a in first if a.kind == "REDISPATCH_STALE"]
    assert rd and rd[0].unit == "GB" and rd[0].task_id == "TASK-001"
    assert "DISPATCH" not in kinds(first)  # no fresh dispatch to the same busy unit
    third = decide(FM + stale, RuntimeState(stale_resets={"TASK-001": 2}), CFG, NOW)
    assert "ESCALATE_P2" in kinds(third)


def test_fresh_heartbeat_not_reset():
    acts = decide(FM + task(status="in_progress", upd_at="2026-07-12T19:50:00Z"),
                  RuntimeState(), CFG, NOW)
    assert "REDISPATCH_STALE" not in kinds(acts)


def test_idle_builder_dispatched_on_eligible_task():
    plan = FM + task(tid="TASK-001", status="pending", assignee="GB") \
              + task(tid="TASK-002", status="in_progress", assignee="CX", owned="lib/b/**")
    acts = decide(plan, RuntimeState(), CFG, NOW)
    d = [a for a in acts if a.kind == "DISPATCH"]
    assert len(d) == 1 and d[0].unit == "GB" and d[0].task_id == "TASK-001"


def test_busy_builder_not_double_dispatched():
    plan = FM + task(tid="TASK-001", status="in_progress", assignee="GB") \
              + task(tid="TASK-002", status="pending", assignee="GB", owned="lib/b/**")
    acts = decide(plan, RuntimeState(), CFG, NOW)
    assert "DISPATCH" not in kinds(acts)


def test_dependency_gates_dispatch():
    plan = FM + task(tid="TASK-001", status="pending", assignee="GB") \
              + task(tid="TASK-002", status="pending", assignee="CX", owned="lib/b/**", deps="TASK-001")
    acts = decide(plan, RuntimeState(), CFG, NOW)
    d = [a for a in acts if a.kind == "DISPATCH"]
    assert [x.unit for x in d] == ["GB"]  # CX waits on TASK-001


def test_priority_ordering_in_dispatch():
    plan = FM + task(tid="TASK-001", status="pending", assignee="GB", prio="low") \
              + task(tid="TASK-002", status="pending", assignee="GB", prio="critical", owned="lib/b/**")
    acts = decide(plan, RuntimeState(), CFG, NOW)
    d = [a for a in acts if a.kind == "DISPATCH"]
    assert d[0].task_id == "TASK-002"


def test_wave_complete_digest():
    plan = FM + task(tid="TASK-001", status="done") + task(tid="TASK-002", status="done", owned="lib/b/**")
    acts = decide(plan, RuntimeState(), CFG, NOW)
    assert kinds(acts) == ["DIGEST"]


def test_all_busy_is_idle_tick():
    plan = FM + task(tid="TASK-001", status="in_progress", assignee="GB") \
              + task(tid="TASK-002", status="in_progress", assignee="CX", owned="lib/b/**")
    acts = decide(plan, RuntimeState(), CFG, NOW)
    assert kinds(acts) == ["IDLE"]


# ------------------------------------------------------------ team_stats ----
REVIEW_SAMPLE = """# REVIEW.md
| Task | Unit | Verdict | Findings | First-pass | Timestamp |
|---|---|---|---|---|---|
| TASK-001 | CX | approved | Territory clean | yes | 2026-07-12T17:00:00Z |
| TASK-002 | GB | approved | Clean | yes | 2026-07-12T17:05:00Z |
| TASK-004 | GB | approved | Clean | yes | 2026-07-12T17:45:00Z |
| TASK-005 | CX | rework | Missing test coverage on error paths | no | 2026-07-12T17:50:00Z |
| TASK-005 | CX | approved | Rework verified | no | 2026-07-12T18:09:00Z |
"""


def test_team_stats_compute():
    s = compute(REVIEW_SAMPLE)
    assert s["GB"]["reviews"] == 2 and s["GB"]["first_pass_rate"] == 1.0
    assert s["CX"]["reviews"] == 3 and s["CX"]["rework"] == 1
    assert s["CX"]["rework_causes"] == {"tests": 1}
    assert "Insufficient evidence" in s["assignment_hint"]  # < 10 reviews


# ------------------------------------------------- model discipline tests ---
def test_review_cmd_default_uses_sonnet5():
    """ORCH model discipline: review must use claude-sonnet-5 (CLAUDE.md 1020f7a)."""
    assert "claude-sonnet-5" in DEFAULT_CONFIG["review_cmd"]


def test_triage_unblock_uses_sonnet5(monkeypatch):
    """Scope triage is architectural judgment — must run on sonnet-5."""
    calls = []
    monkeypatch.setattr("supervisor.run_shell", lambda cmd, repo: calls.append(cmd) or 0)
    from supervisor import execute, RuntimeState, Action
    import pathlib
    execute([Action("TRIAGE_UNBLOCK", "TASK-001: ORCH to re-sequence dependencies", task_id="TASK-001")],
            DEFAULT_CONFIG, RuntimeState(), pathlib.Path("/tmp"), dry_run=False)
    assert calls and "claude-sonnet-5" in calls[0]
