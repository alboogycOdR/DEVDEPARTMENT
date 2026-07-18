"""Wave C tests — scripts/retro.py + scripts/scheduling.py"""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import retro as rm  # noqa: E402
import scheduling as sch  # noqa: E402
import instincts as im  # noqa: E402

PLAN = """---
plan_version: 3
last_updated: 2026-07-14T00:00:00Z
overall_status: active
---

### TASK-301
**Status:** done
**Owned_Paths:** python/orb/**
**Started_At:** 2026-07-10T08:00:00Z
**Updated_At:** 2026-07-10T12:30:00Z

### TASK-302
**Status:** done
**Owned_Paths:** flutter/**
**Started_At:** 2026-07-11T08:00:00Z
**Updated_At:** 2026-07-11T09:00:00Z

### TASK-303
**Status:** in_progress
**Owned_Paths:** docs/**
**Started_At:** 2026-07-12T08:00:00Z
**Updated_At:** 2026-07-12T09:00:00Z

### TASK-304
**Status:** done
**Owned_Paths:** python/orb/**
**Started_At:** 2026-07-13T08:00:00Z
**Updated_At:** 2026-07-13T10:00:00Z
"""

REVIEW = "\n".join([
    "| TASK-301 | GB | rework | tests | note | 2026-07-10T13:00:00Z |",
    "| TASK-301 | GB | approved | — | note | 2026-07-10T15:00:00Z |",
    "| TASK-302 | CX | approved first-pass | — | note | 2026-07-11T10:00:00Z |",
    "| TASK-304 | GB | approved first-pass | — | note | 2026-07-13T11:00:00Z |",
])


class TestCycleTime:
    def test_done_tasks_only_correct_hours(self):
        c = rm.cycle_times_hours(PLAN)
        assert c == {"TASK-301": 4.5, "TASK-302": 1.0, "TASK-304": 2.0}
        assert "TASK-303" not in c  # not done


class TestChurnAndEffectiveness:
    def test_territory_churn(self):
        churn = rm.territory_churn(PLAN, rm.review_outcomes(REVIEW))
        assert churn == {"python": 1}

    def test_instinct_effectiveness_comparison(self, tmp_path):
        # Instinct covering python/orb/** — TASK-301 (rework) and TASK-304 (clean)
        im.save_atomic(tmp_path, [im.Instinct(
            inst_id="INST-001", rule="ship tests", territory=["python/orb/**"],
            confidence=0.7, source=["TASK-301 rework"], status="active")])
        eff = rm.instinct_effectiveness(tmp_path, PLAN, rm.review_outcomes(REVIEW))
        assert eff["total_reviews"] == 4
        assert eff["matched_reviews"] == 3  # both TASK-301 rows + TASK-304
        assert eff["project_first_pass_rate"] == 0.75
        assert eff["instinct_matched_first_pass_rate"] == round(2 / 3, 3)


class TestRetroRun:
    def test_drafts_file_and_never_mutates_state(self, tmp_path):
        (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
        (tmp_path / "REVIEW.md").write_text(REVIEW, encoding="utf-8")
        (tmp_path / "AUTOPILOT_LOG.md").write_text("- [ts] P2 escalation\n", encoding="utf-8")
        im.save_atomic(tmp_path, [im.Instinct(
            inst_id="INST-001", rule="r", territory=["python/**"],
            confidence=0.7, source=["TASK-301 rework"], status="active")])
        d = tmp_path / ".devteam" / "pending_amendments"
        d.mkdir(parents=True)
        (d / "AMEND-002.md").write_text("# AMEND-002\n**Status:** pending\nx\n", encoding="utf-8")

        before_instincts = (tmp_path / "INSTINCTS.md").read_bytes()
        out = rm.run(tmp_path, {})
        assert out and out.name.startswith("RETRO-") and out.exists()
        text = out.read_text(encoding="utf-8")
        assert "TASK-301 (4.5h)" in text
        assert "AMEND-002" in text and "/approve AMEND-002" in text
        assert "Instinct effectiveness" in text
        # descriptive only — no mutation
        assert (tmp_path / "INSTINCTS.md").read_bytes() == before_instincts

    def test_fail_open_on_empty_repo(self, tmp_path):
        out = rm.run(tmp_path, {})
        assert out is not None  # still drafts a (sparse) retro
        assert "No completed tasks" in out.read_text(encoding="utf-8")


class TestScheduling:
    """Real scripts/scheduling.py (shipped in Wave B) uses should_run_daily /
    should_run_weekly, not due_daily / due_weekly — and should_run_weekly
    requires an EXACT weekday match (no "catch up later in the week" grace),
    matching maintenance.py's already-tested daily behaviour. These tests
    exercise the real, shipped semantics rather than the ones retro.py's
    zip package guessed at."""

    def test_should_run_daily_before_hour_false(self, tmp_path):
        m = tmp_path / "marker.txt"
        now = datetime(2026, 7, 15, 1, 0, tzinfo=timezone.utc)
        assert not sch.should_run_daily(m, 2, now)

    def test_should_run_daily_once_per_day(self, tmp_path):
        m = tmp_path / "marker.txt"
        now = datetime(2026, 7, 15, 3, 0, tzinfo=timezone.utc)
        assert sch.should_run_daily(m, 2, now)
        sch.mark_done_daily(m, now)
        assert not sch.should_run_daily(m, 2, now)
        nxt = datetime(2026, 7, 16, 3, 0, tzinfo=timezone.utc)
        assert sch.should_run_daily(m, 2, nxt)

    def test_should_run_weekly_once_per_iso_week(self, tmp_path):
        m = tmp_path / "wk.txt"
        mon = datetime(2026, 7, 13, 9, 0, tzinfo=timezone.utc)  # Monday W29
        assert sch.should_run_weekly(m, 0, 8, mon)
        sch.mark_done_weekly(m, mon)
        # Still Monday's own ISO week — already marked, so no re-fire even
        # later the same day.
        later_mon = datetime(2026, 7, 13, 20, 0, tzinfo=timezone.utc)
        assert not sch.should_run_weekly(m, 0, 8, later_mon)
        nxt_mon = datetime(2026, 7, 20, 9, 0, tzinfo=timezone.utc)
        assert sch.should_run_weekly(m, 0, 8, nxt_mon)

    def test_should_run_weekly_requires_exact_day(self, tmp_path):
        m = tmp_path / "wk.txt"
        # day_of_week=0 (Monday) configured; Friday of the same week must
        # NOT fire, even though it's after Monday and the hour has passed —
        # should_run_weekly matches maintenance's exact-gate philosophy,
        # not a "catch up any day this week" one.
        fri = datetime(2026, 7, 17, 9, 0, tzinfo=timezone.utc)
        assert not sch.should_run_weekly(m, 0, 8, fri)

    def test_should_run_weekly_before_hour_false(self, tmp_path):
        m = tmp_path / "wk.txt"
        mon_early = datetime(2026, 7, 13, 7, 0, tzinfo=timezone.utc)
        assert not sch.should_run_weekly(m, 0, 8, mon_early)
