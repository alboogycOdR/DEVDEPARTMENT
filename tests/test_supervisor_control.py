"""Integration tests for supervisor.py's Wave I (I1) wiring: dossier-aware
stale detection (decide()'s dossier_heartbeats parameter), and
maybe_drain_control()'s queue draining / unreported-streak P2 escalation /
mute respect / legacy-mode no-op.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import supervisor as sup  # noqa: E402
import control as ctl  # noqa: E402
from supervisor import (  # noqa: E402
    Action, RuntimeState, DEFAULT_CONFIG, decide, maybe_drain_control, _dossier_heartbeats,
)

NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
CFG = dict(DEFAULT_CONFIG)
STRICT_CFG = {**CFG, "control": {"mode": "strict"}}

FM = """---
plan_version: 4.5
last_updated: 2026-07-20T10:00:00Z
overall_status: in_progress
---
"""


def task(tid="TASK-700", status="in_progress", assignee="GB", prio="high",
        owned="lib/x/**", branch=None, started="2026-07-19T00:00:00Z",
        upd_at="2026-07-20T08:00:00Z", upd_by="SV", evidence=None):
    if branch is None:
        suffix = {"GB": "gb", "CX": "cx"}.get(assignee, "gb")
        branch = f"task/{tid}-{suffix}"
    if evidence is None:
        evidence = "pytest 10/10 pass" if status == "needs_review" else "—"
    return f"""
### {tid}
**Title:** T {tid}
**Status:** {status}
**Assigned_To:** {assignee}
**Priority:** {prio}
**Spec_References:** specs/x.md
**Owned_Paths:** {owned}
**Depends_On:** —
**Description:** d
**Acceptance_Criteria:**
- [ ] c
**Branch:** {branch}
**Started_At:** {started}
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** {evidence}
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** {upd_by}
**Updated_At:** {upd_at}
"""


def kinds(actions):
    return [a.kind for a in actions]


def make_repo(tmp_path: Path, plan_text: str = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "PLAN.md").write_text(plan_text or (FM + task()), encoding="utf-8")
    return repo


# =================================================== dossier heartbeats ===
class TestDossierHeartbeatStaleDetection:
    def test_legacy_mode_ignores_dossier_heartbeats(self):
        """upd_at is 4h stale (> 90m default) — legacy mode redispatches
        even if a heartbeats dict happens to be passed in, since control
        mode isn't strict."""
        stale = task(status="in_progress", upd_at="2026-07-20T08:00:00Z")
        fresh_hb = {"TASK-700": NOW}  # would rescue it, but legacy ignores this
        acts = decide(FM + stale, RuntimeState(), CFG, NOW, dossier_heartbeats=fresh_hb)
        assert "REDISPATCH_STALE" in kinds(acts)

    def test_strict_mode_dossier_heartbeat_prevents_false_stale(self):
        """PLAN.md's own Updated_At is 4h old, but the dossier was touched
        1 minute ago — strict mode must use the more recent of the two and
        NOT redispatch a builder that's actively working."""
        stale_by_plan = task(status="in_progress", upd_at="2026-07-20T08:00:00Z")
        recent_hb = {"TASK-700": NOW.replace(minute=59)}  # 1 minute before NOW
        acts = decide(FM + stale_by_plan, RuntimeState(), STRICT_CFG, NOW,
                      dossier_heartbeats=recent_hb)
        assert "REDISPATCH_STALE" not in kinds(acts)

    def test_strict_mode_no_heartbeat_falls_back_to_plan_updated_at(self):
        """No dossier heartbeat recorded for this task at all — strict mode
        must still fall back to plain Updated_At staleness, same as legacy."""
        stale = task(status="in_progress", upd_at="2026-07-20T08:00:00Z")
        acts = decide(FM + stale, RuntimeState(), STRICT_CFG, NOW, dossier_heartbeats={})
        assert "REDISPATCH_STALE" in kinds(acts)

    def test_strict_mode_stale_dossier_heartbeat_does_not_rescue(self):
        """The dossier heartbeat itself is also old — no rescue, still stale."""
        stale = task(status="in_progress", upd_at="2026-07-20T08:00:00Z")
        old_hb = {"TASK-700": datetime(2026, 7, 20, 7, 0, tzinfo=timezone.utc)}
        acts = decide(FM + stale, RuntimeState(), STRICT_CFG, NOW, dossier_heartbeats=old_hb)
        assert "REDISPATCH_STALE" in kinds(acts)

    def test_dossier_heartbeats_helper_reads_mtimes(self, tmp_path):
        repo = make_repo(tmp_path)
        d = repo / "dossiers"
        d.mkdir()
        (d / "TASK-700.md").write_text("work log", encoding="utf-8")
        (d / "not-a-task-file.md").write_text("ignored", encoding="utf-8")
        hb = _dossier_heartbeats(repo)
        assert "TASK-700" in hb
        assert "not-a-task-file" not in str(hb.keys())

    def test_dossier_heartbeats_helper_missing_dir_is_empty(self, tmp_path):
        repo = make_repo(tmp_path)
        assert _dossier_heartbeats(repo) == {}


