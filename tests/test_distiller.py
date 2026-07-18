"""Wave C tests — scripts/distiller.py"""
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import distiller as dm  # noqa: E402
import instincts as im  # noqa: E402

TS1 = "2026-07-10T10:00:00Z"
TS2 = "2026-07-12T10:00:00Z"
TS3 = "2026-07-14T10:00:00Z"


def fake_model(tmp_path, output_text):
    """Register a distill_cmd that echoes canned model output."""
    script = tmp_path / "fake_model.py"
    payload = tmp_path / "fake_output.txt"
    payload.write_text(output_text, encoding="utf-8")
    script.write_text(
        "import sys, pathlib\n"
        f"sys.stdout.write(pathlib.Path({str(payload)!r}).read_text(encoding='utf-8'))\n",
        encoding="utf-8")
    return [sys.executable, str(script)]


def repo_with(tmp_path, review_lines, plan="", instincts_text=None, protocol=True):
    (tmp_path / "REVIEW.md").write_text("\n".join(review_lines) + "\n", encoding="utf-8")
    if plan:
        (tmp_path / "PLAN.md").write_text(plan, encoding="utf-8")
    if instincts_text is not None:
        (tmp_path / "INSTINCTS.md").write_text(instincts_text, encoding="utf-8")
    if protocol:
        (tmp_path / "AGENTS.md").write_text("# AGENTS\nconstitution\n", encoding="utf-8")
        (tmp_path / "CLAUDE.md").write_text("# CLAUDE\nproject rules\n", encoding="utf-8")
        (tmp_path / "briefings").mkdir(exist_ok=True)
        (tmp_path / "briefings" / "GROK_BUILD_BRIEFING.md").write_text("gb\n", encoding="utf-8")
        (tmp_path / "briefings" / "CODEX_BRIEFING.md").write_text("cx\n", encoding="utf-8")
    return tmp_path


def sha_tree(repo, names):
    h = {}
    for n in names:
        p = repo / n
        h[n] = hashlib.sha256(p.read_bytes()).hexdigest()
    return h


PROTOCOL_FILES = ["AGENTS.md", "CLAUDE.md",
                  "briefings/GROK_BUILD_BRIEFING.md", "briefings/CODEX_BRIEFING.md"]

FINDINGS = [
    f"| TASK-101 | GB | rework | tests | Territory: python/orb/** | missing unit tests | {TS1} |",
    f"| TASK-102 | GB | rework | tests | Territory: python/orb/** | tests skipped | {TS2} |",
    f"| TASK-103 | CX | rework | tests | Territory: python/orb/** | no coverage | {TS3} |",
]

MODEL_INSTINCT = (
    "### INST-001\n"
    "**Rule:** Python ORB changes must ship with unit tests before needs_review.\n"
    "**Territory:** python/orb/**\n"
    "**Confidence:** 0.6\n"
    "**Source:** TASK-101 rework, TASK-102 rework, TASK-103 rework\n"
    "**Status:** active\n")

MODEL_AMENDMENT = (
    "## PROPOSED AMENDMENT\n"
    "**Target:** briefings/GROK_BUILD_BRIEFING.md\n"
    "**Diff:** add a Common Rationalizations row: 'tests can come later' -> "
    "'tests gate needs_review; later never arrives'.\n"
    "**Evidence:** TASK-101, TASK-102, TASK-103.\n")


# ------------------------------------------------------------ skip logic ----
class TestSkip:
    def test_skips_below_min_new_findings(self, tmp_path):
        repo = repo_with(tmp_path, FINDINGS[:2])
        r = dm.run(repo, {"learning": {"distill_cmd": fake_model(tmp_path, MODEL_INSTINCT)}})
        assert r.ok and r.skipped and "2 new findings" in r.reason
        assert not (repo / "INSTINCTS.md").exists()

    def test_marker_excludes_old_findings(self, tmp_path):
        repo = repo_with(tmp_path, FINDINGS)
        dm.write_marker(repo, TS3)  # everything already processed
        r = dm.run(repo, {"learning": {"distill_cmd": fake_model(tmp_path, MODEL_INSTINCT)}})
        assert r.skipped

    def test_no_review_md_is_clean_skip(self, tmp_path):
        r = dm.run(tmp_path, {})
        assert r.ok and r.skipped


# -------------------------------------------------------- happy path -------
class TestDistill:
    def test_seeded_rework_pattern_produces_instinct(self, tmp_path):
        repo = repo_with(tmp_path, FINDINGS)
        cfg = {"learning": {"distill_cmd": fake_model(tmp_path, MODEL_INSTINCT)}}
        r = dm.run(repo, cfg)
        assert r.ok and not r.skipped
        assert r.new_instincts == ["INST-001"]
        loaded = im.load(repo)
        assert loaded[0].rule.startswith("Python ORB changes")
        assert loaded[0].confidence == im.SEED_CONFIDENCE  # forced seed
        # marker advanced -> immediate re-run is a skip (no re-distilling)
        assert dm.run(repo, cfg).skipped

    def test_new_instinct_id_forced_sequential(self, tmp_path):
        existing = ("### INST-005\n**Rule:** old rule\n**Territory:** docs/**\n"
                    "**Confidence:** 0.8\n**Source:** TASK-001 rework\n**Status:** active\n")
        repo = repo_with(tmp_path, FINDINGS, instincts_text=existing)
        cfg = {"learning": {"distill_cmd": fake_model(tmp_path, MODEL_INSTINCT)}}
        r = dm.run(repo, cfg)
        # model said INST-001, but INST-005 exists -> forced to INST-006
        assert r.new_instincts == ["INST-006"]
        ids = [i.inst_id for i in im.load(repo)]
        assert ids == ["INST-005", "INST-006"]

    def test_lifecycle_bump_on_matching_rework(self, tmp_path):
        existing = ("### INST-001\n**Rule:** ship tests\n**Territory:** python/orb/**\n"
                    "**Confidence:** 0.6\n**Source:** TASK-050 rework\n**Status:** active\n")
        repo = repo_with(tmp_path, FINDINGS, instincts_text=existing)
        cfg = {"learning": {"distill_cmd": fake_model(tmp_path, "")}}
        r = dm.run(repo, cfg)
        assert r.ok
        i = im.load(repo)[0]
        assert i.confidence > 0.6  # bumped by matching rework findings
        assert any("TASK-101" in s for s in i.source)


