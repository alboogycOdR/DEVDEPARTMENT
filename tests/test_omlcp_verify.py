"""OMLCP verifier: manifest completeness, syntax, contract exports, anti-stub."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from omlcp_packet import ManifestFile  # noqa: E402
from omlcp_verify import verify  # noqa: E402


def check(tmp_path, path, text, exports=(), extra=None):
    p = tmp_path / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return verify(tmp_path, [ManifestFile(path, "x", list(exports))], extra)


def msgs(rep):
    return " | ".join(f.message for f in rep.findings)


def test_clean_python_passes(tmp_path):
    rep = check(tmp_path, "a.py", "import logging\nLOG = logging.getLogger(__name__)\n\n"
                "class Widget:\n    def size(self):\n        return 3\n\ndef render(w):\n    return w.size()\n",
                exports=["render", "Widget", "LOG"])
    assert rep.ok, msgs(rep)


def test_missing_export_is_an_error(tmp_path):
    rep = check(tmp_path, "a.py", "def render():\n    return 1\n", exports=["render", "WidgetError"])
    assert not rep.ok and "WidgetError" in msgs(rep)


@pytest.mark.parametrize("body", ["    pass\n", "    ...\n", "    raise NotImplementedError\n",
                                  "    raise NotImplementedError('later')\n", '    """Doc only."""\n'])
def test_stub_bodies_are_errors(tmp_path, body):
    rep = check(tmp_path, "a.py", f"def f():\n{body}")
    assert not rep.ok and "stub body" in msgs(rep)


def test_abstract_overload_and_protocol_methods_are_allowed(tmp_path):
    src = ("from abc import ABC, abstractmethod\nfrom typing import Protocol, overload\n\n"
           "class Base(ABC):\n    @abstractmethod\n    def run(self):\n        ...\n\n"
           "class Port(Protocol):\n    def read(self) -> bytes:\n        ...\n\n"
           "@overload\ndef g(x: int) -> int: ...\n@overload\ndef g(x: str) -> str: ...\n"
           "def g(x):\n    return x\n")
    assert check(tmp_path, "a.py", src).ok


def test_placeholders_are_errors_and_allow_marker_exempts(tmp_path):
    rep = check(tmp_path, "a.js", "function a() {\n  // rest of implementation\n}\n// TODO wire up\n")
    assert len(rep.errors) == 2
    ok = check(tmp_path, "b.py", "TAGS = ['TODO', 'FIXME']  # omlcp: allow\n")
    assert ok.ok


def test_python_syntax_error(tmp_path):
    rep = check(tmp_path, "a.py", "def broken(:\n")
    assert not rep.ok and "syntax" in msgs(rep)


def test_invalid_json(tmp_path):
    assert not check(tmp_path, "c.json", "{bad").ok
    assert check(tmp_path, "d.json", '{"ok": true}').ok


def test_missing_and_empty_files(tmp_path):
    rep = verify(tmp_path, [ManifestFile("nope.py", "x")])
    assert "missing on disk" in msgs(rep)
    assert "empty" in msgs(check(tmp_path, "e.py", "\n"))


@pytest.mark.parametrize("path,text,name", [
    ("a.ts", "export async function loadUser(id: string) {\n  return id;\n}\n", "loadUser"),
    ("a.ts", "export const store = createStore();\n", "store"),
    ("a.ts", "interface Props { a: number }\nexport { Props };\n", "Props"),
    ("w.dart", "class HomePage extends StatelessWidget {\n}\n", "HomePage"),
    ("w.dart", "Future<void> bootstrap() async {\n}\n", "bootstrap"),
    ("e.mq5", "input double InpRiskPercent = 1.0;\nvoid OnTick()\n{\n}\n", "OnTick"),
    ("e.mq5", "input double InpRiskPercent = 1.0;\n", "InpRiskPercent"),
    ("e.mqh", "class CConfluenceScorer\n{\n};\n", "CConfluenceScorer"),
    ("s.ps1", "function Invoke-Build {\n  param()\n}\n", "Invoke-Build"),
    ("s.sh", "build_all() {\n  :\n}\n", "build_all"),
])
def test_generic_exports_found(tmp_path, path, text, name):
    assert check(tmp_path, path, text, exports=[name]).ok


@pytest.mark.parametrize("path,text,name", [
    ("a.ts", "// loadUser is described here\nconst x = 1;\n", "loadUser"),
    ("e.mq5", "void OnInit()\n{\n   OnTick();\n}\n", "OnTick"),
])
def test_generic_exports_need_a_definition_not_a_mention(tmp_path, path, text, name):
    assert not check(tmp_path, path, text, exports=[name]).ok


def test_extra_patterns_and_render(tmp_path):
    rep = check(tmp_path, "a.py", "X = 'lorem ipsum'\n", extra=[r"lorem ipsum"])
    assert not rep.ok
    out = rep.render()
    assert "RESULT: FAIL" in out and "a.py:1" in out
