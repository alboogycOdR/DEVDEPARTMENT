#!/usr/bin/env python3
"""supervisor.py — Autopilot supervisor for the DEVDEPARTMENT multi-agent system.

Watches PLAN.md, decides the next orchestration action per tick, executes it
(dispatch a builder / launch an ORCH review session / triage), and escalates to
the human ONLY per the escalation contract in docs/AUTOPILOT.md.

Design: the decision engine (decide()) is a pure function of plan state +
runtime state, so it is fully unit-testable without git, builders, or Claude.

Usage:
    python scripts/supervisor.py --once            # one tick, print decisions, execute
    python scripts/supervisor.py --once --dry-run  # one tick, print decisions only
    python scripts/supervisor.py --loop            # continuous (Ctrl+C or STOP file to halt)
    python scripts/supervisor.py --loop --interval 300 --max-ticks 50 --budget-minutes 480

Config: autopilot.json in repo root (created with defaults on first run).
"""
from __future__ import annotations

import argparse
import json
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, fields as _dc_fields
from datetime import datetime, timezone
from pathlib import Path

# Reuse the protocol parser — single source of truth.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_plan import parse_tasks, validate, Report, Task  # noqa: E402
import tg_commands as tgc  # noqa: E402 — Wave A-remainder: two-way Telegram
from tg_listener import TelegramListener  # noqa: E402
import scheduling  # noqa: E402 — Wave B: shared daily/weekly idempotency-marker helper
import budget  # noqa: E402 — Wave B: dispatch ceiling tracking
import maintenance  # noqa: E402 — Wave B: nightly self-audit
import distiller  # noqa: E402 — Wave C: post-review-batch distillation
import retro  # noqa: E402 — Wave C: weekly retro drafter
import control  # noqa: E402 — Wave I (I1): CONTROL-block single-writer blackboard
import usage_probe  # noqa: E402 — Wave I (I2): live usage-window meters
import tower_sync  # noqa: E402 — TOWER P1: snapshot push + queue pull (TASK-018 wiring)
import inbox  # noqa: E402 — TOWER P2: local command inbox consumer (TASK-018 wiring)
from slack_listener import SlackListener  # noqa: E402 — SLACK P1b-2: socket-mode listener (TASK-018 wiring)

UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"

import os as _os

# Dispatch command template. v4.8 FIX: this used to be a fixed dict
# (_DISPATCH_DEFAULTS) built ONCE at module-import time by reading the
# builder registry from "." -- the process's cwd at the moment `import
# supervisor` happened to run, which has NO relationship to the actual repo
# any given execute() call operates on. Found live: a project's own real
# autopilot.json (read from the test runner's cwd, not the fixture repo
# under test) produced a dispatch_cmd map missing an active unit, and
# DISPATCH raised KeyError instead of launching. The string TEMPLATE never
# actually needed a registry read at all -- it is the same shape for every
# unit ID on a given OS, and dispatch.sh/.ps1 do their own registry
# resolution internally (v4.7) once launched. So: compute it fresh, per
# call, scoped to the ACTUAL repo -- no import-time cwd dependency, no
# frozen snapshot that can go stale or belong to the wrong project.
def _dispatch_cmd_template() -> str:
    if _os.name == "nt":
        return "powershell -ExecutionPolicy Bypass -File scripts\\dispatch.ps1 -Builder {unit}"
    return "bash scripts/dispatch.sh {unit}"


def dispatch_cmd_for(unit: str, cfg: dict) -> str:
    """The command to launch `unit`. Prefers an explicit per-unit override in
    cfg["dispatch_cmd"] (a project may legitimately want one); otherwise
    computes the template fresh. No registry read, no cwd dependency --
    dispatch.sh/.ps1 resolve the unit themselves once launched (v4.7)."""
    explicit = (cfg.get("dispatch_cmd") or {}).get(unit)
    if explicit:
        return explicit
    return _dispatch_cmd_template().format(unit=unit)


# Kept for backward compatibility with anything reading DEFAULT_CONFIG
# directly (docs, external tooling) -- the LEGACY 3-unit set only, computed
# with zero registry/cwd dependency. Never authoritative: dispatch_cmd_for()
# above is what execute() actually calls, and it works for any unit ID,
# registered or not, on the fly.
_DISPATCH_DEFAULTS = {u: _dispatch_cmd_template().format(unit=u) for u in ("GB", "CX", "S5")}

DEFAULT_CONFIG = {
    "interval_seconds": 300,
    "stale_minutes": 90,
    "max_rework": 2,
    "max_dispatch_failures": 2,
    "digest_hours": 4,
    "notify_channels": ["console", "file"],
    # review uses sonnet-5 per ORCH model discipline table (CLAUDE.md 1020f7a)
    "review_cmd": "claude -p \"/devteam-review\" --model claude-opus-4-8 --dangerously-skip-permissions",
    # Model for the autopilot's OTHER headless judgment calls (scoped /approve
    # reviews, blocked-task triage). One key, consumed everywhere, so the
    # discipline table in CLAUDE.md never has to be hunted down across
    # hardcoded strings again. Opus rather than sonnet-5 since the S5 builder
    # IS sonnet-5 — same-model review shares the maker's failure distribution
    # (see CLAUDE.md "ORCH model discipline" for the full decision record).
    "judgment_model": "claude-opus-4-8",
    "dispatch_cmd": _DISPATCH_DEFAULTS,
    "builders": ["GB", "CX", "S5"],
    "autonomy_level": 2,
    # Wave A-remainder: two-way Telegram. Listener only starts if
    # "telegram" is in notify_channels AND both DEVTEAM_TG_TOKEN/DEVTEAM_TG_CHAT
    # env vars are set (never read from a tracked file — see notify.py).
    "telegram": {"chat_allowlist": [], "poll_interval_seconds": 20},
    # Wave B: nightly self-maintenance + dispatch ceiling.
    "maintenance": dict(maintenance.DEFAULT_MAINTENANCE_CFG),
    "budget": dict(budget.DEFAULT_BUDGET_CFG),
    # Wave C: continuous learning loop (distiller trigger + weekly retro).
    "learning": {
        "min_new_findings": 3,
        "distill_every_n_reviews": 5,
        "model": "claude-sonnet-5",
        "distill_timeout_seconds": 600,
        "rationalization_threshold": 3,
        "retro_day_of_week": 0,
        "retro_hour_utc": 6,
    },
    # Wave I (I1): CONTROL-block single-writer blackboard. Defaults to
    # "legacy" (builders still write PLAN.md themselves) — "strict" is a
    # deliberate opt-in once GB/CX are verified to reliably emit the
    # devteam-control fence in real sessions, not a silent default flip.
    "control": {
        "mode": "legacy",
    },
    # Wave I (I2): usage-window meters + dispatch defer gate.
    "usage": {
        "cache_ttl_minutes": 15,
        "defer_above_pct": 90,
        "critical_overrides": True,
    },
    # TASK-018: tower/slack keys mirror autopilot.json's template blocks
    # exactly (TOWER §1 P1 / SLACK §5). Ships disabled per the
    # ask-don't-auto-flip rule — same posture as ATLAS and control.mode.
    "tower": {
        "enabled": False,
        "url": "",
        "project_id": "",
        "_token_env": "DEVTEAM_TOWER_TOKEN",
    },
    "slack": {
        "enabled": False,
        "project_channel": "",
        "ops_channel": "",
        "thread_tracking": True,
    },
}


# ---------------------------------------------------------------- decisions --
@dataclass
class Action:
    kind: str          # ESCALATE_P1 | ESCALATE_P2 | REVIEW | REVIEW_TG | DISPATCH | DEFER_BUDGET | TRIAGE_UNBLOCK | REDISPATCH_STALE | DIGEST | IDLE | HALT
    detail: str
    unit: str | None = None       # for DISPATCH
    task_id: str | None = None


