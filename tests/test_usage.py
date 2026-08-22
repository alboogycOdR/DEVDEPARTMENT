"""tests/test_usage.py — Wave I (v4.5), I2: usage-window meters.

Covers usage_probe.py's cache (TTL honored, --refresh bypasses, corrupt
cache self-heals), fail-open degradation (a raising/missing CLI never
propagates an exception), budget.py's usage gate composition with the
existing hourly ceiling, board_publisher's read-only usage key, and
tg_commands' /usage rendering.
"""
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import usage_probe as up  # noqa: E402
import budget  # noqa: E402
import tg_commands as tgc  # noqa: E402


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    return repo


# ============================================================== cache =====
class TestUsageCache:
    def test_load_missing_cache_returns_empty_shape(self, tmp_path):
        repo = make_repo(tmp_path)
        cache = up.load_cache(repo)
        assert cache == {"claude": dict(up.EMPTY_PROBE), "codex": dict(up.EMPTY_PROBE)}

    def test_save_then_load_round_trips(self, tmp_path):
        repo = make_repo(tmp_path)
        data = {"claude": {"pct_5h": 10.0, "pct_7d": 20.0, "reset_5h": None,
                           "reset_7d": "2026-07-25T00:00:00Z", "probed_at": "2026-07-20T10:00:00Z"},
                "codex": dict(up.EMPTY_PROBE)}
        up.save_cache(repo, data)
        loaded = up.load_cache(repo)
        assert loaded["claude"]["pct_5h"] == 10.0
        assert loaded["claude"]["pct_7d"] == 20.0

    def test_corrupt_cache_file_self_heals(self, tmp_path):
        repo = make_repo(tmp_path)
        cache_path = repo / up.CACHE_REL
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text("{not valid json,,,", encoding="utf-8")
        cache = up.load_cache(repo)
        assert cache == {"claude": dict(up.EMPTY_PROBE), "codex": dict(up.EMPTY_PROBE)}

    def test_wrong_version_cache_treated_as_empty(self, tmp_path):
        repo = make_repo(tmp_path)
        cache_path = repo / up.CACHE_REL
        cache_path.parent.mkdir(parents=True)
        cache_path.write_text(json.dumps({"version": 999, "claude": {"pct_5h": 50}}), encoding="utf-8")
        cache = up.load_cache(repo)
        assert cache["claude"]["pct_5h"] is None

    def test_save_is_atomic_no_tmp_file_left(self, tmp_path):
        repo = make_repo(tmp_path)
        up.save_cache(repo, {"claude": dict(up.EMPTY_PROBE), "codex": dict(up.EMPTY_PROBE)})
        cache_dir = (repo / up.CACHE_REL).parent
        tmp_files = list(cache_dir.glob("*.tmp"))
        assert tmp_files == []


class TestCacheFreshness:
    def test_fresh_entry_within_ttl(self):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = {**up.EMPTY_PROBE, "probed_at": now}
        assert up._cache_is_fresh(entry, ttl_minutes=15) is True

    def test_stale_entry_beyond_ttl(self):
        old = "2020-01-01T00:00:00Z"
        entry = {**up.EMPTY_PROBE, "probed_at": old}
        assert up._cache_is_fresh(entry, ttl_minutes=15) is False

    def test_no_probed_at_is_stale(self):
        entry = dict(up.EMPTY_PROBE)
        assert up._cache_is_fresh(entry, ttl_minutes=15) is False

    def test_malformed_probed_at_is_stale(self):
        entry = {**up.EMPTY_PROBE, "probed_at": "not-a-timestamp"}
        assert up._cache_is_fresh(entry, ttl_minutes=15) is False


