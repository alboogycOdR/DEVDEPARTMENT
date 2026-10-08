"""OMLCP briefs, manifests, spec excerpts, lane classification, packets, config."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from omlcp_packet import (DEFAULTS, Brief, Manifest, build_packet, classify, continuation_prompt,  # noqa: E402
                          extract_sections, judgment_model, load_config, parse_spec_refs)

REPO_ROOT = Path(__file__).resolve().parents[1]

SPEC = """# Widget spec

## 1. Scope
Widgets render.

## 2. Behaviour
### 2.1 Detail
Widgets resize.

## 3. Errors
Raise WidgetError.
"""

MANIFEST = {
    "language": "python",
    "conventions": "PEP 8.",
    "files": [
        {"path": "src/widget/core.py", "purpose": "Core.", "exports": ["render", "WidgetError"], "est_lines": 200},
        {"path": "tests/test_widget.py", "purpose": "Tests.", "exports": [], "est_lines": 120},
    ],
    "context": ["src/api.py"],
    "dependencies": {"python": ["pytest==8.3.3"]},
    "wiring": ["src/app.py registers render (TASK-090)"],
    "tests": "python -m pytest -q tests/test_widget.py",
}


def make_repo(tmp_path, *, owned="src/widget/** (new), tests/test_widget.py (new)", manifest=MANIFEST,
              description="Build the widget renderer.", lane=None, spec_refs="specs/WIDGET.md §2–§3",
              extra_fields=""):
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "WIDGET.md").write_text(SPEC, encoding="utf-8")
    (tmp_path / "dossiers").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "api.py").write_text("def get():\n    return 1\n", encoding="utf-8")
    lane_line = f"**Lane:** {lane}\n" if lane else ""
    plan = f"""---
plan_version: 1.0
last_updated: 2026-10-06T10:00:00Z
overall_status: active
---

## Work Items

