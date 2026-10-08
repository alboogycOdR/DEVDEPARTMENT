"""OMLCP economics: reproduce the paper, then correct it for pricing and caching."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import omlcp_economics as econ  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_paper_equations_reproduce_published_figures():
    # §4.3: 10k lines -> 880,000 vs 55,000 = 16x ; 30k lines -> ~5.04M vs 115,000 ≈ 44x
    a10, o10 = econ.paper_agentic_tokens(10_000), econ.paper_omlcp_tokens(10_000)
    assert a10["total"] == pytest.approx(880_000, rel=0.001) and o10["total"] == 55_000
    a30, o30 = econ.paper_agentic_tokens(30_000), econ.paper_omlcp_tokens(30_000)
    assert a30["total"] == pytest.approx(5_040_000, rel=0.001) and o30["total"] == 115_000
    assert a30["total"] / o30["total"] == pytest.approx(43.8, abs=0.1)


@pytest.mark.parametrize("r,expected", [(0.50, 724_000), (0.85, 934_000)])
def test_paper_sensitivity_section_4_4(r, expected):
    assert econ.paper_agentic_tokens(10_000, r=r)["total"] == pytest.approx(expected, rel=0.001)


def test_measured_tokens_per_line_compresses_the_paper_ratio():
    t3 = econ.priced_comparison(10_000, "claude-opus-5-5", 3.0, cache_hit=0.9)
    t12 = econ.priced_comparison(10_000, "claude-opus-5-5", 12.0, cache_hit=0.9)
    assert t3["token_ratio"] == pytest.approx(16.0, rel=0.02)
    assert t12["token_ratio"] < 8 < t3["token_ratio"]


def test_caching_makes_paper_style_rehydration_cheap_in_dollars():
    cached = econ.priced_comparison(10_000, "claude-opus-5-5", 12.0, cache_hit=0.9)
    uncached = econ.priced_comparison(10_000, "claude-opus-5-5", 12.0, cache_hit=0.0)
    assert cached["agentic_usd"] < uncached["agentic_usd"]
    assert cached["agentic_input_share_of_usd"] < 0.05  # reasoning/output dominate the bill


def test_usage_cost_and_price_lookup():
    assert econ.usage_cost("claude-opus-5-5", 1_000_000, 0) == pytest.approx(4.0)
    assert econ.usage_cost("claude-opus-5-5", 0, 0, cache_read=1_000_000) == pytest.approx(0.2)
    assert econ.price_for("claude-haiku-4-5-20251001")["out"] == 5.0
    with pytest.raises(KeyError):
        econ.price_for("gpt-unknown")


def test_devdept_model_shapes():
    base = econ.devdept_compare(econ.DevDeptParams(lines=3000, prefix_doc_tokens=100_000))
    slim = econ.devdept_compare(econ.DevDeptParams(lines=3000, prefix_doc_tokens=15_000))
    assert base["token_ratio"] > 1 and base["usd_ratio"] > 1
    assert base["builder_only_token_ratio"] > base["token_ratio"]  # review cost is lane-independent
    assert slim["agentic"]["usd"] < base["agentic"]["usd"]  # PLAN.md size matters on its own
    assert base["omlcp"]["review_tokens"] == base["agentic"]["review_tokens"]
    big = econ.devdept_compare(econ.DevDeptParams(lines=20_000, tokens_per_line=12))
    assert big["omlcp"]["segments"] == 4


def test_measure_repo_on_the_pack_itself():
    m = econ.measure_repo(REPO_ROOT)
    assert m["prefix_tokens"]["CLAUDE.md"] > 0 and m["code_lines"] > 1000
    assert 6 <= m["tokens_per_line"] <= 20  # far above the paper's T = 3
    pp = econ.params_from_repo(REPO_ROOT, lines=1000)
    assert "tokens_per_line" in pp.measured and pp.prefix_doc_tokens == m["prefix_total"]
    pinned = econ.params_from_repo(REPO_ROOT, lines=1000, tokens_per_line=5.0)
    assert pinned.tokens_per_line == 5.0


def test_ledger_report_separates_generation_from_repair(tmp_path):
    rows = [
        {"lane": "generate", "role": "generator", "task_id": "T1", "segment": 0, "continuation": False,
         "input_tokens": 1000, "cache_read_tokens": 0, "output_tokens": 40000, "cost_usd": 0.4,
         "files_completed": 3, "exit": 0, "model": "m", "effort": "low"},
        {"lane": "generate", "role": "generator", "task_id": "T1", "segment": 1, "continuation": True,
         "input_tokens": 100, "cache_read_tokens": 41000, "output_tokens": 10000, "cost_usd": 0.1,
         "files_completed": 5, "exit": 0},
        {"lane": "generate", "role": "repair", "task_id": "T1", "input_tokens": 20000, "output_tokens": 2000,
         "cost_usd": 0.05},
        {"lane": "iterate", "role": "builder", "task_id": "T2"},
    ]
    (tmp_path / ".devteam").mkdir()
    (tmp_path / ".devteam" / "ledger.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\nnot json\n")
    rep = econ.ledger_report(econ.read_ledger(tmp_path))
    assert set(rep) == {"T1"}
    t = rep["T1"]
    assert t["segments"] == 2 and t["continuations"] == 1 and t["files_completed"] == 5
    assert t["gen_output"] == 50_000 and t["repair_input"] == 20_000
    assert t["gen_usd"] == pytest.approx(0.5) and t["repair_usd"] == pytest.approx(0.05)
    assert 0 < t["repair_share_of_tokens"] < 0.5 and 0 < t["output_share"] < 1
    assert econ.read_ledger(tmp_path / "nowhere") == []
