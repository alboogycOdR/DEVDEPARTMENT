"""OMLCP generation: stream-json and SSE parsing, adapters, bounded continuation, ledger."""
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from omlcp_generate import (Adapter, AnthropicApiAdapter, ClaudeCliAdapter, CommandAdapter,  # noqa: E402
                            Continuation, GenerationError, GenResult, SSEDecoder, make_adapter,
                            parse_claude_stream_json, run_generation)
from omlcp_packet import DEFAULTS, Packet  # noqa: E402
from omlcp_stream import MARKER, PathPolicy  # noqa: E402

N = "0a1b2c3d4e5f"
M = f"{MARKER} {N}"
FILE_A = f"{M} FILE src/a.py\ndef a():\n    return 'á'\n{M} END src/a.py\n"
FILE_B = f"{M} FILE src/b.py\ndef b():\n    return 2\n{M} END src/b.py\n"


def packet():
    return Packet(nonce=N, system=f"system with {M}", prompt=f"packet; start with `{M} FILE src/a.py`",
                  est_input_tokens=100, est_output_tokens=50)


def policy():
    return PathPolicy(manifest=["src/a.py", "src/b.py"], owned=["src/**"])


def ledger(repo):
    return [json.loads(x) for x in (repo / ".devteam" / "ledger.jsonl").read_text().splitlines()]


# ------------------------------------------------------- claude stream-json --
def _ev(**kw):
    return json.dumps(kw)


def test_stream_json_text_deltas_and_result_usage():
    seen = []
    lines = [
        _ev(type="system", subtype="init", session_id="s-1"),
        _ev(type="stream_event", event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hel"}}),
        _ev(type="stream_event", event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "lo"}}),
        _ev(type="stream_event", event={"type": "message_delta", "delta": {"stop_reason": "max_tokens"}}),
        _ev(type="assistant", message={"content": [{"type": "text", "text": "hello"}]}),
        "not json at all",
        _ev(type="result", subtype="success", session_id="s-1", total_cost_usd=0.12, is_error=False,
            result="hello", usage={"input_tokens": 10, "output_tokens": 20, "cache_read_input_tokens": 30,
                                   "cache_creation_input_tokens": 40}),
    ]
    r = parse_claude_stream_json(lines, seen.append)
    assert r.text == "hello" and "".join(seen) == "hello"  # never doubled by the assistant message
    assert r.session_id == "s-1" and r.cost_usd == 0.12 and r.stop_reason == "max_tokens"
    assert (r.input_tokens, r.output_tokens, r.cache_read_tokens, r.cache_write_tokens) == (10, 20, 30, 40)


def test_stream_json_falls_back_to_whole_messages_without_partials():
    seen = []
    r = parse_claude_stream_json([_ev(type="assistant", message={"content": [{"type": "text", "text": "abc"}]}),
                                  _ev(type="result", subtype="success", result="abc")], seen.append)
    assert r.text == "abc" and seen == ["abc"]


def test_stream_json_error_and_output_ceiling_event():
    r = parse_claude_stream_json([
        _ev(type="system", subtype="api_retry", error="max_output_tokens"),
        _ev(type="result", subtype="error_during_execution", is_error=True, result="boom")], lambda t: None)
    assert r.error == "boom" and r.stop_reason == "max_tokens"


# ---------------------------------------------------------------------- SSE --
def sse(*events):
    out = b""
    for name, data in events:
        out += f"event: {name}\ndata: {json.dumps(data)}\n\n".encode("utf-8")
    return out


def test_sse_reassembles_multibyte_characters_split_across_chunks():
    payload = sse(("content_block_delta", {"type": "content_block_delta",
                                           "delta": {"type": "text_delta", "text": "naïve — ✓ 日本"}}))
    for size in (1, 2, 3, 5):
        dec, events = SSEDecoder(), []
        for i in range(0, len(payload), size):
            events += dec.feed(payload[i:i + size])
        events += dec.close()
        assert json.loads(events[0][1])["delta"]["text"] == "naïve — ✓ 日本"


def test_sse_handles_crlf_split_comments_and_multiline_data():
    raw = b": ping\r\nevent: a\r\ndata: line1\r\ndata: line2\r\n\r\nevent: b\rdata: x\r\r"
    dec, events = SSEDecoder(), []
    for i in range(len(raw)):
        events += dec.feed(raw[i:i + 1])
    events += dec.close()
    assert events == [("a", "line1\nline2"), ("b", "x")]


