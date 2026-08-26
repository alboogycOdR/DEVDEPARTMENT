"""tests/test_sync_from_pack.py — Option-B sync mode (v4.6).

Real temp-dir packs and projects, no mocks: the whole point of sync is
byte-level filesystem behavior, so that's what gets tested.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sync_from_pack as sfp  # noqa: E402
import sys as _sys
sfp_test_module = _sys.modules[__name__]

REPO_ROOT = Path(__file__).resolve().parents[1]


def _is_pack_repo() -> bool:
    """True when this checkout is the DEVDEPARTMENT pack itself, false when it
    is a project that vendored the pack.

    A consuming project has `.devteam/sync_state.json` — written by
    onboarding's baseline step and by every sync since. The pack is the source
    and never syncs into itself, so it has none. `DEVTEAM_PACK_SELF_TESTS=1`
    forces the pack answer for CI that runs from an unusual layout.

    Why this exists (oikonomos, 2026-08-16): the self-check tests below assert
    properties of the PACK'S OWN template — atlas disabled, control.mode
    legacy, manifest markers present in the pack's CLAUDE.md. Shipped into an
    onboarded project they assert those things of the PROJECT, where diverging
    from the template is the entire POINT of onboard.md STEP 4's
    ask-don't-auto-flip questions. On a project that correctly answered
    `atlas.enabled: true` / `control.mode: strict` and whose CLAUDE.md uses the
    appended-H2 marker, all three failed permanently — breaking the
    full-suites-green gate every builder must pass to reach needs_review, for
    a configuration that was exactly right.
    """
    if os.environ.get("DEVTEAM_PACK_SELF_TESTS") == "1":
        return True
    return not (REPO_ROOT / ".devteam" / "sync_state.json").exists()


pack_self_test = pytest.mark.skipif(
    not _is_pack_repo(),
    reason="pack self-check: asserts properties of the pack's own template, which a "
           "project is expected to diverge from once it has onboarded (see _is_pack_repo)")


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
        sfp.run_sync(pack, proj, apply=True, adopt_pack=True)
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
@pack_self_test
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
            # Plural markers[] is what merge_marker_section() actually consumes
            # (spec.get("markers") or [spec["marker"]]); at least one must match
            # the pack's own file or the pack side of every merge falls back to
            # the whole file. Checking only the singular field guarded the wrong
            # thing once the H2 shape was added (oikonomos, 2026-08-16).
            markers = spec.get("markers") or [spec["marker"]]
            assert any(mk in text for mk in markers), (
                f"{name}: none of the configured markers {markers!r} appears in the pack's own "
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


class TestManifestPathsAreLiteral:
    """v4.9 regression: the ATLAS increment registered its tests as a single
    glob entry, "tests/test_atlas_*.py". sync_from_pack.py does LITERAL path
    lookups -- there is no glob/fnmatch anywhere in it -- so that entry
    matched nothing and all four real test files would never have reached a
    single onboarded project. It failed silently in the one direction that
    matters: the sync report showed a MISSING_IN_PACK line for the glob
    itself, which reads like a harmless pack-hygiene note rather than
    "four files are silently not propagating". Same class as the
    plan_guard/preflight omission (cdf441f); this test closes it as a class
    rather than one instance."""

    def _manifest(self):
        return json.loads((REPO_ROOT / sfp.MANIFEST_NAME).read_text(encoding="utf-8"))

    def test_no_wildcard_entries_in_framework_owned(self):
        globs = [f for f in self._manifest()["framework_owned"] if any(c in f for c in "*?[")]
        assert not globs, (
            f"framework_owned entries must be literal paths -- sync_from_pack.py does not "
            f"expand globs, so these match nothing and silently never propagate: {globs}")

    def test_every_framework_owned_path_exists_in_the_pack(self):
        """The MISSING_IN_PACK runtime report as a build-time assertion, so a
        missing file fails the suite instead of printing a line someone has
        to notice in a sync log."""
        missing = [f for f in self._manifest()["framework_owned"]
                   if not (REPO_ROOT / f).exists()]
        assert not missing, f"manifest lists files absent from the pack: {missing}"

    def _base_branch(self) -> str:
        try:
            cfg = json.loads((REPO_ROOT / "autopilot.json").read_text(encoding="utf-8"))
            return (cfg.get("git") or {}).get("base_branch") or "main"
        except (OSError, json.JSONDecodeError):
            return "main"

    def _integration_branch_test_files(self) -> set[str] | None:
        """Test files as TRACKED ON THE INTEGRATION BRANCH TIP, not whatever
        happens to sit in the local working tree. None if git/branch is
        unavailable (caller falls back to the raw-glob behavior).

        Why this matters (found live, 2026-08-26): a builder's own worktree
        legitimately contains a NEW test file its own task just committed,
        on its own branch, not yet merged. That file is correctly UNregistered
        until merge (sync-manifest.json registration happens in the merge
        commit, never speculatively ahead of it -- see 9f32fa0). Scanning the
        raw filesystem made every such worktree fail this test for the entire
        duration of its own task, independent of anything the task did wrong.
        `git ls-tree <base_branch>` reads what is ACTUALLY on the integration
        branch from any linked worktree, so an in-flight sibling task's
        uncommitted-to-master file never appears here -- while a genuinely
        merged-but-unregistered file on master still correctly fails.
        """
        import subprocess
        branch = self._base_branch()
        try:
            r = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", branch, "--", "tests/"],
                cwd=REPO_ROOT, capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0:
            return None
        return {ln.strip() for ln in r.stdout.splitlines()
                if ln.strip().startswith("tests/test_") and ln.strip().endswith(".py")}

    def test_every_shipped_test_file_is_registered(self):
        """A test suite that does not propagate is a test suite that silently
        stops protecting downstream projects.

        Compares against the INTEGRATION BRANCH's tracked tree (see
        _integration_branch_test_files), not the raw local working directory
        -- falls back to the raw glob only if git/the branch can't be
        resolved at all, so the check never goes silent."""
        on_disk = self._integration_branch_test_files()
        if on_disk is None:
            on_disk = {f"tests/{p.name}" for p in (REPO_ROOT / "tests").glob("test_*.py")}
        registered = set(self._manifest()["framework_owned"])
        assert not (on_disk - registered), (
            f"test files present on {self._base_branch()!r} but not registered for sync: "
            f"{sorted(on_disk - registered)}")


@pack_self_test
class TestPackTemplateShipsSafeDefaults:
    """The pack's own autopilot.json is a TEMPLATE that every onboarded
    project inherits (new keys arrive via sync's add_only_keys merge), so a
    value flipped for local use here silently becomes every project's
    default. Found live: the ATLAS acceptance run enabled atlas on
    DEVDEPARTMENT and the flip landed in the template, contradicting both
    the spec's ask-don't-auto-flip rule and onboard.md's own text, which
    tells the operator the template ships disabled.

    These pin the ask-don't-auto-flip settings specifically -- the ones
    onboarding is supposed to ASK about. They must ship in their safe,
    inert state regardless of how the pack repo is being used day to day."""

    def _cfg(self):
        return json.loads((REPO_ROOT / "autopilot.json").read_text(encoding="utf-8"))

    def test_atlas_ships_disabled(self):
        assert self._cfg().get("atlas", {}).get("enabled") is False, (
            "autopilot.json is the template every project inherits -- atlas.enabled must "
            "ship false and be flipped per project during onboarding, after a scan")

    def test_control_mode_ships_legacy(self):
        assert self._cfg().get("control", {}).get("mode") == "legacy", (
            "control.mode must ship legacy: strict changes who may write PLAN.md and is "
            "an onboarding question, not a pack default")

    def test_tower_ships_disabled(self):
        assert self._cfg().get("tower", {}).get("enabled") is False, (
            "tower.enabled must ship false (TOWER spec H5 + ask-don't-auto-flip): a project "
            "must never start pushing snapshots to a Tower URL nobody configured")

    def test_slack_ships_disabled_and_not_in_notify_channels(self):
        # Slack spec §10 claims this class "already covers new ask-don't-auto-flip
        # keys" — it does not; every safe-default here is pinned per-key. These two
        # assertions ARE that coverage for the slack key.
        assert self._cfg().get("slack", {}).get("enabled") is False, (
            "slack.enabled must ship false; enabling requires the §10 live-verification "
            "checklist (app, channels, env vars, --test smoke)")
        assert "slack" not in self._cfg().get("notify_channels", []), (
            "'slack' must not ship in notify_channels — adding it is the per-project "
            "enable step, exactly as telegram was")

    def test_s5b_ships_defined_but_inactive(self):
        """S5B requires a live CLAUDE_CONFIG_DIR verification on the target
        machine; shipping it active would dispatch to an unauthenticated
        second login on every project that syncs."""
        builders = self._cfg().get("builders", {})
        if isinstance(builders, dict):
            assert "S5B" not in builders.get("active", []), (
                "S5B must ship defined-but-inactive until per-machine auth is verified")


# ============================================ two onboarding shapes (2026-08-15) ==
class TestMarkerSectionBothOnboardingShapes:
    """onboard.md STEP 4 produces two legal CLAUDE.md shapes, and the pack
    only ever knew one of them.

    A project with NO CLAUDE.md gets the pack's file verbatim (H1 marker).
    A project that ALREADY has one -- the common case -- gets the section
    appended under an H2, plus its own territory map appended BELOW the pack
    content. Found live on oikonomos: its CLAUDE.md could never be synced
    ('cannot merge safely' forever), and the naive marker fix would then have
    silently deleted the territory map on the first successful run.
    """

    MANIFEST = {"manifest_version": 1, "framework_owned": [], "project_owned": [],
                "merge_special": {"CLAUDE.md": {
                    "strategy": "marker_section",
                    "marker": "# CLAUDE.md — Orchestrator Briefing (ORCH)",
                    "markers": ["# CLAUDE.md — Orchestrator Briefing (ORCH)",
                                "## Multi-Agent Orchestration — DEVDEPARTMENT (ORCH)"],
                    "preserve_after": ["### Builder territory mapping for THIS project"]}}}

    PACK_CLAUDE = ("# CLAUDE.md — Orchestrator Briefing (ORCH)\n\n"
                   "You are ORCH.\n\n## Review standard\nNEW PACK RULE v5.0\n")

    def test_h2_appended_shape_syncs_and_keeps_its_own_heading(self, tmp_path):
        pack = make_pack(tmp_path, {"CLAUDE.md": self.PACK_CLAUDE}, manifest=self.MANIFEST)
        proj = make_project(tmp_path, {"CLAUDE.md":
            "# CLAUDE.md — MYPROJECT\n\nProject non-negotiables.\n\n---\n\n"
            "## Multi-Agent Orchestration — DEVDEPARTMENT (ORCH)\n"
            "> Auto-appended by DEVDEPARTMENT onboarding.\n\n"
            "You are ORCH.\n\n## Review standard\nOLD RULE v1.0\n"})
        report = sfp.run_sync(pack, proj, apply=True, adopt_pack=True)
        merged = (proj / "CLAUDE.md").read_text(encoding="utf-8")
        assert "Project non-negotiables." in merged, "project preamble must survive"
        assert "## Multi-Agent Orchestration — DEVDEPARTMENT (ORCH)" in merged, \
            "the project's own H2 heading must be kept, not replaced by the pack's H1"
        assert "# CLAUDE.md — Orchestrator Briefing (ORCH)" not in merged, \
            "the pack's H1 must not be spliced into the middle of the project's document"
        assert "NEW PACK RULE v5.0" in merged and "OLD RULE v1.0" not in merged
        assert not any("cannot merge safely" in n for n in report.merge_notes)

    def test_project_territory_map_below_the_section_is_preserved(self, tmp_path):
        pack = make_pack(tmp_path, {"CLAUDE.md": self.PACK_CLAUDE}, manifest=self.MANIFEST)
        proj = make_project(tmp_path, {"CLAUDE.md":
            "# CLAUDE.md — MYPROJECT\n\n"
            "## Multi-Agent Orchestration — DEVDEPARTMENT (ORCH)\n\n"
            "You are ORCH.\n\n## Review standard\nOLD RULE v1.0\n\n"
            "### Builder territory mapping for THIS project\n"
            "- Source root: `packages/`\n- Test root: colocated vitest\n"})
        sfp.run_sync(pack, proj, apply=True, adopt_pack=True)
        merged = (proj / "CLAUDE.md").read_text(encoding="utf-8")
        assert "### Builder territory mapping for THIS project" in merged
        assert "- Source root: `packages/`" in merged, \
            "the project's REAL territory map must never be destroyed by a pack refresh"
        assert "NEW PACK RULE v5.0" in merged and "OLD RULE v1.0" not in merged
        assert merged.index("NEW PACK RULE v5.0") < merged.index("Builder territory mapping"), \
            "pack content belongs above the preserved project tail"

    def test_h1_verbatim_shape_still_syncs(self, tmp_path):
        pack = make_pack(tmp_path, {"CLAUDE.md": self.PACK_CLAUDE}, manifest=self.MANIFEST)
        proj = make_project(tmp_path, {"CLAUDE.md":
            "## graphify\nuser preamble\n\n"
            "# CLAUDE.md — Orchestrator Briefing (ORCH)\n\nYou are ORCH.\n\n"
            "## Review standard\nOLD RULE v1.0\n"})
        sfp.run_sync(pack, proj, apply=True, adopt_pack=True)
        merged = (proj / "CLAUDE.md").read_text(encoding="utf-8")
        assert merged.startswith("## graphify\nuser preamble")
        assert "# CLAUDE.md — Orchestrator Briefing (ORCH)" in merged
        assert "NEW PACK RULE v5.0" in merged and "OLD RULE v1.0" not in merged

    def test_unknown_marker_still_refuses(self, tmp_path):
        pack = make_pack(tmp_path, {"CLAUDE.md": self.PACK_CLAUDE}, manifest=self.MANIFEST)
        original = "# Some project file with neither marker\n"
        proj = make_project(tmp_path, {"CLAUDE.md": original})
        report = sfp.run_sync(pack, proj, apply=True)
        assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == original
        assert any("cannot merge safely" in n for n in report.merge_notes)

    @pack_self_test
    def test_real_manifest_has_at_least_one_marker_in_the_real_pack_file(self):
        """markers[] legitimately contains project-side shapes the pack itself
        does not use -- but at least one must match the pack's own file, or the
        pack side of every merge falls back to the whole file."""
        m = json.loads((REPO_ROOT / sfp.MANIFEST_NAME).read_text(encoding="utf-8"))
        for name, spec in m.get("merge_special", {}).items():
            if spec.get("strategy") != "marker_section":
                continue
            text = (REPO_ROOT / name).read_text(encoding="utf-8")
            markers = spec.get("markers") or [spec["marker"]]
            assert any(mk in text for mk in markers), (
                f"{name}: none of {markers!r} appears in the pack's own file")


# ==================================== local-edit guard for marker sections ==
class TestMarkerSectionLocalEditGuard:
    """marker_section used to overwrite unconditionally — safe only while no
    project edits inside the section. oikonomos does: a hard-won "always run
    the FULL recursive suite" review rule with its incident report. Conflict
    is judged against the section BASELINE (what the pack had at the last
    merge), never against the current pack — the latter would call every
    legitimately out-of-date project a conflict and defeat the strategy."""

    MANIFEST = TestMarkerSectionBothOnboardingShapes.MANIFEST
    PACK_V1 = "# CLAUDE.md \u2014 Orchestrator Briefing (ORCH)\n\n## Review standard\nRULE v1\n"
    PACK_V2 = "# CLAUDE.md \u2014 Orchestrator Briefing (ORCH)\n\n## Review standard\nRULE v2 IMPROVED\n"
    PROJ = ("# CLAUDE.md \u2014 MYPROJECT\n\nmine\n\n"
            "## Multi-Agent Orchestration \u2014 DEVDEPARTMENT (ORCH)\n\n"
            "### Review standard\nRULE v1\n")

    def test_no_baseline_and_differs_refuses_until_adopted(self, tmp_path):
        pack = make_pack(tmp_path, {"CLAUDE.md": self.PACK_V2}, manifest=self.MANIFEST)
        proj = make_project(tmp_path, {"CLAUDE.md": self.PROJ})
        report = sfp.run_sync(pack, proj, apply=True)
        assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == self.PROJ
        assert any("no section baseline" in n for n in report.merge_notes)

    def test_matching_section_records_baseline_then_updates_cleanly(self, tmp_path):
        pack = make_pack(tmp_path, {"CLAUDE.md": self.PACK_V1}, manifest=self.MANIFEST)
        proj = make_project(tmp_path, {"CLAUDE.md": self.PROJ})
        sfp.run_sync(pack, proj, apply=True)
        assert "CLAUDE.md#section" in sfp.load_state(proj)["files"], \
            "a clean pass must record the section baseline"
        (pack / "CLAUDE.md").write_text(self.PACK_V2, encoding="utf-8", newline="\n")
        report = sfp.run_sync(pack, proj, apply=True)
        merged = (proj / "CLAUDE.md").read_text(encoding="utf-8")
        assert "RULE v2 IMPROVED" in merged
        assert "### Review standard" in merged, "H2 shape keeps subsections demoted to H3"
        assert not any("LOCAL EDITS" in n for n in report.merge_notes)

    def test_local_edit_after_baseline_is_a_conflict_not_a_clobber(self, tmp_path):
        pack = make_pack(tmp_path, {"CLAUDE.md": self.PACK_V1}, manifest=self.MANIFEST)
        proj = make_project(tmp_path, {"CLAUDE.md": self.PROJ})
        sfp.run_sync(pack, proj, apply=True)
        customized = self.PROJ.replace("RULE v1", "RULE v1 + ALWAYS run the FULL suite")
        (proj / "CLAUDE.md").write_text(customized, encoding="utf-8", newline="\n")
        (pack / "CLAUDE.md").write_text(self.PACK_V2, encoding="utf-8", newline="\n")
        report = sfp.run_sync(pack, proj, apply=True)
        assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == customized, \
            "a project's local rule must never be silently discarded by a pack refresh"
        assert any("LOCAL EDITS" in n for n in report.merge_notes)

    def test_adopt_pack_overrides_the_guard(self, tmp_path):
        pack = make_pack(tmp_path, {"CLAUDE.md": self.PACK_V1}, manifest=self.MANIFEST)
        proj = make_project(tmp_path, {"CLAUDE.md": self.PROJ})
        sfp.run_sync(pack, proj, apply=True)
        (proj / "CLAUDE.md").write_text(self.PROJ.replace("RULE v1", "MY EDIT"),
                                        encoding="utf-8", newline="\n")
        (pack / "CLAUDE.md").write_text(self.PACK_V2, encoding="utf-8", newline="\n")
        report = sfp.run_sync(pack, proj, apply=True, adopt_pack=True)
        merged = (proj / "CLAUDE.md").read_text(encoding="utf-8")
        assert "RULE v2 IMPROVED" in merged and "MY EDIT" not in merged
        assert any("DISCARDED" in n for n in report.merge_notes)

    def test_shorter_sentinel_does_not_match_inside_a_longer_heading(self, tmp_path):
        """`## Builder territory mapping` is a SUBSTRING of
        `### Builder territory mapping`, so an unanchored find() matched at
        offset+1 and left a stray '#' in the section body. That single
        character made the comparison unequal forever: oikonomos's CLAUDE.md
        could never reach a clean sync, and a merge would have written the
        stray '#' back into the file."""
        manifest = {"manifest_version": 1, "framework_owned": [], "project_owned": [],
                    "merge_special": {"CLAUDE.md": {
                        "strategy": "marker_section",
                        "marker": "# CLAUDE.md \u2014 Orchestrator Briefing (ORCH)",
                        "markers": ["# CLAUDE.md \u2014 Orchestrator Briefing (ORCH)",
                                    "## Multi-Agent Orchestration \u2014 DEVDEPARTMENT (ORCH)"],
                        # SHORTER form listed first, exactly as the real manifest has it
                        "preserve_after": ["## Builder territory mapping for THIS project",
                                           "### Builder territory mapping for THIS project"]}}}
        pack = make_pack(tmp_path, {"CLAUDE.md":
            "# CLAUDE.md \u2014 Orchestrator Briefing (ORCH)\n\n## Git conventions\nSAME\n"},
            manifest=manifest)
        proj_text = ("# CLAUDE.md \u2014 MYPROJECT\n\n"
                     "## Multi-Agent Orchestration \u2014 DEVDEPARTMENT (ORCH)\n\n"
                     "### Git conventions\nSAME\n\n"
                     "### Builder territory mapping for THIS project\n- Source root: `packages/`\n")
        proj = make_project(tmp_path, {"CLAUDE.md": proj_text})
        report = sfp.run_sync(proj_file_pack := pack, proj, apply=True)
        assert any("already current" in n for n in report.merge_notes), (
            "section is identical modulo heading level; the H3 sentinel must be matched "
            f"whole-line, not as a substring. notes={report.merge_notes}")
        assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == proj_text


# ============ integration-branch-tree comparison (2026-08-26 field defect) ==
class TestShippedTestFileRegistrationIsBranchAware:
    """A builder's own worktree has an uncommitted-to-master new test file
    for the ENTIRE duration of its task -- that must never fail this guard,
    while a genuinely merged-but-unregistered file on the integration branch
    still must. Found live: TASK-013 (GB) hit exactly this after ORCH's own
    manifest fix, re-blocking on the identical symptom the fix was meant to
    resolve, because the check scanned the raw worktree instead of what was
    actually on master.

    Tests the real TestManifestPathsAreLiteral methods against a temp repo by
    monkeypatching this module's REPO_ROOT -- the same constant the class
    under test reads -- rather than reimplementing the git plumbing here.
    """

    def _git(self, repo, *args, check=True):
        return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                              text=True, check=check)

    def _repo(self, tmp_path, base_branch="master"):
        r = tmp_path / "repo"
        (r / "tests").mkdir(parents=True)
        (r / "tests" / "test_existing.py").write_text("def test_x(): pass\n", encoding="utf-8")
        (r / sfp.MANIFEST_NAME).write_text(json.dumps({
            "manifest_version": 1, "project_owned": [], "merge_special": {},
            "framework_owned": ["tests/test_existing.py"],
        }), encoding="utf-8")
        (r / "autopilot.json").write_text(json.dumps({"git": {"base_branch": base_branch}}),
                                          encoding="utf-8")
        self._git(r, "init", "-q", "-b", base_branch)
        self._git(r, "config", "user.email", "t@example.com")
        self._git(r, "config", "user.name", "T")
        self._git(r, "add", "-A")
        self._git(r, "commit", "-q", "-m", "seed")
        return r

    def test_in_flight_branch_with_uncommitted_to_master_test_file_does_not_fail(self, tmp_path, monkeypatch):
        repo = self._repo(tmp_path)
        self._git(repo, "checkout", "-q", "-b", "task/TASK-999-x")
        (repo / "tests" / "test_new_feature.py").write_text("def test_y(): pass\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-q", "-m", "feat: new module [TASK-999]")
        monkeypatch.setattr(sfp_test_module, "REPO_ROOT", repo)
        inst = TestManifestPathsAreLiteral()
        inst.test_every_shipped_test_file_is_registered()  # must NOT raise

    def test_file_actually_on_master_but_unregistered_still_fails(self, tmp_path, monkeypatch):
        repo = self._repo(tmp_path)
        (repo / "tests" / "test_orphan.py").write_text("def test_z(): pass\n", encoding="utf-8")
        self._git(repo, "add", "-A")
        self._git(repo, "commit", "-q", "-m", "oops: forgot to register")
        monkeypatch.setattr(sfp_test_module, "REPO_ROOT", repo)
        inst = TestManifestPathsAreLiteral()
        with pytest.raises(AssertionError, match="test_orphan.py"):
            inst.test_every_shipped_test_file_is_registered()

    def test_falls_back_to_raw_glob_when_git_unavailable(self, tmp_path, monkeypatch):
        """No .git at all (e.g. a stripped export) -- must not go silent;
        falls back to the pre-fix raw-directory-scan behavior."""
        r = tmp_path / "no_git_repo"
        (r / "tests").mkdir(parents=True)
        (r / "tests" / "test_a.py").write_text("def test_a(): pass\n", encoding="utf-8")
        (r / sfp.MANIFEST_NAME).write_text(json.dumps({
            "manifest_version": 1, "project_owned": [], "merge_special": {},
            "framework_owned": [],
        }), encoding="utf-8")
        monkeypatch.setattr(sfp_test_module, "REPO_ROOT", r)
        inst = TestManifestPathsAreLiteral()
        assert inst._integration_branch_test_files() is None
        with pytest.raises(AssertionError, match="test_a.py"):
            inst.test_every_shipped_test_file_is_registered()

