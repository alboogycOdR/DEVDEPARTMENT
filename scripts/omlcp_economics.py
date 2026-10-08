#!/usr/bin/env python3
"""OMLCP token economics — the paper's model, corrected for caching, and calibrated to
DEVDEPARTMENT's own session shape.

Three models, deliberately kept separate so their assumptions stay visible:

1. ``paper`` — equations (1)–(6) of Viviers 2026 exactly as published (§4). Useful for
   reproducing the paper's 16× / 44× figures and for seeing what its assumptions do.
2. ``priced`` — the same token flows priced in dollars with real per-model rates
   *including prompt caching*. The paper prices nothing and ignores caching; on Claude,
   cache reads bill at 0.05×–0.1× input, which changes which term dominates.
3. ``devdept`` — a session model for one DEVDEPARTMENT task: every builder turn re-sends
   the session prefix (harness + CLAUDE.md + AGENTS.md + briefing + PLAN.md + dossier),
   measured from the repo's real files. Compared against the generate lane: one packet,
   long output, bounded continuations, a short repair session, and the SAME review.

Every assumption is a named, overridable parameter, and ``from_ledger`` replaces
assumptions with measured averages once `.devteam/ledger.jsonl` has data (Wave H's
"measure first" rule).

Prices verified 2026-10-06 against platform.claude.com/docs/en/about-claude/pricing and
/models/overview. Re-verify before quoting; prices move.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from pathlib import Path

PRICES_VERIFIED_AT = "2026-10-06"
# $ per million tokens; cache multipliers relative to input.
PRICES = {
    "claude-fable-5-1": {"in": 10.0, "out": 50.0, "cache_read": 0.025, "cache_write": 1.25},
    "claude-opus-5-5": {"in": 4.0, "out": 20.0, "cache_read": 0.05, "cache_write": 1.25},
    "claude-sonnet-5-5": {"in": 2.0, "out": 10.0, "cache_read": 0.10, "cache_write": 1.25},
    "claude-sonnet-5": {"in": 2.0, "out": 10.0, "cache_read": 0.10, "cache_write": 1.25},
    "claude-haiku-4-5": {"in": 1.0, "out": 5.0, "cache_read": 0.10, "cache_write": 1.25},
}


def price_for(model: str) -> dict:
    if model in PRICES:
        return PRICES[model]
    for key, val in PRICES.items():  # tolerate dated suffixes, e.g. claude-haiku-4-5-20251001
        if model.startswith(key):
            return val
    raise KeyError(f"no price for model {model!r}; known: {', '.join(PRICES)}")


def usage_cost(model: str, input_tokens: int, output_tokens: int,
               cache_read: int = 0, cache_write: int = 0) -> float:
    p = price_for(model)
    return (input_tokens * p["in"] + output_tokens * p["out"]
            + cache_read * p["in"] * p["cache_read"]
            + cache_write * p["in"] * p["cache_write"]) / 1e6


# ------------------------------------------------------------------ 1. paper --
def paper_agentic_tokens(lines: int, r: float = 0.76, out_per_iter: int = 120,
                         ctx_frac: float = 0.2, lines_per_iter: int = 50,
                         reasoning_budget: int = 3000) -> dict:
    """Eq. (5): Cost ≈ (L/50)·(0.2L + 3000r + 120). r=0.76 reproduces eq. (2)."""
    iters = lines / lines_per_iter
    ctx = ctx_frac * lines
    reasoning = reasoning_budget * r
    return {"iterations": iters, "input": iters * ctx, "reasoning": iters * reasoning,
            "output": iters * out_per_iter, "total": iters * (ctx + reasoning + out_per_iter)}


def paper_omlcp_tokens(lines: int, tokens_per_line: float = 3.0, plan: int = 25000) -> dict:
    """Eq. (4): Cost ≈ C_plan + L·T."""
    return {"input": plan, "output": lines * tokens_per_line, "total": plan + lines * tokens_per_line}


# ----------------------------------------------------------------- 2. priced --
def priced_comparison(lines: int, model: str, tokens_per_line: float, cache_hit: float,
                      r: float = 0.76) -> dict:
    """Paper flows, but output-per-iteration scaled to the real tokens/line and priced.
    Reasoning bills as output. ``cache_hit`` is the share of agentic input served from
    cache (Claude Code typically re-sends an unchanged prefix every turn)."""
    out_per_iter = 50 * tokens_per_line
    ag = paper_agentic_tokens(lines, r=r, out_per_iter=out_per_iter)
    om = paper_omlcp_tokens(lines, tokens_per_line)
    p = price_for(model)
    ag_in_cost = ag["input"] * p["in"] * (cache_hit * p["cache_read"] + (1 - cache_hit)) / 1e6
    ag_cost = ag_in_cost + (ag["reasoning"] + ag["output"]) * p["out"] / 1e6
    om_cost = usage_cost(model, int(om["input"]), int(om["output"]))
    return {"agentic_tokens": ag["total"], "omlcp_tokens": om["total"],
            "token_ratio": ag["total"] / om["total"], "agentic_usd": ag_cost, "omlcp_usd": om_cost,
            "usd_ratio": ag_cost / om_cost if om_cost else math.inf,
            "agentic_input_share_of_usd": ag_in_cost / ag_cost if ag_cost else 0.0}


# ---------------------------------------------------------------- 3. devdept --
PREFIX_FILES = ["CLAUDE.md", "AGENTS.md", "briefings/S5_BUILD_BRIEFING.md",
                ".claude/agents/devteam-builder.md", "PLAN.md"]


@dataclass
class DevDeptParams:
    """One task, two lanes. Fields marked (assumption) are not measurable from the repo;
    replace them with ledger averages as soon as F2's ledger exists."""

    lines: int = 1500
    tokens_per_line: float = 12.0
    harness_tokens: int = 15000          # (assumption) system prompt + tool schemas
    prefix_doc_tokens: int = 0           # measured: CLAUDE.md+AGENTS.md+briefing+agent+PLAN.md
    dossier_spec_tokens: int = 4000      # measured median dossier + (assumption) spec excerpt
    builder_turns: int = 60              # (assumption) tool turns per builder session
    growth_per_turn: int = 1500          # (assumption) tool output appended per turn
    reasoning_per_turn: int = 600        # (assumption) thinking + narration per turn
    builder_sessions: float = 1.5        # (assumption) incl. resume / one rework in two
    review_turns: int = 40               # (assumption) review session turns
    packet_tokens: int = 12000           # measured from a real packet when available
    repair_turns: int = 15               # (assumption) agentic repair after generation
    practical_ceiling: int = 60000
    cache_hit: float = 0.9               # (assumption) share of re-sent prefix served from cache
    builder_model: str = "claude-sonnet-5"
    review_model: str = "claude-opus-5-5"
    measured: list[str] = field(default_factory=list)