### TASK-051
**Title:** Widget renderer
**Status:** pending
**Assigned_To:** S5
**Priority:** high
**Spec_References:** {spec_refs}
**Owned_Paths:** {owned}
{lane_line}**Depends_On:** —
**Description:** {description}
**Acceptance_Criteria:**
- [ ] render() draws widgets (spec §2)
- [ ] errors raise WidgetError (spec §3)
{extra_fields}**Updated_By:** ORCH
**Updated_At:** 2026-10-06T10:00:00Z
"""
    (tmp_path / "PLAN.md").write_text(plan, encoding="utf-8")
    dossier = "# TASK-051\n\n## Brief\nWidgets.\n\n## Intended approach\nPure functions first.\n\n"
    if manifest is not None:
        dossier += "## Generation manifest\n\n```omlcp-manifest\n" + json.dumps(manifest, indent=2) + "\n```\n"
    dossier += "\n## Work Log\n"
    (tmp_path / "dossiers" / "TASK-051.md").write_text(dossier, encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------- manifest --
def test_manifest_parse_roundtrip():
    m = Manifest.parse(json.dumps(MANIFEST))
    assert m.paths == ["src/widget/core.py", "tests/test_widget.py"]
    assert m.files[0].exports == ["render", "WidgetError"] and m.files[0].est_lines == 200


@pytest.mark.parametrize("bad,msg", [
    ("not json", "valid JSON"),
    (json.dumps({"files": []}), "non-empty"),
    (json.dumps({"files": [{"path": "a.py"}]}), "purpose"),
    (json.dumps({"files": [{"path": "a.py", "purpose": "x"}, {"path": "a.py", "purpose": "y"}]}), "twice"),
    (json.dumps({"files": [{"path": "a.py", "purpose": "x", "exports": "run"}]}), "exports"),
    (json.dumps({"files": [{"path": "a.py", "purpose": "x", "est_lines": -1}]}), "est_lines"),
    (json.dumps({"files": [{"path": "a.py", "purpose": "x"}], "dependencies": ["x"]}), "dependencies"),
])
def test_manifest_validation(bad, msg):
    with pytest.raises(ValueError, match=msg):
        Manifest.parse(bad)


# ------------------------------------------------------------ spec excerpts --
def test_parse_spec_refs_ranges_and_multiple_specs():
    refs = parse_spec_refs("specs/A.md §0–§3, §6 (A1), §7 (exit); specs/B.md")
    assert refs == [("specs/A.md", [0, 1, 2, 3, 6, 7]), ("specs/B.md", [])]
    assert parse_spec_refs("specs/LOOP.md §10 (E-H.4) Acceptance") == [("specs/LOOP.md", [10])]


def test_extract_sections_includes_subsections_and_stops_at_peer():
    out = extract_sections(SPEC, [2])
    assert "Widgets resize." in out and "2.1 Detail" in out
    assert "Raise WidgetError" not in out and "Widgets render." not in out
    assert extract_sections(SPEC, []) == SPEC
    assert extract_sections(SPEC, [9]) == ""


def test_extract_sections_on_a_real_pack_spec():
    text = (REPO_ROOT / "specs" / "CLAUDE_NATIVE_LEVERAGE_2026-09.md").read_text(encoding="utf-8")
    out = extract_sections(text, [3])
    assert out.startswith("## 3. F2") and "## 4." not in out


# -------------------------------------------------------------------- brief --
def test_brief_from_plan_collects_everything(tmp_path):
    b = Brief.from_plan(make_repo(tmp_path), "TASK-051")
    assert b.title == "Widget renderer" and b.mode == "pack"
    assert b.owned == ["src/widget/**", "tests/test_widget.py"]
    assert b.owned_new == ["src/widget/**", "tests/test_widget.py"]
    assert len(b.acceptance) == 2 and b.acceptance[0].startswith("render()")
    assert b.approach == "Pure functions first."
    label, body = b.spec_excerpts[0]
    assert label == "specs/WIDGET.md §2,3" and "Widgets resize." in body and "Widgets render." not in body
    assert b.context_files[0][0] == "src/api.py"
    assert b.est_output_tokens(12) == 320 * 12


def test_brief_reports_missing_pieces(tmp_path):
    repo = make_repo(tmp_path, manifest=None, spec_refs="specs/NOPE.md §1")
    b = Brief.from_plan(repo, "TASK-051")
    assert b.manifest is None
    assert any("NOPE.md does not exist" in w for w in b.warnings)
    with pytest.raises(KeyError):
        Brief.from_plan(repo, "TASK-999")


def test_brief_from_standalone_file(tmp_path):
    from omlcp import BRIEF_TEMPLATE

    p = tmp_path / "brief.md"
    p.write_text(BRIEF_TEMPLATE, encoding="utf-8")
    b = Brief.from_file(p)
    assert b.task_id == "FEAT-001" and b.mode == "standalone"
    assert b.manifest.paths == ["src/feature/core.py", "tests/test_core.py"]
    assert len(b.acceptance) == 2


# ----------------------------------------------------------------- classify --
CFG = dict(DEFAULTS)


def test_classify_generate_for_clean_greenfield_task(tmp_path):
    repo = make_repo(tmp_path)
    d = classify(Brief.from_plan(repo, "TASK-051"), CFG, repo)
    assert d.lane == "generate", d.blockers
    assert d.segments_needed == 1


def test_classify_iterate_when_files_exist(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "src" / "widget").mkdir()
    (repo / "src" / "widget" / "core.py").write_text("x = 1\n")
    d = classify(Brief.from_plan(repo, "TASK-051"), CFG, repo)
    assert d.lane == "iterate" and any("already exists" in b for b in d.blockers)


def test_classify_iterate_for_discovery_work(tmp_path):
    repo = make_repo(tmp_path, description="Investigate the flaky renderer and fix it.")
    d = classify(Brief.from_plan(repo, "TASK-051"), CFG, repo)
    assert d.lane == "iterate" and any("discovery" in b for b in d.blockers)


def test_classify_iterate_without_manifest(tmp_path):
    repo = make_repo(tmp_path, manifest=None)
    assert classify(Brief.from_plan(repo, "TASK-051"), CFG, repo).lane == "iterate"


def test_classify_respects_orch_lane_field(tmp_path):
    repo = make_repo(tmp_path, lane="iterate")
    d = classify(Brief.from_plan(repo, "TASK-051"), CFG, repo)
    assert d.lane == "iterate" and any("Lane:** iterate" in b for b in d.blockers)


def test_classify_iterate_when_manifest_leaves_territory(tmp_path):
    repo = make_repo(tmp_path, owned="src/widget/** (new)")
    d = classify(Brief.from_plan(repo, "TASK-051"), CFG, repo)
    assert d.lane == "iterate" and any("outside the task's Owned_Paths" in b for b in d.blockers)


def test_classify_splits_oversized_work(tmp_path):
    big = json.loads(json.dumps(MANIFEST))
    big["files"][0]["est_lines"] = 20000
    repo = make_repo(tmp_path, manifest=big)
    d = classify(Brief.from_plan(repo, "TASK-051"), CFG, repo)
    assert d.lane == "iterate" and any("split the task" in b for b in d.blockers)


def test_classify_flags_unmarked_new_paths_and_unpinned_deps(tmp_path):
    m = json.loads(json.dumps(MANIFEST))
    m["dependencies"] = {"python": ["requests"]}
    repo = make_repo(tmp_path, owned="src/widget/**, tests/test_widget.py", manifest=m)
    d = classify(Brief.from_plan(repo, "TASK-051"), CFG, repo)
    assert d.lane == "generate"
    assert any("not marked '(new)'" in s for s in d.signals)
    assert any("unpinned" in s for s in d.signals)


# ------------------------------------------------------------------- packet --
def test_packet_contains_contract_manifest_order_and_nonce(tmp_path):
    repo = make_repo(tmp_path)
    pkt = build_packet(Brief.from_plan(repo, "TASK-051"), CFG, nonce="0123456789ab")
    assert "@@OMLCP 0123456789ab FILE <path>" in pkt.system
    assert pkt.prompt.index("src/widget/core.py") < pkt.prompt.index("tests/test_widget.py")
    assert "Start now with `@@OMLCP 0123456789ab FILE src/widget/core.py`" in pkt.prompt
    assert "Read-only context — src/api.py" in pkt.prompt and "pytest==8.3.3" in pkt.prompt
    assert "src/app.py registers render" in pkt.prompt
    # Static material precedes task-specific material (prompt-cache friendly).
    assert pkt.prompt.index("## Conventions") < pkt.prompt.index("## Task")
    assert pkt.est_input_tokens > 0 and pkt.est_output_tokens == 320 * 12


def test_packet_never_includes_plan_md(tmp_path):
    repo = make_repo(tmp_path)
    pkt = build_packet(Brief.from_plan(repo, "TASK-051"), CFG)
    assert "plan_version" not in pkt.prompt and "## Work Items" not in pkt.prompt


def test_packet_budget_warn_and_hard_limit(tmp_path):
    repo = make_repo(tmp_path)
    brief = Brief.from_plan(repo, "TASK-051")
    warn = build_packet(brief, dict(CFG, input_warn_tokens=10))
    assert any("constrained-input target" in w for w in warn.warnings)
    with pytest.raises(ValueError, match="split the task"):
        build_packet(brief, dict(CFG, input_max_tokens=10))


def test_packet_fence_survives_backticks_in_context(tmp_path):
    repo = make_repo(tmp_path)
    (repo / "src" / "api.py").write_text('DOC = """\n```\ncode\n```\n"""\n')
    pkt = build_packet(Brief.from_plan(repo, "TASK-051"), CFG)
    assert "````\nDOC" in pkt.prompt


def test_packet_requires_manifest(tmp_path):
    repo = make_repo(tmp_path, manifest=None)
    with pytest.raises(ValueError, match="omlcp-manifest"):
        build_packet(Brief.from_plan(repo, "TASK-051"), CFG)


def test_continuation_prompt_orders_resume_first():
    txt = continuation_prompt("abcdef123456", ["a.py", "b.py", "c.py"], ["a.py"], "c.py")
    assert txt.index("- c.py") < txt.index("- b.py")
    assert "Start immediately with `@@OMLCP abcdef123456 FILE c.py`" in txt
    stateless = continuation_prompt("abcdef123456", ["a.py", "b.py"], ["a.py"], "b.py", {"a.py": "x = 1\n"})
    assert "### a.py" in stateless and "x = 1" in stateless
    done = continuation_prompt("abcdef123456", ["a.py"], ["a.py"], None)
    assert "Emit only `@@OMLCP abcdef123456 DONE`" in done


# ------------------------------------------------------------------- config --
def test_load_config_overlays_local_file(tmp_path):
    (tmp_path / "autopilot.json").write_text(json.dumps({"omlcp": {"model": "m1", "_note": "x"}}))
    (tmp_path / "autopilot.local.json").write_text(json.dumps({"omlcp": {"effort": "low"}}))
    cfg = load_config(tmp_path)
    assert cfg["model"] == "m1" and cfg["effort"] == "low" and "_note" not in cfg
    assert cfg["max_continuations"] == DEFAULTS["max_continuations"]


def test_judgment_model_prefers_wave_f_models_block(tmp_path):
    (tmp_path / "autopilot.json").write_text(json.dumps({"judgment_model": "legacy"}))
    assert judgment_model(tmp_path) == "legacy"
    (tmp_path / "autopilot.json").write_text(json.dumps(
        {"judgment_model": "legacy", "models": {"roles": {"judgment": {"model": "new"}}}}))
    assert judgment_model(tmp_path) == "new"


def test_shipped_template_keeps_the_lane_disabled_and_maker_ne_checker():
    """Ask-don't-auto-flip (Wave F N4): the pack template ships the lane off, and the
    default generator model is never the reviewer model."""
    cfg = json.loads((REPO_ROOT / "autopilot.json").read_text(encoding="utf-8"))
    assert cfg["omlcp"]["enabled"] is False
    assert load_config(REPO_ROOT)["model"] != judgment_model(REPO_ROOT)
