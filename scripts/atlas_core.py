"""Deterministic Layer 0 scanner and read-only queries for ATLAS.

JS/TS and Dart extraction is deliberately regex based: it recognizes ordinary
static imports, exported/function declarations, classes and top-level Dart
functions, but does not parse dynamic imports, decorators, generated code, or
complex multi-line grammar. Python uses the standard-library AST parser.
"""
from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1"
TEXT_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".dart", ".mq5", ".mqh", ".md", ".json", ".yaml", ".yml", ".ps1", ".sh", ".txt"}
LANGUAGES = {".py": "python", ".js": "javascript", ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".dart": "dart", ".mq5": "mql5", ".mqh": "mql5"}


@dataclass
class Symbol:
    name: str
    kind: str
    start: int
    end: int
    signature: str = ""


def slash(path: Path | str) -> str:
    return str(path).replace("\\", "/")


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def db_path(repo: Path) -> Path:
    return repo / ".devteam" / "atlas.db"


def connect(repo: Path) -> sqlite3.Connection:
    path = db_path(repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    return con


def init_schema(con: sqlite3.Connection) -> None:
    con.executescript("""
    CREATE TABLE IF NOT EXISTS files (
      id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, lang TEXT NOT NULL,
      content_hash TEXT NOT NULL, loc INTEGER NOT NULL, mtime REAL NOT NULL, indexed_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS symbols (
      id INTEGER PRIMARY KEY, file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
      name TEXT NOT NULL, kind TEXT NOT NULL, line_start INTEGER NOT NULL, line_end INTEGER NOT NULL, signature TEXT NOT NULL DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS edges (
      id INTEGER PRIMARY KEY, src_file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
      dst_file_id INTEGER REFERENCES files(id) ON DELETE SET NULL, kind TEXT NOT NULL, detail TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS cards (
      file_id INTEGER PRIMARY KEY REFERENCES files(id) ON DELETE CASCADE, source_hash TEXT NOT NULL,
      generated_at TEXT NOT NULL, model TEXT NOT NULL, purpose TEXT NOT NULL, invariants TEXT NOT NULL,
      gotchas TEXT NOT NULL, entry_points TEXT NOT NULL, tokens_estimate INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS episodes (
      id INTEGER PRIMARY KEY, kind TEXT NOT NULL, ref TEXT NOT NULL, ts TEXT, unit TEXT,
      indexed_hash TEXT NOT NULL, body_fts TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE VIRTUAL TABLE IF NOT EXISTS files_fts USING fts5(path, body);
    CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(ref, body);
    """)
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)", (SCHEMA_VERSION,))
    con.commit()


def _atlas_excludes(repo: Path) -> list[str]:
    try:
        data = json.loads((repo / "autopilot.json").read_text(encoding="utf-8"))
        return [str(x) for x in data.get("atlas", {}).get("exclude", [])]
    except (OSError, json.JSONDecodeError, AttributeError):
        return []


def _ignore_patterns(repo: Path) -> list[str]:
    patterns = [".git/", ".devteam/"]
    try:
        for raw in (repo / ".gitignore").read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and not line.startswith("!"):
                patterns.append(line)
    except OSError:
        pass
    return patterns + _atlas_excludes(repo)


