#!/usr/bin/env python3
"""OMLCP stream protocol: parse a long multi-file generation and materialize it safely.

Background
----------
OMLCP (Output-Maximizing Long-Context Programming, Viviers 2026) generates a whole
artifact in one or a few long model outputs instead of many short agent turns. The
paper's own failure analysis (§11.4) is that the expensive failures are not model
failures but *stream* failures: a protocol mismatch corrupted punctuation during
stream reconstruction and forced a full re-stream. This module is the deterministic
half that makes that class of failure cheap:

* The model emits files between **nonce-delimited marker lines**. The nonce is random
  per packet, so file *content* can never accidentally contain a marker.
* A file only counts once its END marker arrives. A truncated tail file is discarded,
  never written half-done, and becomes the continuation point.
* Every path is checked before a byte is written: relative, no traversal, no Windows
  reserved names, inside the manifest, inside Owned_Paths, outside protected paths.
* Writes are atomic (temp file + ``os.replace``), so an interrupted materialize never
  leaves a truncated file on disk.

Wire format (column 0, one marker per line)::

    @@OMLCP <nonce> FILE <relative/path>
    ...file content, verbatim...
    @@OMLCP <nonce> END <relative/path>
    @@OMLCP <nonce> NOTE <free text, logged, never written>
    @@OMLCP <nonce> DONE

The runner itself may insert ``@@OMLCP <nonce> SEAM`` where the transport stitched two model
messages together (Claude Code silently continues a reply that hit its output cap, and the
continuation can restart the interrupted line). A file open at a seam cannot be trusted: it is
closed as incomplete and its remaining lines are discarded until the next FILE marker, so the
continuation regenerates it from line 1.

Anything outside a FILE block is "noise" (model chatter, code fences) and is reported
as a warning, not written. CRLF is normalised to LF.

Pure stdlib. Imported by ``omlcp.py``; usable on its own.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path

MARKER = "@@OMLCP"
NONCE_RE = re.compile(r"^[0-9a-f]{8,32}$")

# Mirrors hooks/lib.js PROTECTED_FOR_BUILDERS (a parity test pins the two together).
# Generated output may never land here unless the task's Protected_Grants names it.
PROTECTED_FOR_BUILDERS = [
    "specs/**", "AGENTS.md", "CLAUDE.md", "docs/**", "REVIEW.md",
    ".claude/**", ".codex/**", "scripts/**", "hooks/**", "briefings/**", "onboard.md",
    "autopilot.json", "AUTOPILOT_LOG.md", "deploy/**",
    "INSTINCTS.md", ".devteam/pending_amendments/**",
]
# Never writable by a generator, whatever the grants say: coordination state and git.
ALWAYS_PROTECTED = ["PLAN.md", ".git/**", ".devteam/**", "dossiers/**"]

_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                     *(f"LPT{i}" for i in range(1, 10))}
_MAX_PATH_LEN = 240


def new_nonce() -> str:
    """A fresh 12-hex-char nonce for one generation packet."""
    return secrets.token_hex(6)


# ---------------------------------------------------------------- glob matching --
def _brace_expand(glob: str) -> list[str]:
    m = re.search(r"\{([^{}]*)\}", glob)
    if not m:
        return [glob]
    out: list[str] = []
    for alt in m.group(1).split(","):
        out.extend(_brace_expand(glob[: m.start()] + alt + glob[m.end():]))
    return out


def _glob_to_regex(glob: str) -> re.Pattern[str]:
    i, out = 0, []
    while i < len(glob):
        c = glob[i]
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        elif c == "[":
            j = glob.find("]", i)
            if j == -1:
                out.append(re.escape(c))
                i += 1
            else:
                body = glob[i + 1 : j].replace("!", "^", 1) if glob[i + 1 : i + 2] == "!" else glob[i + 1 : j]
                out.append(f"[{body}]")
                i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def glob_match(path: str, glob: str) -> bool:
    """Strict glob match: ``*`` never crosses ``/``, ``**`` does, ``{a,b}`` expands.
    A glob naming a directory (``dir/``) matches everything beneath it."""
    glob = glob.strip()
    if not glob:
        return False
    if glob.endswith("/"):
        glob += "**"
    return any(_glob_to_regex(g).match(path) for g in _brace_expand(glob))


def glob_prefix_match(path: str, glob: str) -> bool:
    """Prefix semantics, identical to hooks/lib.js ``pathInGlob``: truncate the glob at
    its first wildcard and prefix-match. Deliberately broad; used for *protection*,
    where matching too much is the safe direction."""
    glob = glob.strip()
    cut = len(glob)
    for ch in "*?[{":
        k = glob.find(ch)
        if k != -1:
            cut = min(cut, k)
    prefix = glob[:cut]
    if cut == len(glob):
        return path == glob or path.startswith(glob.rstrip("/") + "/")
    return path.startswith(prefix)


# ----------------------------------------------------------------- path safety --
def path_problems(path: str) -> list[str]:
    """Structural problems with a generated path, independent of any policy."""
    problems: list[str] = []
    if not path:
        return ["empty path"]
    if len(path) > _MAX_PATH_LEN:
        problems.append(f"longer than {_MAX_PATH_LEN} characters")
    if "\\" in path:
        problems.append("backslash in path (use forward slashes)")
    if path.startswith("/") or re.match(r"^[A-Za-z]:", path):
        problems.append("absolute path")
    if any(ord(ch) < 32 or ch == "\x7f" for ch in path):
        problems.append("control character in path")
    if re.search(r"[<>:\"|?*]", path):
        problems.append("character illegal on Windows (<>:\"|?*)")
    for seg in path.split("/"):
        if seg in ("", ".", ".."):
            problems.append(f"empty, '.' or '..' segment ({path!r})")
            break
        if seg.split(".")[0].upper() in _WINDOWS_RESERVED:
            problems.append(f"Windows reserved name {seg!r}")
        if seg.endswith((" ", ".")):
            problems.append(f"segment {seg!r} ends with a space or dot (illegal on Windows)")
    return problems


@dataclass
class PathPolicy:
    """Where a generation may write. ``manifest`` is the contract; ``owned`` and
    ``protected``/``grants`` are the DEVDEPARTMENT territory rules layered on top."""

    manifest: list[str]
    owned: list[str] = field(default_factory=list)  # empty = standalone mode, manifest only
    grants: list[str] = field(default_factory=list)
    protected: list[str] = field(default_factory=lambda: list(PROTECTED_FOR_BUILDERS))

    def violations(self, path: str) -> list[str]:
        out = path_problems(path)
        if out:
            return out
        if any(glob_prefix_match(path, g) for g in ALWAYS_PROTECTED):
            return [f"{path} is coordination/VCS state; a generator may never write it"]
        if path not in self.manifest:
            out.append(f"{path} is not in the generation manifest")
        if self.owned and not any(glob_match(path, g) for g in self.owned):
            out.append(f"{path} is outside the task's Owned_Paths")
        if any(glob_prefix_match(path, g) for g in self.protected) and not any(
            glob_match(path, g) for g in self.grants
        ):
            out.append(f"{path} is a protected path and the task has no Protected_Grants for it")
        return out


# ---------------------------------------------------------------------- parsing --
@dataclass
class ParsedFile:
    path: str
    content: str
    complete: bool
    start_line: int
    segment: int = 0

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()


@dataclass
class ParseResult:
    files: list[ParsedFile] = field(default_factory=list)
    done: bool = False
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    noise_lines: int = 0

    def complete_files(self) -> dict[str, ParsedFile]:
        """First complete version of each path wins (deterministic under repeats)."""
        out: dict[str, ParsedFile] = {}
        for f in self.files:
            if f.complete and f.path not in out:
                out[f.path] = f
        return out

    def trailing_incomplete(self) -> ParsedFile | None:
        if self.files and not self.files[-1].complete:
            return self.files[-1]
        return None


class StreamParser:
    """Incremental parser. ``feed`` arbitrary chunks (they may split lines anywhere),
    then ``close``. Segments (initial generation + continuations) are fed in order with
    ``begin_segment`` between them; a FILE left open at a segment boundary is closed as
    incomplete, because a continuation is instructed to restart that file from line 1."""

    def __init__(self, nonce: str):
        if not NONCE_RE.match(nonce):
            raise ValueError(f"invalid nonce {nonce!r}")
        self.nonce = nonce
        self._prefix = f"{MARKER} {nonce} "
        self._buf = ""
        self._line_no = 0
        self._segment = 0
        self._open: ParsedFile | None = None
        self._lines: list[str] = []
        self._discarding = False  # after a SEAM inside a file, until the next FILE marker
        self.result = ParseResult()

    # -- public -----------------------------------------------------------
    def feed(self, chunk: str) -> None:
        self._buf += chunk.replace("\r\n", "\n").replace("\r", "\n")
        *lines, self._buf = self._buf.split("\n")
        for line in lines:
            self._line(line)

    def begin_segment(self) -> None:
        self._flush_partial_line()
        self._close_open(complete=False, reason="segment ended inside this file")
        self._discarding = False
        self._segment += 1

    def close(self) -> ParseResult:
        self._flush_partial_line()
        self._close_open(complete=False, reason="stream ended inside this file")
        return self.result

    # -- internals ----------------------------------------------------------
    def _flush_partial_line(self) -> None:
        if self._buf:
            line, self._buf = self._buf, ""
            self._line(line, partial=True)

    def _line(self, line: str, partial: bool = False) -> None:
        self._line_no += 1
        if line.startswith(self._prefix):
            self._marker(line[len(self._prefix):].strip())
            return
        if line.startswith(MARKER + " ") and self._open is None:
            self.result.warnings.append(
                f"line {self._line_no}: marker with a foreign nonce ignored: {line[:60]!r}")
        if self._open is not None:
            # A final line with no newline (partial) inside an open file belongs to a file
            # that will be discarded as incomplete anyway; it is kept for diagnostics.
            self._lines.append(line)
        elif line.strip() and not self._discarding:
            self.result.noise_lines += 1

    def _marker(self, rest: str) -> None:
        kind, _, arg = rest.partition(" ")
        arg = arg.strip()
        if kind == "SEAM":
            if self._open is not None:
                self._close_open(complete=False, reason="transport seam inside this file; it will be regenerated")
                self._discarding = True
            return
        if kind == "FILE":
            self._discarding = False
            if self._open is not None:
                self._close_open(complete=False, reason=f"new FILE {arg!r} began before END")
            if not arg:
                self.result.errors.append(f"line {self._line_no}: FILE marker without a path")
                return
            self._open = ParsedFile(arg, "", False, self._line_no, self._segment)
            self._lines = []
        elif kind == "END":
            if self._open is None:
                if not self._discarding:
                    self.result.warnings.append(f"line {self._line_no}: END {arg!r} with no open FILE")
                self._discarding = False
                return
            if arg and arg != self._open.path:
                self.result.errors.append(
                    f"line {self._line_no}: END {arg!r} does not match open FILE {self._open.path!r}")
                self._close_open(complete=False, reason="mismatched END")
                return
            self._close_open(complete=True)
        elif kind == "NOTE":
            self.result.notes.append(arg)
        elif kind == "DONE":
            if self._open is not None:
                self._close_open(complete=False, reason="DONE arrived inside an open FILE")
            self.result.done = True
        else:
            self.result.warnings.append(f"line {self._line_no}: unknown marker kind {kind!r}")

    def _close_open(self, complete: bool, reason: str = "") -> None:
        if self._open is None:
            return
        lines = _strip_wrapping_fence(self._lines, self.result.warnings, self._open.path)
        self._open.content = ("\n".join(lines) + "\n") if lines else ""
        self._open.complete = complete
        if not complete and reason:
            self.result.warnings.append(f"{self._open.path}: incomplete ({reason})")
        self.result.files.append(self._open)
        self._open, self._lines = None, []


_FENCE_OPEN = re.compile(r"^\s*```[\w.+-]*\s*$")


def _strip_wrapping_fence(lines: list[str], warnings: list[str], path: str) -> list[str]:
    """Models sometimes wrap a file's content in a markdown fence despite instructions.
    Strip exactly one wrapping fence pair (first and last line), and say so."""
    if len(lines) >= 2 and _FENCE_OPEN.match(lines[0]) and lines[-1].strip() == "```":
        warnings.append(f"{path}: stripped a wrapping markdown code fence")
        return lines[1:-1]
    return lines


def parse_segments(nonce: str, segments: list[str]) -> ParseResult:
    parser = StreamParser(nonce)
    for i, seg in enumerate(segments):
        if i:
            parser.begin_segment()
        parser.feed(seg)
    return parser.close()


# ---------------------------------------------------------------- materializing --
@dataclass
class MaterializeResult:
    written: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    rejected: dict[str, list[str]] = field(default_factory=dict)  # manifest files refused (blocking)
    extraneous: dict[str, list[str]] = field(default_factory=dict)  # non-manifest files, never written
    pending: list[str] = field(default_factory=list)  # manifest files with no complete version
    resume_from: str | None = None
    done_marker: bool = False
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    hashes: dict[str, str] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.done_marker and not self.pending and not self.rejected and not self.errors

    def to_dict(self) -> dict:
        d = asdict(self)
        d["complete"] = self.complete
        return d


def _atomic_write(target: Path, content: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".omlcp-", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def materialize(parsed: ParseResult, policy: PathPolicy, root: Path, *,
                owned_hashes: dict[str, str] | None = None, dry_run: bool = False,
                allow_overwrite_foreign: bool = False) -> MaterializeResult:
    """Write every complete, policy-clean file under ``root``.

    ``owned_hashes`` maps paths this task's earlier materialize runs wrote to the hash
    they wrote. A pre-existing file is only overwritten when it is one of those and is
    still byte-identical to what we wrote (i.e. nobody repaired it by hand since);
    anything else is "foreign" and refused — the generate lane creates new files, it
    does not edit existing code (paper §11.9; brownfield work stays agentic).
    """
    owned_hashes = dict(owned_hashes or {})
    res = MaterializeResult(done_marker=parsed.done, warnings=list(parsed.warnings),
                            errors=list(parsed.errors))
    complete = parsed.complete_files()
    seen_paths = {f.path for f in parsed.files}
    for f in parsed.files:
        if f.complete and complete.get(f.path) is not f:
            res.warnings.append(f"{f.path}: repeated in segment {f.segment}; first complete version kept")
    for path in sorted(seen_paths - set(policy.manifest)):
        res.extraneous[path] = policy.violations(path) or ["not in manifest"]
        res.warnings.append(f"{path}: emitted but not in the manifest; not written")

    root = root.resolve()
    for path, pf in complete.items():
        if path not in policy.manifest:
            continue  # already reported as extraneous
        problems = policy.violations(path)
        if problems:
            res.rejected[path] = problems
            continue
        target = (root / path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            res.rejected[path] = ["resolves outside the target root (symlink?)"]
            continue
        if target.exists():
            current = hashlib.sha256(target.read_bytes()).hexdigest()
            if current == pf.sha256:
                res.unchanged.append(path)
                res.hashes[path] = current
                continue
            ours = owned_hashes.get(path)
            if ours != current and not allow_overwrite_foreign:
                why = ("was edited after this task generated it (repair work is protected)"
                       if ours else "already exists and was not created by this task's generation")
                res.rejected[path] = [f"{path} {why}"]
                continue
        if not dry_run:
            _atomic_write(target, pf.content)
        res.written.append(path)
        res.hashes[path] = pf.sha256

    done_paths = set(res.written) | set(res.unchanged)
    res.pending = [p for p in policy.manifest if p not in done_paths and p not in res.rejected]
    tail = parsed.trailing_incomplete()
    if tail is not None and tail.path in res.pending:
        res.resume_from = tail.path
    elif res.pending:
        res.resume_from = res.pending[0]
    if parsed.noise_lines:
        res.warnings.append(f"{parsed.noise_lines} non-empty line(s) outside FILE blocks were ignored")
    return res


# ----------------------------------------------------------------------- state --
@dataclass
class RunState:
    """Persisted beside the raw segments in ``.devteam/omlcp/<TASK>/state.json``."""

    task_id: str
    nonce: str
    segments: list[str] = field(default_factory=list)  # file names, in order
    session_id: str | None = None
    written_hashes: dict[str, str] = field(default_factory=dict)
    complete: bool = False
    pending: list[str] = field(default_factory=list)
    resume_from: str | None = None
    target_root: str = ""

    @classmethod
    def load(cls, path: Path) -> "RunState":
        data = json.loads(path.read_text(encoding="utf-8"))
        known = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, json.dumps(asdict(self), indent=2, sort_keys=True) + "\n")
