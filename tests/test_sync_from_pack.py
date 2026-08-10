"""tests/test_sync_from_pack.py — Option-B sync mode (v4.6).

Real temp-dir packs and projects, no mocks: the whole point of sync is
byte-level filesystem behavior, so that's what gets tested.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sync_from_pack as sfp  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def make_pack(tmp_path: Path, files: dict[str, str],
              manifest: dict | None = None) -> Path:
    pack = tmp_path / "pack"
    pack.mkdir(exist_ok=True)
    for rel, content in files.items():
        p = pack / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
    if manifest is None:
        manifest = {"manifest_version": 1,
                    "framework_owned": sorted(files.keys()),
                    "project_owned": ["PLAN.md"],
                    "merge_special": {}}
    (pack / sfp.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    return pack


def make_project(tmp_path: Path, files: dict[str, str]) -> Path:
    proj = tmp_path / "project"
    proj.mkdir(exist_ok=True)
    for rel, content in files.items():
        p = proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
    return proj


def seed_baseline(project: Path, files: dict[str, str]) -> None:
    """Simulate a prior sync having written these exact contents."""
    state = {"version": sfp.STATE_VERSION,
             "files": {rel: sfp.sha256_bytes(content.encode()) for rel, content in files.items()}}
    sfp.save_state(project, state)


# ================================================================ verdicts ==
class TestVerdicts:
    def test_in_sync(self, tmp_path):
        pack = make_pack(tmp_path, {"scripts/a.py": "same\n"})
        proj = make_project(tmp_path, {"scripts/a.py": "same\n"})
        report = sfp.run_sync(pack, proj)
        assert [d.verdict for d in report.decisions] == [sfp.IN_SYNC]

    def test_add_when_absent_in_project(self, tmp_path):
        pack = make_pack(tmp_path, {"scripts/new_tool.py": "new\n"})
        proj = make_project(tmp_path, {})
        report = sfp.run_sync(pack, proj)
        assert report.by(sfp.ADD)[0].rel == "scripts/new_tool.py"

    def test_update_when_baseline_matches_project(self, tmp_path):
        pack = make_pack(tmp_path, {"scripts/a.py": "v2 improved\n"})
        proj = make_project(tmp_path, {"scripts/a.py": "v1 original\n"})
        seed_baseline(proj, {"scripts/a.py": "v1 original\n"})
        report = sfp.run_sync(pack, proj)
        assert report.by(sfp.UPDATE)[0].rel == "scripts/a.py"

    def test_conflict_when_project_locally_modified(self, tmp_path):
        pack = make_pack(tmp_path, {"scripts/a.py": "v2\n"})
        proj = make_project(tmp_path, {"scripts/a.py": "v1 WITH LOCAL EDITS\n"})
        seed_baseline(proj, {"scripts/a.py": "v1\n"})
        report = sfp.run_sync(pack, proj)
        assert report.by(sfp.CONFLICT)[0].rel == "scripts/a.py"
        assert report.has_conflicts

    def test_legacy_project_no_baseline_is_conflict(self, tmp_path):
        """The exact orb-jun-26 situation: onboarded long ago, no sync state,
        files differ from the current pack. Conservative: conflict, not
        silent overwrite."""
        pack = make_pack(tmp_path, {"docs/PROTO.md": "v4.5 protocol\n"})
        proj = make_project(tmp_path, {"docs/PROTO.md": "v1.0.0 protocol\n"})
        report = sfp.run_sync(pack, proj)
        conflicts = report.by(sfp.CONFLICT)
        assert len(conflicts) == 1
        assert "legacy" in conflicts[0].detail

    def test_adopt_pack_resolves_conflict(self, tmp_path):
        pack = make_pack(tmp_path, {"docs/PROTO.md": "v4.5\n"})
        proj = make_project(tmp_path, {"docs/PROTO.md": "v1.0.0\n"})
        report = sfp.run_sync(pack, proj, apply=True, adopt_pack=True)
        assert report.by(sfp.CONFLICT_ADOPTED)
        assert (proj / "docs/PROTO.md").read_text() == "v4.5\n"

    def test_manifest_lists_file_missing_from_pack(self, tmp_path):
        pack = make_pack(tmp_path, {"scripts/a.py": "x\n"},
                         manifest={"manifest_version": 1,
                                   "framework_owned": ["scripts/a.py", "scripts/ghost.py"],
                                   "project_owned": [], "merge_special": {}})
        proj = make_project(tmp_path, {"scripts/a.py": "x\n"})
        report = sfp.run_sync(pack, proj)
        assert report.by(sfp.MISSING_IN_PACK)[0].rel == "scripts/ghost.py"


# ============================================================== dry-run =====
class TestDryRunSafety:
    def test_dry_run_writes_nothing(self, tmp_path):
        pack = make_pack(tmp_path, {"scripts/a.py": "v2\n", "scripts/b.py": "new\n"})
        proj = make_project(tmp_path, {"scripts/a.py": "v1\n"})
        seed_baseline(proj, {"scripts/a.py": "v1\n"})
        state_before = (proj / sfp.SYNC_STATE_REL).read_bytes()
        sfp.run_sync(pack, proj, apply=False)
        assert (proj / "scripts/a.py").read_text() == "v1\n"          # unchanged
        assert not (proj / "scripts/b.py").exists()                    # not added
        assert (proj / sfp.SYNC_STATE_REL).read_bytes() == state_before  # state untouched

    def test_apply_writes_and_updates_state(self, tmp_path):
        pack = make_pack(tmp_path, {"scripts/a.py": "v2\n", "scripts/b.py": "new\n"})
        proj = make_project(tmp_path, {"scripts/a.py": "v1\n"})
        seed_baseline(proj, {"scripts/a.py": "v1\n"})
        sfp.run_sync(pack, proj, apply=True)
        assert (proj / "scripts/a.py").read_text() == "v2\n"
        assert (proj / "scripts/b.py").read_text() == "new\n"
        state = sfp.load_state(proj)
        assert state["files"]["scripts/a.py"] == sfp.sha256_bytes(b"v2\n")
        assert state["files"]["scripts/b.py"] == sfp.sha256_bytes(b"new\n")

    def test_conflict_file_never_written_without_adopt(self, tmp_path):
        pack = make_pack(tmp_path, {"scripts/a.py": "pack version\n"})
        proj = make_project(tmp_path, {"scripts/a.py": "my local edits\n"})
        sfp.run_sync(pack, proj, apply=True)  # no adopt_pack
        assert (proj / "scripts/a.py").read_text() == "my local edits\n"

    def test_first_apply_on_legacy_project_establishes_baseline_for_in_sync_files(self, tmp_path):
        pack = make_pack(tmp_path, {"scripts/a.py": "same\n"})
        proj = make_project(tmp_path, {"scripts/a.py": "same\n"})
        sfp.run_sync(pack, proj, apply=True)
        state = sfp.load_state(proj)
        assert state["files"]["scripts/a.py"] == sfp.sha256_bytes(b"same\n")

    def test_project_owned_files_never_touched(self, tmp_path):
        pack = make_pack(tmp_path, {"scripts/a.py": "x\n"})
        # PLAN.md exists in the pack too (as a template) but is project_owned
        (tmp_path / "pack" / "PLAN.md").write_text("pack template plan\n", encoding="utf-8")
        proj = make_project(tmp_path, {"scripts/a.py": "x\n", "PLAN.md": "MY REAL PLAN\n"})
        sfp.run_sync(pack, proj, apply=True, adopt_pack=True)
        assert (proj / "PLAN.md").read_text() == "MY REAL PLAN\n"


# ================================================================ --only ====
class TestOnlyFilter:
    def test_only_restricts_scope(self, tmp_path):
        pack = make_pack(tmp_path, {"scripts/a.py": "v2\n", "scripts/b.py": "v2\n"})
        proj = make_project(tmp_path, {"scripts/a.py": "v1\n", "scripts/b.py": "v1\n"})
        seed_baseline(proj, {"scripts/a.py": "v1\n", "scripts/b.py": "v1\n"})
        sfp.run_sync(pack, proj, apply=True, only=["scripts/a.py"])
        assert (proj / "scripts/a.py").read_text() == "v2\n"
        assert (proj / "scripts/b.py").read_text() == "v1\n"

    def test_only_with_unknown_path_flags_it(self, tmp_path):
        pack = make_pack(tmp_path, {"scripts/a.py": "x\n"})
        proj = make_project(tmp_path, {"scripts/a.py": "x\n"})
        report = sfp.run_sync(pack, proj, only=["scripts/not_in_manifest.py"])
        assert report.by(sfp.MISSING_IN_PACK)


# ========================================================= merge_special ====
class TestMarkerSectionMerge:
    MANIFEST = {"manifest_version": 1, "framework_owned": [], "project_owned": [],
                "merge_special": {"CLAUDE.md": {"strategy": "marker_section",
                                                "marker": "## Multi-Agent Orchestration"}}}

    def test_replaces_marker_section_preserves_project_content_above(self, tmp_path):
        pack = make_pack(tmp_path, {"CLAUDE.md": "## Multi-Agent Orchestration\nNEW PACK RULES v4.6\n"},
                         manifest=self.MANIFEST)
        proj = make_project(tmp_path, {
            "CLAUDE.md": "# My Project\nproject conventions here\n\n"
                          "## Multi-Agent Orchestration\nOLD RULES v1.0\n"})
        sfp.run_sync(pack, proj, apply=True)
        merged = (proj / "CLAUDE.md").read_text()
        assert merged.startswith("# My Project\nproject conventions here")
        assert "NEW PACK RULES v4.6" in merged
        assert "OLD RULES v1.0" not in merged

    def test_missing_marker_in_project_flags_not_clobbers(self, tmp_path):
        pack = make_pack(tmp_path, {"CLAUDE.md": "## Multi-Agent Orchestration\nX\n"},
                         manifest=self.MANIFEST)
        proj = make_project(tmp_path, {"CLAUDE.md": "# Project file with no marker at all\n"})
        report = sfp.run_sync(pack, proj, apply=True)
        assert (proj / "CLAUDE.md").read_text() == "# Project file with no marker at all\n"
        assert any("cannot merge safely" in n for n in report.merge_notes)

    def test_absent_project_file_is_onboardings_job(self, tmp_path):
        pack = make_pack(tmp_path, {"CLAUDE.md": "## Multi-Agent Orchestration\nX\n"},
                         manifest=self.MANIFEST)
        proj = make_project(tmp_path, {})
        report = sfp.run_sync(pack, proj, apply=True)
        assert not (proj / "CLAUDE.md").exists()
        assert any("onboard.md's job" in n for n in report.merge_notes)

    def test_already_current_section_is_noop(self, tmp_path):
        content = "# Mine\n## Multi-Agent Orchestration\nCURRENT\n"
        pack = make_pack(tmp_path, {"CLAUDE.md": "## Multi-Agent Orchestration\nCURRENT\n"},
                         manifest=self.MANIFEST)
        proj = make_project(tmp_path, {"CLAUDE.md": content})
        sfp.run_sync(pack, proj, apply=True)
        assert (proj / "CLAUDE.md").read_text() == content


class TestAddOnlyKeysMerge:
    MANIFEST = {"manifest_version": 1, "framework_owned": [], "project_owned": [],
                "merge_special": {"autopilot.json": {"strategy": "add_only_keys"}}}

    def test_adds_missing_keys_preserves_existing_values(self, tmp_path):
        pack = make_pack(tmp_path, {"autopilot.json": json.dumps({
            "interval_seconds": 300,
            "control": {"mode": "legacy"},
            "usage": {"defer_above_pct": 90, "critical_overrides": True},
        })}, manifest=self.MANIFEST)
        proj = make_project(tmp_path, {"autopilot.json": json.dumps({
            "interval_seconds": 120,               # project's own tuning
            "control": {"mode": "strict"},         # project chose strict
        })})
        sfp.run_sync(pack, proj, apply=True)
        merged = json.loads((proj / "autopilot.json").read_text())
        assert merged["interval_seconds"] == 120           # preserved
        assert merged["control"]["mode"] == "strict"       # preserved
        assert merged["usage"]["defer_above_pct"] == 90    # added

    def test_nested_missing_key_added_inside_existing_section(self, tmp_path):
        pack = make_pack(tmp_path, {"autopilot.json": json.dumps({
            "telegram": {"chat_allowlist": [], "poll_interval_seconds": 20}})},
            manifest=self.MANIFEST)
        proj = make_project(tmp_path, {"autopilot.json": json.dumps({
            "telegram": {"chat_allowlist": ["12345"]}})})
        sfp.run_sync(pack, proj, apply=True)
        merged = json.loads((proj / "autopilot.json").read_text())
        assert merged["telegram"]["chat_allowlist"] == ["12345"]      # preserved
        assert merged["telegram"]["poll_interval_seconds"] == 20      # added

    def test_corrupt_project_json_skipped_not_crashed(self, tmp_path):
        pack = make_pack(tmp_path, {"autopilot.json": json.dumps({"a": 1})},
                         manifest=self.MANIFEST)
        proj = make_project(tmp_path, {"autopilot.json": "{broken json,,,"})
        report = sfp.run_sync(pack, proj, apply=True)
        assert (proj / "autopilot.json").read_text() == "{broken json,,,"
        assert any("skipped" in n for n in report.merge_notes)


# ============================================================ state/misc ====
class TestStateAndCli:
    def test_corrupt_state_self_heals(self, tmp_path):
        proj = make_project(tmp_path, {})
        state_path = proj / sfp.SYNC_STATE_REL
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text("{not json", encoding="utf-8")
        assert sfp.load_state(proj) == {"version": sfp.STATE_VERSION, "files": {}}

    def test_pack_without_manifest_errors_clearly(self, tmp_path):
        pack = tmp_path / "pack"
        pack.mkdir()
        proj = make_project(tmp_path, {})
        with pytest.raises(FileNotFoundError, match="predates sync support"):
            sfp.run_sync(pack, proj)

    def test_cli_exit_codes(self, tmp_path):
        pack = make_pack(tmp_path, {"scripts/a.py": "v2\n"})
        proj = make_project(tmp_path, {"scripts/a.py": "local edits\n"})
        rc = sfp.main(["--pack", str(pack), "--project", str(proj)])
        assert rc == 2  # conflicts
        rc = sfp.main(["--pack", str(pack), "--project", str(proj), "--apply", "--adopt-pack"])
        assert rc == 0
        rc = sfp.main(["--pack", str(pack), "--project", str(proj)])
        assert rc == 0  # now in sync

    def test_cli_rejects_pack_equals_project(self, tmp_path):
        pack = make_pack(tmp_path, {"scripts/a.py": "x\n"})
        rc = sfp.main(["--pack", str(pack), "--project", str(pack)])
        assert rc == 1

    def test_byte_exact_copy_preserves_lf(self, tmp_path):
        """The CRLF lesson, locked in for sync too: copies are byte-exact."""
        pack = make_pack(tmp_path, {})
        script = tmp_path / "pack" / "scripts" / "tool.sh"
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_bytes(b"#!/usr/bin/env bash\nset -euo pipefail\n")
        (tmp_path / "pack" / sfp.MANIFEST_NAME).write_text(json.dumps({
            "manifest_version": 1, "framework_owned": ["scripts/tool.sh"],
            "project_owned": [], "merge_special": {}}), encoding="utf-8")
        proj = make_project(tmp_path, {})
        sfp.run_sync(tmp_path / "pack", proj, apply=True)
        data = (proj / "scripts" / "tool.sh").read_bytes()
        assert b"\r" not in data
        assert data == b"#!/usr/bin/env bash\nset -euo pipefail\n"


# =================================================== self-check on the REAL pack ==
class TestManifestMarkersMatchRealFiles:
    """v4.8 regression: sync-manifest.json's CLAUDE.md marker was
    "## Multi-Agent Orchestration" -- a string that existed in NEITHER the
    pack's own CLAUDE.md NOR any project that synced from it, since the
    heading had been rewritten at some point and the manifest never updated
    to match. Every prior test in this file used a SYNTHETIC pack fixture
    with a marker chosen to match, so nothing caught the real pack drifting
    away from its own manifest -- exactly the class of bug MISSING_IN_PACK
    already catches for framework_owned paths, but with no equivalent check
    for merge_special markers. These run against THIS repo, not a fixture,
    for that reason."""

    def _manifest(self):
        return json.loads((REPO_ROOT / sfp.MANIFEST_NAME).read_text(encoding="utf-8"))

    def test_every_marker_section_marker_exists_in_the_real_pack_file(self):
        m = self._manifest()
        for name, spec in m.get("merge_special", {}).items():
            if spec.get("strategy") != "marker_section":
                continue
            target = REPO_ROOT / name
            assert target.exists(), f"{name}: file listed in merge_special does not exist in the pack"
            text = target.read_text(encoding="utf-8")
            assert spec["marker"] in text, (
                f"{name}: configured marker {spec['marker']!r} does not appear in the pack's own "
                f"file -- every project's merge would silently fail with 'cannot merge safely' "
                f"forever. This is exactly the bug that shipped once already.")

    def test_no_dead_merge_special_entries(self):
        """Every merge_special key must correspond to logic sync_from_pack.py
        actually executes (or a manual_only entry, which is intentionally a
        report-only stub) -- not aspirational config for a strategy nobody
        wired up. AGENTS.md_when_project_has_own_content was exactly this:
        described a marker_section merge that was never implemented, so
        AGENTS.md was silently whole-file the entire time regardless of what
        the manifest claimed."""
        m = self._manifest()
        implemented_strategies = {"marker_section", "add_only_keys", "manual_only"}
        for name, spec in m.get("merge_special", {}).items():
            assert spec.get("strategy") in implemented_strategies, (
                f"{name}: strategy {spec.get('strategy')!r} is not one sync_from_pack.py "
                f"implements -- dead config")
            # marker_section and add_only_keys must correspond to a REAL path
            # sync_from_pack.py's run_sync() actually branches on by name --
            # today that means literally "CLAUDE.md" or "autopilot.json".
            if spec.get("strategy") in ("marker_section", "add_only_keys"):
                assert name in ("CLAUDE.md", "autopilot.json"), (
                    f"{name}: has a real strategy configured but run_sync() only ever calls "
                    f"merge_marker_section/merge_add_only_keys for CLAUDE.md/autopilot.json by "
                    f"literal name -- this entry would be silently ignored. Either wire it into "
                    f"run_sync() or remove the entry.")
