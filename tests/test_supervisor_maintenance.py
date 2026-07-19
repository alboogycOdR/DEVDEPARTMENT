"""Integration tests for supervisor.py's Wave B wiring: budget-gated
DISPATCH, unreachable-builder P2 escalation (T1 Watchtower topology),
the nightly maintenance scheduler tick, and pending_digest_lines folding
into the next P0 digest.
"""
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import supervisor as sup  # noqa: E402
import maintenance as maint  # noqa: E402
from supervisor import (  # noqa: E402
    Action, RuntimeState, DEFAULT_CONFIG, decide, execute, reap_inflight,
)

NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)
CFG = dict(DEFAULT_CONFIG)

FM = """---
plan_version: 4.2
last_updated: 2026-07-19T10:00:00Z
overall_status: in_progress
---
"""


def task(tid="TASK-001", status="pending", assignee="GB", prio="high",
        owned="lib/a/**", branch="—", started="—", evidence="—",
        blocked="—", deps="—", upd_at="2026-07-19T11:50:00Z"):
    if status in ("claimed", "in_progress", "needs_review") and branch == "—":
        suffix = {"GB": "gb", "CX": "cx"}.get(assignee, "gb")
        branch = f"task/{tid}-{suffix}"
        started = started if started != "—" else "2026-07-19T10:00:00Z"
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


def make_repo(tmp_path, plan_text):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "PLAN.md").write_text(plan_text, encoding="utf-8")
    return repo


def kinds(actions):
    return [a.kind for a in actions]


# =============================================================== budget ====
class TestBudgetGatingInDecide:
    def test_dispatch_allowed_under_ceiling(self):
        cfg = {**CFG, "budget": {"max_dispatches_per_hour": 6, "quiet_hours": []}}
        acts = decide(FM + task(), RuntimeState(), cfg, NOW)
        assert "DISPATCH" in kinds(acts)
        assert "DEFER_BUDGET" not in kinds(acts)

    def test_dispatch_deferred_at_ceiling(self):
        cfg = {**CFG, "budget": {"max_dispatches_per_hour": 1, "quiet_hours": []}}
        state = RuntimeState(dispatch_log=[(NOW - timedelta(minutes=5)).strftime(sup.UTC_FMT)])
        acts = decide(FM + task(), state, cfg, NOW)
        assert "DEFER_BUDGET" in kinds(acts)
        assert "DISPATCH" not in kinds(acts)

    def test_deferred_action_carries_unit_and_task(self):
        cfg = {**CFG, "budget": {"max_dispatches_per_hour": 0, "quiet_hours": []}}
        acts = decide(FM + task(tid="TASK-007"), RuntimeState(), cfg, NOW)
        deferred = [a for a in acts if a.kind == "DEFER_BUDGET"][0]
        assert deferred.unit == "GB"
        assert deferred.task_id == "TASK-007"

    def test_quiet_hours_defers_dispatch(self):
        cfg = {**CFG, "budget": {"max_dispatches_per_hour": 100, "quiet_hours": [NOW.hour]}}
        acts = decide(FM + task(), RuntimeState(), cfg, NOW)
        assert "DEFER_BUDGET" in kinds(acts)

    def test_redispatch_stale_not_budget_gated(self):
        """REDISPATCH_STALE (heartbeat recovery) is deliberately exempt from
        the budget ceiling — only NEW dispatches onto pending work are
        throttled."""
        cfg = {**CFG, "budget": {"max_dispatches_per_hour": 0, "quiet_hours": []}}
        stale_task = task(tid="TASK-002", status="in_progress", upd_at="2026-07-19T09:00:00Z")
        acts = decide(FM + stale_task, RuntimeState(), cfg, NOW)
        assert "REDISPATCH_STALE" in kinds(acts)

    def test_execute_records_dispatch_in_state(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sup, "run_shell", lambda cmd, repo: 0)
        repo = make_repo(tmp_path, FM + task())
        state = RuntimeState()
        execute([Action("DISPATCH", "GB idle; dispatching", unit="GB", task_id="TASK-001")],
                CFG, state, repo, dry_run=False, now=NOW)
        assert len(state.dispatch_log) == 1
        assert state.dispatch_log[0] == NOW.strftime(sup.UTC_FMT)

    def test_full_cycle_hits_ceiling_across_ticks(self, tmp_path, monkeypatch):
        """Simulates several ticks: dispatch until the ceiling engages, then
        confirm the next tick defers instead of dispatching again."""
        monkeypatch.setattr(sup, "run_shell", lambda cmd, repo: 0)
        cfg = {**CFG, "budget": {"max_dispatches_per_hour": 2, "quiet_hours": []},
               "builders": ["GB"]}
        state = RuntimeState()
        repo = make_repo(tmp_path, FM)  # placeholder, PLAN.md text built per tick below

        plan_pending = FM + task(tid="TASK-001", status="pending")
        for _ in range(2):
            acts = decide(plan_pending, state, cfg, NOW)
            assert "DISPATCH" in kinds(acts)
            execute(acts, cfg, state, repo, dry_run=False, now=NOW)
        # Third attempt this hour: ceiling of 2 reached.
        acts3 = decide(plan_pending, state, cfg, NOW)
        assert "DEFER_BUDGET" in kinds(acts3)


