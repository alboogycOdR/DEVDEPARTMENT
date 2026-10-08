#!/usr/bin/env python3
"""OMLCP generation runner: adapters, bounded continuation, crash-safe resume, ledger.

The runner turns a packet into files on disk:

1. Each generation *segment* streams to ``segment-N.raw`` as it arrives (flushed per
   chunk), so a dropped connection loses nothing already received.
2. After every segment, all raw segments are re-parsed and materialized. Completed
   files land on disk immediately; a truncated tail file is discarded.
3. If the stream did not finish, a continuation resumes at the first unfinished file —
   never a full re-generation (the paper's §11.4 SSE incident cost a full re-stream).
4. Continuations are bounded (``max_continuations``) and stop early on stagnation
   (a segment that completes no new file), mirroring Wave D's "no third identical retry".
5. ``omlcp run`` on a task that already has state *resumes* it; ``--fresh`` restarts.
6. Every segment appends one line to ``.devteam/ledger.jsonl`` in the Wave F (F2) ledger
   shape plus OMLCP fields, so the paper's §6.4 reproducibility metrics fall out of data.

Adapters
--------
* ``claude-cli`` — ``claude -p`` with every tool disabled, ``stream-json`` partial
  messages, a replacement system prompt, run from an empty temp directory so no
  CLAUDE.md, hooks or MCP servers load. Uses the subscription login (``--bare`` would
  require an API key). Continuation uses ``--resume <session_id>``.
* ``anthropic-api`` — Messages API over SSE with a byte-level incremental UTF-8 decoder
  (multi-byte characters split across network chunks are the classic corruption bug).
  Needs ``ANTHROPIC_API_KEY``.
* ``command`` — any argv (Codex, Grok, a local model); stdout is the raw stream.
* ``manual`` — no model call: you paste a stream you produced elsewhere into
  ``segment-N.raw`` and run ``omlcp materialize``.
"""

from __future__ import annotations

import codecs
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parent))

from omlcp_economics import usage_cost  # noqa: E402
from omlcp_packet import Packet, continuation_prompt  # noqa: E402
from omlcp_stream import MARKER, MaterializeResult, PathPolicy, RunState, materialize, parse_segments  # noqa: E402


class GenerationError(RuntimeError):
    pass


@dataclass
class GenResult:
    text: str = ""
    session_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None
    stop_reason: str | None = None
    subtype: str | None = None
    error: str | None = None
    seams: int = 0


@dataclass
class Continuation:
    """Everything an adapter may need to continue: the session to resume, the original
    packet, the text produced so far, and the continuation instruction."""

    session_id: str | None
    packet_prompt: str
    prior_text: str
    instruction: str
    stateless_prompt: str


# A CLI started from inside a Claude Code session (ORCH running `omlcp stage`, a builder running
# a script) inherits that session's identity through these variables, reports the PARENT's
# session_id, and `--resume <that id>` would then append to the parent conversation. Observed
# live on Claude Code 2.1.291 (2026-10-06). Authentication variables are left alone.
SESSION_BOUND_ENV = (
    "CLAUDECODE", "CLAUDE_PID", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_CHILD_SESSION",
    "CLAUDE_CODE_SESSION_ATTENDED", "CLAUDE_CODE_SESSION_ORIGIN", "CLAUDE_CODE_ENTRYPOINT",
    "CLAUDE_CODE_REMOTE_SESSION_ID", "CLAUDE_CODE_REMOTE_SDK_URL", "CLAUDE_CODE_TEE_SDK_STDOUT",
    "CLAUDE_CODE_MESSAGING_SOCKET", "CLAUDE_CODE_MESSAGING_TOKEN", "CLAUDE_CODE_POST_TURN_MEMORY",
    "CLAUDE_CODE_POST_TURN_MEMORY_CONFIG", "CLAUDE_AFTER_LAST_COMPACT", "CLAUDE_CODE_DIAGNOSTICS_FILE",
    "CLAUDE_CODE_INCLUDE_PARTIAL_MESSAGES",
)


def child_env(extra: dict | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in SESSION_BOUND_ENV}
    env.update(extra or {})
    return env


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------ adapters --
class Adapter:
    name = "base"
    supports_resume = False

    def generate(self, system: str, prompt: str, on_text: Callable[[str], None],
                 cont: Continuation | None = None,
                 on_seam: Callable[[], None] | None = None) -> GenResult:  # pragma: no cover - interface
        """Stream the model's text through ``on_text``. Call ``on_seam`` wherever the transport
        joined two separate model messages into this one reply."""
        raise NotImplementedError


