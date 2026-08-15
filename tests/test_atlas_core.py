"""Focused coverage for the deterministic ATLAS Layer 0 implementation."""
from __future__ import annotations

import os
import sqlite3
import subprocess
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


def test_function_scoped_python_import_produces_reverse_impact(repo: Path):
    (repo / "scripts" / "consumer.py").write_text(
        "def load():\n    import b as dependency\n    return dependency.work(1)\n",
        encoding="utf-8",
    )
    core.scan(repo)
    assert "scripts/consumer.py" in core.impact(repo, "scripts/b.py", 1)


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


def test_facade_reports_missing_extension_without_exit_two(capsys, monkeypatch):
    # Force the staged-rollout condition instead of probing a fixed subcommand:
    # A2-A4 each install a real extension module, so any hardcoded name here
    # rots exactly one wave later (TASK-003 hit this live when it installed
    # atlas_episodes and this test still expected 'episodes' to be missing).
    class _NoExtensions:
        @staticmethod
        def import_module(name):
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)

    monkeypatch.setattr(atlas, "importlib", _NoExtensions)
    assert atlas.main(["episodes"]) == 1
    assert "not installed" in capsys.readouterr().err


def test_facade_argument_errors_are_one(capsys):
    with pytest.raises(SystemExit) as error:
        atlas.build_parser().parse_args(["scan", "--unknown"])
    assert error.value.code == 1


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_CEILING_DIRECTORIES"] = str(repo.resolve().parent)
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def _init_git(repo: Path) -> str:
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def test_scan_records_last_scan_head(repo: Path):
    head = _init_git(repo)
    core.scan(repo)
    con = core.connect(repo)
    stored = con.execute("SELECT value FROM meta WHERE key='last_scan_head'").fetchone()[0]
    con.close()
    assert stored == head


def test_scan_records_empty_head_when_git_unavailable(repo: Path, monkeypatch: pytest.MonkeyPatch):
    empty = repo / ".empty_path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    core.scan(repo)
    con = core.connect(repo)
    stored = con.execute("SELECT value FROM meta WHERE key='last_scan_head'").fetchone()[0]
    con.close()
    assert stored == ""


def test_status_delta_and_commits_after_commit_then_rescan(repo: Path):
    _init_git(repo)
    core.scan(repo)
    first = "\n".join(core.status(repo))
    assert " — in sync" in first
    assert "commits since last scan: 0" in first
    (repo / "notes.md").write_text("findable words\nand a commit\n", encoding="utf-8")
    _git(repo, "add", "notes.md")
    _git(repo, "commit", "-q", "-m", "change notes")
    mid = "\n".join(core.status(repo))
    assert "commits since last scan: 1" in mid
    assert " — in sync" in mid
    (repo / "extra.md").write_text("brand new tracked file\n", encoding="utf-8")
    _git(repo, "add", "extra.md")
    _git(repo, "commit", "-q", "-m", "add extra")
    stale = "\n".join(core.status(repo))
    assert "commits since last scan: 2" in stale
    assert "not indexed" in stale
    assert " — in sync" not in stale
    core.scan(repo)
    fresh = "\n".join(core.status(repo))
    assert " — in sync" in fresh
    assert "commits since last scan: 0" in fresh
    assert "not indexed" not in fresh


def test_status_dotdir_tracked_files_are_not_stripped(repo: Path):
    hidden = repo / ".claude" / "commands"
    hidden.mkdir(parents=True)
    (hidden / "devteam-status.md").write_text("# cmd\n", encoding="utf-8")
    _init_git(repo)
    core.scan(repo)
    text = "\n".join(core.status(repo))
    con = core.connect(repo)
    indexed = {row[0] for row in con.execute("SELECT path FROM files")}
    con.close()
    assert ".claude/commands/devteam-status.md" in indexed
    assert " — in sync" in text
    assert "not indexed" not in text


def test_status_ignore_rules_exclude_tracked_files_from_delta(repo: Path):
    _init_git(repo)
    (repo / ".gitignore").write_text("skip.py\n", encoding="utf-8")
    (repo / "skip.py").write_text("def hidden():\n    return 1\n", encoding="utf-8")
    _git(repo, "add", "-f", "skip.py", ".gitignore")
    _git(repo, "commit", "-q", "-m", "track ignored skip.py")
    core.scan(repo)
    text = "\n".join(core.status(repo))
    con = core.connect(repo)
    indexed = {row[0] for row in con.execute("SELECT path FROM files")}
    n_files = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    con.close()
    assert "skip.py" not in indexed
    assert f"tracked files: {n_files} (git) vs {n_files} indexed — in sync" in text
    assert "skip.py" not in text


def test_status_degrades_when_git_absent_from_path(repo: Path, monkeypatch: pytest.MonkeyPatch):
    _init_git(repo)
    core.scan(repo)
    empty = repo / ".empty_path"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    lines = core.status(repo)
    text = "\n".join(lines)
    assert "tracked files: n/a" in text
    assert "commits since last scan: n/a" in text
    assert "last scan:" in text
    assert "\\" not in text


def test_status_commits_na_without_recorded_head(repo: Path):
    core.scan(repo)
    text = "\n".join(core.status(repo))
    assert "commits since last scan: n/a" in text
    assert "tracked files: n/a" in text


def test_status_cards_opt_in_hint_when_empty(repo: Path):
    core.scan(repo)
    lines = core.status(repo)
    assert core.CARDS_OPT_IN_HINT in lines
    assert lines[1] == core.CARDS_OPT_IN_HINT
    assert any(line.startswith("stale cards: 0") for line in lines)


def test_status_cards_line_unchanged_when_cards_exist(repo: Path):
    core.scan(repo)
    con = core.connect(repo)
    row = con.execute("select id,content_hash from files where path='notes.md'").fetchone()
    con.execute(
        "insert into cards values(?,?,?,?,?,?,?,?,?)",
        (row["id"], row["content_hash"], "now", "test", "", "", "", "", 1),
    )
    con.commit()
    con.close()
    lines = core.status(repo)
    text = "\n".join(lines)
    assert "cards: 1" in text
    assert core.CARDS_OPT_IN_HINT not in lines
    assert "generation is opt-in" not in text
    assert any(line.startswith("stale cards: 0") for line in lines)


def test_onboard_asks_about_cards_when_atlas_enabled():
    text = (ROOT / "onboard.md").read_text(encoding="utf-8")
    assert "Generate summary cards now" in text
    assert "cards_auto_refresh" in text
    assert "max_cards_per_night" in text
    assert "If (and only if) ATLAS is enabled" in text
    assert "Default on silence: neither" in text
