"""Wave E lane and status contracts."""
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from control import validate_control  # noqa: E402
from supervisor import DEFAULT_CONFIG, RuntimeState, _owner_hold_digest, decide  # noqa: E402
from validate_plan import lint_review, validate  # noqa: E402

FM = """---
plan_version: 1.0
last_updated: 2026-09-30T10:00:00Z
overall_status: in_progress
---
"""
NOW = datetime(2026, 9, 30, 15, tzinfo=timezone.utc)


def task(*, status="pending", assignee="CX", owned="src/a.py", kind="code",
         hold="—", superseded="—", evidence="—", maker="—", artifacts="—",
         blocked="—"):
    return f"""
### TASK-901
**Title:** Fixture
**Status:** {status}
**Type:** {kind}
**Assigned_To:** {assignee}
**Priority:** medium
**Spec_References:** specs/x.md
**Owned_Paths:** {owned}
**Description:** Fixture work
**Acceptance_Criteria:**
- [ ] Complete the task
**Branch:** —
**Started_At:** 2026-09-30T10:00:00Z
**Progress_Notes:** —
**Artifacts:** {artifacts}
**Test_Evidence:** —
**Evidence:** {evidence}
**Review_Findings:** —
**Blocked_Reason:** {blocked}
**Hold_On:** {hold}
**Superseded_By:** {superseded}
**Maker_Model:** {maker}
**Updated_By:** ORCH
**Updated_At:** 2026-09-30T10:00:00Z
"""


def review(model="claude-sonnet-5"):
    return f"| TASK-901 | ORCH-SOLO | approved | reviewer_model: {model}; verified | first-pass: yes | 2026-09-30T15:00:00Z |"


def test_superseded_requires_successor_and_is_terminal():
    assert any("Superseded_By" in e for e in validate(FM + task(status="superseded")).errors)
    plan = FM + task(status="superseded", superseded="TASK-902")
    assert validate(plan).ok
    actions = decide(plan, RuntimeState(), DEFAULT_CONFIG, NOW)
    assert all(a.kind != "REVIEW" for a in actions)
    assert any(a.kind == "DIGEST" for a in actions)


def test_owner_hold_requires_reason_and_is_never_dispatched_or_triaged():
    assert any("Hold_On" in e for e in validate(FM + task(status="owner_hold")).errors)
    plan = FM + task(status="owner_hold", hold="EXTERNAL: store approval")
    assert validate(plan).ok
    actions = decide(plan, RuntimeState(), DEFAULT_CONFIG, NOW)
    assert not {"DISPATCH", "TRIAGE_UNBLOCK", "REDISPATCH_STALE"}.intersection(a.kind for a in actions)
    digest = _owner_hold_digest("Pending action:\n- none\nProd: untouched", plan, NOW)
    assert "TASK-901 owner hold: EXTERNAL: store approval (5h)" in digest


def test_external_done_requires_evidence_and_review_row():
    plan = FM + task(status="done", kind="external", owned="—")
    assert any("Evidence line" in e for e in validate(plan).errors)
    plan = FM + task(status="done", kind="external", owned="—", evidence="store-review ID 42")
    assert any("REVIEW row" in e for e in validate(plan, review_text="").errors)
    assert validate(plan, review_text=review()).ok


def test_solo_done_requires_independent_reviewer_and_obeys_file_cap():
    plan = FM + task(status="done", assignee="ORCH-SOLO", maker="claude-opus-4-8")
    assert any("different model" in e for e in validate(plan, review_text="").errors)
    assert any("different model" in e for e in validate(plan, review_text=review("claude-opus-4-8")).errors)
    assert validate(plan, review_text=review()).ok
    assert validate(plan, review_text=review("human")).ok
    six = FM + task(status="pending", assignee="ORCH-SOLO", maker="claude-opus-4-8",
                    owned=", ".join(f"src/{i}.py" for i in range(6)))
    assert any("solo_max_files" in e for e in validate(six).errors)
    wildcard = FM + task(status="pending", assignee="ORCH-SOLO", maker="claude-opus-4-8",
                         owned="src/**")
    assert any("exact files" in e for e in validate(wildcard).errors)


def test_review_lint_accepts_solo_and_external_rows():
    table = """| Task | Unit | Verdict | Findings | First pass? | Date |
|---|---|---|---|---|---|
""" + review() + "\n| TASK-902 | ORCH | approved | store approval | first-pass: yes | 2026-09-30T15:00:00Z |\n"
    assert lint_review(table).ok


def test_blocked_reason_requires_category_and_detail_in_plan_and_control():
    bad = FM + task(status="blocked", blocked="waiting for credentials")
    assert any("Blocked_Reason" in e for e in validate(bad).errors)
    bare = FM + task(status="blocked", blocked="CAPACITY")
    assert any("Blocked_Reason" in e for e in validate(bare).errors)
    good = FM + task(status="blocked", blocked="CAPACITY: usage window exhausted")
    assert validate(good).ok
    block = {"task": "TASK-901", "unit": "CX", "status": "blocked",
             "blocked_reason": "CAPACITY: usage window exhausted"}
    assert validate_control(block, "TASK-901", "CX")[0]
    block["blocked_reason"] = "CAPACITY"
    assert not validate_control(block, "TASK-901", "CX")[0]