class ClaudeCliAdapter(Adapter):
    name = "claude-cli"
    supports_resume = True

    def __init__(self, cfg: dict):
        self.cli = cfg.get("cli") or "claude"
        self.model = cfg.get("model")
        self.effort = cfg.get("effort")
        self.max_output = int(cfg.get("max_output_tokens") or 64000)
        self.timeout = int(cfg.get("timeout_seconds") or 3600)

    def argv(self, system_file: str, resume: str | None) -> list[str]:
        # ``cli`` is a command name ("claude") or an argv prefix list (["node", "cli.js"]).
        prefix = list(self.cli) if isinstance(self.cli, (list, tuple)) else [self.cli]
        prefix[0] = shutil.which(prefix[0]) or prefix[0]
        argv = [*prefix, "-p", "--output-format", "stream-json", "--verbose",
                "--include-partial-messages", "--tools", "", "--disallowedTools", "mcp__*",
                "--system-prompt-file", system_file]
        if self.model:
            argv += ["--model", self.model]
        if self.effort:
            argv += ["--effort", self.effort]
        if resume:
            argv += ["--resume", resume]
        return argv

    def env(self) -> dict:
        return child_env({"CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(self.max_output), "DEVTEAM_DELEGATED": "1"})

    def generate(self, system, prompt, on_text, cont=None, on_seam=None):
        resume = cont.session_id if cont and cont.session_id else None
        stdin_text = cont.instruction if resume else (cont.stateless_prompt if cont else prompt)
        workdir = tempfile.mkdtemp(prefix="omlcp-gen-")  # outside the repo: no CLAUDE.md/hooks
        try:
            sys_file = os.path.join(workdir, "system.md")
            with open(sys_file, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(system)
            proc = subprocess.Popen(self.argv(sys_file, resume), cwd=workdir, env=self.env(),
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding="utf-8",
                                    errors="replace", bufsize=1)
            return self._drive(proc, stdin_text, on_text, on_seam)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _drive(self, proc: subprocess.Popen, stdin_text: str, on_text, on_seam=None) -> GenResult:
        timer = threading.Timer(self.timeout, proc.kill)
        timer.start()
        stderr_chunks: list[str] = []
        t_err = threading.Thread(target=lambda: stderr_chunks.append(proc.stderr.read() or ""), daemon=True)
        t_err.start()
        try:
            try:
                proc.stdin.write(stdin_text)
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
            res = parse_claude_stream_json(proc.stdout, on_text, on_seam)
            rc = proc.wait()
        finally:
            timer.cancel()
        t_err.join(timeout=5)
        if rc != 0 and not res.error:
            tail = "".join(stderr_chunks).strip()[-500:]
            res.error = f"generator CLI exited {rc}" + (f": {tail}" if tail else "")
        return res


def parse_claude_stream_json(lines: Iterable[str], on_text: Callable[[str], None],
                             on_seam: Callable[[], None] | None = None) -> GenResult:
    """Parse ``claude -p --output-format stream-json --include-partial-messages``.

    Text arrives as ``stream_event`` → ``content_block_delta``/``text_delta``. Older CLIs
    without partial messages only send whole ``assistant`` messages; those are used only
    when no delta was seen, so text is never doubled.

    Every ``message_start`` after the first is a seam: Claude Code hit the output cap and
    silently asked the model to continue in a new message (verified live: two ``max_tokens``
    stops, three messages, one ``result``). The continuation may restart the interrupted line,
    so the seam is reported and the parser regenerates the file it fell inside."""
    res = GenResult()
    got_delta = False
    messages = 0
    whole: list[str] = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except json.JSONDecodeError:
            continue
        kind = ev.get("type")
        if kind == "stream_event":
            inner = ev.get("event") or {}
            if inner.get("type") == "message_start":
                messages += 1
                if messages > 1:
                    res.seams += 1
                    if on_seam:
                        on_seam()
            elif inner.get("type") == "content_block_delta":
                delta = inner.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    got_delta = True
                    res.text += delta["text"]
                    on_text(delta["text"])
            elif inner.get("type") == "message_delta":
                sr = (inner.get("delta") or {}).get("stop_reason")
                if sr:
                    res.stop_reason = sr
        elif kind == "assistant":
            for block in (ev.get("message") or {}).get("content") or []:
                if block.get("type") == "text" and block.get("text"):
                    whole.append(block["text"])
        elif kind == "system" and ev.get("subtype") == "api_retry" and ev.get("error") == "max_output_tokens":
            res.stop_reason = "max_tokens"
        elif kind == "result":
            res.session_id = ev.get("session_id") or res.session_id
            res.cost_usd = ev.get("total_cost_usd")
            res.subtype = ev.get("subtype")
            res.stop_reason = ev.get("stop_reason") or res.stop_reason
            u = ev.get("usage") or {}
            res.input_tokens = int(u.get("input_tokens") or 0)
            res.output_tokens = int(u.get("output_tokens") or 0)
            res.cache_read_tokens = int(u.get("cache_read_input_tokens") or 0)
            res.cache_write_tokens = int(u.get("cache_creation_input_tokens") or 0)
            if ev.get("is_error"):
                res.error = str(ev.get("result") or ev.get("subtype") or "error")
            elif not got_delta and not whole and ev.get("result"):
                whole.append(str(ev["result"]))
        if kind in ("system",) and ev.get("session_id") and not res.session_id:
            res.session_id = ev["session_id"]
    if not got_delta and whole:
        res.text = "".join(whole)
        on_text(res.text)
    return res


class SSEDecoder:
    """Server-sent events from raw bytes. Byte-level incremental UTF-8 decoding means a
    multi-byte character split across two network chunks is reassembled, not replaced
    with U+FFFD; CR, LF and CRLF line endings are all honoured (CR+LF split across
    chunks included)."""

    def __init__(self):
        self._dec = codecs.getincrementaldecoder("utf-8")(errors="strict")
        self._buf = ""
        self._event = ""
        self._data: list[str] = []
        self._pending_cr = False

    def feed(self, chunk: bytes) -> list[tuple[str, str]]:
        text = self._dec.decode(chunk)
        if self._pending_cr and text.startswith("\n"):
            text = text[1:]
        self._pending_cr = text.endswith("\r")
        self._buf += text.replace("\r\n", "\n").replace("\r", "\n")
        events = []
        *lines, self._buf = self._buf.split("\n")
        for line in lines:
            ev = self._line(line)
            if ev:
                events.append(ev)
        return events

    def close(self) -> list[tuple[str, str]]:
        tail = self._dec.decode(b"", final=True)
        out = self.feed(tail.encode("utf-8")) if tail else []
        if self._buf:
            ev = self._line(self._buf)
            self._buf = ""
            if ev:
                out.append(ev)
        ev = self._line("")
        if ev:
            out.append(ev)
        return out

    def _line(self, line: str):
        if line == "":
            if not self._data and not self._event:
                return None
            ev = (self._event or "message", "\n".join(self._data))
            self._event, self._data = "", []
            return ev
        if line.startswith(":"):
            return None
        name, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if name == "event":
            self._event = value
        elif name == "data":
            self._data.append(value)
        return None


Transport = Callable[[str, dict, bytes, int], Iterable[bytes]]


def urllib_transport(url: str, headers: dict, body: bytes, timeout: int) -> Iterable[bytes]:
    import urllib.error
    import urllib.request

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    return
                yield chunk
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise GenerationError(f"HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise GenerationError(f"network error: {exc.reason}") from exc


class AnthropicApiAdapter(Adapter):
    name = "anthropic-api"
    supports_resume = True

    def __init__(self, cfg: dict, transport: Transport | None = None, api_key: str | None = None):
        self.model = cfg.get("model") or "claude-sonnet-5"
        self.max_output = int(cfg.get("max_output_tokens") or 64000)
        self.base = (cfg.get("api_base") or "https://api.anthropic.com").rstrip("/")
        self.version = cfg.get("api_version") or "2023-06-01"
        self.timeout = int(cfg.get("timeout_seconds") or 3600)
        self.extra = cfg.get("api_extra_body") or {}
        self.transport = transport or urllib_transport
        self.api_key = api_key if api_key is not None else os.environ.get("ANTHROPIC_API_KEY", "")

    def body(self, system: str, prompt: str, cont: Continuation | None) -> dict:
        first = {"role": "user", "content": [{"type": "text", "text": prompt,
                                               "cache_control": {"type": "ephemeral"}}]}
        messages = [first]
        if cont:
            prior = cont.prior_text.rstrip()
            if prior:
                messages.append({"role": "assistant", "content": prior})
                messages.append({"role": "user", "content": cont.instruction})
            else:
                messages = [{"role": "user", "content": cont.stateless_prompt}]
        body = {"model": self.model, "max_tokens": self.max_output, "stream": True,
                "system": system, "messages": messages}
        body.update(self.extra)
        return body

    def generate(self, system, prompt, on_text, cont=None, on_seam=None):
        if not self.api_key:
            raise GenerationError("ANTHROPIC_API_KEY is not set (the anthropic-api adapter bills the API, "
                                  "not a subscription; use adapter 'claude-cli' for subscription login)")
        headers = {"x-api-key": self.api_key, "anthropic-version": self.version,
                   "content-type": "application/json", "accept": "text/event-stream"}
        payload = json.dumps(self.body(system, prompt, cont)).encode("utf-8")
        res = GenResult()
        dec = SSEDecoder()
        try:
            for chunk in self.transport(f"{self.base}/v1/messages", headers, payload, self.timeout):
                for name, data in dec.feed(chunk):
                    self._event(name, data, res, on_text)
            for name, data in dec.close():
                self._event(name, data, res, on_text)
        except GenerationError as exc:
            res.error = str(exc)
        except (OSError, UnicodeDecodeError) as exc:
            res.error = f"stream interrupted: {exc}"
        try:
            res.cost_usd = usage_cost(self.model, res.input_tokens, res.output_tokens,
                                      res.cache_read_tokens, res.cache_write_tokens)
        except KeyError:
            res.cost_usd = None
        return res

    @staticmethod
    def _event(name: str, data: str, res: GenResult, on_text) -> None:
        if not data:
            return
        try:
            ev = json.loads(data)
        except json.JSONDecodeError:
            return
        kind = ev.get("type") or name
        if kind == "message_start":
            msg = ev.get("message") or {}
            res.session_id = msg.get("id")
            u = msg.get("usage") or {}
            res.input_tokens = int(u.get("input_tokens") or 0)
            res.cache_read_tokens = int(u.get("cache_read_input_tokens") or 0)
            res.cache_write_tokens = int(u.get("cache_creation_input_tokens") or 0)
        elif kind == "content_block_delta":
            d = ev.get("delta") or {}
            if d.get("type") == "text_delta" and d.get("text"):
                res.text += d["text"]
                on_text(d["text"])
        elif kind == "message_delta":
            res.stop_reason = (ev.get("delta") or {}).get("stop_reason") or res.stop_reason
            res.output_tokens = int((ev.get("usage") or {}).get("output_tokens") or res.output_tokens)
        elif kind == "error":
            err = ev.get("error") or {}
            res.error = f"{err.get('type', 'error')}: {err.get('message', '')}".strip()


class CommandAdapter(Adapter):
    """Generic argv adapter. ``{system}`` and ``{packet}`` in the argv are replaced with
    file paths; the prompt is also piped on stdin. Always stateless on continuation."""

    name = "command"

    def __init__(self, cfg: dict):
        self.command = list(cfg.get("command") or [])
        if not self.command:
            raise GenerationError("adapter 'command' needs omlcp.command (an argv list)")
        self.timeout = int(cfg.get("timeout_seconds") or 3600)

    def generate(self, system, prompt, on_text, cont=None, on_seam=None):
        text_in = cont.stateless_prompt if cont else prompt
        workdir = tempfile.mkdtemp(prefix="omlcp-cmd-")
        try:
            sys_file, pkt_file = os.path.join(workdir, "system.md"), os.path.join(workdir, "packet.md")
            for path, body in ((sys_file, system), (pkt_file, text_in)):
                with open(path, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(body)
            argv = [a.replace("{system}", sys_file).replace("{packet}", pkt_file) for a in self.command]
            argv[0] = shutil.which(argv[0]) or argv[0]
            proc = subprocess.Popen(argv, cwd=workdir, env=child_env({"DEVTEAM_DELEGATED": "1"}),
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
            timer = threading.Timer(self.timeout, proc.kill)
            timer.start()
            res = GenResult()
            err: list[str] = []
            t_err = threading.Thread(target=lambda: err.append(proc.stderr.read() or ""), daemon=True)
            t_err.start()
            try:
                try:
                    proc.stdin.write(text_in)
                    proc.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
                for chunk in iter(lambda: proc.stdout.read(4096), ""):
                    res.text += chunk
                    on_text(chunk)
                rc = proc.wait()
            finally:
                timer.cancel()
            t_err.join(timeout=5)
            if rc != 0:
                res.error = f"{self.command[0]} exited {rc}: {''.join(err).strip()[-500:]}"
            return res
        finally:
            shutil.rmtree(workdir, ignore_errors=True)


def make_adapter(cfg: dict) -> Adapter:
    name = cfg.get("adapter") or "claude-cli"
    if name == "claude-cli":
        return ClaudeCliAdapter(cfg)
    if name == "anthropic-api":
        return AnthropicApiAdapter(cfg)
    if name == "command":
        return CommandAdapter(cfg)
    if name == "manual":
        raise GenerationError("adapter 'manual' makes no model call: write the stream into "
                              "segment-N.raw yourself and run `omlcp materialize`")
    raise GenerationError(f"unknown omlcp adapter {name!r}")


# -------------------------------------------------------------------- ledger --
def append_ledger(repo: Path, entry: dict) -> None:
    """One JSON line per run in `.devteam/ledger.jsonl` (Wave F F2 shape + OMLCP fields).
    Ledger writes are best-effort: a full disk must not fail a generation."""
    p = repo / ".devteam" / "ledger.jsonl"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
    except OSError as exc:
        print(f"[omlcp] WARN ledger write failed: {exc}", file=sys.stderr)


# -------------------------------------------------------------------- runner --
@dataclass
class RunOutcome:
    task_id: str
    complete: bool
    segments_run: int
    stop: str
    materialized: MaterializeResult
    results: list[GenResult] = field(default_factory=list)

    def summary(self) -> dict:
        m = self.materialized
        return {"task_id": self.task_id, "complete": self.complete, "stop": self.stop,
                "segments_run": self.segments_run, "written": m.written, "unchanged": m.unchanged,
                "pending": m.pending, "rejected": m.rejected, "extraneous": m.extraneous,
                "resume_from": m.resume_from,
                "output_tokens": sum(r.output_tokens for r in self.results),
                "input_tokens": sum(r.input_tokens + r.cache_read_tokens for r in self.results),
                "cost_usd": round(sum(r.cost_usd or 0 for r in self.results), 4),
                "errors": [r.error for r in self.results if r.error] + m.errors,
                "warnings": m.warnings}


def read_segments(run_dir: Path, state: RunState) -> list[str]:
    return [(run_dir / name).read_text(encoding="utf-8", errors="replace")
            for name in state.segments if (run_dir / name).is_file()]


def materialize_run(run_dir: Path, state: RunState, policy: PathPolicy, target: Path,
                    dry_run: bool = False) -> MaterializeResult:
    parsed = parse_segments(state.nonce, read_segments(run_dir, state))
    res = materialize(parsed, policy, target, owned_hashes=state.written_hashes, dry_run=dry_run)
    if not dry_run:
        state.written_hashes.update(res.hashes)
        state.complete, state.pending, state.resume_from = res.complete, res.pending, res.resume_from
        state.save(run_dir / "state.json")
    return res


def run_generation(*, repo: Path, run_dir: Path, target: Path, packet: Packet, policy: PathPolicy,
                   cfg: dict, adapter: Adapter, unit: str | None = None,
                   log: Callable[[str], None] = print) -> RunOutcome:
    state_path = run_dir / "state.json"
    state = RunState.load(state_path) if state_path.is_file() else RunState(
        task_id=run_dir.name, nonce=packet.nonce, target_root=str(target))
    if state.nonce != packet.nonce:
        raise GenerationError(f"packet nonce {packet.nonce} does not match run state nonce {state.nonce}; "
                              "rebuild the packet with the state's nonce or pass --fresh")
    state.save(state_path)
    # Budget is per invocation: re-running `omlcp run`/`stage` is a deliberate decision to
    # spend another bounded budget, and a crashed run must be resumable.
    max_segments = 1 + max(0, int(cfg.get("max_continuations", 2)))
    results: list[GenResult] = []
    res = materialize_run(run_dir, state, policy, target) if state.segments else None
    if res and res.complete:
        return RunOutcome(state.task_id, True, 0, "already complete", res, results)
    stop = ""
    prev_failed = False
    while True:
        index = len(state.segments)
        if len(results) >= max_segments:
            stop = f"continuation budget exhausted ({max_segments} segment(s) this run)"
            break
        before = len(state.written_hashes)
        seg_name = f"segment-{index}.raw"
        cont = None
        if index > 0:
            prior_text = "\n".join(read_segments(run_dir, state))
            complete_paths = [p for p in policy.manifest if p in state.written_hashes]
            instruction = continuation_prompt(packet.nonce, policy.manifest, complete_paths, state.resume_from)
            contents = {p: (target / p).read_text(encoding="utf-8", errors="replace")
                        for p in complete_paths if (target / p).is_file()}
            stateless = packet.prompt + "\n\n---\n\n" + continuation_prompt(
                packet.nonce, policy.manifest, complete_paths, state.resume_from, contents)
            use_resume = cfg.get("continuation_mode", "resume") == "resume" and adapter.supports_resume
            cont = Continuation(session_id=state.session_id if use_resume else None,
                                packet_prompt=packet.prompt,
                                prior_text=prior_text if use_resume else "",
                                instruction=instruction, stateless_prompt=stateless)
        log(f"[omlcp] {state.task_id}: segment {index} via {adapter.name}"
            + (f" (resuming at {state.resume_from})" if index else ""))
        state.segments.append(seg_name)
        state.save(state_path)
        started = time.monotonic()
        with (run_dir / seg_name).open("w", encoding="utf-8", newline="\n") as fh:
            def on_text(t: str, _fh=fh) -> None:
                _fh.write(t)
                _fh.flush()

            def on_seam(_fh=fh) -> None:
                _fh.write(f"\n{MARKER} {packet.nonce} SEAM\n")
                _fh.flush()
            try:
                gen = adapter.generate(packet.system, packet.prompt, on_text, cont, on_seam=on_seam)
            except GenerationError as exc:
                gen = GenResult(error=str(exc))
        results.append(gen)
        if gen.session_id:
            state.session_id = gen.session_id
        res = materialize_run(run_dir, state, policy, target)
        new_files = len(state.written_hashes) - before
        append_ledger(repo, {
            "ts": utc_now(), "role": "generator", "lane": "generate", "unit": unit,
            "task_id": state.task_id, "model": cfg.get("model"), "effort": cfg.get("effort"),
            "adapter": adapter.name, "session_id": gen.session_id, "cost_usd": gen.cost_usd,
            "turns": 1, "subtype": gen.subtype, "exit": 0 if not gen.error else 1,
            "segment": index, "continuation": index > 0, "input_tokens": gen.input_tokens,
            "output_tokens": gen.output_tokens, "cache_read_tokens": gen.cache_read_tokens,
            "cache_write_tokens": gen.cache_write_tokens, "stop_reason": gen.stop_reason,
            "files_completed": len(state.written_hashes), "files_new": new_files,
            "est_input_tokens": packet.est_input_tokens,
            "seams": gen.seams, "wall_seconds": round(time.monotonic() - started, 1), "error": gen.error})
        log(f"[omlcp] segment {index}: +{new_files} file(s), {len(res.pending)} pending"
            + (f", error: {gen.error}" if gen.error else ""))
        if res.complete:
            stop = "complete"
            break
        if res.rejected or res.errors:
            stop = "policy or protocol errors — fix the packet/manifest; continuing would repeat them"
            break
        if new_files == 0 and not gen.error:
            stop = "stagnation: segment completed no new file"
            break
        failed_without_progress = bool(gen.error) and new_files == 0
        if failed_without_progress and prev_failed:
            stop = f"two failing segments in a row: {gen.error}"
            break
        prev_failed = failed_without_progress
    return RunOutcome(state.task_id, bool(res and res.complete), len(results), stop, res, results)


def outcome_json(o: RunOutcome) -> str:
    d = o.summary()
    d["results"] = [asdict(r) | {"text": f"<{len(r.text)} chars>"} for r in o.results]
    return json.dumps(d, indent=2)
