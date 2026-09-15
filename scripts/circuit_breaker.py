#!/usr/bin/env python3
"""circuit_breaker.py — stagnation detection for claimed/in_progress tasks.

Ported in concept from ralph-claude-code's circuit_breaker.sh
(github.com/frankbria/ralph-claude-code, MIT): a builder can be ALIVE (fresh
dossier heartbeat, process running or exited 0) while making zero progress —
stuck on a fix it cannot find, or repeatedly hitting a gateguard/firewall
denial without adapting. supervisor.py's existing stale-heartbeat check
(decide() step 4) does not catch this: a heartbeat keeps advancing even when
nothing under Owned_Paths ever changes.

Two independent signals feed one counter per task, both computed by the
CALLER — this module never touches git, gateguard state, or the filesystem,
same "pure function" contract as budget.py:

  * changed  — did anything under the task's Owned_Paths change since the
               last tick that saw progress? (git diff; supervisor.py's tick
               loop resolves this against the unit's worktree)
  * denials  — gateguard denial count for the unit, accumulated since the
               last tick that saw progress (supervisor.py resets the counter
               file itself the instant `changed` is True — see
               _stagnation_signal)

Neither signal alone is damning: a builder legitimately thinks for a tick
before writing anything, and a first-touch gateguard denial is the gate
WORKING as designed, not a problem. The bar is on REPETITION without either
signal moving — mirrored as two independent thresholds (Ralph keeps
CB_NO_PROGRESS_THRESHOLD and CB_PERMISSION_DENIAL_THRESHOLD separate for the
same reason: a stuck permission loop is a stronger, faster signal than mere
silence).

Unlike Ralph's OPEN/HALF_OPEN/CLOSED state machine with a cooldown timer,
this module resolves into the SAME two-stage ladder supervisor.py already
uses for stale-heartbeat: a bounded number of "redispatch and let resume-
first continue the branch" attempts, then hand off to a human via
ESCALATE_P2. Ralph invented cooldown/auto-recovery because it has no human
in the loop to hand off to; we do, and REVIEW.md ownership_conflict /
stale_resets already establish that shape — reusing it instead of a second
state-machine vocabulary keeps supervisor.py's action space small.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_CIRCUIT_BREAKER_CFG = {
    "no_progress_ticks": 3,      # consecutive stagnant ticks -> remedial action
    "denial_ticks": 2,           # denial count reaching this -> remedial action, sooner
    "max_stagnation_resets": 2,  # remedial redispatches (lifetime, per task_id) before
                                 # escalating to a human — mirrors max_rework/stale_resets'
                                 # existing "N attempts then a human looks at it" shape
}


@dataclass
class StagnationSample:
    """One tick's raw observation for one task, computed by the caller."""
    changed: bool   # True if git shows any diff under Owned_Paths since the last reset
    denials: int    # gateguard denial count for the unit, since the last reset


def update_streak(prior_streak: int, sample: StagnationSample) -> int:
    """New consecutive-no-progress streak for this task. A diff resets to 0
    unconditionally: an actual file change is unambiguous progress no matter
    how many denials preceded it in the same tick."""
    return 0 if sample.changed else prior_streak + 1


def is_stagnant(streak: int, sample: StagnationSample, cfg: dict | None = None) -> bool:
    """True when this tick's evidence crosses the bar for 'this task is not
    moving'. `streak` must already reflect update_streak() for the SAME
    sample — this function does not call update_streak() itself, so a
    diff this tick still correctly reports not-stagnant even though the
    caller passes streak=0 for it."""
    if sample.changed:
        return False
    cfg = {**DEFAULT_CIRCUIT_BREAKER_CFG, **(cfg or {})}
    return streak >= cfg["no_progress_ticks"] or sample.denials >= cfg["denial_ticks"]


def remedial_kind(prior_resets: int, cfg: dict | None = None) -> str:
    """'REDISPATCH_STAGNANT' while lifetime resets for this task_id remain
    under the ceiling, else 'ESCALATE_P2' — the same two-stage ladder
    supervisor.py already applies to stale heartbeats via state.stale_resets,
    using its own independent counter (state.stagnation_resets) so a task's
    heartbeat-staleness history and progress-stagnation history don't share
    a budget."""
    cfg = {**DEFAULT_CIRCUIT_BREAKER_CFG, **(cfg or {})}
    return "ESCALATE_P2" if prior_resets >= cfg["max_stagnation_resets"] else "REDISPATCH_STAGNANT"