def _session(base: int, turns: int, growth: int, reasoning: int, out_tokens: int) -> dict:
    """Tokens for one agentic session: every turn re-sends base + accumulated growth."""
    resent = turns * base + growth * turns * (turns - 1) / 2
    return {"input": resent, "reasoning": turns * reasoning, "output": out_tokens}


def _price_session(s: dict, model: str, cache_hit: float, base: int) -> float:
    p = price_for(model)
    fresh = base + (s["input"] - base) * (1 - cache_hit)    # first send + cache misses
    cached = (s["input"] - base) * cache_hit
    return (fresh * p["in"] + cached * p["in"] * p["cache_read"] + base * p["in"] * (p["cache_write"] - 1)
            + (s["reasoning"] + s["output"]) * p["out"]) / 1e6


def devdept_compare(pp: DevDeptParams) -> dict:
    out_tokens = int(pp.lines * pp.tokens_per_line)
    base = pp.harness_tokens + pp.prefix_doc_tokens + pp.dossier_spec_tokens
    b = _session(base, pp.builder_turns, pp.growth_per_turn, pp.reasoning_per_turn, out_tokens)
    builder = {k: v * pp.builder_sessions for k, v in b.items()}
    builder_usd = _price_session(b, pp.builder_model, pp.cache_hit, base) * pp.builder_sessions
    review_base = pp.harness_tokens + pp.prefix_doc_tokens + out_tokens  # reads the diff
    review = _session(review_base, pp.review_turns, pp.growth_per_turn, pp.reasoning_per_turn, 2000)
    review_usd = _price_session(review, pp.review_model, pp.cache_hit, review_base)

    segments = max(1, math.ceil(out_tokens / pp.practical_ceiling))
    gen_input = sum(pp.packet_tokens + min(i * pp.practical_ceiling, out_tokens) for i in range(segments))
    gen_usd = usage_cost(pp.builder_model, pp.packet_tokens, out_tokens,
                         cache_read=gen_input - pp.packet_tokens)
    repair_base = pp.harness_tokens + pp.dossier_spec_tokens   # no PLAN.md: packet replaces it
    repair = _session(repair_base, pp.repair_turns, pp.growth_per_turn, pp.reasoning_per_turn,
                      int(out_tokens * 0.05))
    repair_usd = _price_session(repair, pp.builder_model, pp.cache_hit, repair_base)

    agentic_tokens = sum(builder.values()) + sum(review.values())
    omlcp_tokens = gen_input + out_tokens + sum(repair.values()) + sum(review.values())
    agentic_usd = builder_usd + review_usd
    omlcp_usd = gen_usd + repair_usd + review_usd
    return {
        "params": asdict(pp),
        "session_prefix_tokens": base,
        "agentic": {"builder_tokens": sum(builder.values()), "review_tokens": sum(review.values()),
                    "total_tokens": agentic_tokens, "usd": agentic_usd},
        "omlcp": {"generation_tokens": gen_input + out_tokens, "segments": segments,
                  "repair_tokens": sum(repair.values()), "review_tokens": sum(review.values()),
                  "total_tokens": omlcp_tokens, "usd": omlcp_usd},
        "token_ratio": agentic_tokens / omlcp_tokens,
        "usd_ratio": agentic_usd / omlcp_usd if omlcp_usd else math.inf,
        "builder_only_token_ratio": sum(builder.values()) / (gen_input + out_tokens + sum(repair.values())),
    }