@dataclass
class RuntimeState:
    """Persisted across ticks in .autopilot_state.json."""
    rework_counts: dict[str, int] = field(default_factory=dict)     # task_id -> times sent to rework
    stale_resets: dict[str, int] = field(default_factory=dict)      # task_id -> times reset from stale
    conflict_counts: dict[str, int] = field(default_factory=dict)   # task_id -> OWNERSHIP_CONFLICT occurrences
    dispatch_failures: dict[str, int] = field(default_factory=dict) # unit -> consecutive failed dispatches
    busy_units: dict[str, str] = field(default_factory=dict)        # unit -> task_id currently dispatched
    last_digest_ts: str = ""
    mute_until: str = ""   # Wave A-remainder: ISO-8601 UTC ts; "" = not muted. Set by /mute.
    dispatch_log: list[str] = field(default_factory=list)          # Wave B: budget.py timestamp log
    pending_digest_lines: list[str] = field(default_factory=list)  # Wave B: e.g. "Self-audit: PASS", folded into the next P0 digest
    reviews_since_distill: int = 0  # Wave C: reset to 0 after each distiller.run()
    unreported_counts: dict[str, int] = field(default_factory=dict)  # Wave I: consecutive no-CONTROL-block runs per task

    @classmethod
    def load(cls, path: Path) -> "RuntimeState":
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                known = {f.name for f in _dc_fields(cls)}
                return cls(**{k: v for k, v in raw.items() if k in known})
            except (json.JSONDecodeError, TypeError):
                pass
        return cls()

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.__dict__, indent=2), encoding="utf-8")


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, UTC_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def is_muted(state: "RuntimeState", now: datetime) -> bool:
    """True if P0/P2 notifications are currently suppressed by an active /mute.
    P1 is never subject to this check — callers must not gate ESCALATE_P1 on it."""
    if not state.mute_until:
        return False
    until = _parse_ts(state.mute_until)
    return until is not None and now < until


def _deps_done(task: Task, by_id: dict[str, Task]) -> bool:
    raw = task.get("Depends_On")
    if task.is_empty("Depends_On"):
        return True
    for dep in re.split(r"[,\s]+", raw):
        if re.match(r"^TASK-[A-Z0-9-]+$", dep):
            d = by_id.get(dep)
            if d is None or d.get("Status") != "done":
                return False
    return True


def _active_builders(cfg: dict) -> list:
    """The dispatchable roster, accepting both builders shapes (v4.7):
    legacy flat array used as-is; registry object's `active` list wins.
    Falls back gracefully — decide() must keep working with hand-rolled
    test configs."""
    b = cfg.get("builders")
    if isinstance(b, dict):
        active = b.get("active")
        if isinstance(active, list) and active:
            return list(active)
        defined = b.get("defined")
        if isinstance(defined, dict) and defined:
            return list(defined.keys())
        return []
    if isinstance(b, list):
        return list(b)
    return []


def decide(plan_text: str, state: RuntimeState, cfg: dict,
           now: datetime | None = None, stop_file_exists: bool = False,
           dossier_heartbeats: dict[str, datetime] | None = None,
           usage: dict | None = None) -> list[Action]:
    """Pure decision engine: plan + runtime state -> ordered list of actions for this tick.

    dossier_heartbeats (Wave I, control.mode=strict): task_id -> latest
    dossier work-log timestamp, pre-computed by the caller (decide() itself
    does no filesystem I/O — same "pure" contract as always; the tick loop
    reads dossier mtimes and passes the result in, exactly like state/cfg/now).

    usage (Wave I, I2): {"claude": {...}, "codex": {...}} from
    usage_probe.get_usage(), pre-computed by the caller for the same
    filesystem-purity reason — decide() never touches the usage cache file
    itself, it just consults whatever the tick loop already read once.
    """
    now = now or datetime.now(timezone.utc)
    control_mode = cfg.get("control", {}).get("mode", "legacy")
    dossier_heartbeats = dossier_heartbeats or {}
    usage = usage or {}
    actions: list[Action] = []

    if stop_file_exists:
        return [Action("HALT", "STOP file present in repo root — halting per safety rail #3")]

    # 1. Protocol legality gate
    rep: Report = validate(plan_text, control_mode)
    if not rep.ok:
        return [Action("ESCALATE_P1",
                       "PLAN.md is protocol-illegal — loop paused. Violations: " + " | ".join(rep.errors[:5]))]

    tasks = parse_tasks(plan_text, rep)
    by_id = {t.task_id: t for t in tasks}
    real = [t for t in tasks if "EXAMPLE" not in t.get("Title").upper()]
    if not real:
        return [Action("IDLE", "No real tasks in plan")]

    # 2. Rework-loop guardrail + reviews
    for t in real:
        if t.get("Status") == "needs_review":
            if state.rework_counts.get(t.task_id, 0) >= cfg["max_rework"]:
                actions.append(Action("ESCALATE_P1",
                                      f"{t.task_id} reached max_rework={cfg['max_rework']} — frozen for human review",
                                      task_id=t.task_id))
            else:
                actions.append(Action("REVIEW", f"{t.task_id} awaiting review", task_id=t.task_id))

    # 3. Blocked triage
    for t in real:
        if t.get("Status") != "blocked":
            continue
        reason = t.get("Blocked_Reason")
        if reason.startswith("SPEC_AMBIGUITY"):
            actions.append(Action("ESCALATE_P2", f"{t.task_id} blocked: SPEC_AMBIGUITY — human answer needed",
                                  task_id=t.task_id))
        elif reason.startswith("OWNERSHIP_CONFLICT"):
            n = state.conflict_counts.get(t.task_id, 0)
            if n >= 1:
                actions.append(Action("ESCALATE_P2",
                                      f"{t.task_id} blocked: repeated OWNERSHIP_CONFLICT — territory design needs human eyes",
                                      task_id=t.task_id))
            else:
                actions.append(Action("TRIAGE_UNBLOCK",
                                      f"{t.task_id}: ORCH to re-carve territories and unblock (attempt 1)",
                                      task_id=t.task_id))
        elif reason.startswith("MISSING_DEPENDENCY"):
            actions.append(Action("TRIAGE_UNBLOCK", f"{t.task_id}: ORCH to re-sequence dependencies",
                                  task_id=t.task_id))
        elif reason.startswith("TOOLING_FAILURE"):
            n = state.stale_resets.get(t.task_id, 0)
            kind = "ESCALATE_P2" if n >= 1 else "TRIAGE_UNBLOCK"
            actions.append(Action(kind, f"{t.task_id} blocked: TOOLING_FAILURE (retry {n + 1})", task_id=t.task_id))
        else:
            actions.append(Action("ESCALATE_P2", f"{t.task_id} blocked: {reason}", task_id=t.task_id))

    # 4. Stale heartbeat detection
    for t in real:
        if t.get("Status") in ("claimed", "in_progress"):
            ts = _parse_ts(t.get("Updated_At"))
            if control_mode == "strict":
                hb = dossier_heartbeats.get(t.task_id)
                if hb is not None and (ts is None or hb > ts):
                    ts = hb
            if ts is not None:
                age_min = (now - ts).total_seconds() / 60.0
                if age_min > cfg["stale_minutes"]:
                    n = state.stale_resets.get(t.task_id, 0)
                    if n >= 2:
                        actions.append(Action("ESCALATE_P2",
                                              f"{t.task_id} stale for {int(age_min)}m after {n} redispatches — builder unable to hold session",
                                              task_id=t.task_id))
                    else:
                        actions.append(Action("REDISPATCH_STALE",
                                              f"{t.task_id} heartbeat stale ({int(age_min)}m > {cfg['stale_minutes']}m) — "
                                              f"redispatch {t.get('Assigned_To')}; its resume-first rule (protocol §10a) continues the existing branch",
                                              unit=t.get("Assigned_To"), task_id=t.task_id))

    # 5. Dispatch idle builders onto eligible work
    active_by_unit = {t.get("Assigned_To") for t in real
                      if t.get("Status") in ("claimed", "in_progress")}
    handled = {a.task_id for a in actions if a.task_id}
    for unit in _active_builders(cfg):
        if unit in active_by_unit:
            continue
        if state.dispatch_failures.get(unit, 0) >= cfg["max_dispatch_failures"]:
            continue  # parked after repeated failed launches; reap_inflight emitted the one P2
        eligible = [t for t in real
                    if t.get("Status") == "pending" and t.get("Assigned_To") == unit
                    and _deps_done(t, by_id) and t.task_id not in handled]
        if eligible:
            prio_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
            eligible.sort(key=lambda t: prio_rank.get(t.get("Priority"), 4))
            pick = eligible[0]
            # Wave B: budget ceiling gate. REDISPATCH_STALE (step 4, above) is a
            # heartbeat-recovery safety action and deliberately NOT budget-gated —
            # only genuinely NEW dispatches onto pending work are throttled here.
            # Wave I (I2): usage-window gate composes with it — either can defer,
            # and if BOTH trip for the same pick, that's one combined log line,
            # not two redundant defer actions for the same non-dispatch.
            budget_ok, budget_reason = budget.can_dispatch(state.dispatch_log, cfg.get("budget", {}), now)
            usage_ok, usage_reason = budget.can_dispatch_usage(
                usage, unit, pick.get("Priority"), cfg.get("usage", {}))
            if budget_ok and usage_ok:
                actions.append(Action("DISPATCH", f"{unit} idle; dispatching onto {pick.task_id} ({pick.get('Title')})",
                                      unit=unit, task_id=pick.task_id))
            elif not budget_ok and not usage_ok:
                actions.append(Action("DEFER_BUDGET",
                                      f"{unit} idle, {pick.task_id} eligible, but deferred — budget ceiling "
                                      f"({budget_reason}) AND usage gate ({usage_reason}) both tripped — retried next tick",
                                      unit=unit, task_id=pick.task_id))
            elif not budget_ok:
                actions.append(Action("DEFER_BUDGET",
                                      f"{unit} idle, {pick.task_id} eligible, but budget ceiling hit ({budget_reason}) — retried next tick",
                                      unit=unit, task_id=pick.task_id))
            else:
                actions.append(Action("DEFER_USAGE",
                                      f"{unit} idle, {pick.task_id} eligible, but usage gate hit ({usage_reason}) — retried next tick",
                                      unit=unit, task_id=pick.task_id))

    # 6. Wave complete?
    if all(t.get("Status") == "done" for t in real):
        return [Action("DIGEST", f"WAVE COMPLETE — all {len(real)} tasks done. Digest + halt.")]

    if not actions:
        actions.append(Action("IDLE", "All lanes busy or waiting on dependencies — nothing to do this tick"))
    return actions