# ============================================== unreachable builder (T1) ===
class _FakeProc:
    """Stand-in for subprocess.Popen: poll() returns the preset exit code
    immediately, simulating a background dispatch that has already finished
    by the time reap_inflight() checks it."""
    def __init__(self, rc: int):
        self._rc = rc

    def poll(self):
        return self._rc


class TestUnreachableBuilderEscalation:
    """DISPATCH/REDISPATCH_STALE are non-blocking (subprocess.Popen via
    launch_shell_bg) so builders can run concurrently instead of starving
    each other within one tick -- see launch_shell_bg's docstring. execute()
    only queues the action into `inflight`; the exit code (and any
    unreachable-builder P2) only surfaces once reap_inflight() notices the
    process has finished, which may be a later tick in real usage but is
    called directly here to exercise both halves in one test."""

    def test_dispatch_nonzero_exit_fires_p2(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sup.subprocess, "Popen", lambda *a, **k: _FakeProc(127))  # "command not found"
        sent = []
        monkeypatch.setattr(sup, "notify", lambda cfg, prio, msg, repo: sent.append((prio, msg)))
        repo = make_repo(tmp_path, FM + task())
        state = RuntimeState()
        inflight: dict = {}
        execute([Action("DISPATCH", "GB idle; dispatching", unit="GB", task_id="TASK-001")],
                CFG, state, repo, dry_run=False, now=NOW, inflight=inflight)
        assert sent == []  # not yet -- still "in flight" until reaped
        assert "GB" in inflight
        reap_inflight(inflight, CFG, state, repo, NOW)
        assert "GB" not in inflight
        assert len(sent) == 1
        assert sent[0][0] == "P2"
        assert "unreachable" in sent[0][1].lower()
        assert "TASK-001" in sent[0][1]

    def test_dispatch_success_no_escalation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sup.subprocess, "Popen", lambda *a, **k: _FakeProc(0))
        sent = []
        monkeypatch.setattr(sup, "notify", lambda cfg, prio, msg, repo: sent.append(prio))
        repo = make_repo(tmp_path, FM + task())
        state = RuntimeState()
        inflight: dict = {}
        execute([Action("DISPATCH", "GB idle; dispatching", unit="GB", task_id="TASK-001")],
                CFG, state, repo, dry_run=False, now=NOW, inflight=inflight)
        reap_inflight(inflight, CFG, state, repo, NOW)
        assert sent == []

    def test_redispatch_stale_nonzero_exit_fires_p2(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sup.subprocess, "Popen", lambda *a, **k: _FakeProc(1))
        sent = []
        monkeypatch.setattr(sup, "notify", lambda cfg, prio, msg, repo: sent.append(prio))
        repo = make_repo(tmp_path, FM + task())
        state = RuntimeState()
        inflight: dict = {}
        execute([Action("REDISPATCH_STALE", "heartbeat stale", unit="GB", task_id="TASK-001")],
                CFG, state, repo, dry_run=False, now=NOW, inflight=inflight)
        reap_inflight(inflight, CFG, state, repo, NOW)
        assert sent == ["P2"]

    def test_unreachable_escalation_respects_mute(self, tmp_path, monkeypatch):
        monkeypatch.setattr(sup.subprocess, "Popen", lambda *a, **k: _FakeProc(1))
        sent = []
        monkeypatch.setattr(sup, "notify", lambda cfg, prio, msg, repo: sent.append(prio))
        repo = make_repo(tmp_path, FM + task())
        state = RuntimeState(mute_until="2026-07-20T00:00:00Z")
        inflight: dict = {}
        execute([Action("DISPATCH", "GB idle", unit="GB", task_id="TASK-001")],
                CFG, state, repo, dry_run=False, now=NOW, inflight=inflight)
        reap_inflight(inflight, CFG, state, repo, NOW)
        assert sent == []
        log = (repo / "AUTOPILOT_LOG.md").read_text(encoding="utf-8")
        assert "MUTED" in log

    def test_execute_does_not_crash_on_missing_builder_cli(self, tmp_path):
        """No monkeypatch at all — dispatch_cmd shells out to a real bash
        command for a nonexistent builder script. Must not raise."""
        repo = make_repo(tmp_path, FM + task())
        (repo / "scripts").mkdir()
        state = RuntimeState()
        cfg = {**CFG, "dispatch_cmd": {"GB": "bash /nonexistent/path/dispatch.sh grok", "CX": "true"}}
        keep_going = execute([Action("DISPATCH", "GB idle", unit="GB", task_id="TASK-001")],
                             cfg, state, repo, dry_run=False, now=NOW)
        assert keep_going is True  # a failed dispatch is a P2, not a halt

    def test_two_dispatches_in_one_tick_both_launch_without_blocking(self, tmp_path, monkeypatch):
        """The actual regression this fix closes: CX sat idle behind a
        long-running GB session because DISPATCH used to be a blocking
        subprocess.run() -- the second action in the list never even started
        until the first's whole builder session exited. Both units' Popen
        calls must fire within the same execute() pass now."""
        launched = []

        class _SlowFakeProc:
            def poll(self):
                return None  # never finishes -- would hang forever under the old blocking call

        def fake_popen(cmd, shell=True, cwd=None):
            launched.append(cmd)
            return _SlowFakeProc()

        monkeypatch.setattr(sup.subprocess, "Popen", fake_popen)
        repo = make_repo(tmp_path, FM + task())
        state = RuntimeState()
        inflight: dict = {}
        keep_going = execute(
            [
                Action("DISPATCH", "GB idle; dispatching", unit="GB", task_id="TASK-001"),
                Action("DISPATCH", "CX idle; dispatching", unit="CX", task_id="TASK-002"),
            ],
            CFG, state, repo, dry_run=False, now=NOW, inflight=inflight,
        )
        assert keep_going is True
        assert len(launched) == 2  # both fired -- neither blocked behind the other
        assert set(inflight.keys()) == {"GB", "CX"}


