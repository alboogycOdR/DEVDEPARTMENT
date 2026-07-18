#!/usr/bin/env python3
"""budget.py — Dispatch ceiling tracking (Wave B) + usage-window gating (Wave I, I2).

Consulted by supervisor.decide() before it emits a DISPATCH action. Pure
timestamp-counting rate limiter — intentionally simple, no live usage-API
polling for the hourly ceiling itself (that's usage_probe.py's job, added in
Wave I). Just enough arithmetic to stop a misconfigured or looping wave from
burning through builder-CLI usage unattended overnight.

State lives in RuntimeState.dispatch_log (a list of ISO-8601 UTC timestamp
strings), which is persisted the same way everything else in
.autopilot_state.json already is — no new state file, per the spec's
"extend the existing state file" instruction.

Wave I (I2): before a DISPATCH, also consult usage_probe.get_usage()'s
cached provider usage. Above defer_above_pct on EITHER the 5h or 7d window
-> DEFER_USAGE, unless the task's Priority is critical AND
critical_overrides is true. Composes with the existing ceiling/quiet-hours
checks: can_dispatch() (below) still gates on those; can_dispatch_usage()
adds the usage gate on top, and the caller (supervisor.decide()) combines
both into a single log line when both are tripped, rather than two
redundant DEFER actions for the same non-dispatch.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

DEFAULT_BUDGET_CFG = {"max_dispatches_per_hour": 6, "quiet_hours": []}
DEFAULT_USAGE_BUDGET_CFG = {"defer_above_pct": 90, "critical_overrides": True}

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


# ======================================================= Wave I (I2) usage =
# Only "claude" and "codex" have a live usage probe (see usage_probe.py) —
# CX is dispatched via the codex CLI; GB is dispatched via grok, which has
# no defined provider in this spec, so the usage gate is deliberately a
# no-op for GB (no data to gate on -> never block, per the fail-open rule
# that runs through this entire subsystem).
UNIT_TO_PROVIDER = {"CX": "codex"}


def can_dispatch_usage(usage: dict, unit: str, priority: str, cfg: dict) -> tuple[bool, str]:
    """Returns (allowed, reason). reason is "" when allowed=True.

    usage: the dict from usage_probe.get_usage() (or an equivalent cached
    shape) — {"claude": {...}, "codex": {...}}, each with pct_5h/pct_7d
    possibly None. A None percentage never gates anything (fail-open: no
    data means no opinion, not "assume the worst").
    """
    cfg = {**DEFAULT_USAGE_BUDGET_CFG, **(cfg or {})}
    provider = UNIT_TO_PROVIDER.get(unit)
    if provider is None:
        return True, ""
    entry = (usage or {}).get(provider) or {}
    threshold = cfg["defer_above_pct"]
    over_5h = entry.get("pct_5h") is not None and entry["pct_5h"] >= threshold
    over_7d = entry.get("pct_7d") is not None and entry["pct_7d"] >= threshold
    if not (over_5h or over_7d):
        return True, ""
    if str(priority).lower() == "critical" and cfg["critical_overrides"]:
        return True, ""
    which = []
    if over_5h:
        which.append(f"5h={entry['pct_5h']:.0f}%")
    if over_7d:
        which.append(f"7d={entry['pct_7d']:.0f}%")
    return False, f"{provider} usage {' '.join(which)} >= defer_above_pct {threshold}"