# --------------------------------------------------------- anthropic adapter --
def fake_transport(chunks, capture):
    def t(url, headers, body, timeout):
        capture.update(url=url, headers=headers, body=json.loads(body))
        yield from chunks
    return t


def api_stream(text, stop="end_turn"):
    return sse(
        ("message_start", {"type": "message_start", "message": {"id": "msg_1", "usage": {
            "input_tokens": 100, "cache_read_input_tokens": 50, "cache_creation_input_tokens": 0}}}),
        ("ping", {"type": "ping"}),
        ("content_block_delta", {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": stop}, "usage": {"output_tokens": 7}}),
        ("message_stop", {"type": "message_stop"}))


def test_api_adapter_streams_text_usage_and_cost():
    cap, seen = {}, []
    ad = AnthropicApiAdapter(dict(DEFAULTS, model="claude-sonnet-5"),
                             transport=fake_transport([api_stream("hi there", "max_tokens")], cap), api_key="k")
    r = ad.generate("SYS", "PROMPT", seen.append)
    assert r.text == "hi there" and r.stop_reason == "max_tokens" and r.session_id == "msg_1"
    assert (r.input_tokens, r.output_tokens, r.cache_read_tokens) == (100, 7, 50)
    assert r.cost_usd == pytest.approx((100 * 2 + 7 * 10 + 50 * 2 * 0.1) / 1e6)
    assert cap["url"].endswith("/v1/messages") and cap["headers"]["x-api-key"] == "k"
    assert cap["body"]["stream"] is True and cap["body"]["system"] == "SYS"
    assert cap["body"]["messages"][0]["content"][0]["cache_control"] == {"type": "ephemeral"}


def test_api_adapter_continuation_carries_prior_text_then_instruction():
    cap = {}
    ad = AnthropicApiAdapter(dict(DEFAULTS), transport=fake_transport([api_stream("x")], cap), api_key="k")
    cont = Continuation("msg_1", "PROMPT", "earlier output  \n", "continue please", "STATELESS")
    ad.generate("SYS", "PROMPT", lambda t: None, cont)
    roles = [m["role"] for m in cap["body"]["messages"]]
    assert roles == ["user", "assistant", "user"]
    assert cap["body"]["messages"][1]["content"] == "earlier output"  # no trailing whitespace
    assert cap["body"]["messages"][2]["content"] == "continue please"


def test_api_adapter_error_event_and_missing_key():
    err = sse(("error", {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}}))
    r = AnthropicApiAdapter(dict(DEFAULTS), transport=fake_transport([err], {}), api_key="k").generate(
        "S", "P", lambda t: None)
    assert r.error == "overloaded_error: busy"
    with pytest.raises(GenerationError, match="ANTHROPIC_API_KEY"):
        AnthropicApiAdapter(dict(DEFAULTS), api_key="").generate("S", "P", lambda t: None)


def test_api_adapter_interrupted_transport_keeps_partial_text():
    def broken(url, headers, body, timeout):
        yield api_stream("partial")[:-40]
        raise ConnectionResetError("reset by peer")
    r = AnthropicApiAdapter(dict(DEFAULTS), transport=broken, api_key="k").generate("S", "P", lambda t: None)
    assert r.text == "partial" and "interrupted" in r.error


# --------------------------------------------------------------- claude cli --
FAKE_CLAUDE = textwrap.dedent('''\
    import json, os, re, sys
    args = sys.argv[1:]
    stdin = sys.stdin.read()
    with open(os.environ["FAKE_LOG"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"args": args, "stdin": stdin, "cwd": os.getcwd(),
                             "max_out": os.environ.get("CLAUDE_CODE_MAX_OUTPUT_TOKENS"),
                             "delegated": os.environ.get("DEVTEAM_DELEGATED"),
                             "parent_session": os.environ.get("CLAUDE_CODE_SESSION_ID"),
                             "claudecode": os.environ.get("CLAUDECODE"),
                             "system": open(args[args.index("--system-prompt-file") + 1]).read()}) + "\\n")
    nonce = re.search(r"@@OMLCP ([0-9a-f]+) ", stdin).group(1)
    M = "@@OMLCP " + nonce
    if "--resume" in args:
        text = M + " FILE src/b.py\\ndef b():\\n    return 2\\n" + M + " END src/b.py\\n" + M + " DONE\\n"
        stop = "end_turn"
    else:
        text = M + " FILE src/a.py\\ndef a():\\n    return 1\\n" + M + " END src/a.py\\n" + M + " FILE src/b.py\\ndef b(:"
        stop = "max_tokens"
    def emit(o):
        print(json.dumps(o), flush=True)
    emit({"type": "system", "subtype": "init", "session_id": "sess-42"})
    emit({"type": "stream_event", "event": {"type": "message_start", "message": {}}})
    for i in range(0, len(text), 7):
        emit({"type": "stream_event", "event": {"type": "content_block_delta",
              "delta": {"type": "text_delta", "text": text[i:i + 7]}}})
    emit({"type": "stream_event", "event": {"type": "message_delta", "delta": {"stop_reason": stop}}})
    emit({"type": "result", "subtype": "success", "session_id": "sess-42", "total_cost_usd": 0.01,
          "is_error": False, "usage": {"input_tokens": 5, "output_tokens": 9}})
''')


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent-session-must-not-leak")
    monkeypatch.setenv("CLAUDECODE", "1")
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_CLAUDE, encoding="utf-8")
    log = tmp_path / "calls.jsonl"
    monkeypatch.setenv("FAKE_LOG", str(log))
    return [sys.executable, str(script)], log


