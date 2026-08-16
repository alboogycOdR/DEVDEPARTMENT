"""Coverage for ATLAS A2 episodic indexer (scripts/atlas_episodes.py)."""
from __future__ import annotations

import ast
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import atlas  # noqa: E402
import atlas_core as core  # noqa: E402
import atlas_episodes as episodes  # noqa: E402
from team_stats import ROW_RE  # noqa: E402
from validate_plan import parse_tasks  # noqa: E402


PLAN_TASK = """---
plan_version: 1
last_updated: 2026-08-13T12:00:00Z
overall_status: in_progress
---

### TASK-099
**Title:** Widget indexer
**Status:** pending
**Assigned_To:** GB
**Priority:** high
**Spec_References:** specs/x.md
**Owned_Paths:** scripts/x.py
**Description:** unique-plan-phrase about widgets
**Acceptance_Criteria:**
- [ ] done
**Updated_By:** ORCH
**Updated_At:** 2026-08-13T12:00:00Z
"""

DOSSIER_SHAPED = """### TASK-088
**Assigned_To:** CX
**Updated_At:** 2026-07-01T08:00:00Z
**Title:** Shaped dossier
**Description:** local-shaped-phrase
"""

REVIEW = """# REVIEW.md

| Task | Unit | Verdict | Findings | First-pass | Timestamp |
|---|---|---|---|---|---|
| TASK-099 | GB | approved | unique-review-finding about widgets | yes | 2026-08-13T16:07:16Z |
| not a verdict row | - | - | - | - | - |
"""

INSTINCTS = """# INSTINCTS.md

### INST-007
**Rule:** Firestore emulator-run evidence before needs_review.
**Territory:** functions/**
**Confidence:** 0.9
**Source:** TASK-005 rework
**Status:** active
"""

RETRO = "# RETRO-2026-W32\\n\\nCycle time was high on unique-retro-phrase.\\n"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "dossiers").mkdir()
    (tmp_path / "dossiers" / "TASK-099.md").write_text(
        "# Dossier — TASK-099\n\nWork log: indexed the widget path.\n",
        encoding="utf-8",
    )
    (tmp_path / "dossiers" / "README.md").write_text("not a task dossier\n", encoding="utf-8")
    (tmp_path / "PLAN.md").write_text(PLAN_TASK, encoding="utf-8")
    (tmp_path / "REVIEW.md").write_text(REVIEW, encoding="utf-8")
    (tmp_path / "INSTINCTS.md").write_text(INSTINCTS, encoding="utf-8")
    (tmp_path / "RETRO-2026-W32.md").write_text(
        "# RETRO-2026-W32\n\nCycle time was high on unique-retro-phrase.\n",
        encoding="utf-8",
    )
    return tmp_path


def _rows(repo: Path, **where) -> list[sqlite3.Row]:
    con = core.connect(repo)
    if not where:
        rows = con.execute("SELECT * FROM episodes ORDER BY kind, ref").fetchall()
    else:
        clause = " AND ".join(f"{k}=?" for k in where)
        rows = con.execute(
            f"SELECT * FROM episodes WHERE {clause} ORDER BY kind, ref",
            tuple(where.values()),
        ).fetchall()
    con.close()
    return rows


def test_facade_registers_episodes_without_editing_core(repo: Path, capsys):
    assert atlas.main(["episodes", "--repo", str(repo)]) == 0
    out = capsys.readouterr().out
    assert "episodes indexed:" in out
    assert "\\" not in out
    assert atlas.main(["episodes", "--reindex", "--repo", str(repo)]) == 0


def test_parses_all_four_source_families(repo: Path):
    count, scanned, changed = episodes.index_episodes(repo)
    assert count == 4
    assert scanned == 4
    assert changed == 4
    kinds = {row["kind"] for row in _rows(repo)}
    assert kinds == {"dossier", "review", "instinct", "retro"}
    assert _rows(repo, kind="dossier")[0]["ref"] == "TASK-099"
    assert _rows(repo, kind="review")[0]["ref"] == "TASK-099"
    assert _rows(repo, kind="instinct")[0]["ref"] == "INST-007"
    assert _rows(repo, kind="retro")[0]["ref"] == "RETRO-2026-W32.md"


def test_readme_dossier_is_skipped(repo: Path):
    episodes.index_episodes(repo)
    bodies = " ".join(row["body_fts"] for row in _rows(repo, kind="dossier"))
    assert "not a task dossier" not in bodies


def test_parse_tasks_enriches_dossier_from_plan(repo: Path):
    episodes.index_episodes(repo)
    row = _rows(repo, kind="dossier")[0]
    assert row["unit"] == "GB"
    assert row["ts"] == "2026-08-13T12:00:00Z"
    assert "unique-plan-phrase" in row["body_fts"]
    assert episodes.parse_tasks is parse_tasks


def test_task_shaped_dossier_uses_parse_tasks_without_plan(tmp_path: Path):
    (tmp_path / "dossiers").mkdir()
    (tmp_path / "dossiers" / "TASK-088.md").write_text(DOSSIER_SHAPED, encoding="utf-8")
    episodes.index_episodes(tmp_path)
    row = _rows(tmp_path, kind="dossier")[0]
    assert row["ref"] == "TASK-088"
    assert row["unit"] == "CX"
    assert row["ts"] == "2026-07-01T08:00:00Z"
    assert "local-shaped-phrase" in row["body_fts"]