def is_ignored(rel: str, patterns: list[str]) -> bool:
    rel = rel.strip("/")
    for pattern in patterns:
        pattern = pattern.strip().replace("\\", "/")
        if not pattern:
            continue
        directory = pattern.endswith("/")
        pattern = pattern.strip("/")
        if directory and (rel == pattern or rel.startswith(pattern + "/")):
            return True
        if fnmatch.fnmatch(rel, pattern) or fnmatch.fnmatch(Path(rel).name, pattern):
            return True
    return False


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _python_extract(text: str) -> tuple[list[Symbol], list[tuple[str, str]]]:
    tree = ast.parse(text)
    symbols: list[Symbol] = []
    imports: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ", ".join(arg.arg for arg in node.args.args)
            symbols.append(Symbol(node.name, "func", node.lineno, getattr(node, "end_lineno", node.lineno), f"{node.name}({args})"))
        elif isinstance(node, ast.ClassDef):
            symbols.append(Symbol(node.name, "class", node.lineno, getattr(node, "end_lineno", node.lineno), node.name))
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(getattr(node, "target", None), ast.Name):
            symbols.append(Symbol(node.target.id, "const", node.lineno, node.lineno, node.target.id))
        elif isinstance(node, ast.Import):
            imports.extend((alias.name, "import") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(("." * node.level + (node.module or ""), "import"))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            symbols.append(Symbol(node.func.id, "call", node.lineno, node.lineno, node.func.id))
    return symbols, imports


def _regex_extract(text: str, lang: str) -> tuple[list[Symbol], list[tuple[str, str]]]:
    symbols: list[Symbol] = []
    imports: list[tuple[str, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if lang in {"javascript", "typescript"}:
            for target in re.findall(r"(?:from\s+|import\s*)['\"]([^'\"]+)['\"]", line):
                imports.append((target, "import"))
            match = re.search(r"(?:export\s+)?(?:async\s+)?function\s+(\w+)", line)
            if match: symbols.append(Symbol(match.group(1), "func", lineno, lineno, match.group(1) + "()"))
            match = re.search(r"(?:export\s+)?class\s+(\w+)", line)
            if match: symbols.append(Symbol(match.group(1), "class", lineno, lineno, match.group(1)))
        elif lang == "dart":
            match = re.search(r"^\s*import\s+['\"]([^'\"]+)['\"]", line)
            if match: imports.append((match.group(1), "import"))
            match = re.search(r"^\s*class\s+(\w+)", line)
            if match: symbols.append(Symbol(match.group(1), "class", lineno, lineno, match.group(1)))
            match = re.search(r"^\s*(?:[\w<>?]+\s+)+(\w+)\s*\([^;]*\)\s*(?:\{|=>)", line)
            if match: symbols.append(Symbol(match.group(1), "func", lineno, lineno, match.group(1) + "()"))
        elif lang == "mql5":
            match = re.search(r"#include\s+[<\"]([^>\"]+)[>\"]", line)
            if match: imports.append((match.group(1), "include"))
    return symbols, imports


def extract(text: str, lang: str) -> tuple[list[Symbol], list[tuple[str, str]]]:
    if lang == "python":
        return _python_extract(text)
    if lang in {"javascript", "typescript", "dart", "mql5"}:
        return _regex_extract(text, lang)
    # Tier B deliberately has no symbols, but literal script references still
    # form useful dependency edges (notably shell/PowerShell dispatchers).
    return [], [(match, "include") for match in re.findall(r"(?<![\w.-])(scripts/[\w./-]+\.py)", text)]


def resolve_import(repo: Path, source: str, target: str, lang: str) -> str | None:
    source_path = Path(source)
    candidates: list[Path] = []
    if target.startswith("."):
        base = source_path.parent / target
        candidates.extend([base, base.with_suffix(".py"), base.with_suffix(".js"), base.with_suffix(".ts"), base / "__init__.py"])
    elif lang == "python":
        module = Path(*target.lstrip(".").split(".")).with_suffix(".py")
        # A common repository-local convention is ``scripts/a.py`` importing
        # its sibling as ``import b`` without package qualification.
        candidates.extend([source_path.parent / module, module])
    elif target.startswith("scripts/"):
        candidates.append(Path(target))
    for candidate in candidates:
        cleaned = slash(candidate).replace("//", "/").lstrip("/")
        if (repo / cleaned).is_file():
            return cleaned
    return None


def scan(repo: Path, full: bool = False) -> tuple[int, int, int]:
    repo = repo.resolve()
    con = connect(repo)
    init_schema(con)
    if full:
        con.executescript("DELETE FROM symbols; DELETE FROM edges; DELETE FROM files_fts; DELETE FROM files;")
    existing = {row["path"]: row for row in con.execute("SELECT id, path, content_hash FROM files")}
    seen: set[str] = set()
    scanned = changed = 0
    patterns = _ignore_patterns(repo)
    for path in repo.rglob("*"):
        if not path.is_file():
            continue
        rel = slash(path.relative_to(repo))
        if is_ignored(rel, patterns) or path.suffix.lower() not in TEXT_EXTENSIONS:
            continue
        try:
            raw = path.read_bytes()
            if b"\0" in raw:
                continue
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1; seen.add(rel)
        digest = hashlib.sha256(raw).hexdigest()
        row = existing.get(rel)
        if row and row["content_hash"] == digest:
            continue
        changed += 1
        lang = LANGUAGES.get(path.suffix.lower(), "text")
        con.execute("INSERT INTO files(path,lang,content_hash,loc,mtime,indexed_at) VALUES(?,?,?,?,?,?) ON CONFLICT(path) DO UPDATE SET lang=excluded.lang, content_hash=excluded.content_hash, loc=excluded.loc, mtime=excluded.mtime, indexed_at=excluded.indexed_at", (rel, lang, digest, len(text.splitlines()), path.stat().st_mtime, now()))
        file_id = con.execute("SELECT id FROM files WHERE path=?", (rel,)).fetchone()[0]
        con.execute("DELETE FROM symbols WHERE file_id=?", (file_id,)); con.execute("DELETE FROM edges WHERE src_file_id=?", (file_id,))
        con.execute("DELETE FROM files_fts WHERE path=?", (rel,)); con.execute("INSERT INTO files_fts(path,body) VALUES(?,?)", (rel, text))
        try:
            symbols, imports = extract(text, lang)
        except SyntaxError:
            symbols, imports = [], []
        con.executemany("INSERT INTO symbols(file_id,name,kind,line_start,line_end,signature) VALUES(?,?,?,?,?,?)", [(file_id, s.name, s.kind, s.start, s.end, s.signature) for s in symbols])
        for target, kind in imports:
            resolved = resolve_import(repo, rel, target, lang)
            destination = con.execute("SELECT id FROM files WHERE path=?", (resolved,)).fetchone() if resolved else None
            con.execute("INSERT INTO edges(src_file_id,dst_file_id,kind,detail) VALUES(?,?,?,?)", (file_id, destination[0] if destination else None, kind, target))
    removed = 0
    for rel, row in existing.items():
        if rel not in seen:
            con.execute("DELETE FROM files_fts WHERE path=?", (rel,)); con.execute("DELETE FROM files WHERE id=?", (row["id"],)); removed += 1
    # Imports can target a file scanned later; repair all resolvable destinations.
    for edge in con.execute("SELECT e.id, f.path, f.lang, e.detail FROM edges e JOIN files f ON f.id=e.src_file_id WHERE e.dst_file_id IS NULL"):
        resolved = resolve_import(repo, edge["path"], edge["detail"], edge["lang"])
        if resolved:
            target = con.execute("SELECT id FROM files WHERE path=?", (resolved,)).fetchone()
            if target: con.execute("UPDATE edges SET dst_file_id=? WHERE id=?", (target[0], edge["id"]))
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_full_scan',?)", (now(),))
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('last_scan_head',?)", (_git_head(repo),))
    con.commit(); con.close()
    return scanned, changed, removed


def _annotation(row: sqlite3.Row) -> str:
    if row["source_hash"] is None: return ""
    return " FRESH" if row["source_hash"] == row["content_hash"] else " STALE (source changed since card generated)"


def query(repo: Path, terms: str, limit: int) -> list[str]:
    con = connect(repo); init_schema(con)
    token = terms.strip()
    if not token: return []
    like = f"%{token}%"
    rows = con.execute("SELECT f.path, 1 AS line, f.content_hash, c.source_hash FROM files f LEFT JOIN cards c ON c.file_id=f.id WHERE f.path LIKE ? OR EXISTS(SELECT 1 FROM files_fts x WHERE x.path=f.path AND x.body LIKE ?) ORDER BY f.path LIMIT ?", (like, like, limit)).fetchall()
    results = [f"{r['path']}:{r['line']}{_annotation(r)}" for r in rows]
    remaining = max(0, limit - len(results))
    if remaining:
        for r in con.execute("SELECT f.path, s.line_start, s.kind, s.name, f.content_hash, c.source_hash FROM symbols s JOIN files f ON f.id=s.file_id LEFT JOIN cards c ON c.file_id=f.id WHERE s.name LIKE ? ORDER BY f.path,s.line_start LIMIT ?", (like, remaining)):
            results.append(f"{r['path']}:{r['line_start']} {r['kind']} {r['name']}{_annotation(r)}")
    remaining = max(0, limit - len(results))
    if remaining:
        for r in con.execute("SELECT ref, 1 AS line FROM episodes WHERE body_fts LIKE ? ORDER BY ref LIMIT ?", (like, remaining)):
            results.append(f"{slash(r['ref'])}:{r['line']} episode")
    con.close(); return results


def where(repo: Path, name: str) -> list[str]:
    con = connect(repo); init_schema(con)
    result = []
    for r in con.execute("SELECT f.path,s.line_start,s.kind,s.signature FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.name=? AND s.kind!='call' ORDER BY f.path,s.line_start", (name,)):
        result.append(f"{r['path']}:{r['line_start']} {r['kind']} {r['signature']}")
    for r in con.execute("SELECT f.path,s.line_start FROM symbols s JOIN files f ON f.id=s.file_id WHERE s.name=? AND s.kind='call' ORDER BY f.path,s.line_start", (name,)):
        result.append(f"{r['path']}:{r['line_start']} caller {name}")
    con.close(); return result


def impact(repo: Path, path: str, hops: int) -> list[str]:
    con = connect(repo); init_schema(con)
    normalized = slash(path).lstrip("./")
    start = con.execute("SELECT id,path FROM files WHERE path=?", (normalized,)).fetchone()
    if not start: return []
    frontier = {start["id"]}; seen = set(frontier); result: list[str] = []
    for _ in range(max(1, hops)):
        marks = ",".join("?" for _ in frontier)
        rows = con.execute(f"SELECT DISTINCT src.id,src.path FROM edges e JOIN files src ON src.id=e.src_file_id WHERE e.dst_file_id IN ({marks})", tuple(frontier)).fetchall()
        frontier = set()
        for row in rows:
            if row["id"] not in seen:
                seen.add(row["id"]); frontier.add(row["id"]); result.append(row["path"])
        if not frontier: break
    con.close(); return sorted(result)


CARDS_OPT_IN_HINT = (
    "cards: 0 (generation is opt-in and has never run — python scripts/atlas.py cards --generate)"
)


def _git_env(repo: Path) -> dict[str, str]:
    """Isolate git to *repo* so a parent checkout is never inherited."""
    env = os.environ.copy()
    for key in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        "GIT_COMMON_DIR",
        "GIT_PREFIX",
    ):
        env.pop(key, None)
    ceiling = str(repo.resolve().parent)
    prior = env.get("GIT_CEILING_DIRECTORIES", "")
    env["GIT_CEILING_DIRECTORIES"] = os.pathsep.join(p for p in (ceiling, prior) if p)
    return env


def _run_git(repo: Path, args: list[str]) -> str | None:
    """Return stdout of a git invocation, or None when git is unavailable/fails."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo,
            env=_git_env(repo),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _git_head(repo: Path) -> str:
    out = _run_git(repo, ["rev-parse", "HEAD"])
    return (out or "").strip()


def _git_tracked_paths(repo: Path) -> list[str] | None:
    out = _run_git(repo, ["ls-files", "-z"])
    if out is None:
        return None
    paths = []
    for raw in out.split("\0"):
        if not raw:
            continue
        rel = slash(raw)
        while rel.startswith("./"):
            rel = rel[2:]
        paths.append(rel)
    return paths


def _commits_since_scan(repo: Path, last_head: str) -> str:
    if not last_head:
        return "n/a"
    out = _run_git(repo, ["rev-list", "--count", f"{last_head}..HEAD"])
    if out is None:
        return "n/a"
    text = out.strip()
    return text if text else "n/a"


def _tracked_delta_line(repo: Path, indexed_count: int, indexed_paths: set[str]) -> str:
    tracked = _git_tracked_paths(repo)
    if tracked is None:
        return "tracked files: n/a"
    patterns = _ignore_patterns(repo)
    # Match scanner membership (ignore rules + text extensions) so a fresh
    # scan on a clean tree can report "in sync" — git tracks non-text files
    # the scanner never indexes.
    eligible = []
    for rel in tracked:
        if is_ignored(rel, patterns):
            continue
        if Path(rel).suffix.lower() not in TEXT_EXTENSIONS:
            continue
        eligible.append(rel)
    n = len(eligible)
    missing = sum(1 for rel in eligible if rel not in indexed_paths)
    if missing == 0:
        return f"tracked files: {n} (git) vs {indexed_count} indexed — in sync"
    return f"tracked files: {n} (git) vs {indexed_count} indexed — {missing} not indexed"


def status(repo: Path) -> list[str]:
    con = connect(repo); init_schema(con)
    files = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    cards = con.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
    stale = con.execute("SELECT COUNT(*) FROM cards c JOIN files f ON f.id=c.file_id WHERE c.source_hash != f.content_hash").fetchone()[0]
    last = con.execute("SELECT value FROM meta WHERE key='last_full_scan'").fetchone()
    head_row = con.execute("SELECT value FROM meta WHERE key='last_scan_head'").fetchone()
    indexed_paths = {row[0] for row in con.execute("SELECT path FROM files")}
    con.close()
    size = db_path(repo).stat().st_size if db_path(repo).exists() else 0
    cards_line = CARDS_OPT_IN_HINT if cards == 0 else f"cards: {cards}"
    last_head = head_row[0] if head_row else ""
    return [
        f"files: {files}",
        cards_line,
        f"stale cards: {stale}",
        f"db size: {size}",
        f"last scan: {last[0] if last else 'never'}",
        _tracked_delta_line(repo, files, indexed_paths),
        f"commits since last scan: {_commits_since_scan(repo, last_head)}",
    ]


def _repo_arg(value: str | None) -> Path:
    return Path(value or ".").resolve()


def cmd_scan(args: argparse.Namespace) -> int:
    scanned, changed, removed = scan(_repo_arg(args.repo), args.full)
    print(f"files scanned: {scanned}; changed: {changed}; removed: {removed}"); return 0

def cmd_query(args: argparse.Namespace) -> int:
    print("\n".join(query(Path.cwd(), args.terms, args.limit))); return 0

def cmd_where(args: argparse.Namespace) -> int:
    print("\n".join(where(Path.cwd(), args.symbol))); return 0

def cmd_impact(args: argparse.Namespace) -> int:
    print("\n".join(impact(Path.cwd(), args.path, args.hops))); return 0

def cmd_status(args: argparse.Namespace) -> int:
    print("\n".join(status(Path.cwd()))); return 0

def register(subparsers: argparse._SubParsersAction) -> None:
    scan_p = subparsers.add_parser("scan", help="build or update the project map")
    scan_p.add_argument("--full", action="store_true"); scan_p.add_argument("--repo"); scan_p.set_defaults(handler=cmd_scan)
    query_p = subparsers.add_parser("query", help="search files, symbols, and episodes")
    query_p.add_argument("terms"); query_p.add_argument("--limit", type=int, default=20); query_p.set_defaults(handler=cmd_query)
    where_p = subparsers.add_parser("where", help="find a symbol and direct callers")
    where_p.add_argument("symbol"); where_p.set_defaults(handler=cmd_where)
    impact_p = subparsers.add_parser("impact", help="find reverse dependencies")
    impact_p.add_argument("path"); impact_p.add_argument("--hops", type=int, default=1); impact_p.set_defaults(handler=cmd_impact)
    status_p = subparsers.add_parser("status", help="show map freshness")
    status_p.set_defaults(handler=cmd_status)