# ------------------------------------------------------------------ executor --
def notify(cfg: dict, priority: str, message: str, repo: Path) -> None:
    script = repo / "scripts" / "notify.py"
    if script.exists():
        subprocess.run([sys.executable, str(script), "--priority", priority, "--message", message,
                        "--channels", ",".join(cfg["notify_channels"])], cwd=repo)
    else:
        print(f"[{priority}] {message}")


def log_line(repo: Path, text: str) -> None:
    ts = datetime.now(timezone.utc).strftime(UTC_FMT)
    with open(repo / "AUTOPILOT_LOG.md", "a", encoding="utf-8") as f:
        f.write(f"- [{ts}] {text}\n")


def run_shell(cmd: str, repo: Path) -> int:
    print(f"  $ {cmd}")
    return subprocess.run(cmd, shell=True, cwd=repo).returncode


def refresh_plan_from_head(repo: Path) -> None:
    """Force PLAN.md's working-tree copy to match HEAD before every tick's read.

    Builders' PLAN-only fallback landing (git update-ref, used when
    `git push . HEAD:main` is rejected by receive.denyCurrentBranch on this
    checked-out primary repo) moves the main ref but never touches this
    checkout's index/working tree. Without this refresh, decide() reads a
    stale on-disk PLAN.md and re-dispatches a builder onto a task that is
    already needs_review/done on HEAD (observed live: TASK-012 repeatedly
    redispatched to GB after such a landing). Best-effort/non-fatal: a
    failure here just leaves the previous (possibly stale) file in place for
    this tick, same as before this fix existed.
    """
    try:
        subprocess.run(
            ["git", "checkout", "HEAD", "--", "PLAN.md"],
            cwd=repo, capture_output=True, text=True, check=False,
        )
    except Exception as exc:
        print(f"[supervisor] refresh_plan_from_head skipped (non-fatal): {exc}", file=sys.stderr)


def launch_shell_bg(cmd: str, repo: Path) -> subprocess.Popen:
    """Fire-and-forget launch for DISPATCH/REDISPATCH_STALE: builders must run
    concurrently (one per unit), but execute()'s single-threaded action loop
    would otherwise block on subprocess.run() until the whole builder session
    exits -- starving every other unit's dispatch in the same tick (found live:
    CX sat idle behind a 20+ minute GB session because this call used to be
    synchronous). Popen returns immediately; the caller tracks the handle in
    `inflight` and reap_inflight() below picks up the exit code on a later
    tick. Safe because decide()'s dispatch-eligibility check reads PLAN.md's
    Status field, not any in-process bookkeeping -- once the builder's own
    claim commit lands (Status: claimed/in_progress), decide() already skips
    that unit on its own, with or without inflight tracking."""
    print(f"  $ {cmd}  (background)")
    return subprocess.Popen(cmd, shell=True, cwd=repo)


def reap_inflight(inflight: dict[str, tuple[subprocess.Popen, str, str]], cfg: dict,
                  state: RuntimeState, repo: Path, now: datetime, wait_seconds: float = 0.0) -> None:
    """Check every tracked background dispatch for completion; surface
    _notify_if_builder_unreachable for any that exited nonzero, exactly as
    the old synchronous path did, just deferred to whichever later tick
    notices the process has actually finished. `--once` supplies a short
    wait so a fast launch failure is not discarded when the process exits."""
    deadline = time.monotonic() + wait_seconds
    while True:
        for unit in list(inflight.keys()):
            proc, task_id, command = inflight[unit]
            rc = proc.poll()
            if rc is None:
                continue
            del inflight[unit]
            state.busy_units.pop(unit, None)
            if rc == 0:
                state.dispatch_failures[unit] = 0
                continue
            failures = state.dispatch_failures.get(unit, 0) + 1
            state.dispatch_failures[unit] = failures
            ceiling = cfg["max_dispatch_failures"]
            if failures == ceiling:
                detail = (f"{task_id or '?'}: dispatch for {unit} failed {failures} consecutive times "
                          f"(max_dispatch_failures={ceiling}) and is now parked; no further dispatches "
                          f"will be attempted until a successful dispatch resets the counter. Last exit code: {rc}; "
                          f"command: {command}. Check the dispatch transcript in AUTOPILOT_LOG.md.")
                if is_muted(state, now):
                    log_line(repo, f"MUTED: suppressed P2 dispatch-failure ceiling notice — {detail}")
                else:
                    notify(cfg, "P2", detail, repo)
            else:
                _notify_if_builder_unreachable(rc, unit, task_id or None, command, cfg, state, repo, now)
        if not inflight or time.monotonic() >= deadline:
            return
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


