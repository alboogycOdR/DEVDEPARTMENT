"""Process-boundary E-K regressions for the durable supervisor inbox."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import inbox  # noqa: E402
import supervisor as sup  # noqa: E402
from tg_listener import TelegramListener  # noqa: E402
from tick_harness import make_fixture_repo, run_once_subprocess  # noqa: E402


PLAN = """---
plan_version: 1.0
last_updated: 2026-09-28T00:00:00Z
overall_status: in_progress
---
"""


def _enqueue(repo: Path, command: str, command_id: str = "telegram-7") -> Path:
    return inbox.enqueue(repo, command_id=command_id, source="telegram", actor="42",
                         command=command, args="", issued_at="2026-09-28T12:00:00Z")


def test_resume_inbox_is_consumed_once_across_once_processes(tmp_path):
    """A command delivered between scheduler invocations survives to the next.

    The second separate ``--once`` process must not replay the acknowledged
    envelope.  This exercises the on-disk state, rather than a thread queue.
    """
    repo = make_fixture_repo(tmp_path, PLAN)
    (repo / "autopilot.json").write_text(json.dumps({"builders": []}), encoding="utf-8")
    sup.RuntimeState(parked={"kind": "P1", "reason": "fixture", "since": "2026-09-28T00:00:00Z"}).save(
        repo / ".autopilot_state.json")
    # This first short-lived process is the bounded Telegram poll.  Its
    # offset may advance only after the durable envelope is visible; the
    # next scheduler process is then responsible for applying it.
    update = {"update_id": 7, "message": {"chat": {"id": 42}, "text": "/resume"}}
    listener = TelegramListener("token", ["42"], "42", None,
                               repo / ".devteam" / "tg_offset.txt",
                               fetch=lambda _method, _timeout: {"ok": True, "result": [update]})
    assert listener.poll_once()
    path = next((repo / ".devteam" / "inbox").glob("*-telegram-7.json"))
    assert path.exists()
    assert (repo / ".devteam" / "tg_offset.txt").read_text(encoding="utf-8") == "8"

    first = run_once_subprocess(repo)
    assert first.returncode == 0, first.stderr
    state = json.loads((repo / ".autopilot_state.json").read_text(encoding="utf-8"))
    assert state["parked"] == {}
    assert not path.exists()

    second = run_once_subprocess(repo)
    assert second.returncode == 0, second.stderr
    assert not path.exists()
    consumed = json.loads((repo / ".devteam" / "inbox" / ".consumed_ids.json").read_text(encoding="utf-8"))
    assert consumed == ["telegram-7"]


def test_crash_before_ack_replays_on_next_drain(tmp_path, monkeypatch):
    """The two-phase file remains if the first process dies in its handler."""
    repo = make_fixture_repo(tmp_path, PLAN)
    path = _enqueue(repo, "/resume")
    assert path is not None
    state = sup.RuntimeState(parked={"kind": "P1"})
    original = sup._process_tg_command

    def killed(*args, **kwargs):
        raise SystemExit("simulated process death")

    monkeypatch.setattr(sup, "_process_tg_command", killed)
    # SystemExit is intentionally outside the handler's Exception catch: this
    # is the equivalent of a killed process between drain and acknowledgement.
    try:
        sup.drain_inbox_commands(repo, sup.DEFAULT_CONFIG, state, sup.threading.Event(),
                                 sup.datetime.now(sup.timezone.utc), "")
    except SystemExit:
        pass
    assert path.exists()

    monkeypatch.setattr(sup, "_process_tg_command", original)
    sup.drain_inbox_commands(repo, sup.DEFAULT_CONFIG, state, sup.threading.Event(),
                             sup.datetime.now(sup.timezone.utc), "")
    assert not path.exists()
