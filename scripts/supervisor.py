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
import hashlib
import json
import os
import queue
import re
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, fields as _dc_fields
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Reuse the protocol parser — single source of truth.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_plan import parse_tasks, validate, Report, Task, parse_owned_paths  # noqa: E402
import tg_commands as tgc  # noqa: E402 — Wave A-remainder: two-way Telegram
from tg_listener import TelegramListener  # noqa: E402
import scheduling  # noqa: E402 — Wave B: shared daily/weekly idempotency-marker helper
import budget  # noqa: E402 — Wave B: dispatch ceiling tracking
import maintenance  # noqa: E402 — Wave B: nightly self-audit
import distiller  # noqa: E402 — Wave C: post-review-batch distillation
import retro  # noqa: E402 — Wave C: weekly retro drafter
import control  # noqa: E402 — Wave I (I1): CONTROL-block single-writer blackboard
import push_policy  # noqa: E402 — Wave E: bookkeeping push schedule
import usage_probe  # noqa: E402 — Wave I (I2): live usage-window meters
import circuit_breaker  # noqa: E402 — stagnation detection (ported from ralph-claude-code, MIT)
import builder_registry  # noqa: E402 — worktree/branch resolution for stagnation git-diff
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
    "review_cmd": "claude -p \"Read .claude/commands/devteam-review.md and execute the review workflow end-to-end now for every task with Status: needs_review.\" --model claude-opus-4-8 --dangerously-skip-permissions",
    # Model for the autopilot's OTHER headless judgment calls (scoped /approve
    # reviews, blocked-task triage). One key, consumed everywhere, so the
    # discipline table in CLAUDE.md never has to be hunted down across
    # hardcoded strings again. Opus rather than sonnet-5 since the S5 builder
    # IS sonnet-5 — same-model review shares the maker's failure distribution
    # (see CLAUDE.md "ORCH model discipline" for the full decision record).
    "judgment_model": "claude-opus-4-8",
    "review": {"max_backoff_minutes": 120, "lock_stale_minutes": 90},
    "escalation": {"renotify_hours": {"P2": 4, "P1": 1}, "max_timer_resends": 1},
    "max_triage_attempts": 1,
    # Existing projects do not acquire content-change notifications merely by
    # updating the pack; the on-disk digest remains available either way.
    "status_digest": {"send": False},
    "dispatch_cmd": _DISPATCH_DEFAULTS,
    "builders": ["GB", "CX", "S5"],
    "autonomy_level": 2,
    # Wave A-remainder: two-way Telegram. Listener only starts if
    # "telegram" is in notify_channels AND both DEVTEAM_TG_TOKEN/DEVTEAM_TG_CHAT
    # env vars are set (never read from a tracked file — see notify.py).
    "telegram": {"chat_allowlist": [], "poll_interval_seconds": 20, "once_poll_seconds": 10},
    # Wave B: nightly self-maintenance + dispatch ceiling.
    "maintenance": dict(maintenance.DEFAULT_MAINTENANCE_CFG),
    "budget": dict(budget.DEFAULT_BUDGET_CFG),
    # Stagnation circuit breaker (ported from ralph-claude-code, MIT): a
    # builder can be alive and progress-free at once, which stale-heartbeat
    # detection above cannot see. See circuit_breaker.py's module docstring.
    "circuit_breaker": dict(circuit_breaker.DEFAULT_CIRCUIT_BREAKER_CFG),
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
    kind: str          # ESCALATE_P1 | ESCALATE_P2 | REVIEW | REVIEW_TG | DISPATCH | DEFER_BUDGET | TRIAGE_UNBLOCK | REDISPATCH_STALE | REDISPATCH_STAGNANT | DIGEST | IDLE | HALT
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
    stagnation_counts: dict[str, int] = field(default_factory=dict)  # task_id -> current consecutive no-progress streak
    stagnation_resets: dict[str, int] = field(default_factory=dict)  # task_id -> lifetime REDISPATCH_STAGNANT count (mirrors stale_resets)
    # Token-efficiency pass (ported from oikonomos a14f8976). A review is a full Opus session; without
    # memory the supervisor relaunched one per needs_review task on every tick.
    review_ledger: dict[str, dict] = field(default_factory=dict)    # task_id -> {key, done, fails, retry_after}
    escalated: dict[str, str] = field(default_factory=dict)         # escalation key -> UTC ts of last notify
    escalation_held: dict[str, str] = field(default_factory=dict)   # escalation key -> UTC ts of last held marker
    escalation_timer_resends: dict[str, int] = field(default_factory=dict)  # key -> timer notifications already sent
    halt_mtime: str = ""                                             # STOP file mtime already logged
    triage_counts: dict[str, dict[str, int]] = field(default_factory=dict)  # task_id -> reason -> attempts
    last_status_digest_ts: str = ""                                 # scripted status digest throttle
    # E-C: a parked loop is deliberately still alive: it drains commands,
    # maintenance and observability, but must not make new decisions.
    parked: dict[str, str] = field(default_factory=dict)              # {kind, reason, since}
    _corrupt_note: str = field(default="", repr=False, compare=False)

    @classmethod
    def load(cls, path: Path, quarantine: bool = True) -> "RuntimeState":
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise TypeError("state root is not an object")
                known = {f.name for f in _dc_fields(cls) if not f.name.startswith("_")}
                return cls(**{k: v for k, v in raw.items() if k in known})
            except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
                state = cls()
                if quarantine:
                    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                    aside = path.with_name(f"{path.stem}.corrupt-{stamp}{path.suffix}")
                    try:
                        os.replace(path, aside)
                        state._corrupt_note = f"{path.name} was corrupt ({exc}); renamed to {aside.name}"
                    except OSError as move_exc:
                        state._corrupt_note = f"{path.name} was corrupt ({exc}) and could not be quarantined ({move_exc})"
                return state
        return cls()

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        data = {key: value for key, value in self.__dict__.items() if not key.startswith("_")}
        try:
            temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def _parse_ts(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, UTC_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def review_key(task: "Task") -> str:
    """Fingerprint of ONE submission for review. It changes whenever the builder resubmits
    (new evidence or artifacts) or a rework verdict lands (new findings), and stays put while a
    reviewer leaves the task waiting (e.g. CROSS-MODEL REVIEW REQUIRED). Pure: plan text only."""
    import hashlib
    raw = "\x1f".join(task.get(f) for f in ("Test_Evidence", "Review_Findings", "Artifacts", "Branch"))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def ledger_key(task: "Task", head_shas: dict[str, str] | None) -> str:
    """Submission identity: its branch head, with a safe plan-text fallback."""
    return (head_shas or {}).get(task.task_id) or review_key(task)


def review_backoff_minutes(fails: int, cfg: dict) -> float:
    cap = float((cfg.get("review") or {}).get("max_backoff_minutes", 120))
    return min(5.0 * (2 ** max(0, fails - 1)), cap)


_DIGITS = re.compile(r"\d+")


def _reason_prefix(detail: str) -> str:
    match = re.search(r"(?:blocked:\s*|repeated\s+)([A-Z][A-Z_]+)", detail)
    if match:
        return match.group(1)
    for reason in ("SPEC_AMBIGUITY", "OWNERSHIP_CONFLICT", "MISSING_DEPENDENCY", "TOOLING_FAILURE"):
        if reason in detail:
            return reason
    return "OTHER"


def escalation_key(action: "Action") -> str:
    """Stable H1 identity: kind, task, reason and digit-masked detail."""
    return "|".join((action.kind, action.task_id or "-", _reason_prefix(action.detail),
                     _DIGITS.sub("#", action.detail)))


def _renotify_hours(action: "Action", cfg: dict) -> float:
    values = (cfg.get("escalation") or {}).get("renotify_hours", {})
    fallback = 1 if action.kind == "ESCALATE_P1" else 4
    if isinstance(values, dict):
        return float(values.get("P1" if action.kind == "ESCALATE_P1" else "P2", fallback))
    return float(values or fallback)


def _max_timer_resends(cfg: dict) -> int:
    value = (cfg.get("escalation") or {}).get("max_timer_resends", 1)
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 1


def _record_escalation_sent(action: "Action", state: "RuntimeState", now: datetime) -> None:
    key = escalation_key(action)
    if _parse_ts(state.escalated.get(key, "")) is not None:
        state.escalation_timer_resends[key] = state.escalation_timer_resends.get(key, 0) + 1
    else:
        state.escalation_timer_resends[key] = 0
    state.escalated[key] = now.strftime(UTC_FMT)


def _dedupe_escalations(actions: list, state: "RuntimeState", cfg: dict, now: datetime) -> list:
    """Apply H1's durable escalation ledger and emit one held marker per hold period."""
    out = []
    active = set()
    for a in actions:
        if a.kind in ("ESCALATE_P1", "ESCALATE_P2"):
            key = escalation_key(a)
            active.add(key)
            last = _parse_ts(state.escalated.get(key, ""))
            hold_seconds = _renotify_hours(a, cfg) * 3600
            if last is not None:
                due = (now - last).total_seconds() >= hold_seconds
                if not due or state.escalation_timer_resends.get(key, 0) >= _max_timer_resends(cfg):
                    held = _parse_ts(state.escalation_held.get(key, ""))
                    if held is None or (now - held).total_seconds() >= hold_seconds:
                        out.append(Action("ESCALATION_HELD", f"{a.kind} key={key}"))
                        state.escalation_held[key] = now.strftime(UTC_FMT)
                    continue
                state.escalation_held.pop(key, None)
        out.append(a)
    # Conditions no longer selected this tick are gone; their next appearance is new.
    for key in (set(state.escalated) - active):
        if key.startswith("STATUS_DIGEST|"):
            continue
        state.escalated.pop(key, None)
        state.escalation_held.pop(key, None)
        state.escalation_timer_resends.pop(key, None)
    return out


def judgment_prompt(command: str, args: str = "") -> str:
    """One safe prompt form for all supervisor-launched judgment sessions."""
    suffix = f" {args.strip()}" if args.strip() else ""
    return f"Read .claude/commands/{command}.md and execute it end-to-end now.{suffix}"


def _triage_attempt(state: "RuntimeState", task_id: str, reason: str) -> int:
    counts = state.triage_counts.setdefault(task_id, {})
    # Gracefully migrate state written by the short-lived pre-H2 shape.
    if not isinstance(counts, dict):
        counts = {"MISSING_DEPENDENCY": int(counts)}
        state.triage_counts[task_id] = counts
    if reason == "OWNERSHIP_CONFLICT" and reason not in counts:
        # v4.8 wrote this one category separately; preserve its retry budget
        # when loading an existing supervisor state file.
        counts[reason] = int(state.conflict_counts.get(task_id, 0))
    return int(counts.get(reason, 0))


def _triage_ceiling(reason: str, cfg: dict) -> int:
    if reason == "MISSING_DEPENDENCY":
        return 2
    return int(cfg.get("max_triage_attempts", 1))


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
           stop_file_mtime: str = "",
           dossier_heartbeats: dict[str, datetime] | None = None,
           usage: dict | None = None,
           stagnation_signal: dict[str, dict] | None = None,
           head_shas: dict[str, str] | None = None,
           review_text: str = "") -> list[Action]:
    """Pure decision engine: plan + runtime state -> ordered list of actions for this tick.

    dossier_heartbeats (Wave I, control.mode=strict): task_id -> latest
    dossier work-log timestamp, pre-computed by the caller (decide() itself
    does no filesystem I/O — same "pure" contract as always; the tick loop
    reads dossier mtimes and passes the result in, exactly like state/cfg/now).

    usage (Wave I, I2): {"claude": {...}, "codex": {...}} from
    usage_probe.get_usage(), pre-computed by the caller for the same
    filesystem-purity reason — decide() never touches the usage cache file
    itself, it just consults whatever the tick loop already read once.

    stagnation_signal (circuit breaker, ported from ralph-claude-code):
    task_id -> {"changed": bool, "denials": int}, pre-computed by the tick
    loop's _stagnation_signal() from a git diff in the unit's worktree plus
    gateguard's denial counter — same filesystem-purity reason as the two
    above. A task_id ABSENT from this dict (rather than present with
    changed=True) means the caller could not confidently resolve the
    signal (no worktree yet, branch mismatch, git error) and decide() skips
    it entirely: fail-open, exactly like an empty dossier_heartbeats falls
    back to plain Updated_At staleness rather than guessing.
    """
    now = now or datetime.now(timezone.utc)
    control_mode = cfg.get("control", {}).get("mode", "legacy")
    dossier_heartbeats = dossier_heartbeats or {}
    usage = usage or {}
    stagnation_signal = stagnation_signal or {}
    actions: list[Action] = []

    if stop_file_exists:
        return [Action("HALT", f"STOP file present in repo root — halting per safety rail #3 mtime={stop_file_mtime}")]

    # 1. Protocol legality gate
    rep: Report = validate(plan_text, control_mode, review_text=review_text,
                           solo_max_files=int((cfg.get("plan") or {}).get("solo_max_files", 5)))
    if not rep.ok:
        return [Action("ESCALATE_P1",
                       "PLAN.md is protocol-illegal — loop paused. Violations: " + " | ".join(rep.errors[:5]))]

    tasks = parse_tasks(plan_text, rep)
    by_id = {t.task_id: t for t in tasks}
    real = [t for t in tasks if "EXAMPLE" not in t.get("Title").upper()]
    if not real:
        return [Action("IDLE", "No real tasks in plan")]

    # Durable parking replaces the old "P1 means exit" behaviour.  A valid
    # plan clears a plan-illegal P1; a changed frozen task clears its P1; a
    # newly pending task clears a completed wave.  All other parked ticks are
    # intentionally quiet and do not reach decide()/execute() work below.
    if state.parked:
        kind, reason = state.parked.get("kind", ""), state.parked.get("reason", "")
        frozen = re.search(r"(TASK-[A-Z0-9-]+)", reason)
        clear = (kind == "WAVE_DONE" and any(t.get("Status") == "pending" for t in real))
        clear = clear or (kind == "P1" and reason.startswith("PLAN.md"))
        clear = clear or (kind == "P1" and frozen and by_id.get(frozen.group(1))
                          and by_id[frozen.group(1)].get("Status") != "needs_review")
        if clear:
            state.parked = {}
        else:
            # Parking suppresses new decisions, not the existing P1's slow
            # reminder.  The escalation ledger is durable too, so a scheduled
            # --once process sends this only after the configured P1 interval.
            # This reconciles E-B's hourly frozen-task reminder with E-C's
            # requirement that the parked loop otherwise remain quiet.
            if kind == "P1":
                reminder = Action("ESCALATE_P1", reason,
                                  task_id=frozen.group(1) if frozen else None)
                return _dedupe_escalations([reminder], state, cfg, now)
            return [Action("IDLE", f"parked ({kind}): {reason}")]

    # 2. Rework-loop guardrail + reviews
    review_candidates: list[Task] = []
    for t in real:
        if t.get("Status") == "needs_review":
            if state.rework_counts.get(t.task_id, 0) >= cfg["max_rework"]:
                actions.append(Action("ESCALATE_P1",
                                      f"{t.task_id} reached max_rework={cfg['max_rework']} — frozen for human review",
                                      task_id=t.task_id))
            else:
                led = state.review_ledger.get(t.task_id) or {}
                if led.get("key") == ledger_key(t, head_shas):
                    if led.get("done"):
                        continue                       # this exact submission was already reviewed
                    retry = _parse_ts(led.get("retry_after", ""))
                    if retry is not None and now < retry:
                        continue                       # last attempt failed: back off
                review_candidates.append(t)
    if review_candidates:
        far_future = datetime.max.replace(tzinfo=timezone.utc)
        oldest = min(review_candidates, key=lambda task: _parse_ts(task.get("Updated_At")) or far_future)
        actions.append(Action("REVIEW", f"{oldest.task_id} awaiting review", task_id=oldest.task_id))

    # 3. Blocked triage. H2 owns a separate counter per reason, never the
    # stale-heartbeat counter: they describe different retry domains.
    for t in real:
        if t.get("Status") != "blocked":
            continue
        reason = t.get("Blocked_Reason")
        prefix = _reason_prefix(reason)
        if reason.startswith("SPEC_AMBIGUITY"):
            actions.append(Action("ESCALATE_P2", f"{t.task_id} blocked: SPEC_AMBIGUITY — human answer needed",
                                  task_id=t.task_id))
        elif prefix in ("OWNERSHIP_CONFLICT", "MISSING_DEPENDENCY", "TOOLING_FAILURE"):
            n = _triage_attempt(state, t.task_id, prefix)
            ceiling = _triage_ceiling(prefix, cfg)
            if n >= ceiling:
                actions.append(Action("ESCALATE_P2",
                                      f"{t.task_id} blocked: {prefix} unresolved after {n} triage attempts — needs human eyes",
                                      task_id=t.task_id))
            else:
                actions.append(Action("TRIAGE_UNBLOCK",
                                      f"{t.task_id} blocked: {prefix} triage attempt {n + 1}",
                                      task_id=t.task_id))
        else:
            actions.append(Action("ESCALATE_P2", f"{t.task_id} blocked: {reason}", task_id=t.task_id))

    # A counter has meaning only while the task remains blocked. Prune it when
    # the blackboard transitions the task out of that state.
    blocked_ids = {t.task_id for t in real if t.get("Status") == "blocked"}
    for task_id in set(state.triage_counts) - blocked_ids:
        state.triage_counts.pop(task_id, None)

    # 4. Stale heartbeat detection
    for t in real:
        if t.get("Status") in ("claimed", "in_progress") and t.get("Assigned_To") != "ORCH-SOLO":
            ts = _parse_ts(t.get("Updated_At"))
            # E-C heartbeat is the newest independently observable source:
            # PLAN timestamp, branch commit and dossier mtime.  The caller
            # supplies the latter two, in both legacy and strict mode.
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

    # 4b. Progress-based stagnation detection (circuit breaker, ported from
    # ralph-claude-code). A builder can pass step 4 forever — heartbeat
    # fresh, process alive — while never touching a file under its
    # Owned_Paths. Skip any task step 4 already queued an action for
    # (already_handled): both mechanisms would otherwise try to redispatch
    # the same unit in the same tick.
    already_handled = {a.task_id for a in actions if a.task_id}
    for t in real:
        if (t.get("Status") not in ("claimed", "in_progress")
                or t.get("Assigned_To") == "ORCH-SOLO" or t.task_id in already_handled):
            continue
        sig = stagnation_signal.get(t.task_id)
        if sig is None:
            continue  # caller could not confidently resolve this tick — fail open, never guess
        sample = circuit_breaker.StagnationSample(
            changed=bool(sig.get("changed")), denials=int(sig.get("denials") or 0))
        streak = circuit_breaker.update_streak(state.stagnation_counts.get(t.task_id, 0), sample)
        state.stagnation_counts[t.task_id] = streak
        if not circuit_breaker.is_stagnant(streak, sample, cfg["circuit_breaker"]):
            continue
        resets = state.stagnation_resets.get(t.task_id, 0)
        if circuit_breaker.remedial_kind(resets, cfg["circuit_breaker"]) == "ESCALATE_P2":
            actions.append(Action("ESCALATE_P2",
                                  f"{t.task_id} stagnant for {streak} consecutive tick(s) "
                                  f"({sample.denials} gateguard denial(s)) after {resets} stagnation "
                                  f"redispatch(es) — builder alive but producing no diff under its Owned_Paths",
                                  task_id=t.task_id))
        else:
            actions.append(Action("REDISPATCH_STAGNANT",
                                  f"{t.task_id} stagnant for {streak} consecutive tick(s) "
                                  f"({sample.denials} gateguard denial(s)) — redispatching "
                                  f"{t.get('Assigned_To')}; resume-first rule continues the existing branch",
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
    if all(t.get("Status") in ("done", "superseded") for t in real):
        return [Action("DIGEST", f"WAVE COMPLETE — all {len(real)} tasks done. Digest + park.")]

    actions = _dedupe_escalations(actions, state, cfg, now)
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
    env = dict(os.environ)
    env["DEVTEAM_DELEGATED"] = "1"
    return subprocess.run(cmd, shell=True, cwd=repo, env=env).returncode


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
    # Keep the child-only setting in the shell command instead of temporarily
    # mutating this long-running supervisor's own environment.  Apart from
    # avoiding an environment race with other launches, this keeps the Popen
    # call compatible with lightweight test doubles used by maintenance tests.
    delegated = f'set "DEVTEAM_DELEGATED=1" && {cmd}' if os.name == "nt" else f"DEVTEAM_DELEGATED=1 {cmd}"
    print(f"  $ {delegated}  (background)")
    return subprocess.Popen(delegated, shell=True, cwd=repo)


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
            _inflight_path(repo, unit).unlink(missing_ok=True)
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


def _inflight_path(repo: Path, unit: str) -> Path:
    return repo / ".devteam" / "inflight" / f"{unit}.json"


def _write_inflight(repo: Path, unit: str, proc: subprocess.Popen, task_id: str, command: str, now: datetime) -> None:
    """Persist the minimum cross-process launch record (E-C/H3)."""
    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or pid <= 0:
        return  # lightweight test doubles are intentionally not durable
    path = _inflight_path(repo, unit)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps({"pid": pid, "task_id": task_id, "cmd": command,
                                "started": now.strftime(UTC_FMT)}), encoding="utf-8")
    os.replace(temp, path)


def _reap_durable_inflight(cfg: dict, state: RuntimeState, repo: Path, now: datetime) -> None:
    """Remove dead persisted PIDs so a later --once process observes a launch.

    Exit status cannot be recovered after a supervisor process exits; a dead
    PID therefore counts as an unreachable dispatch, the conservative outcome.
    """
    directory = repo / ".devteam" / "inflight"
    if not directory.is_dir():
        return
    for path in directory.glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            pid, unit = int(record["pid"]), path.stem
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True
            except OSError:
                # Windows raises a generic OSError (for example WinError 87)
                # for a non-existent synthetic PID rather than
                # ProcessLookupError.  It is still a dead persisted launch.
                alive = False
            if alive:
                continue
            path.unlink(missing_ok=True)
            failures = state.dispatch_failures.get(unit, 0) + 1
            state.dispatch_failures[unit] = failures
            _notify_if_builder_unreachable(1, unit, record.get("task_id") or None,
                                           record.get("cmd", ""), cfg, state, repo, now)
        except (OSError, ValueError, TypeError, KeyError):
            path.unlink(missing_ok=True)


def _git_head_sha(repo: Path, branch: str) -> str:
    """Return a task branch's HEAD, without making a missing branch fatal."""
    if not branch or branch in ("—", "-"):
        return ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], cwd=repo,
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _review_head_shas(repo: Path, plan_text: str) -> dict[str, str]:
    return {
        task.task_id: sha
        for task in parse_tasks(plan_text, Report())
        if task.get("Status") == "needs_review"
        if (sha := _git_head_sha(repo, task.get("Branch")))
    }


