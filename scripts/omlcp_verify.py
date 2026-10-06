#!/usr/bin/env python3
"""Deterministic post-generation checks for an OMLCP run (paper §3.2 step 5, §6.4).

What it checks, all without a model call:

* **Manifest completeness** — every manifest file exists on disk.
* **Syntax** — Python (``ast``), JSON. Other languages are left to the project's own
  build/test command, which the builder runs next.
* **Contract coherence** — every declared export exists as a definition in its file.
  This is the paper's "interface misalignment" failure (§5.1) made checkable: the
  planner fixed the names, the verifier proves the generation kept them.
* **Anti-stub** — the paper's §11.6 failure: long outputs drift into placeholders
  ("rest of implementation", bare ``pass`` bodies, ``NotImplementedError``). Those are
  findings, not warnings, because a stub that compiles is exactly how "green did not
  mean working" happened in the field (synthesis §0.4).

Verification is necessary, not sufficient. It never replaces the project's test suite
or ORCH's independent review.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

ALLOW_MARKER = "omlcp: allow"  # put in a line's comment to exempt it from placeholder checks
PLACEHOLDER_PATTERNS = [
    r"\bTODO\b", r"\bFIXME\b", r"\bXXX\b",
    r"rest of (the )?(implementation|code|file|function)",
    r"remaining (implementation|methods|code)",
    r"(similar|same) (as|to) (above|before)",
    r"omitted for brevity", r"implementation (goes|left) here", r"implement (me|this)",
    r"\.\.\.\s*(existing|more|other|rest)", r"<\s*(snip|omitted)\s*>",
    r"^\s*(#|//)\s*\.\.\.\s*$",
]

_DEF_PATTERNS = {
    "js": [r"(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s*\*?\s+{n}\b",
           r"(?:export\s+)?(?:default\s+)?class\s+{n}\b",
           r"(?:export\s+)?(?:const|let|var)\s+{n}\b",
           r"export\s*\{{[^}}]*\b{n}\b[^}}]*\}}", r"(?:export\s+)?(?:interface|type|enum)\s+{n}\b"],
    "dart": [r"\bclass\s+{n}\b", r"\benum\s+{n}\b", r"\bmixin\s+{n}\b", r"\bextension\s+{n}\b",
             r"^[\w<>?,\s]*\b{n}\s*\(", r"\btypedef\s+{n}\b"],
    "c": [r"\bclass\s+{n}\b", r"\bstruct\s+{n}\b", r"\benum\s+{n}\b",
          r"^[\w:<>*&\s]*\b{n}\s*\([^;]*$", r"#define\s+{n}\b", r"\binput\s+[\w\s]+\b{n}\b"],
    "ps1": [r"^\s*function\s+{n}\b", r"^\s*filter\s+{n}\b", r"^\s*\$(?:script:|global:)?{n}\s*="],
    "sh": [r"^\s*(?:function\s+)?{n}\s*\(\s*\)", r"^\s*function\s+{n}\b"],
}
_LANG_BY_EXT = {".js": "js", ".mjs": "js", ".cjs": "js", ".ts": "js", ".tsx": "js", ".jsx": "js",
                ".dart": "dart", ".mq5": "c", ".mq4": "c", ".mqh": "c", ".c": "c", ".h": "c",
                ".cpp": "c", ".hpp": "c", ".cs": "c", ".java": "c", ".go": "c",
                ".ps1": "ps1", ".psm1": "ps1", ".sh": "sh", ".bash": "sh"}


@dataclass
class Finding:
    level: str  # "error" | "warn"
    path: str
    line: int
    message: str


@dataclass
class VerifyReport:
    findings: list[Finding] = field(default_factory=list)
    checked: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict:
        return {"ok": self.ok, "checked": self.checked, "findings": [asdict(f) for f in self.findings]}

    def render(self) -> str:
        out = [f"[omlcp verify] {len(self.checked)} file(s) checked, "
               f"{len(self.errors)} error(s), {len(self.findings) - len(self.errors)} warning(s)"]
        for f in self.findings:
            out.append(f"  {f.level.upper():5s} {f.path}:{f.line}  {f.message}")
        out.append("  RESULT: " + ("PASS" if self.ok else "FAIL"))
        return "\n".join(out)


def _python_exports(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    names.add(t.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                names.add((a.asname or a.name).split(".")[0])
    return names


def _is_stub_body(fn: ast.AST) -> bool:
    body = list(getattr(fn, "body", []))
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]  # docstring
    if not body:
        return True
    if len(body) != 1:
        return False
    node = body[0]
    if isinstance(node, ast.Pass):
        return True
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and node.value.value is Ellipsis:
        return True
    if isinstance(node, ast.Raise) and node.exc is not None:
        exc = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
        return isinstance(exc, ast.Name) and exc.id == "NotImplementedError"
    return False


def _abstract(fn: ast.AST) -> bool:
    for d in getattr(fn, "decorator_list", []):
        name = d.attr if isinstance(d, ast.Attribute) else getattr(d, "id", "")
        if name in {"abstractmethod", "overload", "abstractproperty"}:
            return True
    return False


def _protocol_class(cls: ast.ClassDef) -> bool:
    for b in cls.bases:
        name = b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "")
        if name in {"Protocol", "ABC"}:
            return True
    return False


def _check_python(path: str, text: str, exports: list[str], rep: VerifyReport) -> None:
    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError as exc:
        rep.findings.append(Finding("error", path, exc.lineno or 0, f"Python syntax error: {exc.msg}"))
        return
    defined = _python_exports(tree)
    for name in exports:
        if name.split("(")[0].strip() not in defined:
            rep.findings.append(Finding("error", path, 0, f"declared export {name!r} is not defined at module level"))
    in_protocol: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and _protocol_class(node):
            in_protocol.update(id(n) for n in node.body)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_stub_body(node) \
                and not _abstract(node) and id(node) not in in_protocol:
            rep.findings.append(Finding("error", path, node.lineno,
                                        f"function {node.name!r} has a stub body (pass / ... / NotImplementedError)"))


def _check_generic(path: str, text: str, exports: list[str], rep: VerifyReport) -> None:
    lang = _LANG_BY_EXT.get(Path(path).suffix.lower())
    if path.endswith(".json"):
        try:
            json.loads(text)
        except json.JSONDecodeError as exc:
            rep.findings.append(Finding("error", path, exc.lineno, f"invalid JSON: {exc.msg}"))
        return
    for name in exports:
        n = re.escape(name.split("(")[0].strip())
        pats = _DEF_PATTERNS.get(lang or "", [])
        if pats:
            found = any(re.search(p.format(n=n), text, re.M) for p in pats)
            how = "definition"
        else:
            found = re.search(rf"\b{n}\b", text) is not None
            how = "mention (no definition grammar for this file type)"
        if not found:
            rep.findings.append(Finding("error", path, 0, f"declared export {name!r} not found ({how})"))


def verify(root: Path, manifest_files: list, extra_patterns: list[str] | None = None) -> VerifyReport:
    """``manifest_files`` are ``omlcp_packet.ManifestFile`` objects (path, exports)."""
    rep = VerifyReport()
    patterns = [re.compile(p, re.I | re.M) for p in PLACEHOLDER_PATTERNS + list(extra_patterns or [])]
    for mf in manifest_files:
        p = root / mf.path
        if not p.is_file():
            rep.findings.append(Finding("error", mf.path, 0, "manifest file missing on disk"))
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        rep.checked.append(mf.path)
        if not text.strip():
            rep.findings.append(Finding("error", mf.path, 0, "file is empty"))
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if ALLOW_MARKER in line:  # deliberate: e.g. a parser that matches the word TODO
                continue
            for pat in patterns:
                if pat.search(line):
                    rep.findings.append(Finding("error", mf.path, i,
                                                f"placeholder/stub marker: {line.strip()[:90]!r}"))
                    break
        if mf.path.endswith(".py"):
            _check_python(mf.path, text, list(mf.exports), rep)
        else:
            _check_generic(mf.path, text, list(mf.exports), rep)
    return rep
