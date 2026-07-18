#!/usr/bin/env python3
"""budget.py — Dispatch ceiling tracking (Wave B).

Consulted by supervisor.decide() before it emits a DISPATCH action. Pure
timestamp-counting rate limiter — intentionally simple, no live usage-API
polling (that's a possible future refinement, out of scope here). Just enough
arithmetic to stop a misconfigured or looping wave from burning through
builder-CLI usage unattended overnight.

State lives in RuntimeState.dispatch_log (a list of ISO-8601 UTC timestamp
strings), which is persisted the same way everything else in
.autopilot_state.json already is — no new state file, per the spec's
"extend the existing state file" instruction.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

DEFAULT_BUDGET_CFG = {"max_dispatches_per_hour": 6, "quiet_hours": []}

UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, UTC_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def in_quiet_hours(now: datetime, quiet_hours: list[int]) -> bool:
    """quiet_hours: list of UTC hour integers (0-23) during which no new
    dispatch may be emitted, regardless of the hourly ceiling."""
    return now.hour in (quiet_hours or [])


def dispatches_in_last_hour(dispatch_log: list[str], now: datetime) -> int:
    cutoff = now - timedelta(minutes=60)
    return sum(1 for ts in (dispatch_log or []) if (_parse_ts(ts) or cutoff) > cutoff)


def can_dispatch(dispatch_log: list[str], cfg: dict, now: datetime) -> tuple[bool, str]:
    """Returns (allowed, reason). reason is "" when allowed=True."""
    cfg = {**DEFAULT_BUDGET_CFG, **(cfg or {})}
    if in_quiet_hours(now, cfg["quiet_hours"]):
        return False, f"UTC hour {now.hour} is in quiet_hours {cfg['quiet_hours']}"
    limit = cfg["max_dispatches_per_hour"]
    count = dispatches_in_last_hour(dispatch_log, now)
    if count >= limit:
        return False, f"{count} dispatch(es) in the trailing 60m >= ceiling {limit}"
    return True, ""


def record_dispatch(dispatch_log: list[str], now: datetime, max_keep: int = 500) -> list[str]:
    """Append now's timestamp; prune anything older than 2h so the log (and
    the state file it lives in) never grows unbounded across a long-running
    --loop session."""
    cutoff = now - timedelta(hours=2)
    pruned = [ts for ts in (dispatch_log or []) if (_parse_ts(ts) or cutoff) > cutoff]
    pruned.append(now.strftime(UTC_FMT))
    return pruned[-max_keep:]