def test_cli_argv_disables_tools_and_replaces_system_prompt():
    ad = ClaudeCliAdapter(dict(DEFAULTS, cli="claude", model="claude-sonnet-5", effort="low"))
    argv = ad.argv("/tmp/sys.md", resume="sess-1")
    i = argv.index("--tools")
    assert argv[i + 1] == "" and "--disallowedTools" in argv and "mcp__*" in argv
    assert argv[argv.index("--system-prompt-file") + 1] == "/tmp/sys.md"
    assert argv[argv.index("--model") + 1] == "claude-sonnet-5" and argv[argv.index("--effort") + 1] == "low"
    assert argv[argv.index("--resume") + 1] == "sess-1"
    assert "--include-partial-messages" in argv and "stream-json" in argv
    assert "--bare" not in argv  # bare mode ignores the subscription login
    assert ad.env()["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == str(DEFAULTS["max_output_tokens"])


def test_cli_adapter_end_to_end_with_resume_continuation(tmp_path, fake_claude):
    cli, log = fake_claude
    repo, target = tmp_path / "repo", tmp_path / "wt"
    repo.mkdir()
    target.mkdir()
    cfg = dict(DEFAULTS, cli=cli, model="claude-sonnet-5")
    out = run_generation(repo=repo, run_dir=repo / ".devteam" / "omlcp" / "TASK-1", target=target,
                         packet=packet(), policy=policy(), cfg=cfg, adapter=ClaudeCliAdapter(cfg),
                         unit="S5", log=lambda m: None)
    assert out.complete and out.segments_run == 2 and out.stop == "complete"
    assert (target / "src/a.py").read_text() == "def a():\n    return 1\n"
    assert (target / "src/b.py").read_text() == "def b():\n    return 2\n"
    calls = [json.loads(x) for x in log.read_text().splitlines()]
    assert "--resume" not in calls[0]["args"]
    assert calls[1]["args"][calls[1]["args"].index("--resume") + 1] == "sess-42"
    assert "Continue the same OMLCP output stream" in calls[1]["stdin"]
    assert "packet;" not in calls[1]["stdin"]  # resume sends only the instruction
    for c in calls:
        assert not Path(c["cwd"]).resolve().is_relative_to(repo.resolve())  # no CLAUDE.md pickup
        assert c["max_out"] == str(DEFAULTS["max_output_tokens"]) and c["delegated"] == "1"
        assert c["parent_session"] is None and c["claudecode"] is None  # no parent-session leak
        assert N in c["system"]
    rows = ledger(repo)
    assert [r["segment"] for r in rows] == [0, 1] and rows[1]["continuation"] is True
    assert rows[0]["stop_reason"] == "max_tokens" and rows[0]["files_new"] == 1
    assert all(r["role"] == "generator" and r["lane"] == "generate" and r["unit"] == "S5" for r in rows)


def test_cli_adapter_reports_nonzero_exit(tmp_path):
    script = tmp_path / "die.py"
    script.write_text("import sys\nsys.stdin.read()\nsys.stderr.write('auth failed')\nsys.exit(3)\n")
    r = ClaudeCliAdapter(dict(DEFAULTS, cli=[sys.executable, str(script)])).generate("S", "P", lambda t: None)
    assert "exited 3" in r.error and "auth failed" in r.error


# -------------------------------------------------------------- command adapter --
def test_command_adapter_streams_stdout_and_substitutes_files(tmp_path):
    script = tmp_path / "gen.py"
    script.write_text("import sys\nsys.stdin.read()\nprint(open(sys.argv[1]).read().upper(), end='')\n")
    ad = CommandAdapter({"command": [sys.executable, str(script), "{system}"]})
    seen = []
    r = ad.generate("system text", "P", seen.append)
    assert r.text == "SYSTEM TEXT" and "".join(seen) == "SYSTEM TEXT"
    with pytest.raises(GenerationError):
        CommandAdapter({"command": []})


def test_make_adapter_dispatch():
    assert isinstance(make_adapter(dict(DEFAULTS)), ClaudeCliAdapter)
    assert isinstance(make_adapter(dict(DEFAULTS, adapter="anthropic-api")), AnthropicApiAdapter)
    for bad in ("manual", "nope"):
        with pytest.raises(GenerationError):
            make_adapter(dict(DEFAULTS, adapter=bad))


# ------------------------------------------------------------------- runner --
class ScriptedAdapter(Adapter):
    """Plays back scripted segments: each item is (text, error)."""
    name = "scripted"
    supports_resume = True

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def generate(self, system, prompt, on_text, cont=None, on_seam=None):
        self.calls.append(cont)
        text, err = self.script.pop(0)
        for n, part in enumerate(text.split("<<SEAM>>")):
            if n and on_seam:
                on_seam()
            for i in range(0, len(part), 5):
                on_text(part[i:i + 5])
        return GenResult(text=text, error=err, output_tokens=len(text), session_id="sid")


def _run(tmp_path, adapter, **cfg):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return repo, run_generation(repo=repo, run_dir=repo / ".devteam/omlcp/TASK-1", target=repo,
                                packet=packet(), policy=policy(), cfg=dict(DEFAULTS, **cfg),
                                adapter=adapter, log=lambda m: None)


def test_dropped_connection_resumes_instead_of_regenerating(tmp_path):
    ad = ScriptedAdapter([(FILE_A + f"{M} FILE src/b.py\ndef b", "connection reset"),
                          (FILE_B + f"{M} DONE\n", None)])
    repo, out = _run(tmp_path, ad)
    assert out.complete
    assert ad.calls[1].instruction.count("src/b.py") >= 1 and "- src/a.py" in ad.calls[1].instruction
    raw0 = (repo / ".devteam/omlcp/TASK-1/segment-0.raw").read_text()
    assert raw0.endswith("def b")  # everything received before the drop was kept on disk
    assert ledger(repo)[0]["exit"] == 1 and ledger(repo)[0]["error"] == "connection reset"


def test_stagnation_stops_the_loop(tmp_path):
    ad = ScriptedAdapter([(FILE_A, None), ("I cannot continue.", None), (FILE_B, None)])
    _, out = _run(tmp_path, ad)
    assert not out.complete and out.stop.startswith("stagnation") and out.segments_run == 2


def test_continuation_budget_is_bounded(tmp_path):
    ad = ScriptedAdapter([(FILE_A, None), (FILE_B, None)])
    _, out = _run(tmp_path, ad, max_continuations=0)
    assert not out.complete and "budget exhausted" in out.stop and out.segments_run == 1


def test_two_failures_in_a_row_stop(tmp_path):
    ad = ScriptedAdapter([("", "boom"), ("", "boom again"), (FILE_A, None)])
    _, out = _run(tmp_path, ad, max_continuations=5)
    assert out.segments_run == 2 and "two failing segments" in out.stop


def test_rerun_resumes_from_state_and_completed_run_is_idempotent(tmp_path):
    ad1 = ScriptedAdapter([(FILE_A, None), ("nothing", None)])
    repo, first = _run(tmp_path, ad1)
    assert not first.complete
    ad2 = ScriptedAdapter([(FILE_B + f"{M} DONE\n", None)])
    _, second = _run(tmp_path, ad2, max_continuations=5)
    assert second.complete and ad2.calls[0] is not None  # it continued, it did not restart
    ad3 = ScriptedAdapter([])
    _, third = _run(tmp_path, ad3)
    assert third.complete and third.stop == "already complete" and ad3.calls == []


def test_nonce_mismatch_with_existing_state_is_refused(tmp_path):
    ad = ScriptedAdapter([(FILE_A, None), ("x", None)])
    repo, _ = _run(tmp_path, ad)
    other = Packet(nonce="ffffffffffff", system="s", prompt="p", est_input_tokens=1, est_output_tokens=1)
    with pytest.raises(GenerationError, match="nonce"):
        run_generation(repo=repo, run_dir=repo / ".devteam/omlcp/TASK-1", target=repo, packet=other,
                       policy=policy(), cfg=dict(DEFAULTS), adapter=ad, log=lambda m: None)


def test_stateless_continuation_includes_completed_contents(tmp_path):
    class NoResume(ScriptedAdapter):
        supports_resume = False
    ad = NoResume([(FILE_A, None), (FILE_B + f"{M} DONE\n", None)])
    _, out = _run(tmp_path, ad)
    assert out.complete
    cont = ad.calls[1]
    assert cont.session_id is None and cont.prior_text == ""
    assert "### src/a.py" in cont.stateless_prompt and "packet;" in cont.stateless_prompt


def test_policy_violation_stops_without_burning_continuations(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "src").mkdir()
    (repo / "src/a.py").write_text("hand-written\n")
    ad = ScriptedAdapter([(FILE_A + FILE_B + f"{M} DONE\n", None), (FILE_A, None)])
    out = run_generation(repo=repo, run_dir=repo / ".devteam/omlcp/TASK-1", target=repo, packet=packet(),
                         policy=policy(), cfg=dict(DEFAULTS), adapter=ad, log=lambda m: None)
    assert not out.complete and "policy" in out.stop and out.segments_run == 1
    assert (repo / "src/a.py").read_text() == "hand-written\n"


def test_ledger_write_failure_does_not_fail_generation(tmp_path, capsys):
    import omlcp_generate
    (tmp_path / ".devteam").write_text("a file where a directory should be")
    omlcp_generate.append_ledger(tmp_path, {"x": 1})  # must not raise
    assert "ledger write failed" in capsys.readouterr().err


def test_budget_is_per_invocation_so_a_stopped_run_can_be_resumed(tmp_path):
    ad1 = ScriptedAdapter([(FILE_A, None)])
    repo, first = _run(tmp_path, ad1, max_continuations=0)
    assert not first.complete and "budget exhausted" in first.stop
    ad2 = ScriptedAdapter([(FILE_B + f"{M} DONE\n", None)])
    _, second = _run(tmp_path, ad2, max_continuations=0)
    assert second.complete and second.segments_run == 1


# ------------------------------------------------------------------- seams --
def test_stream_json_reports_seams_between_messages():
    seams = []
    lines = [_ev(type="stream_event", event={"type": "message_start", "message": {}}),
             _ev(type="stream_event", event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "a"}}),
             _ev(type="stream_event", event={"type": "message_delta", "delta": {"stop_reason": "max_tokens"}}),
             _ev(type="stream_event", event={"type": "message_start", "message": {}}),
             _ev(type="stream_event", event={"type": "content_block_delta", "delta": {"type": "text_delta", "text": "b"}}),
             _ev(type="result", subtype="success", session_id="s")]
    r = parse_claude_stream_json(lines, lambda t: None, lambda: seams.append(1))
    assert r.seams == 1 and seams == [1] and r.text == "ab"