def execute(actions: list[Action], cfg: dict, state: RuntimeState, repo: Path, dry_run: bool,
            now: datetime | None = None,
            inflight: dict[str, tuple[subprocess.Popen, str, str]] | None = None) -> bool:
    """Execute actions. Returns False if the loop must halt."""
    now = now or datetime.now(timezone.utc)
    inflight = inflight if inflight is not None else {}
    halt = False
    for a in actions:
        line = f"{a.kind}: {a.detail}"
        print(f"[tick] {line}")
        log_line(repo, line)
        if dry_run:
            continue

        if a.kind == "HALT":
            halt = True
        elif a.kind == "ESCALATE_P1":
            notify(cfg, "P1", a.detail, repo)   # P1 is NEVER muted — safety rail, not a preference
            halt = True
        elif a.kind == "ESCALATE_P2":
            if is_muted(state, now):
                log_line(repo, f"MUTED: suppressed P2 — {a.detail}")
            else:
                notify(cfg, "P2", a.detail, repo)
        elif a.kind == "DIGEST":
            detail = a.detail
            if state.pending_digest_lines:
                # Wave B: fold in any queued maintenance/learning summary lines
                # (e.g. "Self-audit: PASS") that arrived since the last digest.
                detail = detail + "\n" + "\n".join(state.pending_digest_lines)
                state.pending_digest_lines = []
            if is_muted(state, now):
                log_line(repo, f"MUTED: suppressed P0 digest — {detail}")
            else:
                notify(cfg, "P0", detail, repo)
            halt = True
        elif a.kind == "REVIEW":
            rc = run_shell(cfg["review_cmd"], repo)
            if rc == 0:
                state.reviews_since_distill += 1
            if rc == 0 and a.task_id:
                # If task still needs_review after the session, it was sent to rework upstream;
                # rework accounting happens on next tick via REVIEW.md, conservatively bump here
                # only when the review session reports rework via exit conventions is unavailable —
                # so re-read the plan:
                txt = (repo / "PLAN.md").read_text(encoding="utf-8")
                rep = Report()
                for t in parse_tasks(txt, rep):
                    if t.task_id == a.task_id and t.get("Status") == "in_progress":
                        state.rework_counts[a.task_id] = state.rework_counts.get(a.task_id, 0) + 1
        elif a.kind == "REVIEW_TG" and a.task_id:
            # Wave A-remainder /approve: same review_cmd, but scoped explicitly to one
            # task (unlike the generic REVIEW action, which lets /devteam-review pick
            # whatever's needs_review on its own).
            scoped_prompt = f"/devteam-review {a.task_id}"
            jm = cfg.get("judgment_model", DEFAULT_CONFIG["judgment_model"])
            rc = run_shell(f"claude -p {shlex.quote(scoped_prompt)} --model {jm} --dangerously-skip-permissions", repo)
            if rc == 0:
                state.reviews_since_distill += 1
                txt = (repo / "PLAN.md").read_text(encoding="utf-8")
                rep = Report()
                for t in parse_tasks(txt, rep):
                    if t.task_id == a.task_id and t.get("Status") == "in_progress":
                        state.rework_counts[a.task_id] = state.rework_counts.get(a.task_id, 0) + 1
        elif a.kind == "DISPATCH" and a.unit:
            command = dispatch_cmd_for(a.unit, cfg)
            log_line(repo, f"DISPATCH_COMMAND unit={a.unit} task={a.task_id or '—'} command={command}")
            proc = launch_shell_bg(command, repo)
            inflight[a.unit] = (proc, a.task_id or "", command)
            state.busy_units[a.unit] = a.task_id or ""
            state.dispatch_log = budget.record_dispatch(state.dispatch_log, now)
        elif a.kind == "TRIAGE_UNBLOCK" and a.task_id:
            if "OWNERSHIP_CONFLICT" in a.detail:
                state.conflict_counts[a.task_id] = state.conflict_counts.get(a.task_id, 0) + 1
            # Scope triage = architectural judgment → judgment_model (opus-4-8) per
            # ORCH model discipline in CLAUDE.md — must NOT share a model with the
            # S5 builder (sonnet-5) whose blocked tasks it may be triaging.
            triage_prompt = (f"/devteam-status then triage blocked task {a.task_id} per protocol section 7: "
                             f"resolve and unblock if within ORCH authority; otherwise leave blocked and state why.")
            jm = cfg.get("judgment_model", DEFAULT_CONFIG["judgment_model"])
            run_shell(f"claude -p {shlex.quote(triage_prompt)} --model {jm} --dangerously-skip-permissions", repo)
        elif a.kind == "REDISPATCH_STALE" and a.task_id and a.unit:
            state.stale_resets[a.task_id] = state.stale_resets.get(a.task_id, 0) + 1
            # Protocol §10a: do NOT reset the task to pending. The builder's own
            # resume-first rule (briefing step 2) finds its in_progress/claimed
            # task, re-reads the last Progress_Note, and continues on the
            # existing branch. Resetting here would create the ghost-task
            # failure mode the protocol explicitly warns about.
            command = dispatch_cmd_for(a.unit, cfg)
            log_line(repo, f"DISPATCH_COMMAND unit={a.unit} task={a.task_id} command={command}")
            proc = launch_shell_bg(command, repo)
            inflight[a.unit] = (proc, a.task_id or "", command)
    return not halt


def maybe_distill(repo: Path, cfg: dict, state: RuntimeState, now: datetime) -> None:
    """Wave C: post-review-batch distillation trigger (fail-open).

    Counted in execute() above via state.reviews_since_distill; a
    threshold-based trigger (rather than time-based) keeps distillation tied
    to actual review activity, and distiller's own min_new_findings gate
    prevents noise-distilling even if this fires more often than useful.

    Amendments get their own P2 through the normal notify()/is_muted() path
    — distiller.py itself can't check mute state (it has no RuntimeState),
    and deliberately doesn't try to; that responsibility lives here.
    """
    try:
        learning_cfg = cfg.get("learning", {})
        n_trigger = int(learning_cfg.get("distill_every_n_reviews", 5))
        if state.reviews_since_distill < n_trigger:
            return
        d_result = distiller.run(repo, cfg)
        if not (d_result.ok and not d_result.skipped):
            return
        state.reviews_since_distill = 0
        if d_result.new_instincts or d_result.updated_instincts:
            log_line(repo, "DISTILL: "
                     f"new={len(d_result.new_instincts)} "
                     f"updated={len(d_result.updated_instincts)}")
        for amend_id in d_result.amendments:
            amend_file = repo / ".devteam" / "pending_amendments" / f"{amend_id}.md"
            try:
                body = amend_file.read_text(encoding="utf-8")
            except OSError:
                body = ""
            head_lines = [ln.strip("# ").strip()
                         for ln in body.splitlines()[3:6] if ln.strip()]
            head = " / ".join(head_lines)[:300]
            msg = (f"⚠️ P2: constitutional amendment proposed — {amend_id}\n"
                  f"{head}\n"
                  f"Reply: /approve {amend_id}  or  /rework {amend_id} <reason>")
            if is_muted(state, now):
                log_line(repo, f"MUTED: suppressed P2 — amendment {amend_id}")
            else:
                notify(cfg, "P2", msg, repo)
    except Exception as exc:  # never let distillation break a tick
        print(f"[distill] skipped this tick (non-fatal): {exc}", file=sys.stderr)


def maybe_run_retro(repo: Path, cfg: dict, now: datetime) -> None:
    """Wave C: weekly retro drafter (shared scheduling.py marker, same
    idempotency pattern as the Wave B maintenance-hour gate)."""
    try:
        learning_cfg = cfg.get("learning", {})
        retro_marker = repo / ".devteam" / "last_retro_week.txt"
        if not scheduling.should_run_weekly(retro_marker,
                                            int(learning_cfg.get("retro_day_of_week", 0)),
                                            int(learning_cfg.get("retro_hour_utc", 6)),
                                            now):
            return
        retro_path = retro.run(repo, cfg)
        if retro_path is not None:
            scheduling.mark_done_weekly(retro_marker, now)
            log_line(repo, f"RETRO: drafted {retro_path.name}")
    except Exception as exc:  # never let the retro drafter break a tick
        print(f"[retro] skipped this tick (non-fatal): {exc}", file=sys.stderr)