# ======================================================= maintenance tick ==
class TestMaintenanceSchedulerTick:
    def test_supervisor_load_config_merges_maintenance_and_budget(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
        cfg = sup.load_config(repo)
        assert cfg["maintenance"]["hour_utc"] == 2
        assert cfg["budget"]["max_dispatches_per_hour"] == 6

    def test_custom_maintenance_config_preserved(self, tmp_path):
        import json
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
        custom = dict(DEFAULT_CONFIG)
        custom["maintenance"] = {"hour_utc": 4, "backup_retain_days": 14}
        (repo / "autopilot.json").write_text(json.dumps(custom), encoding="utf-8")
        cfg = sup.load_config(repo)
        assert cfg["maintenance"]["hour_utc"] == 4
        assert cfg["maintenance"]["backup_retain_days"] == 14

    def test_maintenance_result_feeds_pending_digest_lines(self, tmp_path, monkeypatch):
        """Simulates exactly what the tick loop does: run maintenance, then
        confirm its digest_line survives into the next DIGEST action."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "PLAN.md").write_text(FM + task(status="done"), encoding="utf-8")

        for name in maint._ORDERED_STEPS:
            if name == "_step_backup":
                monkeypatch.setattr(maint, name, lambda repo, retain_days: maint.StepResult("backup", True, "ok"))
            else:
                monkeypatch.setattr(maint, name, lambda repo: maint.StepResult("x", True, "ok"))

        result = maint.run_nightly_audit(repo, {}, now=NOW, force=True)
        assert result.ran is True
        assert result.digest_line == "Self-audit: PASS"

        state = RuntimeState()
        state.pending_digest_lines.append(result.digest_line)

        sent = []
        monkeypatch.setattr(sup, "notify", lambda cfg, prio, msg, repo: sent.append(msg))
        execute([Action("DIGEST", "WAVE COMPLETE — all 1 tasks done.")], CFG, state, repo,
                dry_run=False, now=NOW)
        assert len(sent) == 1
        assert "WAVE COMPLETE" in sent[0]
        assert "Self-audit: PASS" in sent[0]
        assert state.pending_digest_lines == []  # cleared after folding in

    def test_digest_without_pending_lines_unmodified(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
        sent = []
        monkeypatch.setattr(sup, "notify", lambda cfg, prio, msg, repo: sent.append(msg))
        state = RuntimeState()
        execute([Action("DIGEST", "WAVE COMPLETE")], CFG, state, repo, dry_run=False, now=NOW)
        assert sent == ["WAVE COMPLETE"]


# =========================================================== board summary
class TestBoardMaintenanceSummary:
    def test_board_reflects_last_audit(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
        marker = repo / ".devteam" / "last_audit_date.txt"
        marker.parent.mkdir(parents=True)
        marker.write_text("2026-07-19", encoding="utf-8")
        (repo / "AUTOPILOT_LOG.md").write_text(
            "- [2026-07-19T02:05:00Z] MAINTENANCE: Self-audit: PASS\n", encoding="utf-8")

        from board_publisher import build_board, DEFAULT_BOARD_CFG
        board = build_board(repo, DEFAULT_BOARD_CFG, NOW)
        assert board["maintenance"]["last_audit"] == "2026-07-19"
        assert board["maintenance"]["status"] == "Self-audit: PASS"

    def test_board_maintenance_empty_when_never_run(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
        from board_publisher import build_board, DEFAULT_BOARD_CFG
        board = build_board(repo, DEFAULT_BOARD_CFG, NOW)
        assert board["maintenance"] == {"last_audit": "", "status": ""}
