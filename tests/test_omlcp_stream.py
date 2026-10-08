"""OMLCP stream protocol: path safety, incremental parsing, safe materialization."""
import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from omlcp_stream import (MARKER, PROTECTED_FOR_BUILDERS, PathPolicy, RunState, StreamParser,  # noqa: E402
                          glob_match, glob_prefix_match, materialize, new_nonce, parse_segments,
                          path_problems)

REPO_ROOT = Path(__file__).resolve().parents[1]
N = "a1b2c3d4e5f6"
M = f"{MARKER} {N}"


def stream(*files, done=True, noise=""):
    out = [noise] if noise else []
    for path, body in files:
        out += [f"{M} FILE {path}", body.rstrip("\n"), f"{M} END {path}"]
    if done:
        out.append(f"{M} DONE")
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ globs --
@pytest.mark.parametrize("path,glob,expected", [
    ("src/a.py", "src/**", True),
    ("src/x/y/a.py", "src/**", True),
    ("src/a.py", "src/*.py", True),
    ("src/x/a.py", "src/*.py", False),
    ("tests/test_a.py", "tests/test_*.py", True),
    ("tests/test_x/evil.py", "tests/test_*.py", False),
    ("lib/a.dart", "lib/", True),
    ("src/a.ts", "src/{a,b}.ts", True),
    ("src/c.ts", "src/{a,b}.ts", False),
    ("a/b/c.py", "**/c.py", True),
    ("c.py", "**/c.py", True),
])
def test_glob_match_is_strict(path, glob, expected):
    assert glob_match(path, glob) is expected


def test_prefix_match_mirrors_lib_js_breadth():
    # Protection is deliberately broad: truncate at the first wildcard, prefix-match.
    assert glob_prefix_match("tests/test_x/evil.py", "tests/test_*.py")
    assert glob_prefix_match("scripts/x.py", "scripts/**")
    assert glob_prefix_match("CLAUDE.md", "CLAUDE.md")
    assert not glob_prefix_match("CLAUDE.md.bak", "CLAUDE.md")
    assert glob_prefix_match("deploy/x/y.js", "deploy/**")
    assert not glob_prefix_match("src/a.py", "scripts/**")


# ------------------------------------------------------------- path safety --
@pytest.mark.parametrize("bad", [
    "../etc/passwd", "src/../../x", "/abs/path.py", "C:/win/path.py", "src\\a.py", "",
    "src//a.py", "src/./a.py", "src/CON.py", "aux", "src/com1.txt", "src/trailing. ",
    "src/a?.py", "src/a\x07.py", "x" * 300,
])
def test_path_problems_rejects_unsafe_paths(bad):
    assert path_problems(bad), bad


def test_path_problems_accepts_ordinary_paths():
    for ok in ("src/a.py", "lib/widgets/home_page.dart", "MQL5/Experts/CRT/v3/CRT_EA.mq5", ".github/x.yml"):
        assert path_problems(ok) == []


def test_policy_enforces_manifest_owned_and_protection():
    pol = PathPolicy(manifest=["src/a.py", "scripts/tool.py", "src/b.py"],
                     owned=["src/**", "scripts/tool.py"])
    assert pol.violations("src/a.py") == []
    assert any("not in the generation manifest" in v for v in pol.violations("src/z.py"))
    assert any("protected" in v for v in pol.violations("scripts/tool.py"))
    granted = PathPolicy(manifest=["scripts/tool.py"], owned=["scripts/tool.py"], grants=["scripts/tool.py"])
    assert granted.violations("scripts/tool.py") == []
    outside = PathPolicy(manifest=["lib/x.py"], owned=["src/**"])
    assert any("outside the task's Owned_Paths" in v for v in outside.violations("lib/x.py"))


@pytest.mark.parametrize("path", ["PLAN.md", ".git/config", ".devteam/ledger.jsonl", "dossiers/TASK-1.md"])
def test_coordination_state_is_never_writable_even_with_grants(path):
    pol = PathPolicy(manifest=[path], owned=[path], grants=[path])
    assert any("coordination/VCS state" in v for v in pol.violations(path))


def test_standalone_policy_needs_only_the_manifest():
    pol = PathPolicy(manifest=["app/main.py"], owned=[], protected=[])
    assert pol.violations("app/main.py") == []


def test_protected_list_matches_hooks_lib_js():
    """The generator must refuse exactly what the territory firewall refuses."""
    js = (REPO_ROOT / "hooks" / "lib.js").read_text(encoding="utf-8")
    block = re.search(r"const PROTECTED_FOR_BUILDERS = \[(.*?)\];", js, re.S).group(1)
    js_list = re.findall(r"'([^']+)'", re.sub(r"//[^\n]*", "", block))
    assert sorted(js_list) == sorted(PROTECTED_FOR_BUILDERS)