def maybe_drain_control(repo: Path, cfg: dict, state: RuntimeState, now: datetime) -> None:
    """Wave I (I1): apply every queued CONTROL block and no-block marker
    before this tick's decide() call. A no-op (returns immediately) in
    control.mode=legacy — builders still write PLAN.md themselves, so
    there's nothing in .devteam/control/ to drain.

    Tracks consecutive UNREPORTED runs per task (state.unreported_counts):
    a successful CONTROL application resets the streak to 0; 2 consecutive
    unreported runs for the same task escalate P2, mirroring the dead-
    builder escalation posture already used elsewhere (same "retry once,
    then ask a human" shape as OWNERSHIP_CONFLICT/TOOLING_FAILURE triage).
    """
    if cfg.get("control", {}).get("mode", "legacy") != "strict":
        return
    try:
        ts = now.strftime(UTC_FMT)
        ctrl_results = control.drain_control_queue(repo, ts)
        for name, ok, detail in ctrl_results:
            log_line(repo, f"CONTROL: {name} -> {'applied' if ok else 'REJECTED'} ({detail})")
            if ok:
                # A successful report resets any unreported streak for that task.
                m = re.match(r"^(TASK-[A-Z0-9-]+)-", name)
                if m:
                    state.unreported_counts[m.group(1)] = 0
            else:
                task_match = re.search(r"'(TASK-[A-Z0-9-]+)'", detail)
                task_ref = task_match.group(1) if task_match else name
                if is_muted(state, now):
                    log_line(repo, f"MUTED: suppressed P2 — CONTROL rejected {name}")
                else:
                    notify(cfg, "P2", f"⚠️ P2: CONTROL block rejected for {task_ref}\n{detail}", repo)

        unrep_results = control.drain_unreported_queue(repo, ts)
        for task_id, detail, changed in unrep_results:
            n = state.unreported_counts.get(task_id, 0) + 1
            state.unreported_counts[task_id] = n
            log_line(repo, f"CONTROL: {task_id} UNREPORTED (streak={n}) — {detail}")
            if n >= 2:
                if is_muted(state, now):
                    log_line(repo, f"MUTED: suppressed P2 — {task_id} unreported x{n}")
                else:
                    notify(cfg, "P2",
                          f"⚠️ P2: {task_id} — {n} consecutive builder runs ended with no "
                          f"CONTROL block. Investigate: is the builder crashing before its "
                          f"final print, or silently violating the contract?", repo)
                state.unreported_counts[task_id] = 0  # escalated — restart the streak
    except Exception as exc:  # never let a bad control queue break a tick
        print(f"[control] skipped this tick (non-fatal): {exc}", file=sys.stderr)


def _dossier_heartbeats(repo: Path) -> dict[str, datetime]:
    """Wave I: dossiers/<TASK-ID>.md mtime as the liveness signal for that
    task, per the spec's 'dossier mtime/entries become the liveness signal'
    rule. Fail-open: unreadable dossiers dir -> empty dict (falls back to
    plain Updated_At staleness, same as legacy mode)."""
    out: dict[str, datetime] = {}
    d = repo / "dossiers"
    if not d.is_dir():
        return out
    for p in d.glob("TASK-*.md"):
        m = re.match(r"^(TASK-[A-Z0-9-]+)\.md$", p.name)
        if not m:
            continue
        try:
            out[m.group(1)] = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
    return out


def _notify_if_builder_unreachable(rc: int, unit: str, task_id: str | None, command: str, cfg: dict,
                                   state: RuntimeState, repo: Path, now: datetime) -> None:
    """Wave B, T1 Watchtower topology: dispatch_cmd may need a builder CLI
    (grok/codex) that only lives on a different machine than the one running
    this supervisor (e.g. clawsrv runs the listener/scheduler; the laptop
    holds the authenticated CLIs). That remains one candidate for a nonzero
    exit, but a local dispatch precondition (for example a stale worktree)
    is another, so this P2 deliberately does not diagnose either as certain."""
    detail = (f"{task_id or '?'}: dispatch for {unit} exited {rc}; command: {command}. "
             f"Candidates: builder CLI may be unreachable from this host (T1 Watchtower topology: "
             f"dispatch/review run where the builder CLIs are authenticated), or a local dispatch "
             f"precondition failed (for example a stale worktree directory). Check the dispatch "
             f"transcript in AUTOPILOT_LOG.md before redispatching.")
    if is_muted(state, now):
        log_line(repo, f"MUTED: suppressed P2 dispatch-unreachable notice \u2014 {detail}")
    else:
        notify(cfg, "P2", detail, repo)


# --------------------------------------------------- two-way telegram (Wave A-remainder) --
def _tg_log(repo: Path, cmd: str, task_id: str | None) -> None:
    """Every accepted TG command → one AUTOPILOT_LOG.md line, full audit trail,
    symmetrical with [ORCH]/[GB]/[CX] commit tags already in use."""
    log_line(repo, f"TG_COMMAND unit=TG cmd={cmd} task={task_id or '—'}")


def _process_tg_answer_or_rework(item: dict, repo: Path, cfg: dict, ts: str, token: str) -> None:
    cmd, args, chat_id = item["cmd"], item["args"], item["chat_id"]
    parsed = tgc.parse_task_and_text(args)
    if not parsed:
        tgc.send_reply(token, chat_id, f"Usage: {cmd} TASK-NNN <text>")
        _tg_log(repo, cmd, None)
        return
    task_id, free_text = parsed

    # Micro-transaction, protocol §4/§10a discipline applied to a single remote
    # writer: pull -> parse -> edit ONLY this task's block -> commit -> push.
    tgc.git_pull(repo)
    plan_path = repo / "PLAN.md"
    plan_text = plan_path.read_text(encoding="utf-8")
    apply_fn = tgc.apply_answer if cmd == "/answer" else tgc.apply_rework
    result = apply_fn(plan_text, task_id, free_text, ts)
    _tg_log(repo, cmd, task_id)

    if not result.changed:
        tgc.send_reply(token, chat_id, f"⚠️ {result.detail}")
        return

    plan_path.write_text(result.text, encoding="utf-8")
    committed, pushed, note = tgc.git_commit_and_push_detailed(
        repo, f"chore(plan): {result.detail} [TG]")
    if committed:
        tgc.send_reply(token, chat_id,
                       f"✅ {result.detail}" if pushed else f"✅ {result.detail} — {note}")
    else:
        tgc.send_reply(token, chat_id,
                       f"⚠️ {result.detail} — applied locally, but git commit/push failed "
                       f"(no git repo, or a real push conflict). Check the repo on the host.")