def test_seam_inside_a_file_forces_regeneration_of_that_file(tmp_path):
    # Reproduces the live 2.1.291 failure: the CLI's own output-cap recovery restarted a line
    # ("def test_simple_text(self)    def test_simple_text(self) -> None:").
    broken = (FILE_A + f"{M} FILE src/b.py\ndef b(self)<<SEAM>>    def b(self):\n    return 2\n"
              f"{M} END src/b.py\n{M} DONE\n")
    ad = ScriptedAdapter([(broken, None), (FILE_B + f"{M} DONE\n", None)])
    repo, out = _run(tmp_path, ad)
    assert out.complete and out.segments_run == 2
    assert (repo / "src/b.py").read_text() == "def b():\n    return 2\n"
    assert ad.calls[1].instruction.index("- src/b.py") > 0
    assert f"{M} SEAM" in (repo / ".devteam/omlcp/TASK-1/segment-0.raw").read_text()


def test_child_env_drops_parent_session_identity_but_keeps_auth(monkeypatch):
    import omlcp_generate
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "parent")
    monkeypatch.setenv("CLAUDE_CODE_REMOTE_SDK_URL", "https://x")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "keep-me")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://api.anthropic.com")
    env = omlcp_generate.child_env({"X": "1"})
    assert "CLAUDE_CODE_SESSION_ID" not in env and "CLAUDE_CODE_REMOTE_SDK_URL" not in env
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "keep-me" and env["ANTHROPIC_BASE_URL"] and env["X"] == "1"