class TestGetUsage:
    def test_ttl_honored_no_reprobe_when_fresh(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        fresh = {"pct_5h": 5.0, "pct_7d": 6.0, "reset_5h": None, "reset_7d": None, "probed_at": now}
        up.save_cache(repo, {"claude": fresh, "codex": dict(up.EMPTY_PROBE)})
        # codex is empty/stale -> should reprobe; claude is fresh -> should NOT.
        calls = []
        monkeypatch.setattr(up, "probe", lambda provider: calls.append(provider) or dict(up.EMPTY_PROBE))
        result = up.get_usage(repo, {"usage": {"cache_ttl_minutes": 15}})
        assert "claude" not in calls
        assert "codex" in calls
        assert result["claude"]["pct_5h"] == 5.0  # untouched, still the fresh cached value

    def test_refresh_bypasses_ttl_for_both(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        fresh = {"pct_5h": 5.0, "pct_7d": 6.0, "reset_5h": None, "reset_7d": None, "probed_at": now}
        up.save_cache(repo, {"claude": fresh, "codex": fresh})
        calls = []
        monkeypatch.setattr(up, "probe", lambda provider: calls.append(provider) or dict(up.EMPTY_PROBE))
        up.get_usage(repo, {}, refresh=True)
        assert sorted(calls) == ["claude", "codex"]

    def test_stale_cache_triggers_reprobe_for_both(self, tmp_path, monkeypatch):
        repo = make_repo(tmp_path)
        calls = []
        monkeypatch.setattr(up, "probe", lambda provider: calls.append(provider) or dict(up.EMPTY_PROBE))
        up.get_usage(repo, {})
        assert sorted(calls) == ["claude", "codex"]


# ========================================================== fail-open =====
class TestFailOpenDegradation:
    def test_probe_missing_binary_returns_all_none(self, tmp_path, monkeypatch):
        # Explicitly simulate no claude/codex binary on PATH, rather than
        # assuming the test-running machine has none — this machine DOES have
        # a working, authenticated `claude` CLI (2026-08-17), so the old
        # unmocked version of this test was silently vacuous here: it always
        # passed only because the parser itself was broken and returned
        # all-None regardless of PATH. Fixed alongside the parser fix.
        monkeypatch.setenv("PATH", str(tmp_path))
        result = up.probe("claude")
        assert result["pct_5h"] is None
        assert result["pct_7d"] is None
        assert result["probed_at"] is not None  # timestamp still recorded

    def test_probe_unknown_provider_returns_empty_shape(self):
        result = up.probe("unknown-provider")
        assert result == dict(up.EMPTY_PROBE) or all(
            result[k] is None for k in ("pct_5h", "pct_7d", "reset_5h", "reset_7d"))

    def test_probe_subprocess_exception_never_propagates(self, monkeypatch):
        def boom():
            raise RuntimeError("simulated CLI crash")
        monkeypatch.setattr(up, "_probe_claude", boom)
        result = up.probe("claude")  # must not raise
        assert result["pct_5h"] is None

    def test_stream_parser_malformed_json_returns_none(self):
        assert up._parse_claude_stream_line("not json at all") is None
        assert up._parse_codex_stream_line("{broken") is None

    def test_stream_parser_wrong_event_type_returns_none(self):
        assert up._parse_claude_stream_line('{"type": "assistant"}') is None

    def test_extract_window_pct_tolerant_of_missing_keys(self):
        assert up._extract_window_pct({}) is None
        assert up._extract_window_pct(None) is None
        assert up._extract_window_pct({"usedPct": 55}) == 55.0
        assert up._extract_window_pct({"used_percent": 60}) == 60.0

    def test_clamp_pct_bounds(self):
        assert up._clamp_pct(150) == 100.0
        assert up._clamp_pct(-5) == 0.0
        assert up._clamp_pct("not a number") is None
        assert up._clamp_pct(float("nan")) is None


# ==================================================== stream parsers ======
class TestStreamParsers:
    def test_claude_five_hour_window(self):
        line = json.dumps({"type": "rate_limit_event", "rate_limit_info": {
            "rateLimitType": "five_hour", "usedPct": 42.5, "resetsAt": "2026-07-20T15:00:00Z"}})
        result = up._parse_claude_stream_line(line)
        assert result == {"pct_5h": 42.5, "reset_5h": "2026-07-20T15:00:00Z"}

    def test_claude_seven_day_window(self):
        line = json.dumps({"type": "rate_limit_event", "rate_limit_info": {
            "rateLimitType": "seven_day", "used_percent": 88, "resets_at": "2026-07-25T00:00:00Z"}})
        result = up._parse_claude_stream_line(line)
        assert result == {"pct_7d": 88.0, "reset_7d": "2026-07-25T00:00:00Z"}

    def test_codex_both_windows_from_rate_limits_by_limit_id(self):
        line = json.dumps({"rateLimitsByLimitId": {"codex": {
            "primary": {"window_minutes": 300, "used_percent": 15},
            "secondary": {"window_minutes": 10080, "used_percent": 60, "resets_at": "2026-07-27T00:00:00Z"},
        }}})
        result = up._parse_codex_stream_line(line)
        assert result["pct_5h"] == 15.0
        assert result["pct_7d"] == 60.0
        assert result["reset_7d"] == "2026-07-27T00:00:00Z"

    def test_codex_no_rate_limit_data_returns_none(self):
        line = json.dumps({"type": "turn_complete"})
        assert up._parse_codex_stream_line(line) is None


# =============================================== budget usage gate ========
class TestBudgetUsageGate:
    def test_below_threshold_allows(self):
        usage = {"codex": {"pct_5h": 10.0, "pct_7d": 20.0}}
        ok, reason = budget.can_dispatch_usage(usage, "CX", "high", {})
        assert ok is True

    def test_at_or_above_threshold_defers(self):
        usage = {"codex": {"pct_7d": 95.0, "pct_5h": 10.0}}
        ok, reason = budget.can_dispatch_usage(usage, "CX", "high", {"defer_above_pct": 90})
        assert ok is False
        assert "codex" in reason and "7d=95%" in reason

    def test_5h_window_also_gates(self):
        usage = {"codex": {"pct_5h": 95.0, "pct_7d": 10.0}}
        ok, reason = budget.can_dispatch_usage(usage, "CX", "high", {"defer_above_pct": 90})
        assert ok is False
        assert "5h=95%" in reason

    def test_critical_priority_overrides_when_enabled(self):
        usage = {"codex": {"pct_7d": 99.0}}
        ok, reason = budget.can_dispatch_usage(
            usage, "CX", "critical", {"defer_above_pct": 90, "critical_overrides": True})
        assert ok is True

    def test_critical_priority_does_not_override_when_disabled(self):
        usage = {"codex": {"pct_7d": 99.0}}
        ok, reason = budget.can_dispatch_usage(
            usage, "CX", "critical", {"defer_above_pct": 90, "critical_overrides": False})
        assert ok is False

    def test_no_provider_for_gb_never_gates(self):
        """GB is dispatched via grok, which has no usage provider in this
        spec — the gate must never block GB regardless of usage data."""
        usage = {"claude": {"pct_7d": 100.0}, "codex": {"pct_7d": 100.0}}
        ok, reason = budget.can_dispatch_usage(usage, "GB", "high", {"defer_above_pct": 90})
        assert ok is True

    def test_missing_usage_data_never_gates(self):
        ok, reason = budget.can_dispatch_usage({}, "CX", "high", {"defer_above_pct": 90})
        assert ok is True

    def test_none_percentages_never_gate(self):
        usage = {"codex": {"pct_5h": None, "pct_7d": None}}
        ok, reason = budget.can_dispatch_usage(usage, "CX", "high", {"defer_above_pct": 90})
        assert ok is True


# ================================================ decide() composition ====
class TestDecideUsageComposition:
    def test_usage_defer_when_budget_ok_but_usage_tripped(self):
        from supervisor import decide, RuntimeState, DEFAULT_CONFIG
        NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
        plan = """---
plan_version: 4.5
last_updated: 2026-07-20T00:00:00Z
overall_status: in_progress
---

### TASK-800
**Title:** T
**Status:** pending
**Assigned_To:** CX
**Priority:** high
**Spec_References:** specs/x.md
**Owned_Paths:** lib/x/**
**Depends_On:** —
**Description:** d
**Acceptance_Criteria:**
- [ ] c
**Branch:** —
**Started_At:** —
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** ORCH
**Updated_At:** 2026-07-20T00:00:00Z
"""
        cfg = {**DEFAULT_CONFIG, "builders": ["CX"]}
        usage = {"codex": {"pct_7d": 95.0, "pct_5h": 10.0}}
        acts = decide(plan, RuntimeState(), cfg, NOW, usage=usage)
        kinds = [a.kind for a in acts]
        assert "DEFER_USAGE" in kinds
        assert "DISPATCH" not in kinds

    def test_both_budget_and_usage_tripped_single_combined_action(self):
        from supervisor import decide, RuntimeState, DEFAULT_CONFIG
        NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
        plan = """---
plan_version: 4.5
last_updated: 2026-07-20T00:00:00Z
overall_status: in_progress
---

### TASK-801
**Title:** T
**Status:** pending
**Assigned_To:** CX
**Priority:** high
**Spec_References:** specs/x.md
**Owned_Paths:** lib/x/**
**Depends_On:** —
**Description:** d
**Acceptance_Criteria:**
- [ ] c
**Branch:** —
**Started_At:** —
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** ORCH
**Updated_At:** 2026-07-20T00:00:00Z
"""
        cfg = {**DEFAULT_CONFIG, "builders": ["CX"], "budget": {"max_dispatches_per_hour": 0, "quiet_hours": []}}
        usage = {"codex": {"pct_7d": 95.0, "pct_5h": 10.0}}
        acts = decide(plan, RuntimeState(), cfg, NOW, usage=usage)
        defer_actions = [a for a in acts if a.kind in ("DEFER_BUDGET", "DEFER_USAGE")]
        assert len(defer_actions) == 1  # single combined action, not two
        assert "budget ceiling" in defer_actions[0].detail and "usage gate" in defer_actions[0].detail


# ================================================== board / /usage render =
class TestBoardUsageKey:
    def test_read_usage_summary_present_when_cached(self, tmp_path):
        from board_publisher import read_usage_summary
        repo = make_repo(tmp_path)
        up.save_cache(repo, {"claude": {**up.EMPTY_PROBE, "pct_5h": 5.0}, "codex": dict(up.EMPTY_PROBE)})
        summary = read_usage_summary(repo)
        assert summary["claude"]["pct_5h"] == 5.0

    def test_read_usage_summary_empty_when_no_cache(self, tmp_path):
        from board_publisher import read_usage_summary
        repo = make_repo(tmp_path)
        summary = read_usage_summary(repo)
        assert summary["claude"]["pct_5h"] is None

    def test_read_usage_summary_never_probes(self, tmp_path, monkeypatch):
        """Board publishing must never trigger a live probe — read cache
        only, per the spec's explicit instruction."""
        from board_publisher import read_usage_summary
        repo = make_repo(tmp_path)
        called = []
        monkeypatch.setattr(up, "probe", lambda provider: called.append(provider))
        read_usage_summary(repo)
        assert called == []

    def test_build_board_includes_usage_key(self, tmp_path):
        from board_publisher import build_board, DEFAULT_BOARD_CFG
        repo = make_repo(tmp_path)
        (repo / "PLAN.md").write_text(
            "---\nplan_version: 1\nlast_updated: 2026-07-20T00:00:00Z\noverall_status: in_progress\n---\n",
            encoding="utf-8")
        board = build_board(repo, DEFAULT_BOARD_CFG)
        assert "usage" in board
        assert "claude" in board["usage"] and "codex" in board["usage"]


class TestRenderUsage:
    def test_renders_both_providers_with_dashes_when_unknown(self):
        text = tgc.render_usage({"claude": dict(up.EMPTY_PROBE), "codex": dict(up.EMPTY_PROBE)})
        assert "claude" in text and "codex" in text
        assert text.count("\u2014") >= 4  # 2 providers x (5h, 7d) all dashes

    def test_renders_known_percentages(self):
        usage = {"claude": {"pct_5h": 12.0, "pct_7d": 45.0, "reset_7d": "2026-07-25T00:00:00Z",
                            "probed_at": "2026-07-20T10:00:00Z"},
                 "codex": dict(up.EMPTY_PROBE)}
        text = tgc.render_usage(usage)
        assert "5h=12%" in text
        assert "7d=45%" in text
        assert "2026-07-25T00:00:00Z" in text

    def test_status_line_includes_usage_summary_when_present(self):
        board = {"burndown": {"done": 1, "total": 2, "pct": 50}, "columns": {},
                 "autopilot": {}, "usage": {"claude": {"pct_5h": 10, "pct_7d": 20},
                                            "codex": dict(up.EMPTY_PROBE)}}
        text = tgc.render_status(board)
        assert "claude 5h=10% 7d=20%" in text

    def test_status_line_omitted_when_usage_absent(self):
        """Absent-tolerated: pre-Wave-I board JSON with no 'usage' key at
        all must not crash /status or add a stray line."""
        board = {"burndown": {"done": 1, "total": 2, "pct": 50}, "columns": {}, "autopilot": {}}
        text = tgc.render_status(board)
        assert "\U0001F4CA" not in text  # no usage line emoji present


# ============================================ frontend JS sanity (I2 §4) ==
class TestFrontendJsSanity:
    def test_index_html_script_is_valid_javascript(self):
        """No headless browser available in this environment — the
        documented fallback per the spec is asserting on the JS handling
        paths directly. This at minimum confirms the script block still
        parses as valid JS after the Wave I edits (a syntax error would
        break the ENTIRE board, not just the usage meters)."""
        html_path = Path(__file__).resolve().parents[1] / "board" / "index.html"
        html = html_path.read_text(encoding="utf-8")
        import re
        m = re.search(r"<script>([\s\S]*)</script>", html)
        assert m is not None
        script = m.group(1)
        # encoding="utf-8" explicitly, NOT text=True: text=True decodes/encodes
        # subprocess I/O using the OS's locale-preferred encoding, which is
        # UTF-8 on most Linux boxes but cp1252 on Windows by default — and
        # this script block legitimately contains non-ASCII characters
        # (▲ ■ ⚠ · —, used as board UI icons/dashes) that cp1252 can't
        # represent. Surfaced by a live diagnostic run on Windows; this
        # pins the encoding so the test is deterministic on every platform
        # regardless of the runner's OS locale.
        result = subprocess.run(
            ["node", "-e", "new Function(require('fs').readFileSync(0,'utf-8'))"],
            input=script, capture_output=True, encoding="utf-8")
        assert result.returncode == 0, result.stderr

    def test_render_usage_meter_function_present_and_null_safe(self):
        """Assert on the JS handling path directly (spec's documented
        fallback when headless rendering isn't available): the function
        exists, and its null-guard branch (entry missing/both pct None ->
        em-dash, no exception) is present in source."""
        html_path = Path(__file__).resolve().parents[1] / "board" / "index.html"
        html = html_path.read_text(encoding="utf-8")
        assert "function renderUsageMeter" in html
        assert "pct_5h==null && entry.pct_7d==null" in html or "pct_5h == null" in html.replace(" ", " ")
        assert "(b.usage||{})" in html  # defensive against pre-Wave-I board JSON with no usage key