def _lock_started(lock: Path) -> datetime | None:
    try:
        raw = json.loads(lock.read_text(encoding="utf-8"))
        return _parse_ts(raw.get("start", "")) if isinstance(raw, dict) else None
    except (OSError, ValueError):
        return None


def _acquire_review_lock(repo: Path, cfg: dict, now: datetime) -> str | None:
    """Atomically acquire the cross-process review lock, reclaiming stale locks."""
    lock = repo / ".devteam" / "review.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    stale_minutes = float((cfg.get("review") or {}).get("lock_stale_minutes", 90))
    for _ in range(2):
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            started = _lock_started(lock)
            if started is not None:
                age = (now - started).total_seconds() / 60.0
            else:
                try:
                    age = (time.time() - lock.stat().st_mtime) / 60.0
                except OSError:
                    continue
            if age < stale_minutes:
                return f"a review session is already running (lock {int(age)}m old)"
            log_line(repo, f"REVIEW_LOCK_STALE: taking over {int(age)}m-old review.lock")
            lock.unlink(missing_ok=True)
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"pid": os.getpid(), "start": now.strftime(UTC_FMT)}, stream)
        return None
    return "could not acquire review.lock"


def _run_review(action: Action, cfg: dict, state: RuntimeState, repo: Path, now: datetime) -> None:
    """Execute one selected review and persist a head-SHA keyed retry ledger entry."""
    import uuid
    busy = _acquire_review_lock(repo, cfg, now)
    if busy:
        log_line(repo, f"REVIEW_SKIPPED: {busy}")
        return
    plan_path = repo / "PLAN.md"
    session = uuid.uuid4().hex[:8]
    started = time.monotonic()
    try:
        before = {task.task_id: task for task in parse_tasks(plan_path.read_text(encoding="utf-8"), Report())}
        target = before.get(action.task_id)
        sha = _git_head_sha(repo, target.get("Branch")) if target else ""
        key = sha or (review_key(target) if target else "")
        log_line(repo, f"REVIEW_START task={action.task_id} sha={sha or '-'} session={session}")
        result = run_shell(cfg["review_cmd"], repo)
    finally:
        (repo / ".devteam" / "review.lock").unlink(missing_ok=True)
    after = {task.task_id: task for task in parse_tasks(plan_path.read_text(encoding="utf-8"), Report())}
    status = after.get(action.task_id).get("Status") if action.task_id in after else ""
    verdict = {"done": "approved", "in_progress": "rework"}.get(status, "none") if result == 0 else "none"
    if verdict == "approved":
        pushed, note = push_policy.maybe_push(repo, "merge", now=now)
        if not pushed:
            log_line(repo, f"MERGE_PUSH: {note}")
    if result == 0:
        state.reviews_since_distill += 1
        for task_id, before_task in before.items():
            after_task = after.get(task_id)
            if before_task.get("Status") == "needs_review" and after_task and after_task.get("Status") == "in_progress":
                state.rework_counts[task_id] = state.rework_counts.get(task_id, 0) + 1
    ledger = state.review_ledger.setdefault(action.task_id, {})
    ledger["key"] = key
    if verdict == "none":
        ledger["done"] = False
        ledger["fails"] = int(ledger.get("fails", 0)) + 1
        wait = review_backoff_minutes(ledger["fails"], cfg)
        ledger["retry_after"] = (now + timedelta(minutes=wait)).strftime(UTC_FMT)
        if result == 0:
            log_line(repo, f"REVIEW_NO_VERDICT: {action.task_id} retry in {int(wait)}m unless head moves")
    else:
        ledger.update(done=True, fails=0)
        ledger.pop("retry_after", None)
    log_line(repo, f"REVIEW_END task={action.task_id} verdict={verdict} duration={int(time.monotonic() - started)}s")