def _process_tg_command(item: dict, repo: Path, cfg: dict, state: RuntimeState,
                        wave_event: "threading.Event", now: datetime, token: str) -> Action | None:
    """Handle exactly one queued Telegram command. Returns an extra Action for
    execute() to run this tick (currently only /approve -> REVIEW_TG), or None.

    /stop is handled FIRST and touches nothing but the STOP file itself — per
    the spec's non-negotiable requirement, it must keep working even if every
    other subsystem (PLAN.md, git, the board) is broken.
    """
    cmd, args, chat_id = item["cmd"], item["args"], item["chat_id"]
    ts = now.strftime(UTC_FMT)

    if cmd == "/stop":
        (repo / "STOP").write_text(f"Stopped via Telegram /stop at {ts}\n", encoding="utf-8")
        _tg_log(repo, cmd, None)
        tgc.send_reply(token, chat_id, "⛔ STOP file created. Supervisor halts within one tick.")
        return None

    if cmd == "/resume":
        p = repo / "STOP"
        existed = p.exists()
        if existed:
            p.unlink()
        _tg_log(repo, cmd, None)
        tgc.send_reply(token, chat_id, "▶️ STOP cleared — resuming." if existed else "Already running (no STOP file).")
        return None

    if cmd == "/wave":
        wave_event.set()
        _tg_log(repo, cmd, None)
        tgc.send_reply(token, chat_id, "⏩ Waking the loop early.")
        return None

    if cmd == "/mute":
        secs = tgc.parse_mute_args(args)
        if secs is None:
            tgc.send_reply(token, chat_id, "Usage: /mute <duration e.g. 2h, 30m>")
            _tg_log(repo, cmd, None)
            return None
        until_dt = now.timestamp() + secs
        state.mute_until = datetime.fromtimestamp(until_dt, tz=timezone.utc).strftime(UTC_FMT)
        _tg_log(repo, cmd, None)
        tgc.send_reply(token, chat_id, f"🔇 P0/P2 muted until {state.mute_until} (P1 always gets through).")
        return None

    if cmd == "/digest":
        try:
            from board_publisher import build_board, DEFAULT_BOARD_CFG
            board_cfg = {**DEFAULT_BOARD_CFG, **cfg.get("board", {})}
            board = build_board(repo, board_cfg, now)
            text = tgc.render_digest(board)
        except Exception as exc:  # never let a broken board block /digest's reply
            text = f"Digest generation failed: {exc}"
        if state.pending_digest_lines:
            text = text + "\n" + "\n".join(state.pending_digest_lines)
            state.pending_digest_lines = []
        if is_muted(state, now):
            log_line(repo, f"MUTED: suppressed on-demand P0 digest")
            tgc.send_reply(token, chat_id, "Digest suppressed — currently muted.")
        else:
            notify(cfg, "P0", text, repo)
            state.last_digest_ts = ts
            tgc.send_reply(token, chat_id, "📊 Digest sent.")
        _tg_log(repo, cmd, None)
        return None

    if cmd == "/status":
        try:
            from board_publisher import build_board, DEFAULT_BOARD_CFG
            board_cfg = {**DEFAULT_BOARD_CFG, **cfg.get("board", {})}
            board = build_board(repo, board_cfg, now)
            text = tgc.render_status(board)
        except Exception as exc:
            text = f"/status failed: {exc}"
        _tg_log(repo, cmd, None)
        tgc.send_reply(token, chat_id, text)
        return None

    if cmd == "/board":
        _tg_log(repo, cmd, None)
        tgc.send_reply(token, chat_id, tgc.render_board_url(cfg))
        return None

    if cmd == "/usage":
        try:
            from board_publisher import read_usage_summary
            text = tgc.render_usage(read_usage_summary(repo))
        except Exception as exc:
            text = f"/usage failed: {exc}"
        _tg_log(repo, cmd, None)
        tgc.send_reply(token, chat_id, text)
        return None

    if cmd == "/approve":
        args_stripped = (args or "").strip()
        amend_id = tgc.parse_amend_args(args_stripped)
        if amend_id:
            # Wave C constitutional gate: /approve on an AMEND-NNN only flips
            # that proposal's own Status field. It never touches AGENTS.md,
            # CLAUDE.md, or briefings/*.md — ORCH applies the actual edit in
            # a supervised session (second lock on the gate, beyond the
            # distiller itself never writing those files).
            p = tgc.amend_path(repo, amend_id)
            if not p.exists():
                tgc.send_reply(token, chat_id, f"{amend_id} not found in pending_amendments.")
                _tg_log(repo, cmd, None)
                return None
            result = tgc.apply_amend_approve(p.read_text(encoding="utf-8"))
            _tg_log(repo, cmd, amend_id)
            if result.changed:
                p.write_text(result.text, encoding="utf-8")
                tgc.send_reply(token, chat_id,
                               f"✅ {amend_id} approved — ORCH will apply the amendment "
                               f"in the next supervised review session.")
            else:
                tgc.send_reply(token, chat_id, f"⚠️ {amend_id}: {result.detail}")
            return None

        task_id = tgc.parse_approve_args(args)
        if not task_id:
            tgc.send_reply(token, chat_id, "Usage: /approve TASK-NNN | AMEND-NNN")
            _tg_log(repo, cmd, None)
            return None
        _tg_log(repo, cmd, task_id)
        tgc.send_reply(token, chat_id, f"🔎 Review queued for {task_id}.")
        return Action("REVIEW_TG", f"TG /approve {task_id}", task_id=task_id)

    if cmd == "/rework":
        amend_parsed = tgc.parse_amend_and_text(args)
        if amend_parsed:
            amend_id, reason = amend_parsed
            p = tgc.amend_path(repo, amend_id)
            if not p.exists():
                tgc.send_reply(token, chat_id, f"{amend_id} not found in pending_amendments.")
                _tg_log(repo, cmd, None)
                return None
            result = tgc.apply_amend_rework(p.read_text(encoding="utf-8"), reason, ts)
            _tg_log(repo, cmd, amend_id)
            if result.changed:
                p.write_text(result.text, encoding="utf-8")
                tgc.send_reply(token, chat_id, f"✅ {amend_id} {result.detail}")
            else:
                tgc.send_reply(token, chat_id, f"⚠️ {amend_id}: {result.detail}")
            return None
        _process_tg_answer_or_rework(item, repo, cfg, ts, token)
        return None

    if cmd == "/answer":
        _process_tg_answer_or_rework(item, repo, cfg, ts, token)
        return None

    # cmd == "help" (unrecognised / non-command text; also reached today by
    # Tower P2's "dispatch" vocabulary word, which has no supervisor handler
    # yet — see commands.py's comment on why adding one is a separate
    # behaviour change) — reply with usage where a chat exists, execute
    # nothing, but still log so an unhandled command never vanishes from the
    # audit trail without a trace.
    _tg_log(repo, cmd, None)
    tgc.send_reply(token, chat_id, tgc.HELP_TEXT)
    return None


def drain_command_queue(queues: "list[queue.Queue]", repo: Path, cfg: dict, state: RuntimeState,
                        wave_event: "threading.Event", now: datetime, token: str) -> list[Action]:
    """Drain every queued command from ALL listener queues (Telegram AND
    Slack, called once per tick, BEFORE decide(), so /answer / /rework edits
    are visible to this tick's decision) through the SAME per-item handler.
    SLACK §5: "the slack_listener's queue is the same queue.Queue already
    drained by _drain_tg_queue (renamed _drain_command_queue in the
    shared-validation refactor)". Queues are drained in the order given;
    each command is individually try/excepted so one failure (e.g. a
    corrupted PLAN.md breaking /answer) can never block or crash a later
    command in the same batch or a later queue (e.g. /stop)."""
    extra_actions: list[Action] = []
    for q in queues:
        while True:
            try:
                item = q.get_nowait()
            except queue.Empty:
                break
            try:
                action = _process_tg_command(item, repo, cfg, state, wave_event, now, token)
                if action is not None:
                    extra_actions.append(action)
            except Exception as exc:  # noqa: BLE001 — fail-open: never let one bad command wedge the tick
                log_line(repo, f"TG_COMMAND unit=TG cmd={item.get('cmd')} task=— ERROR: {exc}")
    return extra_actions


def drain_tg_queue(q: "queue.Queue", repo: Path, cfg: dict, state: RuntimeState,
                   wave_event: "threading.Event", now: datetime, token: str) -> list[Action]:
    """Backward-compatible single-queue entry point. Kept byte-identical in
    name and signature because tests/test_supervisor_telegram.py (outside
    this task's Owned_Paths, so it cannot be edited here) imports and calls
    it directly. TASK-018's actual "one drain path for both queues" wiring
    (SLACK §5) is drain_command_queue above; this is now a thin wrapper over
    it, so both entry points share one implementation."""
    return drain_command_queue([q], repo, cfg, state, wave_event, now, token)


