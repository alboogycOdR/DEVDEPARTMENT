"""omlcp CLI end to end: classify, packet, run, manual materialize, verify, ledger."""
import json
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import omlcp  # noqa: E402
from test_omlcp_packet import make_repo  # noqa: E402

FAKE_GEN = textwrap.dedent('''\
    import re, sys
    prompt = sys.stdin.read()
    nonce = re.search(r"@@OMLCP ([0-9a-f]+) FILE", prompt).group(1)
    M = "@@OMLCP " + nonce
    core = ("import logging\\n\\nLOG = logging.getLogger(__name__)\\n\\n\\n"
            "class WidgetError(Exception):\\n    pass\\n\\n\\n"
            "def render(n: int) -> str:\\n    if n < 0:\\n        raise WidgetError(n)\\n"
            "    return '#' * n\\n")
    test = ("from src.widget.core import render\\n\\n\\ndef test_render():\\n"
            "    assert render(3) == '###'\\n")
    if "Continue the same OMLCP output stream" in prompt:
        sys.stdout.write(M + " FILE tests/test_widget.py\\n" + test + M + " END tests/test_widget.py\\n" + M + " DONE\\n")
    else:
        sys.stdout.write(M + " FILE src/widget/core.py\\n" + core + M + " END src/widget/core.py\\n"
                         + M + " FILE tests/test_widget.py\\nfrom src")
''')


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "repo").mkdir()
    r = make_repo(tmp_path / "repo")
    gen = tmp_path / "fakegen.py"
    gen.write_text(FAKE_GEN, encoding="utf-8")
    (r / "autopilot.json").write_text(json.dumps({
        "judgment_model": "claude-opus-5-5",
        "omlcp": {"enabled": False, "adapter": "command", "command": [sys.executable, str(gen)],
                  "model": "claude-sonnet-5"}}), encoding="utf-8")
    return r


def run(repo, *args):
    return omlcp.main([*args, "--repo", str(repo)] if args[0] not in ("brief-template",) else list(args))


def test_classify_json(repo, capsys):
    assert run(repo, "classify", "TASK-051", "--json") == 0
    out = json.loads(capsys.readouterr().out)
    assert out["lane"] == "generate" and out["task_id"] == "TASK-051"


def test_packet_writes_run_dir(repo, capsys):
    assert run(repo, "packet", "TASK-051") == 0
    rd = repo / ".devteam/omlcp/TASK-051"
    meta = json.loads((rd / "packet.json").read_text())
    assert (rd / "packet.md").is_file() and meta["nonce"] in (rd / "system.md").read_text()
    assert "input ~" in capsys.readouterr().out


def test_run_generates_continues_verifies_and_ledgers(repo, capsys):
    assert run(repo, "run", "TASK-051", "--unit", "S5") == 0
    out = capsys.readouterr().out
    assert "COMPLETE" in out and "RESULT: PASS" in out and "2/2 files on disk" in out
    assert "render" in (repo / "src/widget/core.py").read_text()
    rows = [json.loads(x) for x in (repo / ".devteam/ledger.jsonl").read_text().splitlines()]
    assert len(rows) == 2 and rows[1]["continuation"] is True
    assert json.loads((repo / ".devteam/omlcp/TASK-051/verify.json").read_text())["ok"] is True
    # idempotent re-run
    assert run(repo, "run", "TASK-051") == 0
    assert "already complete" in capsys.readouterr().out


def test_run_refuses_iterate_lane_unless_forced(repo, capsys):
    (repo / "src/widget").mkdir(parents=True)
    (repo / "src/widget/core.py").write_text("x = 1\n")
    assert run(repo, "run", "TASK-051") == 1
    assert "ITERATE" in capsys.readouterr().err


def test_run_refuses_maker_equals_checker(repo, capsys):
    assert run(repo, "run", "TASK-051", "--model", "claude-opus-5-5") == 1
    assert "Maker != checker" in capsys.readouterr().err


def test_manual_materialize_and_continue(repo, capsys):
    assert run(repo, "packet", "TASK-051") == 0
    nonce = json.loads((repo / ".devteam/omlcp/TASK-051/packet.json").read_text())["nonce"]
    M = f"@@OMLCP {nonce}"
    pasted = repo / "pasted.txt"
    pasted.write_text(f"{M} FILE src/widget/core.py\ndef render(n):\n    return n\n\n\n"
                      f"class WidgetError(Exception):\n    \"\"\"Raised on bad input.\"\"\"\n\n    code = 1\n"
                      f"{M} END src/widget/core.py\n")
    assert run(repo, "materialize", "TASK-051", "--stream", str(pasted)) == 1  # incomplete, no DONE
    out = capsys.readouterr().out
    assert "WROTE   src/widget/core.py" in out and "resume here" in out
    assert run(repo, "continue", "TASK-051") == 0
    cont = capsys.readouterr().out
    assert f"{M} FILE tests/test_widget.py" in cont and "- src/widget/core.py" in cont
    pasted.write_text(f"{M} FILE tests/test_widget.py\ndef test_x():\n    assert True\n"
                      f"{M} END tests/test_widget.py\n{M} DONE\n")
    assert run(repo, "materialize", "TASK-051", "--stream", str(pasted)) == 0


