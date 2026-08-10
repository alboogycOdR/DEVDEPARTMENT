"""Tests for the autopilot decision engine (supervisor.decide) and team_stats."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from supervisor import decide, RuntimeState, DEFAULT_CONFIG  # noqa: E402
import supervisor as sup  # noqa: E402
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
def test_review_cmd_default_uses_opus():
    """ORCH model discipline: review must use claude-opus-4-8, and specifically
    must NOT share a model with the S5 builder (claude-sonnet-5) it reviews —
    same-model review shares the maker's failure distribution (CLAUDE.md
    "ORCH model discipline", 2026-07-19 decision)."""
    assert "claude-opus-4-8" in DEFAULT_CONFIG["review_cmd"]
    assert "claude-sonnet-5" not in DEFAULT_CONFIG["review_cmd"]


def test_judgment_model_default_is_opus():
    """The autopilot's other headless judgment calls (scoped /approve reviews,
    triage) read one shared config key — and it must not be the S5 builder's
    model either."""
    assert DEFAULT_CONFIG["judgment_model"] == "claude-opus-4-8"


def test_triage_unblock_uses_judgment_model(monkeypatch):
    """Scope triage is architectural judgment — must run on the judgment_model
    (opus-4-8), never the S5 builder's own model."""
    calls = []
    monkeypatch.setattr("supervisor.run_shell", lambda cmd, repo: calls.append(cmd) or 0)
    from supervisor import execute, RuntimeState, Action
    import pathlib
    execute([Action("TRIAGE_UNBLOCK", "TASK-001: ORCH to re-sequence dependencies", task_id="TASK-001")],
            DEFAULT_CONFIG, RuntimeState(), pathlib.Path("/tmp"), dry_run=False)
    assert calls and "claude-opus-4-8" in calls[0]
    assert "claude-sonnet-5" not in calls[0]


class TestDispatchCmdCwdIndependence:
    """v4.8 regression test for a real bug found live: dispatch_cmd used to
    be frozen at `import supervisor` time by reading the builder registry
    from the process's cwd -- completely disconnected from the actual repo
    any given execute() call operates on. On a real machine, running pytest
    (or anything else that imports supervisor) from a DIFFERENT project than
    the one under test/operation produced a dispatch_cmd map missing an
    active unit, and DISPATCH raised KeyError instead of launching.
    """

    def test_dispatch_cmd_for_works_for_any_unit_with_no_cfg_override(self):
        cmd = sup.dispatch_cmd_for("CX", {})
        assert "CX" in cmd
        assert "dispatch." in cmd  # dispatch.sh or dispatch.ps1

    def test_dispatch_cmd_for_works_for_a_unit_not_in_the_legacy_three(self):
        """The actual failure mode: a unit whose ID was never baked into any
        fixed dict still gets a correct command computed on the fly."""
        cmd = sup.dispatch_cmd_for("S5B", {})
        assert "S5B" in cmd

    def test_explicit_cfg_override_is_honored(self):
        cfg = {"dispatch_cmd": {"CX": "custom-launcher CX"}}
        assert sup.dispatch_cmd_for("CX", cfg) == "custom-launcher CX"

    def test_missing_unit_in_cfg_falls_through_to_computed_template(self):
        """The exact bug: cfg["dispatch_cmd"] present but missing an entry
        for the unit being dispatched must NOT raise KeyError."""
        cfg = {"dispatch_cmd": {"GB": "only GB is overridden"}}
        cmd = sup.dispatch_cmd_for("CX", cfg)
        assert "CX" in cmd
        assert cmd != "only GB is overridden"

    def test_result_is_independent_of_process_cwd(self, tmp_path, monkeypatch):
        """The literal regression: chdir to a directory that is NOT the repo
        being operated on (simulating a real-world 'pytest run from a
        different project' or 'supervisor imported from an unrelated cwd'
        scenario) and confirm dispatch_cmd_for still produces a correct
        command for a unit that would have been ABSENT from the old
        import-time-frozen dict if that unrelated directory's own registry
        happened not to define it."""
        unrelated = tmp_path / "some_other_project"
        unrelated.mkdir()
        (unrelated / "autopilot.json").write_text(
            '{"builders": {"active": ["GB"], "defined": {"GB": '
            '{"cli": "grok", "worktree_suffix": "grok", "branch_suffix": "gb", '
            '"briefing": "briefings/GROK_BUILD_BRIEFING.md"}}}}',
            encoding="utf-8")
        monkeypatch.chdir(unrelated)
        # "CX" is not defined in THIS unrelated cwd's registry at all --
        # the old design would have silently produced a dict without it.
        cmd = sup.dispatch_cmd_for("CX", {})
        assert "CX" in cmd

    def test_dispatch_action_does_not_raise_for_a_unit_absent_from_cfg(self, tmp_path, monkeypatch):
        """End-to-end through execute(): a DISPATCH action for a unit with no
        cfg["dispatch_cmd"] entry must launch, not KeyError."""
        launched = []

        class _Proc:
            def poll(self):
                return None

        def fake_popen(cmd, shell=True, cwd=None):
            launched.append(cmd)
            return _Proc()

        monkeypatch.setattr(sup.subprocess, "Popen", fake_popen)
        repo = tmp_path / "repo"
        repo.mkdir()
        cfg = {**DEFAULT_CONFIG, "dispatch_cmd": {"GB": "only GB here"}}  # CX deliberately absent
        inflight: dict = {}
        sup.execute(
            [sup.Action("DISPATCH", "CX idle; dispatching", unit="CX", task_id="TASK-002")],
            cfg, sup.RuntimeState(), repo, dry_run=False,
            now=datetime(2026, 8, 5, tzinfo=timezone.utc), inflight=inflight,
        )
        assert len(launched) == 1
        assert "CX" in launched[0]
