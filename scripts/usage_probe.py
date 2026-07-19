#!/usr/bin/env python3
"""usage_probe.py — DEVDEPARTMENT Wave I (v4.5), I2: live usage-window probes.

Verify-at-build-time finding (per spec §I2 preamble): inspected
Starbirdbeats/dispatch's engine/{claude,codex,usage}.mjs (ISC, the named
reference implementation) directly. The key finding — worth recording here
AND in docs/USAGE.md, since it shapes this whole module's design — is that
usage-window percentages are NOT sitting in a static, readable local file.
They only ever arrive as a side channel of an ACTUAL CLI invocation:

  * claude: piping `--output-format stream-json` emits a `rate_limit_event`
    line containing `rate_limit_info` (status/rateLimitType/resetsAt) once
    the API returns one — which happens on real turns, not idle polling.
  * codex: its exec/JSON-RPC stream emits `rateLimits`/`rate_limits` /
    `rateLimitsByLimitId` objects with per-window used-percentage fields
    alongside normal turn events.

There is no "GET /usage" to poll. The reference tool's own UI literally
labels this a "refresh" (an action, not a passive read) and its e2e test
notes the harness "can't produce real windows... no OAuth token, fake codex
bin" — confirming a real OAuth-authenticated CLI session is what's actually
being sampled, not a config file.

This module's `probe(provider)` therefore does the same thing: a minimal,
cheap, throwaway invocation of the local `claude`/`codex` CLI (a one-token
prompt, tightest reasonable turn limit) with structured output enabled,
parsing the first rate-limit-shaped event off the stream and discarding
the rest. This costs a sliver of real usage each time it's called — exactly
like the reference tool's on-demand refresh — which is why cache_ttl_minutes
defaults to 15: frequent callers (board publish, /usage, the budget gate)
read the cache, not the CLI, almost all of the time.

FAIL-OPEN IS THE ONLY HARD GUARANTEE HERE. The exact JSON key names above
are exactly what the spec warned they'd be: churny, undocumented, and
liable to have drifted by the time this runs against your real CLIs. Every
parse step is wrapped so any shape mismatch, timeout, missing binary, or
unexpected exit code degrades to an all-None result — never an exception,
never a value invented to fill the gap. Verify the exact field names
against your installed `claude`/`codex` versions before trusting the
numbers; see docs/USAGE.md for how.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

CACHE_REL = Path(".devteam") / "usage_cache.json"
CACHE_VERSION = 1
DEFAULT_CACHE_TTL_MINUTES = 15
PROBE_TIMEOUT_SECONDS = 25

EMPTY_PROBE = {
    "pct_5h": None, "pct_7d": None,
    "reset_5h": None, "reset_7d": None,
    "probed_at": None,
}


@dataclass
class ProbeResult:
    pct_5h: float | None = None
    pct_7d: float | None = None
    reset_5h: str | None = None
    reset_7d: str | None = None
    probed_at: str | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        d = {"pct_5h": self.pct_5h, "pct_7d": self.pct_7d,
             "reset_5h": self.reset_5h, "reset_7d": self.reset_7d,
             "probed_at": self.probed_at}
        if self.error:
            d["error"] = self.error
        return d


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _clamp_pct(n) -> float | None:
    try:
        v = float(n)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return max(0.0, min(100.0, v))


def _first_matching_key(d: dict, keys: tuple[str, ...]):
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _extract_window_pct(window_obj: dict) -> float | None:
    """Tolerant of several plausible key spellings — normalizeUsageWindow()
    in the reference implementation does the same thing for the same
    reason: these APIs are not stable across CLI versions."""
    if not isinstance(window_obj, dict):
        return None
    raw = _first_matching_key(window_obj, (
        "usedPct", "used_pct", "usedPercent", "used_percent", "used_percentage", "percent",
    ))
    return _clamp_pct(raw)


def _extract_window_reset(window_obj: dict) -> str | None:
    if not isinstance(window_obj, dict):
        return None
    raw = _first_matching_key(window_obj, ("resetsAt", "resets_at", "reset"))
    if raw is None:
        return None
    return str(raw)


def _parse_claude_stream_line(line: str) -> dict | None:
    """One line of `claude ... --output-format stream-json` output. Returns
    a partial {"pct_5h"/"pct_7d"/"reset_5h"/"reset_7d"} dict on a
    rate_limit_event, else None. rate_limit_info's exact per-window shape
    is the least-documented part of this whole probe — the reference
    implementation captures the object wholesale (`{...obj.rate_limit_info}`)
    without asserting its internal shape, so this function is deliberately
    just as tolerant, trying several plausible nestings/key names rather
    than asserting one."""
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    if obj.get("type") != "rate_limit_event":
        return None
    info = obj.get("rate_limit_info")
    if not isinstance(info, dict):
        return None

    out: dict = {}
    # Try a flat single-window shape first (rateLimitType tells us which).
    kind = str(info.get("rateLimitType") or info.get("rate_limit_type") or "").lower()
    pct = _extract_window_pct(info)
    reset = _extract_window_reset(info)
    if pct is not None:
        if "7d" in kind or "week" in kind or "day" in kind:
            out["pct_7d"], out["reset_7d"] = pct, reset
        else:  # default bucket: 5h / session-window class
            out["pct_5h"], out["reset_5h"] = pct, reset
        return out or None

    # Try a nested {fiveHour: {...}, weekly: {...}} shape (matches usage.mjs's
    # own internal representation, in case a future CLI version emits it
    # directly instead of one event per window).
    five = info.get("fiveHour") or info.get("five_hour")
    weekly = info.get("weekly") or info.get("sevenDay") or info.get("seven_day")
    if five:
        out["pct_5h"] = _extract_window_pct(five)
        out["reset_5h"] = _extract_window_reset(five)
    if weekly:
        out["pct_7d"] = _extract_window_pct(weekly)
        out["reset_7d"] = _extract_window_reset(weekly)
    return out or None


def _parse_codex_stream_line(line: str) -> dict | None:
    """One line of codex's own exec/JSON-RPC stream. rateLimits/rate_limits
    carries per-window used-percentage data; window_minutes<=360 is treated
    as the 5h bucket, else the 7d bucket — mirrors the reference's own
    `minutes <= 360 ? 'fiveHour' : 'weekly'` split exactly."""
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return None
    candidates = []
    for key in ("rateLimits", "rate_limits"):
        if isinstance(obj.get(key), dict):
            candidates.append(obj[key])
    usage = obj.get("usage")
    if isinstance(usage, dict):
        for key in ("rateLimits", "rate_limits"):
            if isinstance(usage.get(key), dict):
                candidates.append(usage[key])
    by_limit_id = obj.get("rateLimitsByLimitId") or obj.get("rate_limits_by_limit_id")
    if isinstance(by_limit_id, dict):
        candidates.extend(v for v in by_limit_id.values() if isinstance(v, dict))

    if not candidates:
        return None

    out: dict = {}
    for candidate in candidates:
        for raw in candidate.values() if all(not isinstance(v, (int, float, str)) for v in candidate.values()) else [candidate]:
            if not isinstance(raw, dict):
                continue
            minutes = _first_matching_key(raw, ("window_minutes", "windowMinutes", "window"))
            pct = _first_matching_key(raw, ("used_percent", "used_percentage", "usedPct", "usedPercent"))
            if minutes is None or pct is None:
                continue
            try:
                is_five_hour = float(minutes) <= 360
            except (TypeError, ValueError):
                continue
            reset = _first_matching_key(raw, ("resets_at", "resetsAt"))
            if is_five_hour and "pct_5h" not in out:
                out["pct_5h"] = _clamp_pct(pct)
                out["reset_5h"] = str(reset) if reset is not None else None
            elif not is_five_hour and "pct_7d" not in out:
                out["pct_7d"] = _clamp_pct(pct)
                out["reset_7d"] = str(reset) if reset is not None else None
        if "pct_5h" in out and "pct_7d" in out:
            break
    return out or None


def _run_probe_subprocess(cmd: list[str], parse_line) -> dict:
    """Shared plumbing: run `cmd`, parse each stdout line with `parse_line`,
    merge partial results, stop as soon as both windows are known. Fully
    fail-open: any exception, timeout, or missing binary -> empty dict."""
    result: dict = {}
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                encoding="utf-8", errors="replace", bufsize=1)
    except (OSError, FileNotFoundError):
        return result
    try:
        start = time.monotonic()
        assert proc.stdout is not None
        for line in proc.stdout:
            if time.monotonic() - start > PROBE_TIMEOUT_SECONDS:
                break
            parsed = parse_line(line.strip())
            if parsed:
                result.update({k: v for k, v in parsed.items() if v is not None})
            if "pct_5h" in result and "pct_7d" in result:
                break
    except Exception:
        pass
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    return result


def _probe_claude() -> dict:
    # A minimal, cheap turn — just enough to provoke a real API response
    # (and therefore a rate_limit_event) without doing real work.
    cmd = ["claude", "-p", "ok", "--output-format", "stream-json",
          "--dangerously-skip-permissions", "--max-turns", "1"]
    return _run_probe_subprocess(cmd, _parse_claude_stream_line)


def _probe_codex() -> dict:
    cmd = ["codex", "exec", "--json", "ok"]
    return _run_probe_subprocess(cmd, _parse_codex_stream_line)


def probe(provider: str) -> dict:
    """provider in {"claude", "codex"}. Returns the exact dict shape the
    spec names: pct_5h/pct_7d/reset_5h/reset_7d/probed_at, all-None on any
    failure. Never raises."""
    probed_at = _now_iso()
    try:
        if provider == "claude":
            raw = _probe_claude()
        elif provider == "codex":
            raw = _probe_codex()
        else:
            return dict(EMPTY_PROBE)
    except Exception:
        raw = {}
    return {
        "pct_5h": raw.get("pct_5h"),
        "pct_7d": raw.get("pct_7d"),
        "reset_5h": raw.get("reset_5h"),
        "reset_7d": raw.get("reset_7d"),
        "probed_at": probed_at,
    }


# ------------------------------------------------------------------ cache --
def _cache_path(repo: str | Path = ".") -> Path:
    return Path(repo) / CACHE_REL


def load_cache(repo: str | Path = ".") -> dict:
    """Fail-open: a missing/corrupt cache file loads as
    {"claude": EMPTY_PROBE, "codex": EMPTY_PROBE} — never an exception, and
    self-healing (the next save_cache overwrites it cleanly)."""
    p = _cache_path(repo)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if raw.get("version") != CACHE_VERSION:
            raise ValueError("cache version mismatch")
        return {
            "claude": {**EMPTY_PROBE, **(raw.get("claude") or {})},
            "codex": {**EMPTY_PROBE, **(raw.get("codex") or {})},
        }
    except Exception:
        return {"claude": dict(EMPTY_PROBE), "codex": dict(EMPTY_PROBE)}


def save_cache(repo: str | Path, data: dict) -> None:
    """Atomic write: temp file + os.replace, matching every other atomic
    write already established in this codebase (instincts.py, distiller.py)."""
    p = _cache_path(repo)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    payload = {"version": CACHE_VERSION, "claude": data.get("claude", EMPTY_PROBE),
              "codex": data.get("codex", EMPTY_PROBE)}
    try:
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def _cache_is_fresh(entry: dict, ttl_minutes: int) -> bool:
    probed_at = entry.get("probed_at")
    if not probed_at:
        return False
    try:
        probed = time.strptime(probed_at, "%Y-%m-%dT%H:%M:%SZ")
        age_seconds = time.mktime(time.gmtime()) - time.mktime(probed)
        return age_seconds < ttl_minutes * 60
    except (ValueError, TypeError):
        return False


def get_usage(repo: str | Path = ".", cfg: dict | None = None, refresh: bool = False) -> dict:
    """The read path everything else in the system uses: board_publisher,
    budget.py, tg_commands's /usage. Returns the cache, re-probing any
    provider whose cache entry is missing/stale/refresh-forced. Never
    probes on every call — that would burn real usage on every board
    publish and every tick's budget check, defeating the whole point of
    a 15-minute cache."""
    usage_cfg = (cfg or {}).get("usage", {})
    ttl = int(usage_cfg.get("cache_ttl_minutes", DEFAULT_CACHE_TTL_MINUTES))
    cache = load_cache(repo)
    changed = False
    for provider in ("claude", "codex"):
        if refresh or not _cache_is_fresh(cache[provider], ttl):
            cache[provider] = probe(provider)
            changed = True
    if changed:
        save_cache(repo, cache)
    return cache


# ------------------------------------------------------------------- CLI ----
def _render_table(usage: dict) -> str:
    lines = []
    for provider in ("claude", "codex"):
        u = usage.get(provider, EMPTY_PROBE)
        p5 = f"{u['pct_5h']:.0f}%" if u.get("pct_5h") is not None else "—"
        p7 = f"{u['pct_7d']:.0f}%" if u.get("pct_7d") is not None else "—"
        r7 = u.get("reset_7d") or "—"
        lines.append(f"{provider:8s} 5h={p5:>5s}  7d={p7:>5s}  reset_7d={r7}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Usage-window probe (Wave I, I2)")
    ap.add_argument("--refresh", action="store_true", help="bypass the cache TTL")
    ap.add_argument("--repo", default=".")
    ns = ap.parse_args(argv)

    cfg_path = Path(ns.repo) / "autopilot.json"
    cfg = {}
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        pass

    usage = get_usage(ns.repo, cfg, refresh=ns.refresh)
    print(_render_table(usage))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
