"""Tests for the A4 pack composer, including its A1-only fallback."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import atlas  # noqa: E402
import atlas_core as core  # noqa: E402
import atlas_episodes as episodes  # noqa: E402
import atlas_pack as pack  # noqa: E402

PLAN = """---
plan_version: 1
last_updated: 2026-08-13T12:00:00Z
overall_status: in_progress
---

### TASK-099
**Title:** Widget pack
**Status:** pending
**Assigned_To:** CX
**Priority:** high
**Spec_References:** specs/x.md
**Owned_Paths:** scripts/widget.py
**Description:** Widget pack history and reverse dependency context.
**Acceptance_Criteria:**
- [ ] works
**Updated_By:** ORCH
**Updated_At:** 2026-08-13T12:00:00Z
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "widget.py").write_text("def widget(value):\n    return value + 1\n", encoding="utf-8")
    (tmp_path / "scripts" / "consumer.py").write_text("from widget import widget\nprint(widget(1))\n", encoding="utf-8")
    (tmp_path / "PLAN.md").write_text(PLAN, encoding="utf-8")
    (tmp_path / "dossiers").mkdir()
    (tmp_path / "dossiers" / "TASK-010.md").write_text("Widget pack previous rework evidence.", encoding="utf-8")
    core.scan(tmp_path, full=True); episodes.index_episodes(tmp_path)
    return tmp_path


def _insert_fresh_card(repo: Path) -> None:
    con = sqlite3.connect(repo / ".devteam" / "atlas.db")
    row = con.execute("SELECT id, content_hash FROM files WHERE path='scripts/widget.py'").fetchone()
    con.execute("INSERT INTO cards(file_id,source_hash,generated_at,model,purpose,invariants,gotchas,entry_points,tokens_estimate) VALUES(?,?,?,?,?,?,?,?,?)", (row[0], row[1], "2026-08-13T12:00:00Z", "fake", "Widget purpose", "[\"stable\"]", "[]", "[\"widget\"]", 20))
    con.commit(); con.close()


def test_pack_degrades_without_cards_and_keeps_required_sections(repo: Path):
    text = pack.compose_pack(repo, "TASK-099", 3000)
    assert "## TERRITORY CORE" in text and "scripts/widget.py [python]:" in text
    assert "scripts/consumer.py (pointer only)" in text and "## EPISODIC HITS" in text
    assert "TASK-010 (dossier): Widget pack previous rework evidence." in text
    assert "A1-only degradation" in text and pack.R1_FOOTER in text and "Truncation: none." in text


def test_pack_uses_fresh_card_and_neighborhood_never_includes_body(repo: Path):
    _insert_fresh_card(repo); text = pack.compose_pack(repo, "TASK-099", 3000)
    assert "card purpose: Widget purpose" in text
    neighborhood = text.split("## ONE-HOP NEIGHBORHOOD", 1)[1].split("## EPISODIC HITS", 1)[0]
    assert "scripts/consumer.py (pointer only)" in neighborhood and "return value + 1" not in neighborhood
    assert "Fresh cards available" in text


def test_budget_drops_lowest_priority_sections_first(repo: Path):
    _insert_fresh_card(repo); text = pack.compose_pack(repo, "TASK-099", 160)
    assert pack._tokens(text) <= 160
    assert "Truncation: episodic hits, one-hop neighborhood." in text
    assert "scripts/consumer.py (pointer only)" not in text
    assert "card purpose: Widget purpose" in text
    assert "## TERRITORY CORE" in text and pack.R1_FOOTER in text


def test_budget_drops_territory_card_bodies_last(repo: Path):
    _insert_fresh_card(repo); text = pack.compose_pack(repo, "TASK-099", 150)
    assert pack._tokens(text) <= 150
    assert "Truncation: episodic hits, one-hop neighborhood, territory card bodies." in text
    assert "scripts/consumer.py (pointer only)" not in text
    assert "card purpose: Widget purpose" not in text
    assert "card: Widget purpose" in text


def test_json_is_machine_readable_and_reports_budget(repo: Path):
    value = json.loads(pack.compose_pack(repo, "TASK-099", 3000, "json"))
    assert value["task"] == "TASK-099" and value["estimated_tokens"] == pack._tokens(value["pack"])


def test_facade_registration_and_error_exit_are_never_two(repo: Path, capsys):
    old = Path.cwd(); os.chdir(repo)
    try:
        assert atlas.main(["pack", "--task", "TASK-099", "--budget", "3000"]) == 0
        assert atlas.main(["pack", "--task", "TASK-404", "--budget", "3000"]) == 1
    finally:
        os.chdir(old)
    captured = capsys.readouterr()
    assert "ATLAS PACK" in captured.out and "task not found" in captured.err


def test_synthetic_exit_criterion_under_budget(repo: Path):
    text = pack.compose_pack(repo, "TASK-099", 3000)
    assert pack._tokens(text) <= 3000 and "scripts/widget.py [python]" in text
    assert "scripts/consumer.py (pointer only)" in text and pack.R1_FOOTER in text