# ==================================================== maybe_drain_control =
class TestMaybeDrainControl:
    def test_legacy_mode_is_noop(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        called = []
        monkeypatch.setattr(ctl, "drain_control_queue", lambda repo, ts: called.append(1))
        state = RuntimeState()
        maybe_drain_control(repo, CFG, state, NOW)  # CFG has no control.mode -> legacy default
        assert called == []

    def test_strict_mode_applies_queued_control_and_resets_streak(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path, FM + task(status="in_progress"))
        monkeypatch.setattr(ctl, "drain_control_queue",
                            lambda repo, ts: [("TASK-700-x.json", True, "CONTROL TASK-700: GB -> needs_review")])
        monkeypatch.setattr(ctl, "drain_unreported_queue", lambda repo, ts: [])
        state = RuntimeState(unreported_counts={"TASK-700": 1})
        maybe_drain_control(repo, STRICT_CFG, state, NOW)
        assert state.unreported_counts["TASK-700"] == 0  # reset by a successful report
        log = (repo / "AUTOPILOT_LOG.md").read_text(encoding="utf-8")
        assert "applied" in log

    def test_rejected_control_sends_p2(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path, FM + task(status="in_progress"))
        monkeypatch.setattr(ctl, "drain_control_queue",
                            lambda repo, ts: [("TASK-700-x.json", False, "REJECTED: illegal status 'TASK-700'")])
        monkeypatch.setattr(ctl, "drain_unreported_queue", lambda repo, ts: [])
        sent = []
        monkeypatch.setattr(sup, "notify", lambda cfg, prio, msg, repo: sent.append((prio, msg)))
        state = RuntimeState()
        maybe_drain_control(repo, STRICT_CFG, state, NOW)
        assert len(sent) == 1 and sent[0][0] == "P2"

    def test_unreported_streak_reaches_two_escalates_and_resets(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path, FM + task(status="in_progress"))
        monkeypatch.setattr(ctl, "drain_control_queue", lambda repo, ts: [])
        monkeypatch.setattr(ctl, "drain_unreported_queue",
                            lambda repo, ts: [("TASK-700", "UNREPORTED TASK-700: logged, state unchanged", True)])
        sent = []
        monkeypatch.setattr(sup, "notify", lambda cfg, prio, msg, repo: sent.append((prio, msg)))
        state = RuntimeState(unreported_counts={"TASK-700": 1})  # one prior miss
        maybe_drain_control(repo, STRICT_CFG, state, NOW)
        assert len(sent) == 1 and sent[0][0] == "P2"
        assert "2 consecutive" in sent[0][1]
        assert state.unreported_counts["TASK-700"] == 0  # streak reset after escalation

    def test_single_unreported_does_not_escalate(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path, FM + task(status="in_progress"))
        monkeypatch.setattr(ctl, "drain_control_queue", lambda repo, ts: [])
        monkeypatch.setattr(ctl, "drain_unreported_queue",
                            lambda repo, ts: [("TASK-700", "logged", True)])
        sent = []
        monkeypatch.setattr(sup, "notify", lambda cfg, prio, msg, repo: sent.append(msg))
        state = RuntimeState()  # no prior misses
        maybe_drain_control(repo, STRICT_CFG, state, NOW)
        assert sent == []
        assert state.unreported_counts["TASK-700"] == 1

    def test_unreported_p2_suppressed_when_muted(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path, FM + task(status="in_progress"))
        monkeypatch.setattr(ctl, "drain_control_queue", lambda repo, ts: [])
        monkeypatch.setattr(ctl, "drain_unreported_queue",
                            lambda repo, ts: [("TASK-700", "logged", True)])
        sent = []
        monkeypatch.setattr(sup, "notify", lambda cfg, prio, msg, repo: sent.append(msg))
        state = RuntimeState(unreported_counts={"TASK-700": 1}, mute_until="2026-07-20T23:59:59Z")
        maybe_drain_control(repo, STRICT_CFG, state, NOW)
        assert sent == []
        log = (repo / "AUTOPILOT_LOG.md").read_text(encoding="utf-8")
        assert "MUTED" in log

    def test_exception_never_propagates(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)

        def boom(repo, ts):
            raise RuntimeError("simulated control drain crash")
        monkeypatch.setattr(ctl, "drain_control_queue", boom)
        state = RuntimeState()
        maybe_drain_control(repo, STRICT_CFG, state, NOW)  # must not raise


# ============================================ validate_plan.py strict warn
class TestValidateStrictModeWarning:
    def test_legacy_mode_no_warning_for_gb_updated_by(self):
        from validate_plan import validate
        plan = FM + task(status="needs_review", upd_by="GB",
                         branch="task/TASK-700-gb", started="2026-07-19T00:00:00Z")
        rep = validate(plan, "legacy")
        assert rep.ok
        assert not any("control.mode=strict" in w for w in rep.warnings)

    def test_strict_mode_warns_on_gb_updated_by(self):
        from validate_plan import validate
        plan = FM + task(status="needs_review", upd_by="GB",
                         branch="task/TASK-700-gb", started="2026-07-19T00:00:00Z")
        rep = validate(plan, "strict")
        assert rep.ok  # warning, not an error — never blocks a tick
        assert any("control.mode=strict" in w for w in rep.warnings)

    def test_strict_mode_no_warning_when_updated_by_sv(self):
        from validate_plan import validate
        plan = FM + task(status="needs_review", upd_by="SV",
                         branch="task/TASK-700-gb", started="2026-07-19T00:00:00Z")
        rep = validate(plan, "strict")
        assert rep.ok
        assert not any("control.mode=strict" in w for w in rep.warnings)

    def test_sv_is_a_legal_updated_by_value(self):
        from validate_plan import validate
        plan = FM + task(upd_by="SV")
        rep = validate(plan)
        assert rep.ok
