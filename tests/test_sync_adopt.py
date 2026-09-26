"""Regression tests for conservative pre-manifest project adoption."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import sync_from_pack as sfp  # noqa: E402


def _commit(repo: Path, message: str) -> None:
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", message], check=True)


def _history_pack(tmp_path: Path) -> Path:
    pack = tmp_path / "pack"
    (pack / "scripts").mkdir(parents=True)
    (pack / "README.md").write_text("# Pack\n\n- v1.2.0 legacy\n", encoding="utf-8")
    (pack / "scripts" / "legacy.py").write_text("OLD = True\n", encoding="utf-8")
    (pack / "scripts" / "changed.py").write_text("OLD = True\n", encoding="utf-8")
    (pack / "hooks").mkdir()
    (pack / "hooks" / "new.py").write_text("NEW LAYER\n", encoding="utf-8")
    (pack / sfp.MANIFEST_NAME).write_text(json.dumps({
        "manifest_version": 1, "role": "pack",
        "framework_owned": ["scripts/legacy.py", "scripts/changed.py", "hooks/new.py"],
        "project_owned": [], "merge_special": {},
    }), encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "master", str(pack)], check=True)
    subprocess.run(["git", "-C", str(pack), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(pack), "config", "user.name", "Test"], check=True)
    _commit(pack, "v1.2 fixture")
    (pack / "README.md").write_text("# Pack\n\n- v2.0.0 current\n", encoding="utf-8")
    (pack / "scripts" / "legacy.py").write_text("NEW = True\n", encoding="utf-8")
    _commit(pack, "v2 fixture")
    return pack


def _legacy_project(tmp_path: Path, pack: Path) -> Path:
    project = tmp_path / "project"
    (project / "scripts").mkdir(parents=True)
    # Build the fixture from the actual v1.2-era Git blob.  This keeps the
    # test byte-exact even on Windows where text-mode writes use CRLF.
    legacy_blob = subprocess.run(
        ["git", "-C", str(pack), "show", "HEAD~1:scripts/legacy.py"],
        capture_output=True, check=True,
    ).stdout
    (project / "scripts" / "legacy.py").write_bytes(legacy_blob)
    (project / "scripts" / "changed.py").write_text("MY LOCAL EDIT\n", encoding="utf-8")
    (project / "autopilot.json").write_text("{}\n", encoding="utf-8")
    return project


def test_adopt_fingerprints_history_and_never_overwrites_or_installs_absent_layers(tmp_path):
    pack = _history_pack(tmp_path)
    project = _legacy_project(tmp_path, pack)
    original = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}

    report = sfp.adopt_project(pack, project, apply=True)

    manifest = json.loads((project / sfp.MANIFEST_NAME).read_text(encoding="utf-8"))
    assert manifest["role"] == "project"
    assert manifest["adopted_files"]["scripts/legacy.py"].startswith("matches v1.2.0+")
    assert manifest["adopted_files"]["scripts/changed.py"] == "diverged"
    assert manifest["adopted_files"]["hooks/new.py"] == "absent"
    assert (project / "scripts" / "legacy.py").read_bytes() == original[Path("scripts/legacy.py")]
    assert (project / "scripts" / "changed.py").read_bytes() == original[Path("scripts/changed.py")]
    assert not (project / "hooks" / "new.py").exists()
    assert len(report.by(sfp.CONFLICT)) == 2
    assert report.by(sfp.ADD)[0].detail.startswith("absent layer — proposed add")


def test_adopt_dry_run_reports_without_writing_manifest_or_project_files(tmp_path, capsys):
    pack = _history_pack(tmp_path)
    project = _legacy_project(tmp_path, pack)
    before = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}

    assert sfp.main(["--pack", str(pack), "--project", str(project), "--adopt", "--dry-run"]) == 2

    output = capsys.readouterr().out
    assert "Adoption fingerprint:" in output
    assert "proposed add" in output
    assert not (project / sfp.MANIFEST_NAME).exists()
    after = {p.relative_to(project): p.read_bytes() for p in project.rglob("*") if p.is_file()}
    assert after == before