# ------------------------------------------------------------------ parsing --
def test_parses_complete_stream():
    r = parse_segments(N, [stream(("src/a.py", "x = 1\n"), ("src/b.py", "y = 2\n"))])
    assert r.done
    assert {p: f.content for p, f in r.complete_files().items()} == {"src/a.py": "x = 1\n", "src/b.py": "y = 2\n"}


def test_markers_split_across_arbitrary_chunks():
    text = stream(("src/a.py", "print('héllo — ✓')\n"), ("src/b.py", "z = 3\n"))
    for size in (1, 2, 3, 7, 50):
        p = StreamParser(N)
        for i in range(0, len(text), size):
            p.feed(text[i:i + size])
        r = p.close()
        assert r.done and set(r.complete_files()) == {"src/a.py", "src/b.py"}
        assert r.complete_files()["src/a.py"].content == "print('héllo — ✓')\n"


def test_crlf_is_normalised():
    r = parse_segments(N, [stream(("a.py", "x = 1\n")).replace("\n", "\r\n")])
    assert r.complete_files()["a.py"].content == "x = 1\n"


def test_truncated_tail_is_incomplete_and_resumable():
    text = f"{M} FILE a.py\nx = 1\n{M} END a.py\n{M} FILE b.py\ndef f(:"
    r = parse_segments(N, [text])
    assert not r.done
    assert set(r.complete_files()) == {"a.py"}
    assert r.trailing_incomplete().path == "b.py"


def test_wrapping_code_fence_is_stripped_with_warning():
    r = parse_segments(N, [stream(("a.py", "```python\nx = 1\n```"))])
    assert r.complete_files()["a.py"].content == "x = 1\n"
    assert any("fence" in w for w in r.warnings)


def test_inner_fences_are_preserved():
    body = 'DOC = """\n```\nnot a wrapper\n```\n"""\n'
    r = parse_segments(N, [stream(("a.py", body))])
    assert r.complete_files()["a.py"].content == body


def test_foreign_nonce_is_content_not_a_marker():
    body = f"{MARKER} deadbeef0000 FILE evil.py\nok = True\n"
    r = parse_segments(N, [stream(("a.py", body))])
    assert set(r.complete_files()) == {"a.py"}
    assert "deadbeef0000" in r.complete_files()["a.py"].content


def test_mismatched_end_is_an_error_and_file_incomplete():
    r = parse_segments(N, [f"{M} FILE a.py\nx\n{M} END b.py\n{M} DONE\n"])
    assert r.errors and not r.complete_files()


def test_new_file_before_end_closes_previous_as_incomplete():
    r = parse_segments(N, [f"{M} FILE a.py\nx\n{M} FILE b.py\ny\n{M} END b.py\n{M} DONE\n"])
    assert set(r.complete_files()) == {"b.py"}


def test_done_inside_open_file_does_not_complete_it():
    r = parse_segments(N, [f"{M} FILE a.py\nx\n{M} DONE\n"])
    assert r.done and not r.complete_files()


def test_segment_boundary_closes_open_file_incomplete():
    seg0 = f"{M} FILE a.py\nx = 1\n{M} END a.py\n{M} FILE b.py\npartial"
    seg1 = f"{M} FILE b.py\ny = 2\n{M} END b.py\n{M} DONE\n"
    r = parse_segments(N, [seg0, seg1])
    files = r.complete_files()
    assert files["b.py"].content == "y = 2\n" and files["b.py"].segment == 1
    assert r.done


def test_notes_and_noise_are_collected_not_written():
    r = parse_segments(N, [stream(("a.py", "x\n"), noise="Sure, here you go!") + f"{M} NOTE hello\n"])
    assert r.noise_lines == 1 and r.notes == ["hello"]


def test_invalid_nonce_rejected():
    with pytest.raises(ValueError):
        StreamParser("not-hex!")
    assert re.fullmatch(r"[0-9a-f]{12}", new_nonce())


# -------------------------------------------------------------- materialize --
def _policy(*paths):
    return PathPolicy(manifest=list(paths), owned=["src/**"])


def test_materialize_writes_complete_files_and_reports_pending(tmp_path):
    text = f"{M} FILE src/a.py\nx = 1\n{M} END src/a.py\n{M} FILE src/b.py\ny ="
    res = materialize(parse_segments(N, [text]), _policy("src/a.py", "src/b.py", "src/c.py"), tmp_path)
    assert res.written == ["src/a.py"] and (tmp_path / "src/a.py").read_text() == "x = 1\n"
    assert not (tmp_path / "src/b.py").exists()
    assert res.pending == ["src/b.py", "src/c.py"] and res.resume_from == "src/b.py"
    assert not res.complete


def test_materialize_complete_requires_done_marker(tmp_path):
    no_done = materialize(parse_segments(N, [stream(("src/a.py", "x\n"), done=False)]), _policy("src/a.py"), tmp_path)
    assert not no_done.complete and no_done.pending == []
    done = materialize(parse_segments(N, [stream(("src/a.py", "x\n"))]), _policy("src/a.py"), tmp_path)
    assert done.complete and done.unchanged == ["src/a.py"]