def drain_inbox_commands(repo: Path, cfg: dict, state: RuntimeState,
                         wave_event: "threading.Event", now: datetime, token: str) -> list[Action]:
    """TOWER P2: drain `.devteam/inbox/` — already validated through
    commands.py by inbox.drain_inbox() itself (H1: "through the same
    handler path commands.py ... exposes") — through the SAME
    action-handler function Telegram/Slack commands use
    (_process_tg_command), so Tower commands are a pass-through onto
    existing handlers, not a second implementation (dossiers/TASK-018.md).

    Two-phase, per inbox.py's own contract: drain_inbox() never deletes a
    file; inbox.ack() is called only once that command's handler has run
    WITHOUT raising, so a crash mid-handling simply leaves the file to be
    retried next tick rather than silently losing or double-applying it.

    Absent/empty inbox is a pure no-op — inbox.drain_inbox() returns []
    when .devteam/inbox doesn't exist, so this call has zero effect on a
    repo that has never enabled Tower (TASK-018's byte-identical-when-
    disabled criterion)."""
    extra_actions: list[Action] = []
    try:
        items = inbox.drain_inbox(repo, cfg)
    except Exception as exc:  # a broken inbox must never wedge the tick
        print(f"[inbox] drain failed (non-fatal): {exc}", file=sys.stderr)
        return extra_actions
    for item in items:
        try:
            action = _process_tg_command(item, repo, cfg, state, wave_event, now, token)
            if action is not None:
                extra_actions.append(action)
            inbox.ack(repo, item)
        except Exception as exc:  # noqa: BLE001 — fail-open, mirrors drain_command_queue
            log_line(repo, f"TG_COMMAND unit=TOWER cmd={item.get('cmd')} task=— ERROR: {exc}")
    return extra_actions


# ---------------------------------------------------------------------- main --
def _merge_local_config(cfg: dict, local: dict) -> None:
    """Recursively merge `local`'s values into `cfg`, in place.

    Priority is the reverse of sync_from_pack.merge_add_only_keys: here a key
    PRESENT in `local` always wins (dicts are merged recursively so a partial
    override, e.g. just `tower.enabled`, doesn't clobber sibling keys like
    `tower.url`); a key ABSENT in `local` falls through untouched to whatever
    `cfg` already has from autopilot.json/DEFAULT_CONFIG. Same recursive-dict
    shape as the pack's add_only_keys merge (scripts/sync_from_pack.py), just
    with the winner reversed (TASK-022)."""
    for key, value in local.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            _merge_local_config(cfg[key], value)
        else:
            cfg[key] = value


def load_config(repo: Path) -> dict:
    cfg_path = repo / "autopilot.json"
    if not cfg_path.exists():
        cfg_path.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
        print(f"[supervisor] Wrote default config to {cfg_path} — review it, especially dispatch_cmd/review_cmd.")
    cfg = {**DEFAULT_CONFIG, **json.loads(cfg_path.read_text(encoding="utf-8"))}
    cfg["telegram"] = {**DEFAULT_CONFIG["telegram"], **cfg.get("telegram", {})}
    cfg["maintenance"] = {**DEFAULT_CONFIG["maintenance"], **cfg.get("maintenance", {})}
    cfg["budget"] = {**DEFAULT_CONFIG["budget"], **cfg.get("budget", {})}
    cfg["learning"] = {**DEFAULT_CONFIG["learning"], **cfg.get("learning", {})}
    cfg["control"] = {**DEFAULT_CONFIG["control"], **cfg.get("control", {})}
    cfg["usage"] = {**DEFAULT_CONFIG["usage"], **cfg.get("usage", {})}
    cfg["tower"] = {**DEFAULT_CONFIG["tower"], **cfg.get("tower", {})}
    cfg["slack"] = {**DEFAULT_CONFIG["slack"], **cfg.get("slack", {})}

    # TASK-022: optional untracked per-project override. autopilot.json is
    # simultaneously the shipped pack template AND (for this repo) the live
    # project config — there is otherwise no way to run this repo differently
    # from what it ships to every other project short of committing the
    # difference into the shared template (twice-observed mistake: ATLAS,
    # then tower.enabled). Same pattern as secrets living in the environment
    # rather than a tracked file — genuinely project-specific values belong
    # beside the tracked config, not inside it. Absent file = silent no-op.
    local_path = repo / "autopilot.local.json"
    if local_path.exists():
        try:
            local_cfg = json.loads(local_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"[supervisor] autopilot.local.json is invalid JSON ({exc}) — "
                  f"ignoring it, using autopilot.json only", file=sys.stderr)
        else:
            if isinstance(local_cfg, dict):
                _merge_local_config(cfg, local_cfg)
            else:
                print(f"[supervisor] autopilot.local.json must be a JSON object — "
                      f"ignoring it, using autopilot.json only", file=sys.stderr)
    return cfg


def _start_tg_listener(repo: Path, cfg: dict) -> tuple[TelegramListener | None, "queue.Queue", "threading.Event"]:
    """Start the Telegram listener thread if configured. Returns (listener_or_None, queue, wave_event).
    The queue and wave_event are always returned (usable even with no listener) so the
    main loop's drain call and sleep-wait logic don't need two code paths."""
    tg_queue: "queue.Queue" = queue.Queue()
    wave_event = threading.Event()
    if "telegram" not in cfg.get("notify_channels", []):
        return None, tg_queue, wave_event

    token = _os.environ.get("DEVTEAM_TG_TOKEN", "")
    chat = _os.environ.get("DEVTEAM_TG_CHAT", "")
    if not token or not chat:
        print("[supervisor] 'telegram' in notify_channels but DEVTEAM_TG_TOKEN/DEVTEAM_TG_CHAT "
              "env vars are not set — two-way listener NOT started (never read credentials from a file).",
              file=sys.stderr)
        return None, tg_queue, wave_event

    tg_cfg = cfg["telegram"]
    offset_path = repo / ".devteam" / "tg_offset.txt"
    listener = TelegramListener(
        token=token,
        allowlist=tg_cfg.get("chat_allowlist", []),
        default_chat=chat,
        out_queue=tg_queue,
        offset_path=offset_path,
        poll_interval_seconds=tg_cfg.get("poll_interval_seconds", 20),
    )
    listener.start()
    print(f"[supervisor] Telegram listener started "
          f"(allowlist size={len(tg_cfg.get('chat_allowlist') or []) or 1}).")
    return listener, tg_queue, wave_event


