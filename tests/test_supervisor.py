"""Tests for the autopilot decision engine (supervisor.decide) and team_stats."""
import json
import queue
import sys
import threading
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


class _FinishedDispatch:
    def __init__(self, returncode):
        self.returncode = returncode

    def poll(self):
        return self.returncode


def test_second_consecutive_dispatch_failure_parks_unit_once(tmp_path, monkeypatch):
    notices = []
    monkeypatch.setattr(sup, "notify", lambda cfg, priority, message, repo: notices.append((priority, message)))
    state = RuntimeState(dispatch_failures={"GB": 1})
    inflight = {"GB": (_FinishedDispatch(23), "TASK-001", "forced-failure-command")}
    sup.reap_inflight(inflight, CFG, state, tmp_path, NOW)

    assert state.dispatch_failures == {"GB": 2}
    assert len(notices) == 1
    assert notices[0][0] == "P2"
    assert "parked" in notices[0][1]
    assert "max_dispatch_failures=2" in notices[0][1]
    assert "DISPATCH" not in kinds(decide(FM + task(), state, CFG, NOW))


def test_successful_dispatch_resets_failure_counter(tmp_path):
    state = RuntimeState(dispatch_failures={"GB": 1})
    inflight = {"GB": (_FinishedDispatch(0), "TASK-001", "successful-command")}
    sup.reap_inflight(inflight, CFG, state, tmp_path, NOW)

    assert state.dispatch_failures == {"GB": 0}
    assert "DISPATCH" in kinds(decide(FM + task(), state, CFG, NOW))


def test_failed_dispatch_notification_names_candidates_command_and_transcript(tmp_path, monkeypatch):
    notices = []
    monkeypatch.setattr(sup, "notify", lambda cfg, priority, message, repo: notices.append((priority, message)))
    inflight = {"GB": (_FinishedDispatch(17), "TASK-001", "dispatch --builder GB")}
    sup.reap_inflight(inflight, CFG, RuntimeState(), tmp_path, NOW)

    assert notices[0][0] == "P2"
    message = notices[0][1]
    assert "17" in message
    assert "dispatch --builder GB" in message
    assert "unreachable" in message
    assert "local dispatch precondition" in message
    assert "AUTOPILOT_LOG.md" in message


def test_once_reaps_forced_dispatch_failure(tmp_path, capsys):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
    command = f'"{sys.executable}" -c "import sys; sys.exit(7)"'
    (repo / "autopilot.json").write_text(
        __import__("json").dumps({"builders": ["GB"], "dispatch_cmd": {"GB": command}}),
        encoding="utf-8",
    )

    assert sup.main(["--once", "--repo", str(repo)]) == 0
    output = capsys.readouterr().out
    assert "exited 7" in output
    assert command in output


# ================================================================ TASK-018 ==
# supervisor.py integration: tower tick wiring, inbox drain, slack listener,
# unified command queue. Spec: TOWER §1 P1+P2/H1/H4/H5, SLACK §5/§9.

def test_default_config_has_tower_and_slack_keys():
    """DEFAULT_CONFIG must mirror autopilot.json's template blocks (§5),
    ships disabled per the ask-don't-auto-flip rule."""
    assert DEFAULT_CONFIG["tower"] == {
        "enabled": False, "url": "", "project_id": "", "_token_env": "DEVTEAM_TOWER_TOKEN",
    }
    assert DEFAULT_CONFIG["slack"] == {
        "enabled": False, "project_channel": "", "ops_channel": "", "thread_tracking": True,
    }


