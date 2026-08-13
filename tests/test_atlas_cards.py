"""Coverage for scripts/atlas_cards.py — ATLAS Layer 1 (hash-pinned cards).

Every test uses a fake model transcript: a real, but stub, subprocess
executable that echoes canned output on stdout, exactly like
tests/test_distiller.py's ``fake_model`` helper. No test in this file makes
a live model call (spec §6 A3).
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import atlas  # noqa: E402
import atlas_cards as cards_mod  # noqa: E402
import atlas_core as core  # noqa: E402

GOOD_CARD = json.dumps({
    "purpose": "Adds two numbers together.",
    "invariants": ["inputs must be numeric"],
    "gotchas": ["no overflow handling"],
    "entry_points": ["add"],
    "tokens_estimate": 42,
})


def fake_model(tmp_path: Path, output_text: str, name: str = "fake_model.py") -> list[str]:
    """Register a headless-call stand-in that echoes canned model output,
    mirroring tests/test_distiller.py's fake_model helper."""
    script = tmp_path / name
    payload = tmp_path / f"{name}.out.txt"
    payload.write_text(output_text, encoding="utf-8")
    script.write_text(
        "import sys, pathlib\n"
        f"sys.stdout.write(pathlib.Path({str(payload)!r}).read_text(encoding='utf-8'))\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


def failing_model(tmp_path: Path, message: str = "boom") -> list[str]:
    script = tmp_path / "failing_model.py"
    script.write_text(
        "import sys\n"
        f"sys.stderr.write({message!r})\n"
        "sys.exit(3)\n",
        encoding="utf-8",
    )
    return [sys.executable, str(script)]


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "add.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (tmp_path / "scripts" / "sub.py").write_text("def sub(a, b):\n    return a - b\n", encoding="utf-8")
    core.scan(tmp_path, full=True)
    return tmp_path


@pytest.fixture
def bindir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Separate scratch dir for fake-model scripts, kept out of the
    scanned repo tree so writing them never perturbs candidate selection."""
    return tmp_path_factory.mktemp("bin")


def _card_row(repo: Path, path: str):
    con = sqlite3.connect(repo / ".devteam" / "atlas.db")
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT c.* FROM cards c JOIN files f ON f.id = c.file_id WHERE f.path = ?", (path,)
    ).fetchone()
    con.close()
    return row


def test_generate_writes_hash_pinned_card(repo: Path, bindir: Path):
    cmd = fake_model(bindir, GOOD_CARD)
    generated, failed, errors = cards_mod.generate_cards(repo, cmd_override=cmd)
    assert (generated, failed, errors) == (2, 0, [])
    row = _card_row(repo, "scripts/add.py")
    assert row is not None
    assert row["purpose"] == "Adds two numbers together."
    assert json.loads(row["invariants"]) == ["inputs must be numeric"]
    assert row["tokens_estimate"] == 42
    file_hash = sqlite3.connect(repo / ".devteam" / "atlas.db").execute(
        "SELECT content_hash FROM files WHERE path='scripts/add.py'"
    ).fetchone()[0]
    assert row["source_hash"] == file_hash


def test_generate_never_runs_from_scan(repo: Path):
    # scan() ran in the fixture already; no model was ever invoked, so no
    # cards can exist yet regardless of how many files were scanned.
    con = sqlite3.connect(repo / ".devteam" / "atlas.db")
    assert con.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0


def test_regenerate_skipped_when_hash_unchanged(repo: Path, bindir: Path):
    cmd = fake_model(bindir, GOOD_CARD)
    cards_mod.generate_cards(repo, cmd_override=cmd)
    # Second run: no files changed since the last card, so nothing is a
    # candidate and the (now-poisoned) fake model must not be called again.
    broken_cmd = failing_model(bindir)
    generated, failed, errors = cards_mod.generate_cards(repo, cmd_override=broken_cmd)
    assert (generated, failed, errors) == (0, 0, [])


def test_regenerate_triggers_only_on_hash_change(repo: Path, bindir: Path):
    cards_mod.generate_cards(repo, cmd_override=fake_model(bindir, GOOD_CARD))
    (repo / "scripts" / "add.py").write_text("def add(a, b):\n    return a + b + 0\n", encoding="utf-8")
    core.scan(repo)
    updated_card = json.dumps({**json.loads(GOOD_CARD), "purpose": "Adds two numbers (updated)."})
    generated, failed, _ = cards_mod.generate_cards(repo, cmd_override=fake_model(bindir, updated_card, "second.py"))
    assert generated == 1 and failed == 0
    row = _card_row(repo, "scripts/add.py")
    assert row["purpose"] == "Adds two numbers (updated)."


def test_only_glob_restricts_candidates(repo: Path, bindir: Path):
    generated, failed, _ = cards_mod.generate_cards(
        repo, only="scripts/add.py", cmd_override=fake_model(bindir, GOOD_CARD)
    )
    assert generated == 1
    assert _card_row(repo, "scripts/add.py") is not None
    assert _card_row(repo, "scripts/sub.py") is None


def test_max_caps_files_per_run(repo: Path, bindir: Path):
    generated, failed, _ = cards_mod.generate_cards(
        repo, max_n=1, cmd_override=fake_model(bindir, GOOD_CARD)
    )
    assert generated == 1
    con = sqlite3.connect(repo / ".devteam" / "atlas.db")
    assert con.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 1


def test_model_failure_leaves_db_untouched(repo: Path, bindir: Path):
    generated, failed, errors = cards_mod.generate_cards(
        repo, cmd_override=failing_model(bindir, "model unreachable")
    )
    assert generated == 0
    assert failed == 2
    assert any("model unreachable" in e or "rc=3" in e for e in errors)
    con = sqlite3.connect(repo / ".devteam" / "atlas.db")
    assert con.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0


def test_malformed_json_output_is_a_clear_error_and_no_write(repo: Path, bindir: Path):
    generated, failed, errors = cards_mod.generate_cards(
        repo, cmd_override=fake_model(bindir, "not json at all, sorry")
    )
    assert generated == 0 and failed == 2
    assert all("JSON" in e or "did not contain" in e for e in errors)
    con = sqlite3.connect(repo / ".devteam" / "atlas.db")
    assert con.execute("SELECT COUNT(*) FROM cards").fetchone()[0] == 0


def test_missing_required_field_is_rejected(repo: Path, bindir: Path):
    bad = json.dumps({"purpose": "x", "invariants": [], "gotchas": []})  # no entry_points
    generated, failed, errors = cards_mod.generate_cards(
        repo, only="scripts/add.py", cmd_override=fake_model(bindir, bad)
    )
    assert generated == 0 and failed == 1
    assert "entry_points" in errors[0]


def test_tokens_estimate_falls_back_when_absent_or_invalid(repo: Path, bindir: Path):
    no_tokens = json.dumps({"purpose": "p", "invariants": [], "gotchas": [], "entry_points": []})
    cards_mod.generate_cards(repo, only="scripts/add.py", cmd_override=fake_model(bindir, no_tokens))
    row = _card_row(repo, "scripts/add.py")
    assert row["tokens_estimate"] > 0


def test_stale_lists_cards_whose_hash_no_longer_matches(repo: Path, bindir: Path):
    cards_mod.generate_cards(repo, cmd_override=fake_model(bindir, GOOD_CARD))
    assert cards_mod.stale_cards(repo) == []
    (repo / "scripts" / "add.py").write_text("def add(a, b):\n    return a + b  # changed\n", encoding="utf-8")
    core.scan(repo)
    stale = cards_mod.stale_cards(repo)
    assert len(stale) == 1
    assert stale[0].startswith("scripts/add.py") and "STALE" in stale[0]


def test_doctored_hash_flips_every_query_path_to_stale(repo: Path, bindir: Path):
    """§7 A3 exit criterion, verified end-to-end through atlas_core.query —
    not just this module's own bookkeeping."""
    cards_mod.generate_cards(repo, only="scripts/add.py", cmd_override=fake_model(bindir, GOOD_CARD))
    hits = core.query(repo, "add", 20)
    assert any("scripts/add.py" in h and "FRESH" in h for h in hits)

    con = sqlite3.connect(repo / ".devteam" / "atlas.db")
    con.execute("UPDATE cards SET source_hash = 'doctored-hash-does-not-match' "
                "WHERE file_id = (SELECT id FROM files WHERE path='scripts/add.py')")
    con.commit()
    con.close()

    hits = core.query(repo, "add", 20)
    assert any("scripts/add.py" in h and "STALE" in h for h in hits)
    assert not any("scripts/add.py" in h and "FRESH" in h for h in hits)
    where_hits = core.where(repo, "add")
    assert where_hits  # symbol still resolvable; freshness lives in query, not where


def test_generate_cli_via_facade_reads_autopilot_override(repo: Path, bindir: Path, capsys):
    cmd = fake_model(bindir, GOOD_CARD)
    (repo / "autopilot.json").write_text(json.dumps({"atlas": {"cards_cmd": cmd}}), encoding="utf-8")
    import os
    old_cwd = Path.cwd()
    os.chdir(repo)
    try:
        assert atlas.main(["cards", "--generate"]) == 0
    finally:
        os.chdir(old_cwd)
    out = capsys.readouterr().out
    assert "cards generated: 2" in out
    assert _card_row(repo, "scripts/add.py") is not None


def test_stale_cli_via_facade(repo: Path, bindir: Path, capsys):
    cards_mod.generate_cards(repo, cmd_override=fake_model(bindir, GOOD_CARD))
    (repo / "scripts" / "add.py").write_text("def add(a, b):\n    return a + b  # v2\n", encoding="utf-8")
    core.scan(repo)
    import os
    old_cwd = Path.cwd()
    os.chdir(repo)
    try:
        assert atlas.main(["cards", "--stale"]) == 0
    finally:
        os.chdir(old_cwd)
    assert "scripts/add.py" in capsys.readouterr().out


def test_cli_requires_generate_or_stale(repo: Path, capsys):
    import os
    old_cwd = Path.cwd()
    os.chdir(repo)
    try:
        assert atlas.main(["cards"]) == 1
    finally:
        os.chdir(old_cwd)
    assert "specify --generate or --stale" in capsys.readouterr().err


def test_generate_failure_exit_code_is_one_not_two(repo: Path, bindir: Path, capsys):
    (repo / "autopilot.json").write_text(
        json.dumps({"atlas": {"cards_cmd": failing_model(bindir)}}), encoding="utf-8"
    )
    import os
    old_cwd = Path.cwd()
    os.chdir(repo)
    try:
        assert atlas.main(["cards", "--generate"]) == 1
    finally:
        os.chdir(old_cwd)


def test_bad_cli_flag_exits_one_not_two():
    with pytest.raises(SystemExit) as error:
        atlas.build_parser().parse_args(["cards", "--generate", "--max", "notanint"])
    assert error.value.code == 1


def test_facade_registers_cards_without_editing_core(repo: Path):
    parser = atlas.build_parser()
    args = parser.parse_args(["cards", "--stale"])
    assert args.handler is cards_mod.cmd_cards


def test_call_model_wraps_unreachable_executable(tmp_path: Path):
    with pytest.raises(cards_mod.CardGenerationError):
        cards_mod.call_model("prompt", [str(tmp_path / "does-not-exist-binary")])


def test_parse_card_output_extracts_json_from_prose_wrapping():
    wrapped = f"Sure, here you go:\n```json\n{GOOD_CARD}\n```\nHope that helps!"
    data = cards_mod.parse_card_output(wrapped)
    assert data["purpose"] == "Adds two numbers together."


def test_parse_card_output_rejects_non_object_json():
    with pytest.raises(cards_mod.CardGenerationError):
        cards_mod.parse_card_output("[1, 2, 3]")
