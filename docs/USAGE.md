# USAGE.md — usage-window meters (Wave I, I2, v4.5)

## Verify-at-build-time findings (spec §I2 §1 preamble)

Inspected `Starbirdbeats/dispatch`'s `engine/{claude,codex,usage}.mjs` directly (ISC, ~200 lines each) rather than guessing at the mechanics. The one finding that shapes this whole module:

**There is no static file to read.** Usage-window percentages only ever arrive as a side channel of an actual, OAuth-authenticated CLI invocation:

- **Claude**: piping `--output-format stream-json` through a real `claude` session emits a `rate_limit_event` line with a `rate_limit_info` object (`rateLimitType`, a used-percentage field, `resetsAt`, `status`) whenever the API happens to return one — which is on real turns, not on an idle poll.
- **Codex**: its own exec/JSON-RPC stream emits `rateLimits` / `rate_limits` / `rateLimitsByLimitId` objects with per-window `used_percent` + `window_minutes` fields alongside normal turn events. `window_minutes <= 360` is the 5h bucket, otherwise the 7d bucket — this exact split is lifted directly from the reference's own `minutes <= 360 ? 'fiveHour' : 'weekly'`.

The reference tool's own UI literally calls this a **"refresh"** — an action — and its e2e test states the harness "can't produce real windows... no OAuth token, fake codex bin," confirming a real authenticated session is what's being sampled, not a config file sitting on disk.

**What this means for `scripts/usage_probe.py`:** `probe(provider)` runs a minimal, cheap, throwaway CLI invocation (`claude -p "ok" --output-format stream-json --max-turns 1`, `codex exec --json "ok"`) and parses the first rate-limit-shaped line off the stream, discarding the rest. This costs a sliver of real usage on every genuine probe — exactly like the reference tool's own on-demand refresh — which is why the cache exists and defaults to a 15-minute TTL: the board, `/usage`, and the budget gate all read the cache almost every time; only an expired entry (or `--refresh`) triggers a real probe.

**The exact JSON key names are the least stable part of this integration**, and the spec warned about exactly this ("these surfaces churn; the spec fixes the architecture, the build session fixes the endpoints"). `_parse_claude_stream_line`/`_parse_codex_stream_line` try several plausible key spellings each (`usedPct`/`used_pct`/`usedPercent`/`used_percent`/`used_percentage`/`percent`, etc.) for the same reason `usage.mjs`'s own `normalizeUsageWindow` does — but **these have not been run against a real installed `claude`/`codex` CLI** (this build session had neither installed). Before trusting the numbers on `clawsrv`:

```bash
# Confirm the CLI actually emits stream-json / --json output at all, and eyeball
# the real field names in a rate_limit_event / rateLimits object:
claude -p "hello" --output-format stream-json --max-turns 1 | grep -i rate_limit
codex exec --json "hello" | grep -i rateLimit
```

If the real shape differs from what's coded, the fix is confined to `_parse_claude_stream_line`/`_parse_codex_stream_line` in `scripts/usage_probe.py` — everything downstream (cache, budget gate, board, `/usage`) is shape-agnostic and only cares about the final `pct_5h`/`pct_7d`/`reset_5h`/`reset_7d` dict.

## Architecture

```
usage_probe.probe("claude"|"codex")
  -> minimal real CLI invocation, stream-json/--json output
  -> parse first rate-limit event, discard the rest
  -> {"pct_5h", "pct_7d", "reset_5h", "reset_7d", "probed_at"} — all-None on ANY failure

usage_probe.get_usage(repo, cfg)          <- the read path everything else uses
  -> load .devteam/usage_cache.json
  -> re-probe only entries older than usage.cache_ttl_minutes (default 15)
  -> save_cache() atomically, return the merged {"claude": {...}, "codex": {...}}

usage_probe.load_cache(repo)              <- the READ-ONLY path (board_publisher, /usage)
  -> never probes, never burns usage, just reads whatever's already cached
```

## Consumers

