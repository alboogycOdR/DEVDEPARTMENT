"""E-J.1 roster rendering and unit retirement tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from retire_unit import retire_unit  # noqa: E402
from sync_from_pack import render_rosters  # noqa: E402
from validate_plan import lint_briefings  # noqa: E402


MARKED = "<!-- devteam:roster -->\nold\n<!-- /devteam:roster -->\n"


def project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "briefings").mkdir(parents=True)
    config = {"builders": {"active": ["CX", "GB"], "defined": {
        "CX": {"cli": "codex", "worktree_suffix": "cx", "branch_suffix": "cx", "briefing": "briefings/cx.md"},
        "GB": {"cli": "grok", "worktree_suffix": "gb", "branch_suffix": "gb", "briefing": "briefings/gb.md"},
    }}}
    (root / "autopilot.json").write_text(json.dumps(config), encoding="utf-8")
    for rel in ("briefings/cx.md", "briefings/gb.md", "CLAUDE.md", "AGENTS.md"):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(MARKED, encoding="utf-8")
    return root


def test_retiring_unit_renders_every_marked_section(tmp_path):
    root = project(tmp_path)
    changed = retire_unit("GB", root)
    assert len(changed) == 4
    assert json.loads((root / "autopilot.json").read_text())["builders"]["active"] == ["CX"]
    for rel in ("briefings/cx.md", "briefings/gb.md", "CLAUDE.md", "AGENTS.md"):
        text = (root / rel).read_text(encoding="utf-8")
        assert "`CX`" in text and "`GB`" not in text


def test_missing_registry_briefing_fails_config_lint(tmp_path):
    root = project(tmp_path)
    (root / "briefings/cx.md").unlink()
    report = lint_briefings(root)
    assert any("CX briefing does not exist" in error for error in report.errors)


def test_render_handles_markers_in_non_briefing_documents(tmp_path):
    root = project(tmp_path)
    render_rosters(root)
    assert "`GB`" in (root / "CLAUDE.md").read_text(encoding="utf-8")


def test_briefing_lint_warns_for_parked_open_task_and_bad_path(tmp_path):
    root = project(tmp_path)
    (root / "PLAN.md").write_text("""---
plan_version: 1
last_updated: 2026-01-01T00:00:00Z
overall_status: in_progress
---
### TASK-123
**Status:** in_progress
""", encoding="utf-8")
    (root / "CLAUDE.md").write_text("TASK-123 is parked; do not run it.\n", encoding="utf-8")
    (root / "briefings/cx.md").write_text("Read `scripts/missing_tool.py`\n", encoding="utf-8")
    report = lint_briefings(root)
    assert any("open TASK-123" in warning for warning in report.warnings)
    assert any("scripts/missing_tool.py" in warning for warning in report.warnings)


def test_retire_handles_backslash_registry_values_without_regex_expansion(tmp_path):
    root = project(tmp_path)
    config_path = root / "autopilot.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["builders"]["defined"]["CX"]["briefing"] = r"briefings\CODEX_BRIEFING.md"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    changed = retire_unit("GB", root)

    assert len(changed) == 3  # The Windows-style CX briefing path is not a local file.
    assert json.loads(config_path.read_text(encoding="utf-8"))["builders"]["active"] == ["CX"]
    assert r"briefings\CODEX_BRIEFING.md" in (root / "CLAUDE.md").read_text(encoding="utf-8")


def test_retire_does_not_mutate_config_or_documents_when_roster_is_invalid(tmp_path):
    root = project(tmp_path)
    config_path = root / "autopilot.json"
    original_config = config_path.read_bytes()
    original_claude = (root / "CLAUDE.md").read_bytes()
    (root / "AGENTS.md").write_text(MARKED + MARKED, encoding="utf-8")

    with pytest.raises(ValueError, match="expected exactly one roster section"):
        retire_unit("GB", root)

    assert config_path.read_bytes() == original_config
    assert (root / "CLAUDE.md").read_bytes() == original_claude