# ------------------------------------------------- atomicity / fail-open ----
class TestAtomicity:
    def test_malformed_output_leaves_instincts_untouched(self, tmp_path):
        existing = ("### INST-001\n**Rule:** ship tests\n**Territory:** flutter/**\n"
                    "**Confidence:** 0.6\n**Source:** TASK-050 rework\n**Status:** active\n")
        repo = repo_with(tmp_path, FINDINGS, instincts_text=existing)
        before = (repo / "INSTINCTS.md").read_bytes()
        cfg = {"learning": {"distill_cmd": fake_model(tmp_path, "utterly not the format!!!")}}
        r = dm.run(repo, cfg)
        assert r.ok  # graceful — nothing usable, nothing applied
        assert r.new_instincts == [] and r.updated_instincts == []
        assert (repo / "INSTINCTS.md").read_bytes() == before

    def test_model_call_failure_fail_open(self, tmp_path):
        repo = repo_with(tmp_path, FINDINGS)
        bad = tmp_path / "boom.py"
        bad.write_text("import sys; sys.exit(3)\n", encoding="utf-8")
        r = dm.run(repo, {"learning": {"distill_cmd": [sys.executable, str(bad)]}})
        assert r.ok is False
        assert "AUTOPILOT_LOG.md" in [p.name for p in repo.iterdir()]  # logged


# ------------------------------------------------------ constitutional gate -
class TestConstitutionalGate:
    def test_amendment_written_only_to_pending_dir(self, tmp_path):
        repo = repo_with(tmp_path, FINDINGS)
        before = sha_tree(repo, PROTOCOL_FILES)
        cfg = {"learning": {"distill_cmd":
                            fake_model(tmp_path, MODEL_INSTINCT + "\n" + MODEL_AMENDMENT)}}
        r = dm.run(repo, cfg)
        assert r.amendments == ["AMEND-001"]
        amend = repo / ".devteam" / "pending_amendments" / "AMEND-001.md"
        assert amend.exists()
        assert "**Status:** pending" in amend.read_text(encoding="utf-8")
        # AGENTS.md / CLAUDE.md / briefings byte-identical
        assert sha_tree(repo, PROTOCOL_FILES) == before

    def test_amendment_only_output_still_gated(self, tmp_path):
        repo = repo_with(tmp_path, FINDINGS)
        before = sha_tree(repo, PROTOCOL_FILES)
        cfg = {"learning": {"distill_cmd": fake_model(tmp_path, MODEL_AMENDMENT)}}
        r = dm.run(repo, cfg)
        assert r.amendments and not r.new_instincts
        assert sha_tree(repo, PROTOCOL_FILES) == before

    def test_amend_ids_sequential_across_runs(self, tmp_path):
        repo = repo_with(tmp_path, FINDINGS)
        d = repo / ".devteam" / "pending_amendments"
        d.mkdir(parents=True)
        (d / "AMEND-004.md").write_text("# AMEND-004\n**Status:** pending\n", encoding="utf-8")
        cfg = {"learning": {"distill_cmd": fake_model(tmp_path, MODEL_AMENDMENT)}}
        r = dm.run(repo, cfg)
        assert r.amendments == ["AMEND-005"]


# -------------------------------------------------------------- mining ------
class TestMining:
    def test_extract_new_findings_respects_marker(self):
        got = dm.extract_new_findings("\n".join(FINDINGS), TS1)
        assert len(got) == 2  # TS2 and TS3 only

    def test_mine_outcomes_paths_from_plan_fallback(self):
        plan = ("---\nplan_version: 1\nlast_updated: 2026-07-14T00:00:00Z\n"
                "overall_status: active\n---\n\n### TASK-200\n"
                "**Status:** done\n**Owned_Paths:** python/orb/**\n")
        outs = dm.mine_outcomes([f"TASK-200 approved first-pass {TS1}"], plan)
        assert outs[0]["paths"] == ["python/orb/**"] and outs[0]["clean"]

    def test_mine_outcomes_inline_territory_wins(self):
        outs = dm.mine_outcomes(
            [f"| TASK-201 | rework | spec | Territory: mql5/** | note | {TS1} |"])
        assert outs[0]["paths"] == ["mql5/**"] and outs[0]["rework"]
        assert outs[0]["category"] == "spec"

    def test_split_model_output(self):
        blocks, amend = dm.split_model_output(MODEL_INSTINCT + MODEL_AMENDMENT)
        assert "INST-001" in blocks and amend.startswith("## PROPOSED AMENDMENT")