def test_verify_command_fails_on_stub(repo, capsys):
    (repo / "src/widget").mkdir(parents=True)
    (repo / "src/widget/core.py").write_text("def render():\n    pass\n")
    (repo / "tests").mkdir()
    (repo / "tests/test_widget.py").write_text("def test_x():\n    assert True\n")
    assert run(repo, "verify", "TASK-051") == 1
    out = capsys.readouterr().out
    assert "stub body" in out and "WidgetError" in out


def test_log_repair_and_report(repo, capsys):
    assert run(repo, "run", "TASK-051") == 0
    capsys.readouterr()
    assert run(repo, "log-repair", "TASK-051", "--input-tokens", "30000", "--output-tokens", "2000") == 0
    assert run(repo, "report", "--json") == 0
    lines = capsys.readouterr().out.splitlines()
    rep = json.loads("\n".join(lines[1:]))
    assert rep["TASK-051"]["repair_input"] == 30000 and rep["TASK-051"]["repair_usd"] > 0
    assert run(repo, "report") == 0
    assert "TASK-051" in capsys.readouterr().out


def test_economics_json_on_fixture_repo(repo, capsys):
    assert run(repo, "economics", "--lines", "2000", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["paper"]["ratio"] > 1 and data["devdept"]["token_ratio"] > 0
    assert run(repo, "economics", "--lines", "2000") == 0
    assert "Paper model, as published" in capsys.readouterr().out


def test_brief_mode_and_template(tmp_path, capsys):
    assert omlcp.main(["brief-template"]) == 0
    tpl = capsys.readouterr().out
    assert "```omlcp-manifest" in tpl
    p = tmp_path / "brief.md"
    p.write_text(tpl, encoding="utf-8")
    assert omlcp.main(["classify", "--brief", str(p), "--repo", str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["lane"] == "generate"


def test_missing_task_is_a_clean_error(repo, capsys):
    assert run(repo, "classify", "TASK-404") == 1
    assert "TASK-404" in capsys.readouterr().err


# ------------------------------------------------------------------- stage --
def _git(cwd, *args):
    import subprocess
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True).stdout


@pytest.fixture
def git_repo(repo, monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
                 "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}.items():
        monkeypatch.setenv(k, v)
    cfg = json.loads((repo / "autopilot.json").read_text())
    cfg["git"] = {"base_branch": "master"}
    (repo / "autopilot.json").write_text(json.dumps(cfg))
    (repo / ".gitignore").write_text(".devteam/\n")
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def test_stage_commits_generation_on_task_branch_and_frees_it(git_repo, capsys):
    assert omlcp.main(["stage", "TASK-051", "--repo", str(git_repo)]) == 0
    out = capsys.readouterr().out
    assert "generation committed on task/TASK-051-s5" in out
    log = _git(git_repo, "log", "--format=%s%n%b", "task/TASK-051-s5", "-1")
    assert "feat(omlcp): generate TASK-051 Widget renderer [TASK-051]" in log
    assert "Generated-By: scripts/omlcp.py stage" in log
    files = _git(git_repo, "show", "--name-only", "--format=", "task/TASK-051-s5").split()
    assert sorted(files) == ["src/widget/core.py", "tests/test_widget.py"]
    assert "wt-omlcp" not in _git(git_repo, "worktree", "list")  # branch free for the builder
    assert _git(git_repo, "rev-parse", "--abbrev-ref", "HEAD").strip() == "master"
    assert not (git_repo / "src/widget/core.py").exists()  # main checkout untouched


def test_stage_needs_a_builder_unit(git_repo, capsys):
    plan = (git_repo / "PLAN.md").read_text().replace("**Assigned_To:** S5", "**Assigned_To:** TBD")
    (git_repo / "PLAN.md").write_text(plan)
    assert omlcp.main(["stage", "TASK-051", "--repo", str(git_repo)]) == 1
    assert "pass --unit" in capsys.readouterr().err


def test_unsafe_standalone_task_id_is_refused(tmp_path, capsys):
    tpl = omlcp.BRIEF_TEMPLATE.replace("**Task:** FEAT-001", "**Task:** ../../escape")
    p = tmp_path / "brief.md"
    p.write_text(tpl, encoding="utf-8")
    assert omlcp.main(["packet", "--brief", str(p), "--repo", str(tmp_path)]) == 1
    assert "not a safe directory name" in capsys.readouterr().err
    assert not (tmp_path.parent / "escape").exists()
