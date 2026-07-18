#!/usr/bin/env python3
"""scheduling.py — Shared "has this scheduled hour/day passed and not yet run?"
helper (Wave B).

Factored out per the v4 completion spec's cross-wave integration note: the
nightly maintenance audit (Wave B) and the weekly retro drafter (Wave C) both
need the exact same idempotency-marker pattern — "am I due, and have I
already run for this period?" — so it lives here once instead of being
duplicated in maintenance.py, retro.py, and any future scheduled task.

Design: pure functions over a marker file's contents + the current time. No
globals, no state beyond the one marker file each caller owns. A marker file
holds nothing but a short string (a date or an ISO-week key) — trivially
inspectable, git-ignorable, and safe to delete to force a re-run.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path


def _read_marker(marker_path: Path) -> str:
    try:
        return marker_path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, OSError):
        return ""


def _write_marker(marker_path: Path, value: str) -> None:
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(value, encoding="utf-8")


# ------------------------------------------------------------------- daily --
def should_run_daily(marker_path: Path, hour_utc: int, now: datetime) -> bool:
    """True iff `now`'s UTC hour has reached `hour_utc` AND today's date isn't
    already recorded in the marker file.

    This single check covers both halves of "runs once per configured UTC
    hour, idempotent": the hour gate (don't fire at 09:00 if configured for
    02:00) and same-day idempotency (a supervisor restart mid-day, or a
    second tick in the same hour, must not re-trigger the run).
    """
    if now.hour < hour_utc:
        return False
    today = now.strftime("%Y-%m-%d")
    return _read_marker(marker_path) != today


def mark_done_daily(marker_path: Path, now: datetime) -> None:
    _write_marker(marker_path, now.strftime("%Y-%m-%d"))


# ------------------------------------------------------------------ weekly --
def _iso_week_key(now: datetime) -> str:
    iso_year, iso_week, _ = now.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def should_run_weekly(marker_path: Path, day_of_week: int, hour_utc: int, now: datetime) -> bool:
    """day_of_week follows datetime.weekday(): 0=Monday .. 6=Sunday.

    True iff `now` is on the configured weekday, at/after the configured UTC
    hour, AND this ISO week isn't already recorded in the marker file.
    """
    if now.weekday() != day_of_week or now.hour < hour_utc:
        return False
    return _read_marker(marker_path) != _iso_week_key(now)


def mark_done_weekly(marker_path: Path, now: datetime) -> None:
    _write_marker(marker_path, _iso_week_key(now))
