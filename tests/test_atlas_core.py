"""Focused coverage for the deterministic ATLAS Layer 0 implementation."""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import atlas  # noqa: E402
import atlas_core as core  # noqa: E402


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "a.py").write_text("import b\n\ndef decide(x):\n    return b.work(x)\n", encoding="utf-8")
    (tmp_path / "scripts" / "b.py").write_text("def work(x):\n    return x\n", encoding="utf-8")
    (tmp_path / "web.ts").write_text("import { x } from './scripts/b';\nexport function run() {}\n", encoding="utf-8")
    (tmp_path / "app.dart").write_text("import 'x.dart';\nclass Widget {}\nvoid main() {}\n", encoding="utf-8")
    (tmp_path / "ea.mq5").write_text('#include <Trade/Trade.mqh>\n', encoding="utf-8")
    (tmp_path / "notes.md").write_text("findable words\n", encoding="utf-8")
    return tmp_path


def test_schema_has_all_primary_tables(repo: Path):
    core.scan(repo)
    con = sqlite3.connect(repo / ".devteam" / "atlas.db")
    tables = {row[0] for row in con.execute("select name from sqlite_master where type in ('table','view')")}
    assert {"files", "symbols", "edges", "cards", "episodes", "meta"} <= tables


def test_full_scan_and_incremental_counts(repo: Path):
    assert core.scan(repo, full=True) == (6, 6, 0)
    assert core.scan(repo) == (6, 0, 0)
    (repo / "notes.md").write_text("changed words\n", encoding="utf-8")
    assert core.scan(repo) == (6, 1, 0)


def test_removed_files_are_removed(repo: Path):
    core.scan(repo); (repo / "notes.md").unlink()
    assert core.scan(repo)[2] == 1


def test_hash_is_stable(repo: Path):
    core.scan(repo); first = core.file_hash(repo / "notes.md")
    assert first == core.file_hash(repo / "notes.md")


def test_gitignore_is_honored(repo: Path):
    (repo / ".gitignore").write_text("skip.py\nignored/\n", encoding="utf-8")
    (repo / "skip.py").write_text("def no(): pass", encoding="utf-8")
    (repo / "ignored").mkdir(); (repo / "ignored" / "x.py").write_text("x=1", encoding="utf-8")
    core.scan(repo)
    assert not any("skip.py" in x or "ignored/" in x for x in core.query(repo, "py", 50))


def test_atlas_exclude_is_honored(repo: Path):
    (repo / "autopilot.json").write_text('{"atlas":{"exclude":["notes.md"]}}', encoding="utf-8")
    core.scan(repo)
    assert not core.query(repo, "findable", 10)


def test_python_symbols_and_callers(repo: Path):
    core.scan(repo)
    found = core.where(repo, "decide")
    assert found[0].startswith("scripts/a.py:3 func decide(x)")


def test_python_import_produces_reverse_impact(repo: Path):
    core.scan(repo)
    assert core.impact(repo, "scripts/b.py", 1) == ["scripts/a.py", "web.ts"]


def test_tier_a_regex_symbols(repo: Path):
    core.scan(repo)
    assert any("web.ts:2 func run" in item for item in core.query(repo, "run", 10))
    assert any("app.dart:2 class Widget" in item for item in core.query(repo, "Widget", 10))


def test_mql_include_is_recorded(repo: Path):
    core.scan(repo); con = core.connect(repo)
    assert con.execute("select count(*) from edges where kind='include'").fetchone()[0] >= 1


def test_file_search_and_forward_slashes(repo: Path):
    core.scan(repo)
    result = core.query(repo, "findable", 10)
    assert result == ["notes.md:1"] and "\\" not in result[0]


def test_fresh_and_stale_card_annotation(repo: Path):
    core.scan(repo); con = core.connect(repo)
    row = con.execute("select id,content_hash from files where path='notes.md'").fetchone()
    con.execute("insert into cards values(?,?,?,?,?,?,?,?,?)", (row["id"], row["content_hash"], "now", "test", "", "", "", "", 1)); con.commit()
    assert "FRESH" in core.query(repo, "findable", 10)[0]
    (repo / "notes.md").write_text("findable altered\n", encoding="utf-8"); core.scan(repo)
    assert "STALE (source changed since card generated)" in core.query(repo, "findable", 10)[0]


def test_empty_episodes_degrade_gracefully(repo: Path):
    core.scan(repo)
    assert core.query(repo, "definitely absent", 10) == []


def test_status_includes_required_freshness_data(repo: Path):
    core.scan(repo)
    text = "\n".join(core.status(repo))
    assert "files:" in text and "last scan:" in text and "db size:" in text


@pytest.mark.parametrize("path,expected", [
    (".git/x.py", True), (".devteam/atlas.db", True), ("node_modules/a.js", False),
    ("scripts/a.py", False), ("ignored/x.py", True), ("foo.pyc", False),
    ("dir/skip.py", True), ("skip.py", True), ("normal.md", False), ("notes.md", False),
    ("build/out.js", False), ("src/main.dart", False), ("ea.mq5", False),
])
def test_ignore_matching_cases(path: str, expected: bool):
    assert core.is_ignored(path, [".git/", ".devteam/", "skip.py", "ignored/"]) is expected


def test_facade_reports_missing_extension_without_exit_two(capsys):
    assert atlas.main(["episodes"]) == 1
    assert "not installed" in capsys.readouterr().err


def test_facade_argument_errors_are_one(capsys):
    with pytest.raises(SystemExit) as error:
        atlas.build_parser().parse_args(["scan", "--unknown"])
    assert error.value.code == 1
