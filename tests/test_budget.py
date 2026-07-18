"""Tests for scripts/budget.py — dispatch ceiling tracking (Wave B)."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import budget  # noqa: E402

NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def ts(minutes_ago: int) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime(budget.UTC_FMT)


# --------------------------------------------------------------- quiet_hours
class TestQuietHours:
    def test_in_quiet_hours(self):
        assert budget.in_quiet_hours(NOW, [12, 13]) is True

    def test_not_in_quiet_hours(self):
        assert budget.in_quiet_hours(NOW, [1, 2, 3]) is False

    def test_empty_quiet_hours(self):
        assert budget.in_quiet_hours(NOW, []) is False

    def test_none_quiet_hours(self):
        assert budget.in_quiet_hours(NOW, None) is False


# ---------------------------------------------------------- dispatch counting
class TestDispatchesInLastHour:
    def test_empty_log(self):
        assert budget.dispatches_in_last_hour([], NOW) == 0

    def test_counts_within_60_minutes(self):
        log = [ts(10), ts(30), ts(59)]
        assert budget.dispatches_in_last_hour(log, NOW) == 3

    def test_excludes_older_than_60_minutes(self):
        log = [ts(10), ts(61), ts(120)]
        assert budget.dispatches_in_last_hour(log, NOW) == 1

    def test_exactly_60_minutes_excluded(self):
        # cutoff is `now - 60m`; an entry exactly at cutoff is not > cutoff.
        log = [ts(60)]
        assert budget.dispatches_in_last_hour(log, NOW) == 0

    def test_malformed_timestamps_ignored_not_crashed(self):
        log = ["not-a-timestamp", ts(5), ""]
        assert budget.dispatches_in_last_hour(log, NOW) == 1


# --------------------------------------------------------------- can_dispatch
class TestCanDispatch:
    def test_allowed_under_ceiling(self):
        cfg = {"max_dispatches_per_hour": 6, "quiet_hours": []}
        log = [ts(10), ts(20)]
        allowed, reason = budget.can_dispatch(log, cfg, NOW)
        assert allowed is True
        assert reason == ""

    def test_denied_at_ceiling(self):
        cfg = {"max_dispatches_per_hour": 3, "quiet_hours": []}
        log = [ts(10), ts(20), ts(30)]
        allowed, reason = budget.can_dispatch(log, cfg, NOW)
        assert allowed is False
        assert "ceiling" in reason.lower() or "3" in reason

    def test_denied_over_ceiling(self):
        cfg = {"max_dispatches_per_hour": 2, "quiet_hours": []}
        log = [ts(10), ts(20), ts(30), ts(40)]
        allowed, _ = budget.can_dispatch(log, cfg, NOW)
        assert allowed is False

    def test_denied_in_quiet_hours_even_with_room_in_ceiling(self):
        cfg = {"max_dispatches_per_hour": 100, "quiet_hours": [12]}
        allowed, reason = budget.can_dispatch([], cfg, NOW)
        assert allowed is False
        assert "quiet_hours" in reason

    def test_quiet_hours_reason_takes_precedence(self):
        cfg = {"max_dispatches_per_hour": 1, "quiet_hours": [12]}
        allowed, reason = budget.can_dispatch([ts(5)], cfg, NOW)
        assert allowed is False
        assert "quiet_hours" in reason

    def test_defaults_used_when_cfg_missing_keys(self):
        allowed, _ = budget.can_dispatch([], {}, NOW)
        assert allowed is True  # empty log, default ceiling 6

    def test_ceiling_resets_after_window_passes(self):
        cfg = {"max_dispatches_per_hour": 2, "quiet_hours": []}
        # Two dispatches, both now stale (>60m old) — ceiling should have
        # "reset" since they're no longer in the trailing-60m window.
        log = [ts(90), ts(100)]
        allowed, _ = budget.can_dispatch(log, cfg, NOW)
        assert allowed is True

    def test_quiet_hours_resets_outside_the_configured_hour(self):
        cfg = {"max_dispatches_per_hour": 100, "quiet_hours": [12]}
        later = NOW.replace(hour=13)
        allowed, _ = budget.can_dispatch([], cfg, later)
        assert allowed is True


# ------------------------------------------------------------- record_dispatch
class TestRecordDispatch:
    def test_appends_current_timestamp(self):
        log = budget.record_dispatch([], NOW)
        assert len(log) == 1
        assert log[0] == NOW.strftime(budget.UTC_FMT)

    def test_prunes_entries_older_than_2h(self):
        old = (NOW - timedelta(hours=3)).strftime(budget.UTC_FMT)
        recent = (NOW - timedelta(minutes=30)).strftime(budget.UTC_FMT)
        log = budget.record_dispatch([old, recent], NOW)
        assert old not in log
        assert recent in log

    def test_keeps_entries_within_2h(self):
        recent = (NOW - timedelta(hours=1, minutes=30)).strftime(budget.UTC_FMT)
        log = budget.record_dispatch([recent], NOW)
        assert recent in log

    def test_caps_at_max_keep(self):
        log = [ts(1)] * 600
        result = budget.record_dispatch(log, NOW, max_keep=500)
        assert len(result) == 500

    def test_new_entry_always_survives_cap(self):
        log = [ts(1)] * 500
        result = budget.record_dispatch(log, NOW, max_keep=500)
        assert result[-1] == NOW.strftime(budget.UTC_FMT)


# ============================================================ integration ===
class TestBudgetIntegrationFlow:
    """Simulates the actual decide()->execute() cycle: record dispatches one
    at a time and confirm the ceiling engages and later resets exactly as
    supervisor.py will use it."""

    def test_hits_ceiling_then_recovers_next_hour(self):
        cfg = {"max_dispatches_per_hour": 3, "quiet_hours": []}
        log: list[str] = []
        now = NOW
        for _ in range(3):
            allowed, _ = budget.can_dispatch(log, cfg, now)
            assert allowed is True
            log = budget.record_dispatch(log, now)
        # 4th dispatch this hour should be denied.
        allowed, reason = budget.can_dispatch(log, cfg, now)
        assert allowed is False
        # An hour later, the ceiling has room again.
        later = now + timedelta(hours=1, minutes=1)
        allowed, _ = budget.can_dispatch(log, cfg, later)
        assert allowed is True
