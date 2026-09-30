#!/usr/bin/env python3
"""Durable push scheduling for bookkeeping commits and merge/park boundaries."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

STATE_NAME = "push_policy.json"
VALID_POLICIES = {"every", "batch", "merge_only"}


def _run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=30)


def _config(repo: Path) -> tuple[str, float, bool]:
    try:
        git = json.loads((repo / "autopilot.json").read_text(encoding="utf-8")).get("git", {})
    except (OSError, ValueError, TypeError):
        git = {}
    configured = "push_policy" in git
    policy = git.get("push_policy", "every")
    if policy not in VALID_POLICIES:
        raise ValueError(f"invalid git.push_policy: {policy!r}")
    minutes = float(git.get("push_batch_minutes", 30))
    if minutes <= 0:
        raise ValueError("git.push_batch_minutes must be positive")
    return policy, minutes, configured


def _main_checkout(repo: Path) -> bool:
    result = _run(repo, "rev-parse", "--show-toplevel")
    return result.returncode == 0 and Path(result.stdout.strip()).resolve() == repo.resolve()


def commit_plan(repo: Path, message: str, *, now: datetime | None = None) -> tuple[bool, bool, str]:
    """Commit PLAN.md alone, then apply the shared bookkeeping push policy.

    The pathspec is essential: unrelated staged files can never enter this commit.
    The return value matches tg_commands.git_commit_and_push_detailed.
    """
    if not _main_checkout(repo):
        return False, False, f"{repo} is not the root of its git work tree"
    result = _run(repo, "diff", "--quiet", "HEAD", "--", "PLAN.md")
    if result.returncode == 0:
        return True, False, "nothing to commit (PLAN.md already at the desired state)"
    result = _run(repo, "commit", "-m", message, "--", "PLAN.md")
    if result.returncode != 0:
        return False, False, f"commit failed: {(result.stderr or result.stdout).strip()}"
    try:
        pushed, note = maybe_push(repo, "bookkeeping", now=now)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return True, False, f"committed locally; push scheduling failed: {exc}"
    return True, pushed, note


def maybe_push(repo: Path, event: str = "bookkeeping", *,
               now: datetime | None = None,
               only_if_configured: bool = False) -> tuple[bool, str]:
    """Push according to config; persist the batch window across processes."""
    if event not in {"bookkeeping", "merge", "park"}:
        raise ValueError(f"invalid push event: {event}")
    if not _main_checkout(repo):
        return False, f"{repo} is not the root of its git work tree"
    policy, minutes, configured = _config(repo)
    if only_if_configured and not configured:
        return False, "policy not configured; preserving local-only plan_commit behaviour"
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    stamp = now.timestamp()
    state_path = repo / ".devteam" / STATE_NAME
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock = state_path.with_suffix(".lock")
    for _ in range(50):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 60:
                    lock.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
            time.sleep(0.1)
    else:
        return False, "push-policy lock busy"
    try:
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        remotes = _run(repo, "remote")
        if not remotes.stdout.strip():
            return False, "committed locally (no remote configured — nothing to push)"
        ahead = _run(repo, "rev-list", "--count", "@{upstream}..HEAD")
        if ahead.returncode == 0 and ahead.stdout.strip() == "0":
            return False, "already published"
        window = state.get("window_start")
        if event == "bookkeeping" and policy == "merge_only":
            return False, "deferred until merge or park"
        if event == "bookkeeping" and policy == "batch":
            if window is None or stamp < float(window):
                state["window_start"] = stamp
                _save_state(state_path, state)
                return False, "deferred until batch boundary"
            if stamp - float(window) < minutes * 60:
                return False, "deferred until batch boundary"
        pushed = _run(repo, "push")
        if pushed.returncode != 0:
            return False, f"committed locally; push failed: {(pushed.stderr or pushed.stdout).strip()}"
        state["window_start"] = stamp
        _save_state(state_path, state)
        return True, "committed and pushed"
    finally:
        lock.unlink(missing_ok=True)


def _save_state(path: Path, state: dict) -> None:
    temp = path.with_suffix(f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(state), encoding="utf-8")
    os.replace(temp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--event", choices=("bookkeeping", "merge", "park"), default="bookkeeping")
    parser.add_argument("--only-if-configured", action="store_true",
                        help="preserve legacy local-only behaviour when the policy key is absent")
    args = parser.parse_args(argv)
    try:
        pushed, note = maybe_push(args.repo.resolve(), args.event,
                                  only_if_configured=args.only_if_configured)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"[push_policy] {exc}", file=sys.stderr)
        return 1
    print(f"[push_policy] {note}")
    return 1 if note.startswith(("committed locally; push failed", "push scheduling failed",
                                 "is not the root of its git work tree")) else 0


if __name__ == "__main__":
    raise SystemExit(main())