def _start_slack_listener(cfg: dict) -> tuple["SlackListener | None", "queue.Queue"]:
    """Start the Slack listener thread if configured — same fail-open
    posture as _start_tg_listener: 'slack' in notify_channels but missing
    env vars → one warning, listener NOT started (never read credentials
    from a file), every other channel unaffected (SLACK §5/§9; §9:
    "Telegram start logic unchanged"). The slack_sdk-not-installed case is
    handled inside SlackListener.start() itself (its own import guard), so
    this function only needs to gate on config + env.

    Unlike _start_tg_listener this does NOT create a second wave_event —
    main() passes the one wave_event already obtained from the Telegram
    listener startup to both drains, so /wave behaves identically
    regardless of which transport it arrived on."""
    slack_queue: "queue.Queue" = queue.Queue()
    if "slack" not in cfg.get("notify_channels", []):
        return None, slack_queue

    app_token = _os.environ.get("DEVTEAM_SLACK_APP_TOKEN", "")
    bot_token = _os.environ.get("DEVTEAM_SLACK_TOKEN", "")
    if not app_token or not bot_token:
        print("[supervisor] 'slack' in notify_channels but DEVTEAM_SLACK_APP_TOKEN/DEVTEAM_SLACK_TOKEN "
              "env vars are not set — Slack listener NOT started (never read credentials from a file).",
              file=sys.stderr)
        return None, slack_queue

    listener = SlackListener(app_token=app_token, bot_token=bot_token, out_queue=slack_queue)
    listener.start()
    if listener.available:
        print("[supervisor] Slack listener started.")
    else:
        # SlackListener.start() already refused internally (its own
        # slack_sdk import guard) and logged via its _log callback, which
        # defaults to Python `logging` (not necessarily visible on stderr) —
        # print an explicit stderr line here too so this is never silent to
        # an operator watching the console, same posture as the missing-env
        # warning above.
        print("[supervisor] Slack listener not started — slack_sdk not installed "
              "(pip install slack_sdk); every other channel is unaffected.", file=sys.stderr)
    return listener, slack_queue


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="DEVDEPARTMENT autopilot supervisor")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--once", action="store_true", help="run a single tick")
    mode.add_argument("--loop", action="store_true", help="run continuously")
    ap.add_argument("--dry-run", action="store_true", help="print decisions without executing")
    ap.add_argument("--interval", type=int, help="seconds between ticks (loop mode)")
    ap.add_argument("--max-ticks", type=int, default=0, help="stop after N ticks (0 = unlimited)")
    ap.add_argument("--budget-minutes", type=int, default=0, help="stop after N minutes (0 = unlimited)")
    ap.add_argument("--repo", default=".", help="repo root (default: cwd)")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    plan = repo / "PLAN.md"
    if not plan.exists():
        print(f"ERROR: {plan} not found — run from the project root.", file=sys.stderr)
        return 2

    cfg = load_config(repo)
    if args.interval:
        cfg["interval_seconds"] = args.interval
    state_path = repo / ".autopilot_state.json"
    state = RuntimeState.load(state_path)

    tg_listener, tg_queue, wave_event = _start_tg_listener(repo, cfg)
    slack_listener, slack_queue = _start_slack_listener(cfg)
    tg_token = _os.environ.get("DEVTEAM_TG_TOKEN", "")

    start = time.monotonic()
    ticks = 0
    # Background dispatch tracking (Popen handles, not persisted -- see
    # launch_shell_bg's docstring): lives for the lifetime of this process
    # only. A supervisor restart while a builder is mid-session (before its
    # claim commit lands) loses this bookkeeping, but decide()'s own
    # PLAN.md-based busy check is what actually prevents double-dispatch,
    # not this dict -- it just carries the exit code through to
    # _notify_if_builder_unreachable once a background dispatch finishes.
    inflight: dict[str, tuple[subprocess.Popen, str, str]] = {}
    print(f"[supervisor] Autopilot L{cfg['autonomy_level']} — repo {repo} — "
          f"{'DRY RUN' if args.dry_run else 'LIVE'} — {'loop' if args.loop else 'single tick'}")

    try:
        while True:
            ticks += 1
            now = datetime.now(timezone.utc)
            print(f"\n===== TICK {ticks} — {now.strftime(UTC_FMT)} =====")

            # Pick up exit codes from any background dispatch that finished
            # since the last tick (see launch_shell_bg/reap_inflight above).
            if not args.dry_run:
                reap_inflight(inflight, cfg, state, repo, now)

            # Drain Telegram + Slack commands BEFORE deciding, so /answer /
            # /rework edits (and /stop) are visible to this tick's decision
            # and PLAN.md read (SLACK §5: one drain path for both queues).
            queue_actions = drain_command_queue([tg_queue, slack_queue], repo, cfg, state, wave_event, now, tg_token) \
                if not args.dry_run else []

            # TOWER P2: drain .devteam/inbox/ BEFORE decide() too (spec
            # wording is exact — "in supervisor.py, before decide()"),
            # through the SAME action-handler path as the queue commands
            # above. Absent/disabled inbox is a no-op (TASK-018).
            inbox_actions = drain_inbox_commands(repo, cfg, state, wave_event, now, tg_token) \
                if not args.dry_run else []

            # Wave B: nightly self-maintenance scheduler check. Cheap outer gate
            # here avoids importing/invoking the full audit every 5-minute tick;
            # run_nightly_audit() itself re-checks the same marker (defense in
            # depth) so this is safe even if the outer gate's clock and the
            # audit's clock ever briefly disagree.
            if not args.dry_run:
                try:
                    m_cfg = {**maintenance.DEFAULT_MAINTENANCE_CFG, **cfg.get("maintenance", {})}
                    marker_path = repo / ".devteam" / "last_audit_date.txt"
                    if scheduling.should_run_daily(marker_path, m_cfg["hour_utc"], now):
                        m_result = maintenance.run_nightly_audit(repo, cfg, now=now)
                        if m_result.ran:
                            log_line(repo, f"MAINTENANCE: {m_result.digest_line}")
                            state.pending_digest_lines.append(m_result.digest_line)
                except Exception as exc:  # never let maintenance break a tick
                    print(f"[maintenance] skipped this tick (non-fatal): {exc}", file=sys.stderr)

            # Wave C: post-review-batch distillation trigger + weekly retro
            # drafter. Both are standalone functions (maybe_distill /
            # maybe_run_retro) precisely so they're unit-testable without
            # driving this whole loop — same reasoning as maintenance.py's
            # run_nightly_audit being a separate callable from its own outer
            # gate above.
            if not args.dry_run:
                maybe_distill(repo, cfg, state, now)
                maybe_run_retro(repo, cfg, now)

            # Wave I (I1): drain queued CONTROL blocks + no-block markers
            # BEFORE decide() — exactly where the Telegram queue is already
            # drained above, and for the same reason: a builder's reported
            # state must be visible to this tick's decision.
            if not args.dry_run:
                maybe_drain_control(repo, cfg, state, now)

            if not args.dry_run:
                refresh_plan_from_head(repo)
            plan_text = plan.read_text(encoding="utf-8")
            dossier_heartbeats = _dossier_heartbeats(repo) if cfg.get("control", {}).get("mode") == "strict" else {}
            try:
                # Cache-only read in the common case — get_usage() only
                # re-probes (burning real usage) when its own TTL has
                # expired. Not gated on args.dry_run: like
                # dossier_heartbeats above, this is a read the decision
                # needs to be accurate, and skipping it would make a
                # --dry-run preview silently disagree with what a real
                # tick would actually decide.
                usage = usage_probe.get_usage(repo, cfg)
            except Exception as exc:
                print(f"[usage] skipped this tick (non-fatal): {exc}", file=sys.stderr)
                usage = {}
            actions = queue_actions + inbox_actions + decide(plan_text, state, cfg, now=now,
                                          stop_file_exists=(repo / "STOP").exists(),
                                          dossier_heartbeats=dossier_heartbeats,
                                          usage=usage)
            keep_going = execute(actions, cfg, state, repo, args.dry_run, now=now, inflight=inflight)
            if args.once and not args.dry_run:
                # A normal loop reaps on its next tick. A single-tick run has
                # no next tick, so wait briefly for dispatch's fast outcome.
                reap_inflight(inflight, cfg, state, repo, datetime.now(timezone.utc), wait_seconds=3.0)
            state.save(state_path)

            # v4: publish Mission Control board (throttled; a dead board never blocks a wave)
            if not args.dry_run:
                try:
                    from board_publisher import publish_throttled, DEFAULT_BOARD_CFG
                    board_cfg = {**DEFAULT_BOARD_CFG, **cfg.get("board", {})}
                    publish_throttled(repo, board_cfg)
                except Exception as _be:
                    print(f"[board] skipped (non-fatal): {_be}", file=sys.stderr)

            # TOWER P1 (TASK-018): snapshot push + queue pull, after the
            # board work above, in the same tick — H4 (one round-trip pair,
            # always project-initiated) and H5 (fail-open: any tower error
            # is one warning line, tick proceeds normally). Disabled by
            # default (tower.enabled=false); sync_tick's own gate makes this
            # a zero-I/O no-op in that case (byte-identical-when-disabled).
            if not args.dry_run:
                try:
                    tower_sync.sync_tick(repo, cfg, state={"mode": "loop" if args.loop else "once", "tick": ticks})
                except Exception as exc:  # belt-and-braces — sync_tick already fails open internally
                    print(f"[tower] skipped this tick (non-fatal): {exc}", file=sys.stderr)

            if not keep_going or args.once:
                break
            if args.max_ticks and ticks >= args.max_ticks:
                print("[supervisor] max-ticks reached — stopping."); break
            if args.budget_minutes and (time.monotonic() - start) / 60 >= args.budget_minutes:
                print("[supervisor] budget-minutes reached — stopping."); break

            # /wave (Wave A-remainder) wakes the loop early by setting wave_event;
            # otherwise this behaves exactly like the old time.sleep(interval).
            if wave_event.wait(timeout=cfg["interval_seconds"]):
                wave_event.clear()
    finally:
        if tg_listener is not None:
            tg_listener.stop()
        if slack_listener is not None:
            slack_listener.stop()


    print("[supervisor] Halted.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