def test_materialize_refuses_preexisting_foreign_file(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text("hand written\n")
    res = materialize(parse_segments(N, [stream(("src/a.py", "generated\n"))]), _policy("src/a.py"), tmp_path)
    assert "src/a.py" in res.rejected and (tmp_path / "src/a.py").read_text() == "hand written\n"


def test_materialize_overwrites_only_its_own_untouched_output(tmp_path):
    pol = _policy("src/a.py")
    first = materialize(parse_segments(N, [stream(("src/a.py", "v1\n"))]), pol, tmp_path)
    again = materialize(parse_segments(N, [stream(("src/a.py", "v2\n"))]), pol, tmp_path,
                        owned_hashes=first.hashes)
    assert again.written == ["src/a.py"] and (tmp_path / "src/a.py").read_text() == "v2\n"
    (tmp_path / "src/a.py").write_text("repaired by builder\n")
    third = materialize(parse_segments(N, [stream(("src/a.py", "v3\n"))]), pol, tmp_path,
                        owned_hashes=again.hashes)
    assert "src/a.py" in third.rejected
    assert (tmp_path / "src/a.py").read_text() == "repaired by builder\n"


def test_extraneous_files_are_never_written(tmp_path):
    text = stream(("src/a.py", "x\n"), ("src/evil.py", "boom\n"), ("../escape.py", "boom\n"))
    res = materialize(parse_segments(N, [text]), _policy("src/a.py"), tmp_path)
    assert set(res.extraneous) == {"src/evil.py", "../escape.py"}
    assert not (tmp_path / "src/evil.py").exists() and not (tmp_path.parent / "escape.py").exists()
    assert res.complete  # the manifest is satisfied; extras are warnings, not blockers


def test_protected_manifest_entry_blocks_completion(tmp_path):
    pol = PathPolicy(manifest=["scripts/x.py"], owned=["scripts/x.py"])
    res = materialize(parse_segments(N, [stream(("scripts/x.py", "x\n"))]), pol, tmp_path)
    assert "scripts/x.py" in res.rejected and not res.complete
    assert not (tmp_path / "scripts/x.py").exists()


def test_dry_run_writes_nothing(tmp_path):
    res = materialize(parse_segments(N, [stream(("src/a.py", "x\n"))]), _policy("src/a.py"), tmp_path, dry_run=True)
    assert res.written == ["src/a.py"] and not (tmp_path / "src/a.py").exists()


def test_first_complete_version_wins_on_repeat(tmp_path):
    seg0 = stream(("src/a.py", "first\n"), done=False)
    seg1 = stream(("src/a.py", "second\n"))
    res = materialize(parse_segments(N, [seg0, seg1]), _policy("src/a.py"), tmp_path)
    assert (tmp_path / "src/a.py").read_text() == "first\n"
    assert any("repeated" in w for w in res.warnings)


def test_written_files_use_lf_and_utf8(tmp_path):
    materialize(parse_segments(N, [stream(("src/a.py", "s = 'ünïcødé'\n"))]), _policy("src/a.py"), tmp_path)
    raw = (tmp_path / "src/a.py").read_bytes()
    assert b"\r\n" not in raw and raw.decode("utf-8") == "s = 'ünïcødé'\n"


def test_run_state_roundtrip(tmp_path):
    st = RunState(task_id="TASK-9", nonce=N, segments=["segment-0.raw"], written_hashes={"a": "h"})
    st.save(tmp_path / "state.json")
    back = RunState.load(tmp_path / "state.json")
    assert back == st
    data = json.loads((tmp_path / "state.json").read_text())
    data["future_field"] = 1
    (tmp_path / "state.json").write_text(json.dumps(data))
    assert RunState.load(tmp_path / "state.json").task_id == "TASK-9"


def test_seam_inside_file_discards_rest_until_next_file():
    text = (f"{M} FILE a.py\nx = 1\n{M} END a.py\n{M} FILE b.py\ndef f(self)\n{M} SEAM\n"
            f"    def f(self):\n        return 1\n{M} END b.py\n{M} FILE c.py\nz = 3\n{M} END c.py\n{M} DONE\n")
    r = parse_segments(N, [text])
    assert set(r.complete_files()) == {"a.py", "c.py"}
    assert any("seam" in w for w in r.warnings)
    assert r.noise_lines == 0  # discarded tail is not counted as model chatter
    assert not any("with no open FILE" in w for w in r.warnings)


def test_seam_between_files_is_harmless():
    text = f"{M} FILE a.py\nx = 1\n{M} END a.py\n{M} SEAM\n{M} FILE b.py\ny = 2\n{M} END b.py\n{M} DONE\n"
    r = parse_segments(N, [text])
    assert set(r.complete_files()) == {"a.py", "b.py"} and r.done