def test_load_config_merges_tower_slack_defaults(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
    cfg = sup.load_config(repo)
    assert cfg["tower"]["enabled"] is False
    assert cfg["slack"]["thread_tracking"] is True


def test_load_config_preserves_custom_tower_section(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
    custom = dict(DEFAULT_CONFIG)
    custom["tower"] = {"enabled": True, "url": "https://tower.example", "project_id": "p1",
                       "_token_env": "DEVTEAM_TOWER_TOKEN"}
    (repo / "autopilot.json").write_text(json.dumps(custom), encoding="utf-8")
    cfg = sup.load_config(repo)
    assert cfg["tower"]["enabled"] is True
    assert cfg["tower"]["url"] == "https://tower.example"


# ------------------------------------------------------- drain_command_queue
def _q_item(cmd, args="", chat_id="12345"):
    return {"cmd": cmd, "args": args, "chat_id": chat_id, "update_id": 1, "raw": f"{cmd} {args}"}


class TestDrainCommandQueue:
    def test_drains_both_queues_through_one_handler(self, tmp_path, monkeypatch):
        """SLACK §5: one drain path for both queues — a command queued on
        EITHER transport reaches the same handler and has its effect."""
        import tg_commands as tgc
        monkeypatch.setattr(tgc, "send_reply", lambda *a, **kw: True)
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
        tg_q, slack_q = queue.Queue(), queue.Queue()
        tg_q.put(_q_item("/mute", "2h"))
        slack_q.put(_q_item("/wave", ""))
        wave_event = threading.Event()
        state = RuntimeState()
        sup.drain_command_queue([tg_q, slack_q], repo, DEFAULT_CONFIG, state, wave_event, NOW, token="tok")
        assert state.mute_until != ""          # tg-queue command applied
        assert wave_event.is_set()              # slack-queue command applied

    def test_bad_command_on_one_queue_does_not_block_the_other(self, tmp_path, monkeypatch):
        import tg_commands as tgc
        monkeypatch.setattr(tgc, "send_reply", lambda *a, **kw: True)
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
        tg_q, slack_q = queue.Queue(), queue.Queue()
        tg_q.put({"cmd": "/answer", "args": "TASK-001 x", "chat_id": None, "update_id": 1})

        def boom(*a, **kw):
            raise RuntimeError("simulated failure")
        monkeypatch.setattr(tgc, "apply_answer", boom)
        slack_q.put(_q_item("/wave", ""))
        wave_event = threading.Event()
        sup.drain_command_queue([tg_q, slack_q], repo, DEFAULT_CONFIG, RuntimeState(), wave_event, NOW, token="tok")
        assert wave_event.is_set()
        log = (repo / "AUTOPILOT_LOG.md").read_text(encoding="utf-8")
        assert "ERROR" in log

    def test_drain_tg_queue_alias_still_works(self, tmp_path, monkeypatch):
        """Backward-compat: tests/test_supervisor_telegram.py (outside this
        task's territory) imports drain_tg_queue directly — it must keep
        working with its original single-queue signature."""
        import tg_commands as tgc
        monkeypatch.setattr(tgc, "send_reply", lambda *a, **kw: True)
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
        q = queue.Queue()
        q.put(_q_item("/wave", ""))
        wave_event = threading.Event()
        sup.drain_tg_queue(q, repo, DEFAULT_CONFIG, RuntimeState(), wave_event, NOW, token="tok")
        assert wave_event.is_set()


# ------------------------------------------------------------- inbox (P2) --
def _inbox_envelope(command, args=None, ident="cmd-1"):
    return {
        "id": ident, "issued_at": "2026-08-26T08:00:00Z", "source": "tower",
        "actor": "alister", "command": command, "args": args or {},
    }


def _write_inbox(repo, envelope):
    inbox_dir = repo / ".devteam" / "inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    (inbox_dir / f"{envelope['id']}.json").write_text(json.dumps(envelope), encoding="utf-8")
    return inbox_dir


class TestInboxDrain:
    def test_stop_command_applies_and_is_acked(self, tmp_path, monkeypatch):
        import tg_commands as tgc
        monkeypatch.setattr(tgc, "send_reply", lambda *a, **kw: True)
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
        inbox_dir = _write_inbox(repo, _inbox_envelope("stop"))
        sup.drain_inbox_commands(repo, DEFAULT_CONFIG, RuntimeState(), threading.Event(), NOW, token="tok")
        assert (repo / "STOP").exists()
        # Two-phase ack: the handled command's file is gone, consumed-id ledger written.
        assert not (inbox_dir / "cmd-1.json").exists()
        assert (inbox_dir / ".consumed_ids.json").exists()

    def test_approve_returns_review_tg_action(self, tmp_path, monkeypatch):
        import tg_commands as tgc
        monkeypatch.setattr(tgc, "send_reply", lambda *a, **kw: True)
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
        _write_inbox(repo, _inbox_envelope("approve", {"task_id": "TASK-001"}))
        actions = sup.drain_inbox_commands(repo, DEFAULT_CONFIG, RuntimeState(), threading.Event(), NOW, token="tok")
        assert len(actions) == 1
        assert actions[0].kind == "REVIEW_TG"
        assert actions[0].task_id == "TASK-001"

    def test_absent_inbox_dir_is_noop(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
        actions = sup.drain_inbox_commands(repo, DEFAULT_CONFIG, RuntimeState(), threading.Event(), NOW, token="tok")
        assert actions == []

    def test_drain_inbox_failure_is_fail_open(self, tmp_path, monkeypatch, capsys):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")

        def boom(repo, cfg=None):
            raise RuntimeError("simulated inbox corruption")
        monkeypatch.setattr(sup.inbox, "drain_inbox", boom)
        actions = sup.drain_inbox_commands(repo, DEFAULT_CONFIG, RuntimeState(), threading.Event(), NOW, token="tok")
        assert actions == []
        assert "simulated inbox corruption" in capsys.readouterr().err

    def test_handler_exception_leaves_file_unacked_for_retry(self, tmp_path, monkeypatch):
        """Two-phase contract: a handler failure must not ack — the file
        stays so the same command is retried next tick."""
        import tg_commands as tgc
        monkeypatch.setattr(tgc, "send_reply", lambda *a, **kw: True)
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
        inbox_dir = _write_inbox(repo, _inbox_envelope("answer", {"task_id": "TASK-001", "text": "x"}))

        def boom(*a, **kw):
            raise RuntimeError("simulated failure")
        monkeypatch.setattr(tgc, "apply_answer", boom)
        sup.drain_inbox_commands(repo, DEFAULT_CONFIG, RuntimeState(), threading.Event(), NOW, token="tok")
        assert (inbox_dir / "cmd-1.json").exists()  # NOT acked
        log = (repo / "AUTOPILOT_LOG.md").read_text(encoding="utf-8")
        assert "ERROR" in log


# --------------------------------------------------------- tower_sync tick -
class TestTowerTick:
    def test_sync_tick_called_with_repo_and_cfg(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(sup.tower_sync, "sync_tick", lambda repo, cfg, state=None: calls.append((repo, cfg, state)))
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
        (repo / "autopilot.json").write_text(json.dumps({"builders": ["GB"]}), encoding="utf-8")
        assert sup.main(["--once", "--repo", str(repo)]) == 0
        assert len(calls) == 1
        assert calls[0][0] == repo
        assert calls[0][2]["mode"] == "once"

    def test_sync_tick_failure_is_one_warning_line_tick_continues(self, tmp_path, monkeypatch, capsys):
        def boom(repo, cfg, state=None):
            raise RuntimeError("tower unreachable")
        monkeypatch.setattr(sup.tower_sync, "sync_tick", boom)
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
        (repo / "autopilot.json").write_text(json.dumps({"builders": ["GB"]}), encoding="utf-8")
        assert sup.main(["--once", "--repo", str(repo)]) == 0
        err = capsys.readouterr().err
        assert err.count("[tower]") == 1
        assert "tower unreachable" in err

    def test_disabled_tower_produces_no_tower_output(self, tmp_path, capsys):
        """Real tower_sync.sync_tick (not mocked) with the default disabled
        config must be a true no-op — no network, no warning lines."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
        (repo / "autopilot.json").write_text(json.dumps({"builders": ["GB"]}), encoding="utf-8")
        assert sup.main(["--once", "--repo", str(repo)]) == 0
        out = capsys.readouterr()
        assert "[tower]" not in out.out and "[tower]" not in out.err


# -------------------------------------------------------- slack listener --
class _FakeSlackListener:
    instances = []

    def __init__(self, app_token, bot_token, out_queue, client_factory=None, log_fn=None):
        self.app_token = app_token
        self.bot_token = bot_token
        self.out_queue = out_queue
        self.available = True
        self.started = False
        self.stopped = False
        _FakeSlackListener.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class TestSlackListenerStartup:
    def setup_method(self):
        _FakeSlackListener.instances.clear()

    def test_started_when_configured_and_env_present(self, monkeypatch):
        monkeypatch.setattr(sup, "SlackListener", _FakeSlackListener)
        monkeypatch.setenv("DEVTEAM_SLACK_APP_TOKEN", "xapp-1")
        monkeypatch.setenv("DEVTEAM_SLACK_TOKEN", "xoxb-1")
        cfg = {**DEFAULT_CONFIG, "notify_channels": ["console", "file", "slack"]}
        listener, q = sup._start_slack_listener(cfg)
        assert listener is not None and listener.started
        assert isinstance(q, queue.Queue)

    def test_not_started_when_slack_not_in_notify_channels(self, monkeypatch):
        monkeypatch.setattr(sup, "SlackListener", _FakeSlackListener)
        monkeypatch.setenv("DEVTEAM_SLACK_APP_TOKEN", "xapp-1")
        monkeypatch.setenv("DEVTEAM_SLACK_TOKEN", "xoxb-1")
        listener, q = sup._start_slack_listener(DEFAULT_CONFIG)  # default notify_channels has no "slack"
        assert listener is None
        assert _FakeSlackListener.instances == []

    def test_not_started_missing_env(self, monkeypatch, capsys):
        monkeypatch.setattr(sup, "SlackListener", _FakeSlackListener)
        monkeypatch.delenv("DEVTEAM_SLACK_APP_TOKEN", raising=False)
        monkeypatch.delenv("DEVTEAM_SLACK_TOKEN", raising=False)
        cfg = {**DEFAULT_CONFIG, "notify_channels": ["console", "file", "slack"]}
        listener, q = sup._start_slack_listener(cfg)
        assert listener is None
        assert _FakeSlackListener.instances == []
        assert "DEVTEAM_SLACK_APP_TOKEN" in capsys.readouterr().err

    def test_telegram_start_logic_unchanged_when_slack_also_configured(self, tmp_path, monkeypatch):
        """§9: Telegram start logic must be byte-preserved regardless of
        Slack config — both listeners are independent, additive wiring."""
        monkeypatch.delenv("DEVTEAM_TG_TOKEN", raising=False)
        monkeypatch.delenv("DEVTEAM_TG_CHAT", raising=False)
        cfg = {**DEFAULT_CONFIG, "notify_channels": ["console", "file", "telegram"]}
        repo = tmp_path / "repo"
        repo.mkdir()
        listener, q, wave_event = sup._start_tg_listener(repo, cfg)
        assert listener is None  # missing env, same as before Slack existed


# ---------------------------------------------- byte-identical when disabled
def test_tick_identical_when_tower_slack_inbox_all_disabled(tmp_path, capsys):
    """The ATLAS-A5-style graded criterion: with tower disabled (default),
    slack absent from notify_channels (default), and no .devteam/inbox
    directory, a tick must show zero trace of any of the three new wirings."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "PLAN.md").write_text(FM + task(), encoding="utf-8")
    (repo / "autopilot.json").write_text(json.dumps({"builders": ["GB"]}), encoding="utf-8")
    assert sup.main(["--once", "--repo", str(repo)]) == 0
    out = capsys.readouterr()
    combined = out.out + out.err
    for marker in ("[tower]", "Slack listener", "TOWER_COMMAND"):
        assert marker not in combined
    assert not (repo / ".devteam" / "inbox").exists()
