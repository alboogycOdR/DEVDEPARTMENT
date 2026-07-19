# CONTROL.md — the single-writer blackboard (Wave I, I1, v4.5)

## The inversion, in one sentence

**Before:** builders edit `PLAN.md` themselves (claim, `Progress_Notes`, status flips), policed after the fact by the validator, the firewall's `PLAN.md` carve-out, and review.
**After (`control.mode=strict`):** builders never write `PLAN.md` at all. The dispatcher claims the task before launch; the builder reports back via one machine-parseable block; the supervisor is the sole writer.

`control.mode=legacy` is the default and is byte-for-byte the old behavior — this is entirely opt-in.

## Why

The one soft spot in the territory firewall was always `PLAN.md` itself — every unit could write it, because block-level enforcement (only touch your own `### TASK-NNN`) isn't something a file-write hook can reliably see. That carve-out is where every "builder corrupted the blackboard" failure class came from. I1 closes it by removing builder write access to `PLAN.md` entirely, rather than trying to detect corruption after the fact.

## The two modes

| | `legacy` (default) | `strict` |
|---|---|---|
| Who claims the task | The builder, at session start | The dispatcher, before launch (`scripts/control.py claim`) |
| How the builder reports state | Edits `PLAN.md` directly | Emits a `devteam-control` fenced JSON block as the last thing it prints |
| Who applies the state to `PLAN.md` | The builder | The supervisor, next tick (`drain_control_queue`) |
| Builder's writable files | `Owned_Paths` + `PLAN.md` | `Owned_Paths` + its own `dossiers/<task>.md` only |
| Heartbeat source (stale detection) | `PLAN.md`'s `Updated_At` | `max(Updated_At, dossier mtime)` |
| Firewall on `PLAN.md` | Allowed | Protected (blocked outright) |

Switch via `autopilot.json`:
```json
"control": { "mode": "strict" }
```
No hybrid mode exists — half-enforced single-writer is worse than either pole. If `autopilot.json` is missing, malformed, or unreadable, every consumer (firewall, `dispatch.*`, `supervisor.py`) fails safe to `legacy`, never to `strict`.

## The CONTROL block contract

Emitted by the builder as the **last thing printed** in its session:

```
​```devteam-control
{
  "control_version": 1,
  "task": "TASK-NNN",
  "unit": "GB",
  "status": "needs_review",
  "progress_note": "One-line summary.",
  "artifacts": ["lib/auth/jwt.dart"],
  "test_evidence": "flutter test test/auth/ — 34/34 pass",
  "blocked_reason": null,
  "next_step": null
}
​```
```

Enforced by `scripts/control.py`'s `validate_control()` before anything touches `PLAN.md` — any violation is rejected and escalated P2, never coerced into something legal:

- `status` ∈ `in_progress | needs_review | blocked` only. `done`/`pending`/`claimed` are illegal from a builder — mechanically, not just by convention.
- `needs_review` requires non-empty `test_evidence`.
- `blocked` requires `blocked_reason` starting with a legal vocabulary term (`SPEC_AMBIGUITY`, `MISSING_DEPENDENCY`, `OWNERSHIP_CONFLICT`, `SYNC_MISMATCH`, `TOOLING_FAILURE`, `OTHER:`).
- `task`/`unit` must match what the dispatcher actually launched (cross-checked against `.devteam/inflight/<unit>.json`, written at claim time) — a builder cannot report against another unit's task.
- `in_progress` is a checkpoint: the applier appends `progress_note` + `next_step` to `Progress_Notes` and leaves `Status` untouched — this is the strict-mode equivalent of the legacy "commit as in_progress before stopping" context-limit rule.
- All strings are written into `PLAN.md` as inert data via the same line-editing primitives Wave A-remainder's `/answer`/`/rework` already use (`tg_commands._set_field`/`_append_to_field`) — never eval'd, shelled out, or interpreted as a path or command. Every commit is tagged `[SV origin=<unit>]`.

## Claim-at-dispatch

