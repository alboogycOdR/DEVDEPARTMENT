#!/usr/bin/env python3
"""notify.py — Notification hub for the autopilot.

Channels:
    console  — print to stdout (always safe)
    file     — append to AUTOPILOT_LOG.md
    telegram — send via Bot API. Credentials come ONLY from environment
               variables (DEVTEAM_TG_TOKEN, DEVTEAM_TG_CHAT). Never hardcode.

Usage:
    python scripts/notify.py --priority P1 --message "..." --channels console,file,telegram

Wave A-remainder (two-way Telegram): P2 escalations get an actionable
"Reply: /answer TASK-NNN <your decision>" line appended automatically so the
outbound alert doubles as the exact command needed to resolve it from a
phone. P0 and P1 are left untouched — P1 already implies "go look now" and P0
is a batched digest; neither benefits from a canned reply command.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BADGE = {"P0": "🟢 DIGEST", "P1": "🔴 STOP-THE-LINE", "P2": "🟠 DECISION NEEDED"}
TASK_ID_RE = re.compile(r"\bTASK-\d+\b")


def append_reply_hint(priority: str, message: str) -> str:
    """Append the literal /answer reply command to a P2 escalation message.

    No-op for P0/P1, and a no-op if the message doesn't reference a task ID
    (defensive: some P2 messages may not be task-scoped in future).
    """
    if priority != "P2":
        return message
    m = TASK_ID_RE.search(message)
    if not m:
        return message
    return f"{message}\nReply: /answer {m.group(0)} <your decision>"


def send_console(priority: str, message: str) -> None:
    print(f"[{BADGE.get(priority, priority)}] {message}")


def send_file(priority: str, message: str) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(Path("AUTOPILOT_LOG.md"), "a", encoding="utf-8") as f:
        f.write(f"- [{ts}] **{priority}** {message}\n")


def send_telegram(priority: str, message: str) -> None:
    token = os.environ.get("DEVTEAM_TG_TOKEN")
    chat = os.environ.get("DEVTEAM_TG_CHAT")
    if not token or not chat:
        print("[notify] telegram channel requested but DEVTEAM_TG_TOKEN / DEVTEAM_TG_CHAT "
              "env vars are not set — skipping (never hardcode credentials).", file=sys.stderr)
        return
    text = f"{BADGE.get(priority, priority)}\n{message}"
    data = urllib.parse.urlencode({"chat_id": chat, "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode())
            if not body.get("ok"):
                print(f"[notify] telegram API error: {body}", file=sys.stderr)
    except Exception as exc:  # network failure must never crash the supervisor
        print(f"[notify] telegram send failed: {exc}", file=sys.stderr)


CHANNELS = {"console": send_console, "file": send_file, "telegram": send_telegram}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--priority", required=True, choices=["P0", "P1", "P2"])
    ap.add_argument("--message", required=True)
    ap.add_argument("--channels", default="console")
    args = ap.parse_args(argv)

    message = append_reply_hint(args.priority, args.message)
    for ch in [c.strip() for c in args.channels.split(",") if c.strip()]:
        fn = CHANNELS.get(ch)
        if fn is None:
            print(f"[notify] unknown channel '{ch}'", file=sys.stderr)
            continue
        fn(args.priority, message)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