def execute(actions: list[Action], cfg: dict, state: RuntimeState, repo: Path, dry_run: bool,
            now: datetime | None = None,
            inflight: dict[str, tuple[subprocess.Popen, str, str]] | None = None) -> bool:
    """Execute actions. Returns False if the loop must halt."""
    now = now or datetime.now(timezone.utc)
    inflight = inflight if inflight is not None else {}
    halt = False
    review_ran = False
    for a in actions:
        if a.kind == "REVIEW" and review_ran:
            continue
        line = f"{a.kind}: {a.detail}"
        print(f"[tick] {line}")
        halt_mtime = a.detail.rsplit("mtime=", 1)[-1] if a.kind == "HALT" and "mtime=" in a.detail else ""
        if a.kind != "HALT" or halt_mtime != state.halt_mtime:
            log_line(repo, line)
            if a.kind == "HALT":
                state.halt_mtime = halt_mtime
        if dry_run:
            continue

        if a.kind == "HALT":
            halt = True
        elif a.kind == "ESCALATE_P1":
            notify(cfg, "P1", a.detail, repo)   # P1 is NEVER muted — safety rail, not a preference
            _record_escalation_sent(a, state, now)
            state.parked = {"kind": "P1", "reason": a.detail, "since": now.strftime(UTC_FMT)}
            pushed, note = push_policy.maybe_push(repo, "park", now=now)
            if not pushed:
                log_line(repo, f"PARK_PUSH: {note}")
        elif a.kind == "ESCALATE_P2":
            if is_muted(state, now):
                log_line(repo, f"MUTED: suppressed P2 — {a.detail}")
            else:
                notify(cfg, "P2", a.detail, repo)
                _record_escalation_sent(a, state, now)
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
            state.parked = {"kind": "WAVE_DONE", "reason": a.detail, "since": now.strftime(UTC_FMT)}
            pushed, note = push_policy.maybe_push(repo, "park", now=now)
            if not pushed:
                log_line(repo, f"PARK_PUSH: {note}")
        elif a.kind == "REVIEW":
            review_ran = True
            _run_review(a, cfg, state, repo, now)
        elif a.kind == "REVIEW_TG" and a.task_id:
            # Wave A-remainder /approve: same review_cmd, but scoped explicitly to one
            # task (unlike the generic REVIEW action, which lets /devteam-review pick
            # whatever's needs_review on its own).
            jm = cfg.get("judgment_model", DEFAULT_CONFIG["judgment_model"])
            prompt = judgment_prompt("devteam-review", f"Review only {a.task_id}.")
            rc = run_shell(f"claude -p {shlex.quote(prompt)} --model {jm} --dangerously-skip-permissions", repo)
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
            _write_inflight(repo, a.unit, proc, a.task_id or "", command, now)
            state.busy_units[a.unit] = a.task_id or ""
            state.dispatch_log = budget.record_dispatch(state.dispatch_log, now)
        elif a.kind == "TRIAGE_UNBLOCK" and a.task_id:
            reason = _reason_prefix(a.detail)
            counts = state.triage_counts.setdefault(a.task_id, {})
            if not isinstance(counts, dict):
                counts = {"MISSING_DEPENDENCY": int(counts)}
                state.triage_counts[a.task_id] = counts
            counts[reason] = int(counts.get(reason, 0)) + 1
            if reason == "OWNERSHIP_CONFLICT":
                state.conflict_counts[a.task_id] = counts[reason]
            # Scope triage = architectural judgment → judgment_model (opus-4-8) per
            # ORCH model discipline in CLAUDE.md — must NOT share a model with the
            # S5 builder (sonnet-5) whose blocked tasks it may be triaging.
            jm = cfg.get("judgment_model", DEFAULT_CONFIG["judgment_model"])
            prompt = judgment_prompt("devteam-status",
                                     f"Triage blocked task {a.task_id} per protocol section 7; resolve and unblock if within ORCH authority, otherwise leave it blocked and state why.")
            run_shell(f"claude -p {shlex.quote(prompt)} --model {jm} --dangerously-skip-permissions", repo)
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
        elif a.kind == "REDISPATCH_STAGNANT" and a.task_id and a.unit:
            # Same non-reset, resume-first relaunch as REDISPATCH_STALE above —
            # a separate lifetime counter (state.stagnation_resets) so a task's
            # heartbeat-staleness history and progress-stagnation history don't
            # share a redispatch budget.
            state.stagnation_resets[a.task_id] = state.stagnation_resets.get(a.task_id, 0) + 1
            command = dispatch_cmd_for(a.unit, cfg)
            log_line(repo, f"DISPATCH_COMMAND unit={a.unit} task={a.task_id} command={command}")
            proc = launch_shell_bg(command, repo)
            inflight[a.unit] = (proc, a.task_id or "", command)
    return not halt