`dispatch.sh`/`dispatch.ps1` perform the claim themselves, before launching the builder, via `scripts/control.py claim --unit GB`:

- An existing `in_progress`/`claimed` task for that unit is **resumed**, not re-claimed (mirrors the resume-first protocol rule — no re-branching, no ghost tasks).
- Otherwise, the highest-priority `pending` task with dependencies done is claimed: `Status → claimed`, `Branch`, `Started_At`, `Updated_By: SV`, committed `[SV origin=<unit>]`.
- Nothing eligible → the dispatch is skipped entirely (`NONE:...`) rather than launching a builder with nothing to do.
- `--dry-run` **predicts** the outcome (same resume/claim/none decision, same task ID) without writing `PLAN.md`, `.devteam/inflight/`, or making any git commit — previewing a dispatch must never itself be a side effect.
- The result is recorded to `.devteam/inflight/<unit>.json` so the firewall (which task's dossier may this unit write?) and the applier (does this CONTROL block match what was actually launched?) can both cross-check it.

The builder is **told its task ID explicitly** in the dispatch prompt — no more "scan and claim".

## Updated_By: SV

One writer identity for the whole single-writer blackboard — claim-at-dispatch and CONTROL-block application are both "the dispatcher/supervisor wrote this," never a builder. `validate_plan.py`'s `VALID_UNITS` was extended to `{ORCH, GB, CX, S5, SV}` for this reason (an error otherwise — `PLAN.md` would become protocol-illegal the instant a strict-mode claim landed). A soft, warn-only check flags `Updated_By: GB`/`CX` directly on a `needs_review`/`blocked` transition while `control.mode=strict` — a signal that a CONTROL block may have been bypassed, not a hard failure.

## No-block fallback (§6)

A run that ends with no parseable fence isn't guessed at. `dispatch.sh`/`.ps1` write a `.devteam/control/<task>-<ts>.unreported` marker instead of a `.json` one; the supervisor's `drain_unreported_queue()` appends `[SV] run ended without CONTROL block — state unchanged, see <log path>` to `Progress_Notes` (state itself is never touched) and tracks a consecutive-unreported streak per task. Two in a row escalates P2 — "is the builder crashing before its final print, or silently violating the contract?" — and resets the streak. A single unreported run stays quiet; the contract assumes builders aren't perfectly obedient, not that every miss is an emergency.

## Dossier heartbeats

In strict mode, `dossiers/<TASK-ID>.md`'s file mtime becomes part of the staleness signal (`decide()`'s `dossier_heartbeats` parameter, computed once per tick by `_dossier_heartbeats()` and passed in — `decide()` itself stays a pure function, doing no filesystem I/O). A builder mid-task that's diligently appending work-log entries every ~30 minutes won't be wrongly flagged stale and redispatched just because it hasn't emitted an `in_progress` CONTROL checkpoint recently.

## Firewall changes

`hooks/lib.js`'s `controlMode()` reads `autopilot.json` (fail-safe default `legacy`). `territory-firewall.js`:
- `legacy`: `PLAN.md` is writable by builders, exactly as before.
- `strict`: `PLAN.md` joins the protected-path list outright. A builder MAY write `dossiers/<their-active-task>.md` — resolved via `.devteam/inflight/<unit>.json` first, falling back to a `PLAN.md` scan — and nothing else changes about territory enforcement for `Owned_Paths`.

## Rollback

Set `"control": {"mode": "legacy"}` in `autopilot.json`. Everything reverts to v4.4-era behavior immediately: the firewall re-allows `PLAN.md` writes, `dispatch.*` stop claiming/capturing, and the supervisor's `maybe_drain_control()`/dossier-heartbeat paths become no-ops. If one builder harness (say, Codex) proves unreliable at emitting the fence while the other complies, the per-unit strict/legacy split described in the spec's R1 is a small config extension on top of the same `controlMode()`/`cfg.get("control",...)` read points — not built here, since it wasn't needed for either builder in testing, but the seam is exactly where you'd add it.
