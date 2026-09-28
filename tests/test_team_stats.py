"""E-D: generated REVIEW tallies match a recount; rounded stamps are flagged."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from team_stats import (  # noqa: E402
    render_tally_table, rounded_stamps, tally_counts, write_tallies, main,
)

REVIEW = """# REVIEW.md — Review Log

## Per-unit performance tallies

| Unit | Reviews | First-pass approvals | Rework | Common rework causes |
|---|---|---|---|---|
| GB | 99 | 99 | 99 | invented |
| CX | 1 | 1 | 0 | — |
| S5 | 0 | 0 | 0 | — |

Evidence here is hand-maintained and wrong on purpose.

## Verdicts

| Task | Unit | Verdict | Findings | First-pass | Timestamp |
|---|---|---|---|---|---|
| TASK-001 | GB | approved | Territory clean | yes | 2026-07-12T10:00:01Z |
| TASK-002 | GB | rework | Missing test coverage on the error path | no | 2026-07-12T11:00:00Z |
| TASK-002 | GB | approved | Rework verified | no | 2026-07-12T12:00:02Z |
| TASK-003 | CX | approved | Clean | yes | 2026-07-12T13:00:03Z |
| TASK-004 | S5 | rework | spec criterion was not implemented | no | 2026-07-12T14:15:04Z |
"""


def _table_counts(text: str) -> dict:
    counts = {}
    in_section = False
    for line in text.splitlines():
        if line.startswith("## Per-unit performance tallies"):
            in_section = True
            continue
        if in_section and line.startswith("## "):
            break
        if not in_section or not line.startswith("| ") or line.startswith("| Unit") or line.startswith("|---"):
            continue
        unit, reviews, first_pass, rework, causes = [c.strip() for c in line.strip().strip("|").split("|")]
        counts[unit] = {
            "reviews": int(reviews),
            "first_pass_approvals": int(first_pass),
            "rework": int(rework),
            "causes": causes,
        }
    return counts


def test_generated_tallies_equal_a_recount_of_the_rows():
    rewritten = write_tallies(REVIEW)
    assert "invented" not in rewritten
    assert "Evidence here is hand-maintained" in rewritten
    assert rewritten.count("| TASK-001 |") == 1
    recount = tally_counts(REVIEW)
    parsed = _table_counts(rewritten)
    for unit, row in recount.items():
        assert parsed[unit] == row
    assert parsed["GB"] == {
        "reviews": 3, "first_pass_approvals": 1, "rework": 1, "causes": "tests (1)",
    }
    assert parsed["CX"]["reviews"] == 1 and parsed["CX"]["first_pass_approvals"] == 1
    assert parsed["S5"] == {
        "reviews": 1, "first_pass_approvals": 0, "rework": 1, "causes": "spec (1)",
    }
    # Rendering the recount again is stable.
    assert render_tally_table(tally_counts(rewritten)) in rewritten


def test_rounded_stamps_are_flagged(tmp_path, capsys):
    found = rounded_stamps(REVIEW)
    assert ("TASK-002", "2026-07-12T11:00:00Z") in found
    assert all(not ts.endswith(":00:01Z") for _task, ts in found)
    path = tmp_path / "REVIEW.md"
    path.write_text(REVIEW, encoding="utf-8")
    rc = main([str(path)])
    assert rc == 0
    err = capsys.readouterr().err
    assert "WARN  rounded stamp TASK-002 2026-07-12T11:00:00Z" in err
    assert "2026-07-12T10:00:01Z" not in err


def test_write_tallies_cli_rewrites_the_file(tmp_path, capsys):
    path = tmp_path / "REVIEW.md"
    path.write_text(REVIEW, encoding="utf-8")
    rc = main([str(path), "--write-tallies"])
    assert rc == 0
    rewritten = path.read_text(encoding="utf-8")
    assert "| GB | 3 | 1 | 1 | tests (1) |" in rewritten
    assert "invented" not in rewritten
    assert "HINT:" in capsys.readouterr().out