def measure_repo(repo: Path) -> dict:
    """Measure what can be measured: prefix documents and tokens/line of tracked code."""
    from omlcp_packet import CHARS_PER_TOKEN

    prefix = {}
    for rel in PREFIX_FILES:
        p = repo / rel
        if p.is_file():
            prefix[rel] = math.ceil(p.stat().st_size / 4)  # prose ≈ 4 chars/token
    dossiers = [p.stat().st_size for p in (repo / "dossiers").glob("TASK-*.md")] if (repo / "dossiers").is_dir() else []
    chars = lines = 0
    for pattern in ("scripts/*.py", "src/**/*.py", "lib/**/*.dart", "src/**/*.ts", "**/*.mq5", "**/*.mqh"):
        for p in repo.glob(pattern):
            if ".git" in p.parts or "node_modules" in p.parts:
                continue
            try:
                t = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            chars += len(t)
            lines += t.count("\n")
    return {"prefix_tokens": prefix, "prefix_total": sum(prefix.values()),
            "dossier_median_tokens": math.ceil(statistics.median(dossiers) / 4) if dossiers else None,
            "code_lines": lines,
            "tokens_per_line": round(chars / lines / CHARS_PER_TOKEN, 1) if lines else None}


def params_from_repo(repo: Path, **overrides) -> DevDeptParams:
    m = measure_repo(repo)
    pp = DevDeptParams(**overrides)
    if "prefix_doc_tokens" not in overrides:
        pp.prefix_doc_tokens = m["prefix_total"]
        pp.measured.append("prefix_doc_tokens")
    if m["dossier_median_tokens"] and "dossier_spec_tokens" not in overrides:
        pp.dossier_spec_tokens = m["dossier_median_tokens"] + 3000
        pp.measured.append("dossier_spec_tokens (dossier measured, +3000 spec assumed)")
    if m["tokens_per_line"] and "tokens_per_line" not in overrides:
        pp.tokens_per_line = m["tokens_per_line"]
        pp.measured.append("tokens_per_line")
    return pp


# ----------------------------------------------------------------- ledger use --
def read_ledger(repo: Path) -> list[dict]:
    p = repo / ".devteam" / "ledger.jsonl"
    if not p.is_file():
        return []
    rows = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def ledger_report(rows: list[dict]) -> dict:
    """Paper §6.4 primary metrics per task, from ledger lines. Generation and repair are
    kept apart (paper §6.4 item 6: repair accounting)."""
    tasks: dict[str, dict] = {}
    for r in rows:
        if r.get("lane") != "generate":
            continue
        t = tasks.setdefault(r.get("task_id") or "?", {
            "segments": 0, "continuations": 0, "gen_input": 0, "gen_output": 0, "gen_usd": 0.0,
            "repair_input": 0, "repair_output": 0, "repair_usd": 0.0, "files_completed": 0,
            "errors": 0, "model": r.get("model"), "effort": r.get("effort")})
        if r.get("role") == "repair":
            t["repair_input"] += int(r.get("input_tokens") or 0)
            t["repair_output"] += int(r.get("output_tokens") or 0)
            t["repair_usd"] += float(r.get("cost_usd") or 0)
            continue
        t["segments"] += 1
        t["continuations"] += 1 if r.get("continuation") else 0
        t["gen_input"] += int(r.get("input_tokens") or 0) + int(r.get("cache_read_tokens") or 0)
        t["gen_output"] += int(r.get("output_tokens") or 0)
        t["gen_usd"] += float(r.get("cost_usd") or 0)
        t["files_completed"] = max(t["files_completed"], int(r.get("files_completed") or 0))
        t["errors"] += 0 if r.get("exit", 0) == 0 else 1
    for t in tasks.values():
        total_out = t["gen_output"] + t["repair_output"]
        total = t["gen_input"] + t["repair_input"] + total_out
        t["output_share"] = round(total_out / total, 3) if total else None
        t["repair_share_of_tokens"] = (round((t["repair_input"] + t["repair_output"]) / total, 3)
                                       if total else None)
    return tasks
