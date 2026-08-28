import json
import sys

sys.path.insert(0, "scripts")
import tower_sync


PLAN = '''---
plan_version: "1"
last_updated: "2026-01-01T00:00:00Z"
overall_status: active
---
### TASK-001
**Title:** Test task
**Status:** needs_review
**Assigned_To:** CX
**Priority:** high
**Spec_References:** x
**Owned_Paths:** x
**Description:** x
**Acceptance_Criteria:**
- [x] x
**Branch:** task/TASK-001-cx
**Started_At:** 2026-01-01T00:00:00Z
**Test_Evidence:** x
**Updated_By:** CX
**Updated_At:** 2026-01-01T00:00:00Z
'''


def cfg(enabled=True):
    return {"autonomy_level": 2, "builders": {"active": ["CX", "GB"]},
            "tower": {"enabled": enabled, "url": "https://tower", "project_id": "demo", "_token_env": "TOWER_TEST"}}


def test_snapshot_schema_uses_parser_and_cache_only(tmp_path, monkeypatch):
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    monkeypatch.setattr(tower_sync.usage_probe, "load_cache", lambda repo: {"codex": {"pct_5h": 30}})
    snap = tower_sync.build_snapshot(tmp_path, cfg(), {"tick": 4})
    assert set(("schema", "project_id", "ts", "pack_version", "supervisor", "wave", "tasks", "builders", "review_queue", "usage", "recent_events")) <= set(snap)
    assert snap["tasks"][0]["id"] == "TASK-001" and snap["review_queue"][0]["id"] == "TASK-001"
    assert snap["supervisor"]["tick"] == 4 and snap["pack_version"] is None


def test_disabled_is_exact_noop(tmp_path):
    tower_sync.sync_tick(tmp_path, cfg(False), transport=lambda *a: (_ for _ in ()).throw(AssertionError()))


def test_push_pull_materialise_and_ack(tmp_path, monkeypatch):
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    monkeypatch.setenv("TOWER_TEST", "secret")
    calls = []
    def transport(method, url, token, data=None):
        calls.append((method, url, token, data))
        return [{"id": "abc", "command": "wave"}] if method == "GET" else None
    tower_sync.sync_tick(tmp_path, cfg(), transport=transport)
    assert [c[0] for c in calls] == ["POST", "GET", "DELETE"]
    assert calls[0][2] == "secret"
    assert json.loads((tmp_path / ".devteam" / "inbox" / "abc.json").read_text()) == {"id": "abc", "command": "wave"}


def test_failure_warns_once_and_does_not_raise(tmp_path, capsys):
    tower_sync.sync_tick(tmp_path, cfg(), transport=lambda *a: (_ for _ in ()).throw(OSError("down")))
    assert capsys.readouterr().err.count("[tower] warning:") == 1


# --- TASK-019: recent_events must carry real ts/kind, never invented nulls ---

_VALID_KINDS = {"DISPATCH", "REVIEW", "MERGE", "BLOCKED", "DIGEST"}


def test_recent_events_contract_matches_spec_shape(tmp_path):
    """This is the test whose absence let the break ship: it must fail
    against code that emits {"ts": None, "kind": None, ...} for every line."""
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    log = ("- [2026-08-16T18:34:45Z] DISPATCH: S5 idle; dispatching onto TASK-010\n"
           "- [2026-08-16T18:35:00Z] REVIEW: TASK-004 approved\n"
           "- [2026-08-16T18:36:00Z] MERGE: TASK-004 merged to master\n"
           "- [2026-08-16T18:37:00Z] BLOCKED: TASK-007 waiting on OWNERSHIP_CONFLICT\n"
           "- [2026-08-16T18:38:00Z] DIGEST: wave complete\n")
    (tmp_path / "AUTOPILOT_LOG.md").write_text(log, encoding="utf-8")
    snap = tower_sync.build_snapshot(tmp_path, cfg())
    events = snap["recent_events"]
    assert len(events) == 5
    for event in events:
        assert isinstance(event["ts"], str) and event["ts"]
        assert event["kind"] in _VALID_KINDS
        assert isinstance(event["text"], str)


def test_recent_events_ts_and_kind_parsed_from_real_log_line(tmp_path):
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    line = "- [2026-08-16T18:34:45Z] DISPATCH: S5 idle; dispatching onto TASK-010 (line-endings)"
    (tmp_path / "AUTOPILOT_LOG.md").write_text(line + "\n", encoding="utf-8")
    snap = tower_sync.build_snapshot(tmp_path, cfg())
    [event] = snap["recent_events"]
    assert event == {"ts": "2026-08-16T18:34:45Z", "kind": "DISPATCH",
                      "text": "S5 idle; dispatching onto TASK-010 (line-endings)"}


def test_recent_events_skips_lines_not_matching_log_format(tmp_path):
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    log = ("- [2026-08-16T18:34:45Z] DISPATCH: valid line\n"
           "this line has no bracketed timestamp at all\n"
           "- missing brackets entirely: still not a match\n")
    (tmp_path / "AUTOPILOT_LOG.md").write_text(log, encoding="utf-8")
    snap = tower_sync.build_snapshot(tmp_path, cfg())
    assert len(snap["recent_events"]) == 1
    assert snap["recent_events"][0]["kind"] == "DISPATCH"


def test_recent_events_skips_out_of_enum_kind(tmp_path):
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    log = ("- [2026-08-16T18:34:43Z] MAINTENANCE: Self-audit: PASS\n"
           "- [2026-08-16T18:34:45Z] DISPATCH: real event\n")
    (tmp_path / "AUTOPILOT_LOG.md").write_text(log, encoding="utf-8")
    snap = tower_sync.build_snapshot(tmp_path, cfg())
    assert len(snap["recent_events"]) == 1
    assert snap["recent_events"][0]["kind"] == "DISPATCH"


def test_recent_events_empty_when_log_missing(tmp_path):
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    snap = tower_sync.build_snapshot(tmp_path, cfg())
    assert snap["recent_events"] == []


def test_recent_events_empty_when_log_empty(tmp_path):
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    (tmp_path / "AUTOPILOT_LOG.md").write_text("", encoding="utf-8")
    snap = tower_sync.build_snapshot(tmp_path, cfg())
    assert snap["recent_events"] == []
