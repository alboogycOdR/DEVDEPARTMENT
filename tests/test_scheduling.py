"""Tests for scripts/scheduling.py — shared daily/weekly idempotency-marker
helper (Wave B, reused by Wave C's retro drafter)."""
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import scheduling  # noqa: E402


def dt(y, m, d, h=0, mi=0):
    return datetime(y, m, d, h, mi, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------- daily --
class TestShouldRunDaily:
    def test_before_hour_never_runs(self, tmp_path):
        marker = tmp_path / "marker.txt"
        assert scheduling.should_run_daily(marker, hour_utc=2, now=dt(2026, 7, 18, 1, 59)) is False

    def test_at_or_after_hour_runs_when_no_marker(self, tmp_path):
        marker = tmp_path / "marker.txt"
        assert scheduling.should_run_daily(marker, hour_utc=2, now=dt(2026, 7, 18, 2, 0)) is True
        assert scheduling.should_run_daily(marker, hour_utc=2, now=dt(2026, 7, 18, 23, 0)) is True

    def test_already_ran_today_is_false(self, tmp_path):
        marker = tmp_path / "marker.txt"
        scheduling.mark_done_daily(marker, dt(2026, 7, 18, 2, 5))
        assert scheduling.should_run_daily(marker, hour_utc=2, now=dt(2026, 7, 18, 14, 0)) is False

    def test_new_day_runs_again(self, tmp_path):
        marker = tmp_path / "marker.txt"
        scheduling.mark_done_daily(marker, dt(2026, 7, 18, 2, 5))
        assert scheduling.should_run_daily(marker, hour_utc=2, now=dt(2026, 7, 19, 2, 5)) is True

    def test_restart_mid_day_does_not_rerun(self, tmp_path):
        """Simulates the exact scenario the spec calls out: a supervisor
        restart mid-day must not re-trigger an already-completed run."""
        marker = tmp_path / "marker.txt"
        scheduling.mark_done_daily(marker, dt(2026, 7, 18, 2, 5))
        # "Restart" = fresh check against the same marker file, later same day.
        assert scheduling.should_run_daily(marker, hour_utc=2, now=dt(2026, 7, 18, 16, 30)) is False

    def test_missing_marker_file_treated_as_never_run(self, tmp_path):
        marker = tmp_path / "nested" / "marker.txt"
        assert not marker.exists()
        assert scheduling.should_run_daily(marker, hour_utc=0, now=dt(2026, 7, 18, 0, 0)) is True

    def test_corrupted_marker_treated_as_never_run(self, tmp_path):
        marker = tmp_path / "marker.txt"
        marker.write_text("\x00\x01garbage", encoding="utf-8", errors="ignore")
        # Corrupted/garbage content just won't match today's date string —
        # treated the same as "never run", not an error.
        assert scheduling.should_run_daily(marker, hour_utc=0, now=dt(2026, 7, 18, 0, 0)) is True

    def test_mark_done_daily_creates_parent_dirs(self, tmp_path):
        marker = tmp_path / "a" / "b" / "c" / "marker.txt"
        scheduling.mark_done_daily(marker, dt(2026, 7, 18, 2, 0))
        assert marker.exists()
        assert marker.read_text(encoding="utf-8").strip() == "2026-07-18"


# ------------------------------------------------------------------ weekly --
class TestShouldRunWeekly:
    def test_wrong_weekday_never_runs(self, tmp_path):
        marker = tmp_path / "marker.txt"
        # 2026-07-18 is a Saturday (weekday()==5); ask for Monday (0).
        assert scheduling.should_run_weekly(marker, day_of_week=0, hour_utc=0, now=dt(2026, 7, 18)) is False

    def test_correct_weekday_before_hour_does_not_run(self, tmp_path):
        marker = tmp_path / "marker.txt"
        assert scheduling.should_run_weekly(marker, day_of_week=5, hour_utc=10, now=dt(2026, 7, 18, 9, 0)) is False

    def test_correct_weekday_and_hour_runs(self, tmp_path):
        marker = tmp_path / "marker.txt"
        assert scheduling.should_run_weekly(marker, day_of_week=5, hour_utc=10, now=dt(2026, 7, 18, 10, 0)) is True

    def test_already_ran_this_week_is_false(self, tmp_path):
        marker = tmp_path / "marker.txt"
        scheduling.mark_done_weekly(marker, dt(2026, 7, 18, 10, 0))
        assert scheduling.should_run_weekly(marker, day_of_week=5, hour_utc=10, now=dt(2026, 7, 18, 20, 0)) is False

    def test_next_week_same_weekday_runs_again(self, tmp_path):
        marker = tmp_path / "marker.txt"
        scheduling.mark_done_weekly(marker, dt(2026, 7, 18, 10, 0))
        assert scheduling.should_run_weekly(marker, day_of_week=5, hour_utc=10, now=dt(2026, 7, 25, 10, 0)) is True

    def test_iso_week_key_format(self, tmp_path):
        marker = tmp_path / "marker.txt"
        scheduling.mark_done_weekly(marker, dt(2026, 7, 18, 10, 0))
        content = marker.read_text(encoding="utf-8").strip()
        assert content.startswith("2026-W")