def _owner_hold_digest(digest: str, plan_text: str, now: datetime) -> str:
    """Add durable owner holds to the scripted digest's Pending action section."""
    holds = []
    for task in parse_tasks(plan_text, Report()):
        if task.get("Status") != "owner_hold":
            continue
        since = _parse_ts(task.get("Updated_At"))
        age = max(0, int((now - since).total_seconds() // 3600)) if since else 0
        holds.append(f"- {task.task_id} owner hold: {task.get('Hold_On')} ({age}h)")
    if not holds or "\nProd:" not in digest:
        return digest
    before, after = digest.split("\nProd:", 1)
    if "\nPending action:\n- none" in before:
        before = before.replace("\nPending action:\n- none", "\nPending action:", 1)
    return before + "\n" + "\n".join(holds) + "\nProd:" + after


def maybe_status_digest(repo: Path, cfg: dict, state: RuntimeState, now: datetime) -> None:
    """Scripted status digest (no model call): rewrites .devteam/STATUS.md every interval and
    sends it on the notify channels only when it changed. Fail-open."""
    try:
        minutes = float(cfg.get("status_digest_minutes", 30))
        last = _parse_ts(state.last_status_digest_ts)
        if last is not None and (now - last).total_seconds() < minutes * 60:
            return
        import status_digest
        # The digest script always writes STATUS.md, but notification belongs
        # to this process so it shares H1's durable ledger rather than sending
        # once per new supervisor process.
        digest = status_digest.run(repo, cfg, now=now, send=False)
        digest = _owner_hold_digest(digest, (repo / "PLAN.md").read_text(encoding="utf-8"), now)
        (repo / ".devteam" / "STATUS.md").write_text(digest, encoding="utf-8")
        if bool((cfg.get("status_digest") or {}).get("send", False)):
            body = digest.rsplit("\nLocal time:", 1)[0]
            key = "STATUS_DIGEST|-|STATUS_DIGEST|" + hashlib.sha1(body.encode("utf-8")).hexdigest()
            if key not in state.escalated:
                notify(cfg, "P0", digest, repo)
                state.escalated[key] = now.strftime(UTC_FMT)
                # A changed digest is a new condition; retaining old hashes
                # would grow state without bound and serves no de-dup purpose.
                for old in [k for k in state.escalated if k.startswith("STATUS_DIGEST|") and k != key]:
                    state.escalated.pop(old, None)
        state.last_status_digest_ts = now.strftime(UTC_FMT)
    except Exception as exc:
        print(f"[status_digest] skipped this tick (non-fatal): {exc}", file=sys.stderr)


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
        inbox.report_source_missing(repo, "dossiers", d)
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


def _task_heartbeats(repo: Path, plan_text: str) -> dict[str, datetime]:
    """Newest dossier mtime or task-branch commit timestamp, per E-C/H5."""
    out = _dossier_heartbeats(repo)
    for task in parse_tasks(plan_text, Report()):
        branch = task.get("Branch")
        if not branch or branch in ("—", "-"):
            continue
        try:
            result = subprocess.run(["git", "log", "-1", "--format=%cI", branch], cwd=repo,
                                    capture_output=True, text=True, timeout=10)
            stamp = datetime.fromisoformat(result.stdout.strip().replace("Z", "+00:00"))
            if stamp.tzinfo and (task.task_id not in out or stamp > out[task.task_id]):
                out[task.task_id] = stamp
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
    return out


# ---------------------------------------------------- stagnation signal (I/O) --
# Best-effort git/gateguard readers feeding circuit_breaker.py's pure arithmetic.
# Every function below fails toward "no opinion" (None / omitted key), never
# toward "stagnant" — a wrong guess here must only under-detect a real stall,
# it must never falsely redispatch or escalate a builder that is fine.

def _worktree_and_branch(repo: Path, task_id: str, unit: str) -> tuple[Path, str] | None:
    """Resolve the unit's worktree path and this task's branch name from
    builder_registry, mirroring dispatch.sh's own WT=.../branch=task/<id>-<suffix>
    computation. Reimplemented rather than shared: dispatch.sh is bash and this
    needs it from Python — same reasoning as _deps_done's deliberate
    reimplementation elsewhere in this file. Returns None for an unregistered
    unit (fail-open path for the caller)."""
    try:
        _, entry = builder_registry.resolve(unit, repo)
    except builder_registry.RegistryError:
        return None
    worktree = repo.parent / f"wt-{entry['worktree_suffix']}-{repo.name}"
    branch = f"task/{task_id}-{entry['branch_suffix']}"
    return worktree, branch


def _git_diff_since(worktree: Path, branch: str, base_branch: str, owned_paths: list[str]) -> bool | None:
    """True/False if a diff-since-base could be confidently computed for
    owned_paths in worktree; None if not (worktree missing, HEAD isn't on the
    expected task branch yet, or a git command failed) — None is the caller's
    cue to omit the task_id entirely rather than guess. Counts both modified
    tracked files (git diff) and new untracked ones (git ls-files --others),
    since a builder's first commit on a brand-new file wouldn't show in the
    former alone."""
    if not worktree.is_dir():
        return None
    path_args = ["--", *owned_paths] if owned_paths else []
    try:
        head = subprocess.run(["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"],
                              capture_output=True, text=True, timeout=10)
        if head.returncode != 0 or head.stdout.strip() != branch:
            return None  # not (yet) checked out on the expected task branch
        diff = subprocess.run(["git", "-C", str(worktree), "diff", base_branch, "--name-only", *path_args],
                              capture_output=True, text=True, timeout=15)
        if diff.returncode != 0:
            return None
        if diff.stdout.strip():
            return True
        untracked = subprocess.run(
            ["git", "-C", str(worktree), "ls-files", "--others", "--exclude-standard", *path_args],
            capture_output=True, text=True, timeout=15)
        if untracked.returncode != 0:
            return None
        return bool(untracked.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None


def _gateguard_denials(repo: Path, unit: str) -> int:
    """Best-effort read of the denial counter hooks/gateguard.js maintains at
    .devteam/gateguard/denials/<UNIT>.json. Missing/corrupt -> 0: this is
    telemetry feeding a threshold, not a gate, and must never raise."""
    p = repo / ".devteam" / "gateguard" / "denials" / f"{unit}.json"
    try:
        return int(json.loads(p.read_text(encoding="utf-8")).get("count", 0))
    except FileNotFoundError:
        inbox.report_source_missing(repo, "gateguard", p)
        return 0
    except (OSError, json.JSONDecodeError, ValueError, TypeError):
        return 0


def _reset_gateguard_denials(repo: Path, unit: str) -> None:
    """Called only when this tick observed real progress (changed=True) for
    the unit's task — progress fully absolves whatever denials preceded it.
    gateguard.js itself never resets this counter; only progress does, so a
    healthy deny-once-then-allow cycle doesn't wash out a genuinely stuck
    unit's count between ticks."""
    p = repo / ".devteam" / "gateguard" / "denials" / f"{unit}.json"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"count": 0, "last": ""}), encoding="utf-8")
    except OSError:
        pass  # best-effort; a failed reset just delays the count returning to 0, never corrupts it