- **`scripts/budget.py`**: `can_dispatch_usage(usage, unit, priority, cfg)` — before a DISPATCH, checks the target unit's provider (`CX -> codex`; `GB` has no provider in this spec, since Grok isn't tracked — the gate is always a no-op for GB) against `usage.defer_above_pct` (default 90) on either window. `Priority: critical` + `usage.critical_overrides: true` (default) bypasses it. Composes with the existing hourly-ceiling check in `supervisor.decide()`: either can defer alone (`DEFER_BUDGET` / `DEFER_USAGE`); if BOTH trip for the same pick, that's one combined `DEFER_BUDGET` action with both reasons in one line, not two redundant defers for the same non-dispatch.
- **`scripts/board_publisher.py`**: `read_usage_summary(repo)` — cache-only, added as the board's `"usage"` top-level key. Never invokes `usage_probe.probe()` from inside a publish, for the same reason `read_maintenance_summary()` never invokes `maintenance.py`: a broken/slow probe must never stall or break a board publish.
- **`board/index.html`**: two new top-bar meters (`CLAUDE 5H/7D`, `CODEX 5H/7D`), populated by `renderUsageMeter()`. Red accent at/above a client-side `USAGE_DEFER_ABOVE_PCT` constant (mirrors the `autopilot.json` default of 90 — a custom override there won't move the board's accent threshold without also editing the constant; the board reads the board JSON, not `autopilot.json`, and plumbing the configured threshold through `build_board()`'s output was left as a follow-up rather than expanding this wave's scope further). Defensive against older, pre-Wave-I board JSON with no `"usage"` key at all (`(b.usage||{}).claude`) — renders `—` for anything unknown, never throws.
- **`scripts/tg_commands.py`**: `/usage` — same table as the CLI, Telegram-formatted (`render_usage`). `/status` gains one summary line (`render_status` reads `board["usage"]` if present; omitted cleanly if absent, e.g. on an old cached board).
- **CLI**: `python scripts/usage_probe.py [--refresh] [--repo .]` — prints the same table `/usage` sends, for a quick terminal check.

## Config (`autopilot.json`)

```json
"usage": {
  "cache_ttl_minutes": 15,
  "defer_above_pct": 90,
  "critical_overrides": true
}
```

## Fail-open, end to end

| Failure | Behavior |
|---|---|
| `claude`/`codex` binary not on PATH | `probe()` returns all-None; no exception |
| CLI invocation times out (25s) | Process killed, partial/no data returned, no exception |
| Stream line isn't valid JSON, or has an unrecognized shape | That line is skipped; a later line may still match |
| Cache file missing or corrupt | `load_cache()` self-heals to an all-None shape; next `save_cache()` overwrites cleanly |
| `usage_cache.json`'s version doesn't match | Treated as empty, same as missing |
| Board publish | Reads cache only — a broken probe can never break or stall a publish |
| Budget gate with no data for a provider | Never defers — no data means no opinion, not "assume the worst" |

## What was deliberately NOT built

- **T2-style live rate-limit capture from every headless `claude -p` call already in the system** (`review_cmd`, `REVIEW_TG`, `TRIAGE_UNBLOCK`, the distiller's `call_model`) — the reference implementation's real mechanism captures usage as a side effect of *every* CLI invocation it makes, not just dedicated probes. Wiring that in here would mean adding `--output-format stream-json` and a parsing hook to every existing headless call site across `supervisor.py` and `distiller.py` — a cross-cutting change to how this system invokes Claude everywhere, not a Wave I concern. `usage_probe.py`'s dedicated throwaway-prompt probe gets the same data at the cost of one small extra call per cache-TTL window, which is a much smaller, more contained change for the same visibility.
- **Plumbing `usage.defer_above_pct` from `autopilot.json` into the board JSON** so the frontend's red-accent threshold always matches the configured one exactly (see the `board/index.html` note above) — the client-side constant covers the default correctly; matching a custom override is a small, clearly-scoped follow-up if it turns out to matter in practice.
