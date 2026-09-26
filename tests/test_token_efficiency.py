"""Tests for the 2026-09-25 token-efficiency pass: review ledger, escalation dedupe,
triage cap, review lock, and the scripted status digest."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import supervisor as sup  # noqa: E402
import status_digest  # noqa: E402
from supervisor import decide, execute, RuntimeState, DEFAULT_CONFIG, review_key  # noqa: E402
from validate_plan import parse_tasks, Report  # noqa: E402
from test_supervisor import FM, task, kinds  # noqa: E402

NOW = datetime(2026, 7, 12, 20, 0, 0, tzinfo=timezone.utc)
CFG = dict(DEFAULT_CONFIG)
UTC_FMT = sup.UTC_FMT


def _key_of(plan):
    return review_key(parse_tasks(plan, Report())[0])


# ------------------------------------------------------------------ reviews --
def test_reviewed_submission_is_not_reviewed_again():
    plan = FM + task(status="needs_review")
    st = RuntimeState(review_ledger={"TASK-001": {"key": _key_of(plan), "done": True}})
    assert "REVIEW" not in kinds(decide(plan, st, CFG, NOW))


def test_resubmission_with_new_evidence_is_reviewed_again():
    plan = FM + task(status="needs_review", evidence="pytest 12/12 pass")
    st = RuntimeState(review_ledger={"TASK-001": {"key": "stale-key", "done": True}})
    assert "REVIEW" in kinds(decide(plan, st, CFG, NOW))


def test_failed_review_backs_off_then_retries():
    plan = FM + task(status="needs_review")
    retry = (NOW + timedelta(minutes=15)).strftime(UTC_FMT)
    st = RuntimeState(review_ledger={"TASK-001": {"key": _key_of(plan), "done": False, "retry_after": retry}})
    assert "REVIEW" not in kinds(decide(plan, st, CFG, NOW))
    assert "REVIEW" in kinds(decide(plan, st, CFG, NOW + timedelta(minutes=16)))


def _repo_with(tmp_path, plan):
    (tmp_path / "PLAN.md").write_text(plan, encoding="utf-8")
    (tmp_path / "AUTOPILOT_LOG.md").write_text("", encoding="utf-8")
    return tmp_path


def test_one_review_session_covers_all_waiting_tasks(tmp_path, monkeypatch):
    plan = FM + task("TASK-001", status="needs_review") + task("TASK-002", status="needs_review", assignee="CX", owned="lib/b/**")
    repo = _repo_with(tmp_path, plan)
    calls = []
    monkeypatch.setattr(sup, "run_shell", lambda cmd, r: calls.append(cmd) or 0)
    st = RuntimeState()
    actions = decide(plan, st, CFG, NOW)
    assert kinds(actions).count("REVIEW") == 2
    execute(actions, CFG, st, repo, False, now=NOW)
    assert len(calls) == 1                                   # ONE Opus session, not two
    assert st.review_ledger["TASK-001"]["done"] and st.review_ledger["TASK-002"]["done"]
    # ...and the next tick does not launch another
    assert "REVIEW" not in kinds(decide(plan, st, CFG, NOW + timedelta(minutes=5)))


def test_failed_review_session_is_recorded_for_backoff(tmp_path, monkeypatch):
    plan = FM + task(status="needs_review")
    repo = _repo_with(tmp_path, plan)
    monkeypatch.setattr(sup, "run_shell", lambda cmd, r: 1)
    st = RuntimeState()
    execute(decide(plan, st, CFG, NOW), CFG, st, repo, False, now=NOW)
    led = st.review_ledger["TASK-001"]
    assert led["done"] is False and led["fails"] == 1 and led["retry_after"]
    assert "REVIEW" not in kinds(decide(plan, st, CFG, NOW + timedelta(minutes=5)))


def test_fresh_review_lock_prevents_a_second_session(tmp_path, monkeypatch):
    plan = FM + task(status="needs_review")
    repo = _repo_with(tmp_path, plan)
    (repo / ".devteam").mkdir()
    (repo / ".devteam" / "review.lock").write_text("running", encoding="utf-8")
    calls = []
    monkeypatch.setattr(sup, "run_shell", lambda cmd, r: calls.append(cmd) or 0)
    st = RuntimeState()
    execute(decide(plan, st, CFG, NOW), CFG, st, repo, False, now=NOW)
    assert calls == []
    assert st.review_ledger == {}                            # nothing was reviewed, so nothing is recorded


def test_review_lock_is_removed_after_a_session(tmp_path, monkeypatch):
    plan = FM + task(status="needs_review")
    repo = _repo_with(tmp_path, plan)
    monkeypatch.setattr(sup, "run_shell", lambda cmd, r: 0)
    st = RuntimeState()
    execute(decide(plan, st, CFG, NOW), CFG, st, repo, False, now=NOW)
    assert not (repo / ".devteam" / "review.lock").exists()


# -------------------------------------------------------------- escalations --
def test_parked_task_escalates_once_per_day(tmp_path, monkeypatch):
    plan = FM + task(status="blocked", blocked="OTHER: waiting for the owner", assignee="GB")
    repo = _repo_with(tmp_path, plan)
    sent = []
    monkeypatch.setattr(sup, "notify", lambda cfg, prio, msg, r: sent.append(msg))
    st = RuntimeState()
    execute(decide(plan, st, CFG, NOW), CFG, st, repo, False, now=NOW)
    assert len(sent) == 1
    later = NOW + timedelta(hours=1)
    assert "ESCALATE_P2" not in kinds(decide(plan, st, CFG, later))   # silent within 24h
    assert "ESCALATE_P2" in kinds(decide(plan, st, CFG, NOW + timedelta(hours=25)))


def test_escalation_key_ignores_changing_numbers():
    a = sup.Action("ESCALATE_P2", "TASK-1 stale for 95m after 2 redispatches", task_id="TASK-1")
    b = sup.Action("ESCALATE_P2", "TASK-1 stale for 120m after 2 redispatches", task_id="TASK-1")
    assert sup.escalation_key(a) == sup.escalation_key(b)


def test_p1_is_never_deduplicated():
    st = RuntimeState(escalated={})
    plan = FM + task(status="needs_review", evidence="—").replace("pytest 10/10 pass", "—")
    assert kinds(decide(plan, st, CFG, NOW)) == ["ESCALATE_P1"]


def test_missing_dependency_triage_is_capped():
    plan = FM + task(status="blocked", blocked="MISSING_DEPENDENCY", assignee="GB")
    assert "TRIAGE_UNBLOCK" in kinds(decide(plan, RuntimeState(), CFG, NOW))
    capped = decide(plan, RuntimeState(triage_counts={"TASK-001": 2}), CFG, NOW)
    assert "TRIAGE_UNBLOCK" not in kinds(capped) and "ESCALATE_P2" in kinds(capped)


# ---------------------------------------------------------------- digest ----
def test_digest_layout_and_change_detection(tmp_path):
    plan = (FM + task("TASK-001", status="done")
            + task("TASK-002", status="in_progress", assignee="CX")
            + task("TASK-003", status="needs_review")
            + task("TASK-004", status="pending", deps="TASK-001")
            + task("TASK-005", status="pending", deps="TASK-002")
            + task("TASK-006", status="blocked", blocked="OTHER: needs a live check", assignee="GB"))
    repo = _repo_with(tmp_path, plan)
    text = status_digest.run(repo, {}, now=NOW)
    for heading in ("Merged (since last update):", "In progress:", "Queue:", "Pending action:", "Prod:"):
        assert heading in text
    assert "TASK-002" in text and "building" in text
    assert "TASK-003" in text and "in review" in text
    assert "ready: TASK-004" in text                  # dependency done
    assert "1 waiting on dependencies" in text        # TASK-005 waits on the unfinished TASK-002
    assert "TASK-006 blocked: OTHER: needs a live check" in text
    assert text.strip().splitlines()[-1].startswith("Local time:")
    assert (repo / ".devteam" / "STATUS.md").read_text(encoding="utf-8").startswith("Merged")


def test_digest_only_notifies_when_content_changed(tmp_path, monkeypatch):
    repo = _repo_with(tmp_path, FM + task("TASK-001", status="pending"))
    sent = []
    monkeypatch.setattr(status_digest, "_notify", lambda cfg, r, text: sent.append(text))
    status_digest.run(repo, {}, now=NOW, send=True)
    status_digest.run(repo, {}, now=NOW + timedelta(minutes=30), send=True)      # unchanged plan
    assert len(sent) == 1
    (repo / "PLAN.md").write_text(FM + task("TASK-001", status="in_progress"), encoding="utf-8")
    status_digest.run(repo, {}, now=NOW + timedelta(minutes=60), send=True)
    assert len(sent) == 2
    status_digest.run(repo, {}, now=NOW + timedelta(minutes=90), send=True, force=True)
    assert len(sent) == 3