def _stagnation_signal(repo: Path, plan_text: str, cfg: dict) -> dict[str, dict]:
    """Best-effort per-task {"changed": bool, "denials": int} for decide()'s
    circuit-breaker step (4b). Fail-open PER TASK, not just per-call: any
    resolution failure for one task (unit not registered, worktree/branch not
    ready, git error) omits that task_id, while other tasks this tick still
    get a real signal. decide() treats an absent key as 'no opinion', never
    as 'stagnant'."""
    out: dict[str, dict] = {}
    try:
        rep = Report()
        tasks = parse_tasks(plan_text, rep)
    except Exception:
        return out
    base_branch = cfg.get("git", {}).get("base_branch", "main")
    for t in tasks:
        if t.get("Status") not in ("claimed", "in_progress"):
            continue
        unit = t.get("Assigned_To")
        resolved = _worktree_and_branch(repo, t.task_id, unit)
        if resolved is None:
            continue
        worktree, branch = resolved
        changed = _git_diff_since(worktree, branch, base_branch, parse_owned_paths(t.get("Owned_Paths")))
        if changed is None:
            continue
        denials = _gateguard_denials(repo, unit)
        if changed:
            _reset_gateguard_denials(repo, unit)
        out[t.task_id] = {"changed": changed, "denials": denials}
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
    committed, pushed, note = push_policy.commit_plan(
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
        # E-C: /resume is also the explicit operator escape hatch for the
        # durable P1/WAVE_DONE park state.  Merely clearing STOP would leave
        # a --once supervisor silently parked forever on its next start.
        was_parked = bool(state.parked)
        state.parked = {}
        _tg_log(repo, cmd, None)
        tgc.send_reply(token, chat_id,
                       "▶️ Resuming." if existed or was_parked else "Already running (no STOP file).")
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
    cfg["review"] = {**DEFAULT_CONFIG["review"], **cfg.get("review", {})}
    cfg["status_digest"] = {**DEFAULT_CONFIG["status_digest"], **cfg.get("status_digest", {})}
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


def _start_tg_listener(repo: Path, cfg: dict, *, start: bool = True) -> tuple[TelegramListener | None, "queue.Queue", "threading.Event"]:
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
    if start:
        listener.start()
        print(f"[supervisor] Telegram listener started "
              f"(allowlist size={len(tg_cfg.get('chat_allowlist') or []) or 1}).")
    return listener, tg_queue, wave_event


def _start_slack_listener(repo: Path, cfg: dict) -> tuple["SlackListener | None", "queue.Queue"]:
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

    listener = SlackListener(app_token=app_token, bot_token=bot_token, out_queue=slack_queue, repo=repo)
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
    state = RuntimeState.load(state_path, quarantine=not args.dry_run)
    if state._corrupt_note:
        log_line(repo, f"STATE_CORRUPT: {state._corrupt_note}")
        notify(cfg, "P2", f"supervisor state file was corrupt: {state._corrupt_note}", repo)

    tg_listener, tg_queue, wave_event = _start_tg_listener(repo, cfg, start=not args.once)
    slack_listener, slack_queue = _start_slack_listener(repo, cfg)
    tg_token = _os.environ.get("DEVTEAM_TG_TOKEN", "")

    # E-K.5: under --once there is no background listener lifetime, so a
    # command handed off between processes lives entirely in the durable
    # inbox. A repo that has never seen .devteam/inbox/ is either brand new
    # (fine) or a legacy install that predates the durable inbox and is about
    # to silently lose two-way commands under --once — warn loudly either way
    # rather than let onboarding.drain_inbox's per-tick SOURCE_MISSING be the
    # only signal.
    if args.once and not (repo / ".devteam" / "inbox").is_dir():
        print("[supervisor] WARNING: --once with no .devteam/inbox/ yet — if this is a "
              "legacy install (pre-durable-inbox), two-way commands sent between ticks "
              "will be lost. A brand-new install will create it on first use.",
              file=sys.stderr)

    start = time.monotonic()
    ticks = 0
    stopped = False
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
                _reap_durable_inflight(cfg, state, repo, now)

            # E-K: in --once mode there is no background listener lifetime.
            # Poll once, bounded by config, then drain the durable inbox below.
            if args.once and tg_listener is not None and not args.dry_run:
                tg_listener.poll_once(timeout=int(cfg["telegram"].get("once_poll_seconds", 10)))
            queue_actions: list[Action] = []

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
                maybe_status_digest(repo, cfg, state, now)

            # Wave I (I1): drain queued CONTROL blocks + no-block markers
            # BEFORE decide() — exactly where the Telegram queue is already
            # drained above, and for the same reason: a builder's reported
            # state must be visible to this tick's decision.
            if not args.dry_run:
                maybe_drain_control(repo, cfg, state, now)

            if not args.dry_run:
                refresh_plan_from_head(repo)
            plan_text = plan.read_text(encoding="utf-8")
            dossier_heartbeats = _task_heartbeats(repo, plan_text)
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
            try:
                stagnation_signal = _stagnation_signal(repo, plan_text, cfg)
            except Exception as exc:
                print(f"[circuit_breaker] skipped this tick (non-fatal): {exc}", file=sys.stderr)
                stagnation_signal = {}
            try:
                head_shas = _review_head_shas(repo, plan_text)
            except Exception as exc:
                print(f"[review] branch heads unavailable (non-fatal): {exc}", file=sys.stderr)
                head_shas = {}
            stop_path = repo / "STOP"
            stop_mtime = str(stop_path.stat().st_mtime_ns) if stop_path.exists() else ""
            review_path = repo / "REVIEW.md"
            review_text = review_path.read_text(encoding="utf-8") if review_path.is_file() else ""
            actions = queue_actions + inbox_actions + decide(plan_text, state, cfg, now=now,
                                          stop_file_exists=stop_path.exists(), stop_file_mtime=stop_mtime,
                                          dossier_heartbeats=dossier_heartbeats,
                                          usage=usage,
                                          stagnation_signal=stagnation_signal,
                                          head_shas=head_shas,
                                          review_text=review_text)
            keep_going = execute(actions, cfg, state, repo, args.dry_run, now=now, inflight=inflight)
            stopped = any(a.kind == "HALT" for a in actions)
            if args.once and not args.dry_run:
                # A normal loop reaps on its next tick. A single-tick run has
                # no next tick, so wait briefly for dispatch's fast outcome.
                reap_inflight(inflight, cfg, state, repo, datetime.now(timezone.utc), wait_seconds=3.0)
            if not args.dry_run:
                state.save(state_path)
                try:
                    # A batch boundary can arrive with no new PLAN.md commit.
                    # The persisted window is therefore checked every tick.
                    push_policy.maybe_push(repo, "bookkeeping", now=now,
                                           only_if_configured=True)
                except (OSError, ValueError, subprocess.SubprocessError) as exc:
                    print(f"[push_policy] skipped this tick: {exc}", file=sys.stderr)

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
    return 3 if stopped else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
