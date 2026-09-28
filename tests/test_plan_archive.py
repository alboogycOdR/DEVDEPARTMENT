"""E-D plan archive: stubs, round-trip, notes cap, dependency visibility."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import plan_archive  # noqa: E402
from control import _deps_done as control_deps_done  # noqa: E402
from instincts import _deps_done as instincts_deps_done  # noqa: E402
from validate_plan import Report, parse_tasks, validate  # noqa: E402
from validate_plan import _deps_done as validate_deps_done  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "plan_archive" / "sample_plan.md"
NOW = datetime(2026, 9, 27, 14, 30, 0, tzinfo=timezone.utc)

FM = """---
plan_version: 1.0
last_updated: 2026-09-01T00:00:00Z
overall_status: in_progress
---

# Plan

"""


def _block(n: int, wave: str | None, status: str, updated: str, depends: str = "—") -> str:
    title = f"Wave {wave} item {n}" if wave else f"Pre-wave item {n}"
    return f"""### TASK-{n:03d}
**Title:** {title}
**Status:** {status}
**Assigned_To:** GB
**Priority:** low
**Spec_References:** specs/x.md
**Owned_Paths:** src/t{n}/**
**Depends_On:** {depends}
**Description:** {"body " * 40}task {n}
**Acceptance_Criteria:**
- [x] done
**Branch:** —
**Started_At:** —
**Progress_Notes:** {"note " * 20}
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** ORCH
**Updated_At:** {updated}
"""


def _synthetic_366() -> tuple[str, dict[str, str]]:
    """366 done tasks older than wave E, plus one open wave-E task and one
    done wave-E task that must stay in PLAN.md."""
    parts = [FM]
    original: dict[str, str] = {}
    months = [f"2026-{m:02d}" for m in range(1, 9)]
    for n in range(1, 366):
        wave = "ABCD"[(n - 1) % 4]
        updated = f"{months[(n - 1) % 8]}-15T12:00:{n % 60:02d}Z"
        block = _block(n, wave, "done", updated)
        original[f"TASK-{n:03d}"] = block
        parts.append(block)
    # 366th done task has no wave letter: still older than the current wave.
    block = _block(366, None, "done", "2026-04-02T03:04:05Z")
    original["TASK-366"] = block
    parts.append(block)
    parts.append(_block(367, "E", "pending", "2026-09-01T08:00:01Z", depends="TASK-001"))
    parts.append(_block(368, "E", "done", "2026-09-02T08:00:02Z"))
    return "".join(parts), original


def test_fixture_block_round_trips_including_interior_blank_line(tmp_path):
    text = FIXTURE.read_text(encoding="utf-8")
    original = dict(plan_archive.iter_blocks(text))
    assert "\n\nblank line above stays" in original["TASK-010"]
    result = plan_archive.apply_archive(tmp_path, text)
    assert result.changed
    assert [b.task_id for b in result.blocks] == ["TASK-010"]
    archive = (tmp_path / "plan" / "archive" / "2026-01.md").read_bytes()
    assert b"\r\n" not in archive
    recovered = plan_archive.read_archived_block(archive.decode("utf-8"), "TASK-010")
    assert recovered == original["TASK-010"]
    assert "### TASK-010\n**Status:** done\n**Archived:** plan/archive/2026-01.md\n" in result.text
    assert "Wave E still open" in result.text


def test_synthetic_366_is_under_60kb_validates_and_round_trips(tmp_path):
    text, original = _synthetic_366()
    assert len(text.encode("utf-8")) > 60 * 1024
    result = plan_archive.apply_archive(tmp_path, text)
    assert len(result.blocks) == 366
    encoded = result.text.encode("utf-8")
    assert len(encoded) < 60 * 1024
    rep = validate(result.text)
    assert rep.ok, rep.errors
    assert "TASK-368" not in {b.task_id for b in result.blocks}
    assert "Wave E item 368" in result.text

    by_month: dict[str, str] = {}
    for path in (tmp_path / "plan" / "archive").glob("*.md"):
        by_month[path.name] = path.read_text(encoding="utf-8")
        assert b"\r\n" not in path.read_bytes()
    for block in result.blocks:
        recovered = plan_archive.read_archived_block(by_month[f"{block.month}.md"], block.task_id)
        assert recovered == original[block.task_id]
        assert recovered == block.block

    tasks = parse_tasks(result.text, Report())
    by_id = {t.task_id: t for t in tasks}
    pending = by_id["TASK-367"]
    assert validate_deps_done(pending, by_id)
    assert control_deps_done(pending, by_id)
    assert instincts_deps_done(pending, by_id)
    assert by_id["TASK-001"].get("Status") == "done"
    assert by_id["TASK-001"].get("Archived") == "plan/archive/2026-01.md"


def test_second_pass_is_idempotent_and_append_only(tmp_path):
    first_text = FM + _block(1, "A", "done", "2026-01-15T01:02:03Z") + _block(
        9, "E", "pending", "2026-09-01T08:00:01Z", depends="TASK-001")
    first = plan_archive.apply_archive(tmp_path, first_text)
    archive_path = tmp_path / "plan" / "archive" / "2026-01.md"
    before = archive_path.read_bytes()

    again = plan_archive.apply_archive(tmp_path, first.text)
    assert again.changed is False
    assert archive_path.read_bytes() == before

    second_text = first.text.replace(
        "### TASK-009",
        _block(2, "B", "done", "2026-01-20T04:05:06Z") + "### TASK-009",
    )
    second = plan_archive.apply_archive(tmp_path, second_text)
    assert [b.task_id for b in second.blocks] == ["TASK-002"]
    after = archive_path.read_bytes()
    assert after.startswith(before)
    assert after != before
    combined = after.decode("utf-8")
    assert plan_archive.read_archived_block(combined, "TASK-001") == first.blocks[0].block
    assert "### TASK-002" in plan_archive.read_archived_block(combined, "TASK-002")


def test_notes_overflow_rotates_to_handover_and_leaves_a_pointer(tmp_path):
    notes = "N" * 4001
    text = (
        "---\n"
        "plan_version: 1.0\n"
        "last_updated: 2026-09-01T00:00:00Z\n"
        "overall_status: in_progress\n"
        f"orchestrator_notes: {__import__('json').dumps(notes)}\n"
        "---\n\n"
        + _block(9, "E", "pending", "2026-09-01T08:00:01Z")
    )
    assert any("notes_max_chars" in w for w in validate(text).warnings)
    rotated, path = plan_archive.rotate_notes(text, tmp_path, NOW)
    assert path is not None
    assert path == tmp_path / "docs" / "handovers" / "2026-09-27-notes.md"
    assert notes in path.read_text(encoding="utf-8")
    assert "docs/handovers/2026-09-27-notes.md" in plan_archive.orchestrator_notes(rotated)
    assert len(plan_archive.orchestrator_notes(rotated)) <= 4000
    assert not any("notes_max_chars" in w for w in validate(rotated).warnings)
    # A second rotation of an already-short pointer writes nothing.
    again, again_path = plan_archive.rotate_notes(rotated, tmp_path, NOW)
    assert again_path is None
    assert again == rotated


def test_dry_run_writes_nothing(tmp_path, capsys):
    plan = tmp_path / "PLAN.md"
    plan.write_text(
        FM
        + _block(1, "A", "done", "2026-03-01T00:00:07Z")
        + _block(9, "E", "pending", "2026-09-01T08:00:09Z", depends="TASK-001"),
        encoding="utf-8", newline="\n",
    )
    rc = plan_archive.main(["--repo", str(tmp_path), "--dry-run"])
    assert rc == 0
    assert "would archive TASK-001" in capsys.readouterr().out
    assert not (tmp_path / "plan").exists()
    assert "Wave A item 1" in plan.read_text(encoding="utf-8")


def test_no_wave_context_archives_nothing():
    text = FM + _block(1, None, "done", "2026-01-01T00:00:08Z")
    result = plan_archive.plan_archive(text)
    assert result.changed is False
    assert result.text == text
