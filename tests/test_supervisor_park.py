"""E-C durable park and on-disk inflight regression tests."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import supervisor as sup  # noqa: E402
from test_supervisor import CFG, FM, task, kinds  # noqa: E402

NOW = datetime(2026, 7, 12, 20, tzinfo=timezone.utc)


def test_p1_parks_across_ten_ticks_and_notifies_once(tmp_path, monkeypatch):
    notices = []
    monkeypatch.setattr(sup, "notify", lambda *args: notices.append(args[1:3]))
    state = sup.RuntimeState(rework_counts={"TASK-001": 2})
    plan = FM + task(status="needs_review")
    for n in range(10):
        now = NOW + timedelta(minutes=n)
        actions = sup.decide(plan, state, CFG, now)
        sup.execute(actions, CFG, state, tmp_path, dry_run=False, now=now)
        assert "ESCALATE_P1" in kinds(actions) if n == 0 else kinds(actions) == ["IDLE"]
    assert len(notices) == 1
    assert state.parked["kind"] == "P1"


def test_durable_inflight_reaped_by_next_process(tmp_path, monkeypatch):
    notices = []
    monkeypatch.setattr(sup, "notify", lambda *args: notices.append(args[1:3]))
    record = tmp_path / ".devteam" / "inflight" / "GB.json"
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"pid": 99999999, "task_id": "TASK-001", "cmd": "bad", "started": "x"}), encoding="utf-8")
    state = sup.RuntimeState()
    sup._reap_durable_inflight(CFG, state, tmp_path, NOW)
    assert not record.exists()
    assert state.dispatch_failures["GB"] == 1
    assert notices
