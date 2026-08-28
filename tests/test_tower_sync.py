import json
import re
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
        # Open vocabulary (§1 v1.2): all five example kinds are well-formed
        # uppercase tokens, whether or not they're in the historical enum.
        assert event["kind"] in _VALID_KINDS
        assert isinstance(event["text"], str)


def test_recent_events_ts_and_kind_parsed_from_real_log_line(tmp_path):
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    line = "- [2026-08-16T18:34:45Z] DISPATCH: S5 idle; dispatching onto TASK-010 (line-endings)"
    (tmp_path / "AUTOPILOT_LOG.md").write_text(line + "\n", encoding="utf-8")
    snap = tower_sync.build_snapshot(tmp_path, cfg())
    [event] = snap["recent_events"]
    assert event == {"ts": "2026-08-16T18:34:45Z", "kind": "DISPATCH",
                      "text": "S5 idle; dispatching onto TASK-010 (line-endings)",
                      "task_id": "TASK-010", "unit": None}


def test_recent_events_skips_lines_not_matching_log_format(tmp_path):
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    log = ("- [2026-08-16T18:34:45Z] DISPATCH: valid line\n"
           "this line has no bracketed timestamp at all\n"
           "- missing brackets entirely: still not a match\n")
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


# --- TASK-020: open vocabulary (§1 v1.1) — the closed enum silently dropped
# 14 of the 17 kinds the pack actually emits. These lines are the REAL
# formats supervisor.py's log_line() call sites write (verbatim f-strings,
# not a hand-simplified fixture): DISPATCH_COMMAND/TG_COMMAND have no colon
# at all (`KIND key=value ...`); everything else uses the generic
# `KIND: detail` shape from execute()'s `f"{a.kind}: {a.detail}"`. ---

_REAL_LOG_LINES = {
    "DISPATCH": "- [2026-08-16T17:58:36Z] DISPATCH: S5 idle; dispatching onto TASK-010 (line-ending policy)",
    "REVIEW": "- [2026-08-16T18:00:00Z] REVIEW: TASK-004 awaiting review",
    "DISPATCH_COMMAND": "- [2026-08-16T18:00:05Z] DISPATCH_COMMAND unit=GB task=TASK-117 command=claude -p /devteam-dispatch",
    "TG_COMMAND": "- [2026-08-16T18:01:00Z] TG_COMMAND unit=TG cmd=/approve task=TASK-114",
    "TG_COMMAND_MISSING_TASK": "- [2026-08-16T18:01:05Z] TG_COMMAND unit=TOWER cmd=/status task=—",
    "MAINTENANCE": "- [2026-08-16T18:34:43Z] MAINTENANCE: Self-audit: PASS",
    "CONTROL": "- [2026-08-16T18:35:00Z] CONTROL: atlas.enabled -> applied (widen budget)",
    "DISTILL": "- [2026-08-16T18:36:00Z] DISTILL: queued 3 findings",
    "TRIAGE_UNBLOCK": "- [2026-08-16T18:37:00Z] TRIAGE_UNBLOCK: TASK-007: ORCH to re-carve territories and unblock (attempt 1)",
    "REDISPATCH_STALE": "- [2026-08-16T18:38:00Z] REDISPATCH_STALE: TASK-011 heartbeat stale (95m > 60m) — redispatch GB; its resume-first rule continues the existing branch",
    "DIGEST": "- [2026-08-16T21:43:37Z] DIGEST: WAVE COMPLETE — all 11 tasks done. Digest + halt.",
    "IDLE": "- [2026-08-16T18:39:00Z] IDLE: All lanes busy or waiting on dependencies — nothing to do this tick",
    "HALT": "- [2026-08-16T18:40:00Z] HALT: STOP file present in repo root — halting per safety rail #3",
    "MUTED": "- [2026-08-16T18:41:00Z] MUTED: suppressed P2 — amendment abc123",
    "RETRO": "- [2026-08-16T18:42:00Z] RETRO: drafted RETRO-2026-08-16.md",
    "DEFER_BUDGET": "- [2026-08-16T18:43:00Z] DEFER_BUDGET: GB deferred — dispatch-failure ceiling reached",
    "DEFER_USAGE": "- [2026-08-16T18:44:00Z] DEFER_USAGE: CX deferred — usage ceiling reached",
    "REVIEW_TG": "- [2026-08-16T18:45:00Z] REVIEW_TG: TG /approve TASK-114",
}


def test_recent_events_open_vocabulary_includes_previously_dropped_kinds(tmp_path):
    """§1 v1.1: the closed enum let only DISPATCH/REVIEW/DIGEST through and
    silently dropped 14 real kinds, MAINTENANCE among them. The producer must
    now emit any well-formed kind verbatim."""
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    log = "\n".join([_REAL_LOG_LINES["MAINTENANCE"], _REAL_LOG_LINES["CONTROL"],
                      _REAL_LOG_LINES["DISTILL"], _REAL_LOG_LINES["TG_COMMAND"]]) + "\n"
    (tmp_path / "AUTOPILOT_LOG.md").write_text(log, encoding="utf-8")
    snap = tower_sync.build_snapshot(tmp_path, cfg())
    kinds = {event["kind"] for event in snap["recent_events"]}
    assert kinds == {"MAINTENANCE", "CONTROL", "DISTILL", "TG_COMMAND"}


