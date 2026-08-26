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
