"""Test-only entry point: real supervisor main, accelerated clock, local I/O.

No scheduling, review ledger, parking, inbox, or push-policy logic is mocked.
The model and notification transports are recorded locally. Telegram returns
one canned update at hour six. All Git operations target the temporary repo.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.environ["WAVE_E_SCRIPTS"])
sys.path.insert(0, os.environ["WAVE_E_TESTS"])
import supervisor as sup
import push_policy
import scheduling
from tick_harness import FakeClock
from tg_listener import TelegramListener

repo = Path(sys.argv[sys.argv.index("--repo") + 1]).resolve()
clock = FakeClock(datetime(2026, 9, 30, tzinfo=timezone.utc)
                  + timedelta(minutes=int(os.environ["WAVE_E_MINUTE"])))


class ClockDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return clock.now.astimezone(tz) if tz else clock.now.replace(tzinfo=None)


sup.datetime = ClockDateTime
push_policy.datetime = ClockDateTime


def record(kind, **data):
    with (repo / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"kind": kind, "time": clock.now.isoformat(),
                                 "pid": os.getpid(), **data}) + "\n")


def model_command(command, root):
    # A successful model process with no verdict is the required unreviewable
    # submission. The actual review ledger must decide whether to retry it.
    record("model", command=command)
    return 0


sup.run_shell = model_command
sup.notify = lambda cfg, priority, message, root: record(
    "notification", priority=priority, message=message)
sup.tgc.send_reply = lambda token, chat, text: record("reply", text=text)
original_push_run = push_policy._run


def observe_git(root, *args):
    result = original_push_run(root, *args)
    if args == ("push",):
        record("push", exit_code=result.returncode)
    return result


push_policy._run = observe_git


class ClockEvent:
    def wait(self, timeout):
        clock.advance(seconds=timeout)
        return False

    def clear(self):
        pass

    def set(self):
        pass


# Disable network listener threads; deliver a real TelegramListener poll at
# the chosen tick boundary instead. The listener still persists its envelope
# and offset, and supervisor.main drains/handles/acks it itself.
sup._start_tg_listener = lambda root, cfg, **kw: (None, None, ClockEvent())
sup._start_slack_listener = lambda root, cfg: (None, None)
original_drain = sup.drain_inbox_commands


def drain(root, cfg, state, event, now, token):
    if now.hour >= 6 and not (repo / "telegram-delivered").exists():
        update = {"update_id": 43, "message": {"chat": {"id": 42},
                  "text": "/answer TASK-903 use the documented default"}}
        listener = TelegramListener("fixture", ["42"], "42", None,
                                    repo / ".devteam" / "tg_offset.txt",
                                    fetch=lambda *_: {"ok": True, "result": [update]})
        assert listener.poll_once()
        assert list((repo / ".devteam" / "inbox").glob("*-telegram-43.json"))
        (repo / "telegram-delivered").touch()
        record("telegram_delivery", offset=listener.offset)
    return original_drain(root, cfg, state, event, now, token)


sup.drain_inbox_commands = drain
# Prevent unrelated nightly/weekly model jobs by using their real durable
# markers on the simulated date. Usage/board/Tower are disabled in config.
scheduling.mark_done_daily(repo / ".devteam" / "last_audit_date.txt", clock.now)
scheduling.mark_done_weekly(repo / ".devteam" / "last_retro_week.txt", clock.now)
record("process", mode="once" if "--once" in sys.argv else "loop")
raise SystemExit(sup.main(sys.argv[1:]))