def test_review_rows_use_team_stats_grammar(repo: Path):
    assert episodes.ROW_RE is ROW_RE
    episodes.index_episodes(repo)
    row = _rows(repo, kind="review")[0]
    assert row["unit"] == "GB"
    assert row["ts"] == "2026-08-13T16:07:16Z"
    assert "unique-review-finding" in row["body_fts"]
    assert len(_rows(repo, kind="review")) == 1


def test_instincts_use_existing_parser(repo: Path):
    episodes.index_episodes(repo)
    row = _rows(repo, kind="instinct")[0]
    assert "emulator-run evidence" in row["body_fts"]
    assert row["ref"] == "INST-007"


def test_query_returns_episode_hits_after_index(repo: Path):
    episodes.index_episodes(repo)
    hits = core.query(repo, "unique-review-finding", 20)
    assert any(item.endswith(" episode") for item in hits)
    assert any(item.startswith("TASK-099:") for item in hits)
    assert all("\\" not in item for item in hits)
    assert any("episode" in item for item in core.query(repo, "unique-retro-phrase", 20))
    assert any("episode" in item for item in core.query(repo, "emulator-run", 20))


def test_facade_query_sees_indexed_episodes(repo: Path, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    assert atlas.main(["episodes"]) == 0
    assert atlas.main(["query", "unique-review-finding"]) == 0
    out = capsys.readouterr().out
    assert "episode" in out
    assert "\\" not in out
    assert atlas.main(["episodes"]) == 0


def test_plan_md_change_reindexes_dossiers(repo: Path):
    episodes.index_episodes(repo)
    assert episodes.index_episodes(repo)[2] == 0
    (repo / "PLAN.md").write_text(
        PLAN_TASK.replace("unique-plan-phrase about widgets", "revised-plan-phrase"),
        encoding="utf-8",
    )
    count, scanned, changed = episodes.index_episodes(repo)
    assert count == 4
    assert scanned == 4
    assert changed == 1
    assert "revised-plan-phrase" in _rows(repo, kind="dossier")[0]["body_fts"]


def test_incremental_skips_unchanged_sources(repo: Path):
    assert episodes.index_episodes(repo)[2] == 4
    assert episodes.index_episodes(repo) == (4, 4, 0)
    (repo / "dossiers" / "TASK-099.md").write_text(
        "# Dossier — TASK-099\n\nchanged unique-dossier-phrase\n",
        encoding="utf-8",
    )
    count, scanned, changed = episodes.index_episodes(repo)
    assert (count, scanned, changed) == (4, 4, 1)
    assert "unique-dossier-phrase" in _rows(repo, kind="dossier")[0]["body_fts"]
    assert _rows(repo, kind="review")[0]["body_fts"].find("unique-review-finding") >= 0


def test_incremental_records_hash_for_zero_episode_source(repo: Path):
    (repo / "INSTINCTS.md").write_text("# INSTINCTS.md\n", encoding="utf-8")

    assert episodes.index_episodes(repo) == (3, 4, 4)
    assert _rows(repo, kind="instinct") == []

    con = core.connect(repo)
    stored = con.execute(
        "SELECT value FROM meta WHERE key=?",
        ("episodes_source_hash:instinct:INSTINCTS.md",),
    ).fetchone()
    con.close()
    assert stored is not None
    assert episodes.index_episodes(repo) == (3, 4, 0)


def test_reindex_rebuilds_table(repo: Path):
    episodes.index_episodes(repo)
    con = core.connect(repo)
    con.execute(
        "INSERT INTO episodes(kind, ref, ts, unit, indexed_hash, body_fts) "
        "VALUES('review','TASK-GHOST',NULL,NULL,'stale','ghost-row')"
    )
    con.commit()
    con.close()
    count, _, changed = episodes.index_episodes(repo, reindex=True)
    assert count == 4
    assert changed == 4
    assert not any(row["ref"] == "TASK-GHOST" for row in _rows(repo))


def test_deleted_source_removes_episodes(repo: Path):
    episodes.index_episodes(repo)
    (repo / "RETRO-2026-W32.md").unlink()
    count, _, changed = episodes.index_episodes(repo)
    assert count == 3
    assert changed == 1
    assert _rows(repo, kind="retro") == []


def test_missing_sources_degrade_to_empty_success(tmp_path: Path):
    count, scanned, changed = episodes.index_episodes(tmp_path)
    assert (count, scanned, changed) == (0, 0, 0)
    assert atlas.main(["episodes", "--repo", str(tmp_path)]) == 0


def test_bad_repo_is_exit_one_never_two(capsys):
    assert atlas.main(["episodes", "--repo", str(Path("C:/no/such/atlas-repo"))]) == 1
    err = capsys.readouterr().err
    assert "atlas:" in err
    with pytest.raises(SystemExit) as error:
        atlas.build_parser().parse_args(["episodes", "--unknown"])
    assert error.value.code == 1


def test_episodes_fts_is_populated(repo: Path):
    episodes.index_episodes(repo)
    con = core.connect(repo)
    fts = con.execute("SELECT COUNT(*) FROM episodes_fts").fetchone()[0]
    body = con.execute("SELECT COUNT(*) FROM episodes").fetchone()[0]
    con.close()
    assert fts == body == 4


def test_zero_model_calls_in_module():
    source = Path(episodes.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module.split(".")[0])
    banned = {"anthropic", "openai", "httpx", "requests", "subprocess"}
    assert banned.isdisjoint(imported)
    assert "claude-sonnet" not in source.lower()


def test_forward_slash_refs_only(repo: Path):
    episodes.index_episodes(repo)
    for row in _rows(repo):
        assert "\\" not in row["ref"]