def test_recent_events_task_id_parsed_from_field_form(tmp_path):
    """DISPATCH_COMMAND/TG_COMMAND lines carry an explicit `task=TASK-NNN`
    field (and no colon after the kind at all) — both must parse."""
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    log = _REAL_LOG_LINES["DISPATCH_COMMAND"] + "\n" + _REAL_LOG_LINES["TG_COMMAND"] + "\n"
    (tmp_path / "AUTOPILOT_LOG.md").write_text(log, encoding="utf-8")
    snap = tower_sync.build_snapshot(tmp_path, cfg())
    [dc, tg] = snap["recent_events"]
    assert dc["kind"] == "DISPATCH_COMMAND" and dc["task_id"] == "TASK-117" and dc["unit"] == "GB"
    assert tg["kind"] == "TG_COMMAND" and tg["task_id"] == "TASK-114" and tg["unit"] == "TG"


def test_recent_events_task_id_parsed_from_inline_form(tmp_path):
    """DISPATCH/REVIEW/TRIAGE_UNBLOCK lines name TASK-NNN inline in prose,
    with no `task=` field at all — must still parse; unit stays null because
    it is never guessed from prose (H2)."""
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    log = _REAL_LOG_LINES["REVIEW"] + "\n" + _REAL_LOG_LINES["TRIAGE_UNBLOCK"] + "\n"
    (tmp_path / "AUTOPILOT_LOG.md").write_text(log, encoding="utf-8")
    snap = tower_sync.build_snapshot(tmp_path, cfg())
    [review, triage] = snap["recent_events"]
    assert review["task_id"] == "TASK-004" and review["unit"] is None
    assert triage["task_id"] == "TASK-007" and triage["unit"] is None


def test_recent_events_task_id_and_unit_null_when_absent(tmp_path):
    """No task/unit data at all in the line (MAINTENANCE), and the explicit
    `task=—` missing-marker TG_COMMAND writes when task_id is falsy — both
    must resolve to None, never a guessed value or the literal em-dash."""
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    log = _REAL_LOG_LINES["MAINTENANCE"] + "\n" + _REAL_LOG_LINES["TG_COMMAND_MISSING_TASK"] + "\n"
    (tmp_path / "AUTOPILOT_LOG.md").write_text(log, encoding="utf-8")
    snap = tower_sync.build_snapshot(tmp_path, cfg())
    [maint, tg] = snap["recent_events"]
    assert maint["task_id"] is None and maint["unit"] is None
    assert tg["task_id"] is None and tg["unit"] == "TOWER"


def test_recent_events_cross_side_contract_against_real_log_line_formats(tmp_path):
    """The cross-side contract gap that let TASK-019 ship: neither prior
    review exercised the producer's real output against the consumer's real
    field contract. This asserts, over EVERY one of the real supervisor.py
    log-line shapes (not a hand-simplified fixture), the exact field
    contract Tower's SnapshotV1 requires for recent_events entries: a
    non-empty string `ts`, a `kind` matching `[A-Z][A-Z0-9_]*`, and `task_id`/`unit`
    each either a string or None."""
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    log = "\n".join(_REAL_LOG_LINES.values()) + "\n"
    (tmp_path / "AUTOPILOT_LOG.md").write_text(log, encoding="utf-8")
    snap = tower_sync.build_snapshot(tmp_path, cfg())
    events = snap["recent_events"]
    assert len(events) == len(_REAL_LOG_LINES)
    kind_token = re.compile(r"^[A-Z][A-Z0-9_]*$")
    for event in events:
        assert isinstance(event["ts"], str) and event["ts"]
        assert kind_token.match(event["kind"])
        assert isinstance(event["text"], str)
        assert event["task_id"] is None or isinstance(event["task_id"], str)
        assert event["unit"] is None or isinstance(event["unit"], str)
    # The previously-dropped kinds are actually present, not just shaped right.
    assert {"MAINTENANCE", "CONTROL", "DISTILL", "TG_COMMAND", "DISPATCH_COMMAND",
            "REDISPATCH_STALE", "TRIAGE_UNBLOCK"} <= {e["kind"] for e in events}


def test_recent_events_accepts_alert_priority_kinds(tmp_path):
    """§1 v1.2 admits this project's real ALERT_P0/P1/P2 vocabulary."""
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    (tmp_path / "AUTOPILOT_LOG.md").write_text(
        "- [2026-08-28T16:30:00Z] ALERT_P0: digest\n"
        "- [2026-08-28T16:30:01Z] ALERT_P1: stop the line\n"
        "- [2026-08-28T16:30:02Z] ALERT_P2: decision needed\n",
        encoding="utf-8",
    )
    events = tower_sync.build_snapshot(tmp_path, cfg())["recent_events"]
    assert [event["kind"] for event in events] == ["ALERT_P0", "ALERT_P1", "ALERT_P2"]
