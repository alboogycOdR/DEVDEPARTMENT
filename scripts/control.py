#!/usr/bin/env python3
"""control.py — DEVDEPARTMENT Wave I (v4.5), I1: CONTROL-block single-writer
blackboard.

Today (legacy, control.mode="legacy"): builders edit PLAN.md themselves —
claim commit, Progress_Notes appends, status flips — policed after the fact
by the validator, the territory firewall's PLAN.md carve-out, and review.

After I1 (control.mode="strict"): builders never write PLAN.md at all. The
dispatcher (dispatch.sh/.ps1, via this module's `claim` CLI) claims the task
before launch. The builder emits a machine-parseable CONTROL block as the
last thing it prints; the supervisor is the sole writer applying that block
to PLAN.md through the exact same micro-transaction discipline Wave
A-remainder's tg_commands.py already established for Telegram commands
(pull -> parse -> edit ONLY the target task's block -> commit -> push). This
module deliberately reuses tg_commands.py's git plumbing and line-editing
primitives (_task_span/_set_field/_append_to_field/git_pull/
git_commit_and_push) rather than reimplementing a second PLAN.md editor.

All PLAN.md/claim writes made by this module use Updated_By: "SV" — one
writer identity for the whole single-writer blackboard (claim-at-dispatch
and CONTROL application are both "the dispatcher/supervisor wrote this",
never a builder). scripts/validate_plan.py's VALID_UNITS was extended with
"SV" for exactly this reason (Wave I amendment).

Design rules (same discipline as the rest of this codebase):
  * Pure functions wherever possible: parse_control_block / validate_control /
    apply_control_to_plan take strings/dicts in, return results out — no I/O,
    fully unit-testable without a repo, git, or a network connection.
  * All strings from a CONTROL block are written into PLAN.md strictly as
    data — never eval'd, shelled out, or interpreted as a path or command.
    Same injection posture as Wave A-remainder's /answer and Wave C's
    /rework AMEND-NNN.
  * Fail-open at the I/O boundary: a malformed block, a missing task, or a
    git failure never crashes a dispatch or a tick — it's rejected/logged
    and escalated per the contract's own rules (P2 on rejection; UNREPORTED
    + P2-after-2 on no block at all).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tg_commands as tgc  # noqa: E402 — reuse git plumbing + PLAN.md line editor
from validate_plan import Report, Task, parse_tasks  # noqa: E402

FENCE_RE = re.compile(
    r"```devteam-control\s*\n(.*?)\n```", re.DOTALL
)
LEGAL_STATUSES = {"in_progress", "needs_review", "blocked"}
BLOCKED_VOCAB = ("SPEC_AMBIGUITY", "MISSING_DEPENDENCY", "OWNERSHIP_CONFLICT",
                 "SYNC_MISMATCH", "TOOLING_FAILURE", "OTHER:")
_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
INFLIGHT_DIR_REL = Path(".devteam") / "inflight"
RUNS_DIR_REL = Path(".devteam") / "runs"
CONTROL_DIR_REL = Path(".devteam") / "control"
CONTROL_APPLIED_DIR_REL = CONTROL_DIR_REL / "applied"


@dataclass
class ApplyResult:
    text: str
    changed: bool
    detail: str


@dataclass
class ClaimResult:
    kind: str          # "resume" | "claimed" | "none"
    task_id: str | None = None
    detail: str = ""


# ------------------------------------------------------------- extraction ---
def parse_control_block(log_text: str) -> dict | None:
    """Extract the LAST ```devteam-control fenced JSON block from raw builder
    stdout. Returns None if no fence is found or the fenced content isn't
    valid JSON — callers treat that as the "no-block fallback" case (§6),
    never as a partial/best-effort parse."""
    matches = FENCE_RE.findall(log_text or "")
    if not matches:
        return None
    try:
        return json.loads(matches[-1])
    except (json.JSONDecodeError, TypeError):
        return None


# --------------------------------------------------------------- contract ---
def validate_control(block: dict, expected_task: str, expected_unit: str) -> tuple[bool, str]:
    """Enforce every rule in the CONTROL block contract (spec §1). Returns
    (True, "") if legal, else (False, "<specific reason>") — the applier
    MUST reject and escalate P2 on any violation, never guess or coerce."""
    if not isinstance(block, dict):
        return False, "CONTROL block is not a JSON object"

    task = block.get("task")
    unit = block.get("unit")
    status = block.get("status")

    if task != expected_task:
        return False, f"task mismatch: block claims '{task}', dispatcher launched '{expected_task}'"
    if unit != expected_unit:
        return False, f"unit mismatch: block claims '{unit}', dispatcher launched '{expected_unit}'"

    if status not in LEGAL_STATUSES:
        return False, (f"illegal status '{status}' — builders may only report "
                       f"{sorted(LEGAL_STATUSES)} (done/pending/claimed are ORCH/SV-only)")

    if status == "needs_review":
        evidence = block.get("test_evidence")
        if not isinstance(evidence, str) or not evidence.strip():
            return False, "status=needs_review requires non-empty test_evidence (Commandment 8)"

    if status == "blocked":
        reason = block.get("blocked_reason")
        if not isinstance(reason, str) or not reason.strip():
            return False, "status=blocked requires a non-empty blocked_reason"
        if not any(reason.startswith(v) for v in BLOCKED_VOCAB):
            return False, (f"blocked_reason '{reason}' does not start with a legal vocabulary "
                           f"term {BLOCKED_VOCAB}")

    return True, ""


# ------------------------------------------------------------ pure apply ----
def apply_control_to_plan(plan_text: str, block: dict, ts: str) -> ApplyResult:
    """Pure PLAN.md text transform for one validated CONTROL block. Caller
    (apply_control_file) is responsible for validate_control() having
    already passed — this function does not re-validate the contract, only
    that the task exists in the plan (defensive against a stale claim).

    status == "in_progress" is a checkpoint emission (§10 stop-point note):
    appends progress_note + next_step to Progress_Notes and leaves Status
    untouched, per §1's explicit rule.
    """
    task_id = block["task"]
    unit = block["unit"]
    status = block["status"]

    lines = plan_text.split("\n")
    span = tgc._task_span(lines, task_id)
    if span is None:
        return ApplyResult(plan_text, False, f"CONTROL {task_id}: unknown task ID — no edit made")
    start, end = span

    # Every free-text field is sanitized exactly like tg_commands.py's
    # /answer and /rework free text: newlines/control characters collapsed
    # to spaces so nothing can ever land at the START of a line and be
    # mistaken for a real "**Field:**"/"### TASK-NNN" structural token.
    # Reusing _sanitize_free_text (not reimplementing it) keeps this
    # guarantee identical across every writer of PLAN.md, present and future.
    note = tgc._sanitize_free_text(block.get("progress_note") or "")
    tag = f"[SV:{unit}]"

    if status == "in_progress":
        next_step = tgc._sanitize_free_text(block.get("next_step") or "")
        bullet_text = note or "(checkpoint — no progress_note provided)"
        if next_step:
            bullet_text += f" NEXT: {next_step}"
        bullet = f"- [{ts}] {tag} {bullet_text}"
        if tgc._field_line_index(lines, start, end, "Progress_Notes") is not None:
            lines = tgc._append_to_field(lines, start, end, "Progress_Notes", bullet)
            start, end = tgc._task_span(lines, task_id)
        lines = tgc._set_field(lines, start, end, "Updated_By", "SV")
        start, end = tgc._task_span(lines, task_id)
        lines = tgc._set_field(lines, start, end, "Updated_At", ts)
        return ApplyResult("\n".join(lines), True, f"CONTROL {task_id}: checkpoint recorded")

    # needs_review / blocked: real state transitions.
    if note and tgc._field_line_index(lines, start, end, "Progress_Notes") is not None:
        bullet = f"- [{ts}] {tag} {note}"
        lines = tgc._append_to_field(lines, start, end, "Progress_Notes", bullet)
        start, end = tgc._task_span(lines, task_id)

    if status == "needs_review":
        evidence = tgc._sanitize_free_text(block.get("test_evidence") or "")
        if tgc._field_line_index(lines, start, end, "Test_Evidence") is not None:
            lines = tgc._set_field(lines, start, end, "Test_Evidence", evidence)
            start, end = tgc._task_span(lines, task_id)
        artifacts = block.get("artifacts")
        if artifacts and tgc._field_line_index(lines, start, end, "Artifacts") is not None:
            clean_artifacts = [tgc._sanitize_free_text(str(a)) for a in artifacts]
            lines = tgc._set_field(lines, start, end, "Artifacts", ", ".join(clean_artifacts))
            start, end = tgc._task_span(lines, task_id)
        lines = tgc._set_field(lines, start, end, "Status", "needs_review")
        start, end = tgc._task_span(lines, task_id)

    elif status == "blocked":
        reason = tgc._sanitize_free_text(block.get("blocked_reason") or "")
        if tgc._field_line_index(lines, start, end, "Blocked_Reason") is not None:
            lines = tgc._set_field(lines, start, end, "Blocked_Reason", reason)
            start, end = tgc._task_span(lines, task_id)
        lines = tgc._set_field(lines, start, end, "Status", status)
        start, end = tgc._task_span(lines, task_id)

    lines = tgc._set_field(lines, start, end, "Updated_By", "SV")
    start, end = tgc._task_span(lines, task_id)
    lines = tgc._set_field(lines, start, end, "Updated_At", ts)

    return ApplyResult("\n".join(lines), True, f"CONTROL {task_id}: {unit} -> {status}")


def apply_unreported_to_plan(plan_text: str, task_id: str, ts: str, log_rel_path: str) -> ApplyResult:
    """§6 no-block fallback: state unchanged, Progress_Note only."""
    lines = plan_text.split("\n")
    span = tgc._task_span(lines, task_id)
    if span is None:
        return ApplyResult(plan_text, False, f"UNREPORTED {task_id}: unknown task ID — no edit made")
    start, end = span
    if tgc._field_line_index(lines, start, end, "Progress_Notes") is None:
        return ApplyResult(plan_text, False, f"UNREPORTED {task_id}: malformed task block — no edit made")
    bullet = (f"- [{ts}] [SV] run ended without CONTROL block — state unchanged, "
             f"see {log_rel_path}")
    lines = tgc._append_to_field(lines, start, end, "Progress_Notes", bullet)
    return ApplyResult("\n".join(lines), True, f"UNREPORTED {task_id}: logged, state unchanged")


# --------------------------------------------------------- file-level apply -
def apply_control_file(repo: Path, control_json_path: Path, ts: str) -> tuple[bool, str]:
    """Full micro-transaction for one .devteam/control/<task>-<ts>.json file:
    pull -> parse block -> cross-check against inflight -> validate contract
    -> edit ONLY the target task's block -> commit [SV] -> push -> archive.
    Never touches any file but PLAN.md and the control queue itself."""
    try:
        block = json.loads(control_json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return False, f"unreadable/invalid control file {control_json_path.name}: {e}"

    unit = block.get("unit")
    expected_task = _inflight_task_id(repo, unit) if unit else None
    if expected_task is None:
        expected_task = block.get("task")  # best-effort if inflight record is gone

    ok, reason = validate_control(block, expected_task, unit or "")
    if not ok:
        return False, f"REJECTED {control_json_path.name}: {reason}"

    tgc.git_pull(repo)
    plan_path = repo / "PLAN.md"
    plan_text = plan_path.read_text(encoding="utf-8")
    result = apply_control_to_plan(plan_text, block, ts)
    if not result.changed:
        return False, result.detail

    plan_path.write_text(result.text, encoding="utf-8")
    committed = tgc.git_commit_and_push(
        repo, f"chore(plan): {result.detail} [SV origin={unit}]")
    if not committed:
        return True, result.detail + " (applied locally; git commit/push failed — check repo)"
    return True, result.detail


def _inflight_task_id(repo: Path, unit: str) -> str | None:
    try:
        obj = json.loads((repo / INFLIGHT_DIR_REL / f"{unit}.json").read_text(encoding="utf-8"))
        return obj.get("task_id")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def drain_control_queue(repo: Path, ts: str) -> list[tuple[str, bool, str]]:
    """Process every .devteam/control/*.json file (oldest first — filename
    sort works since they're <task>-<ts>.json and ts is lexicographically
    sortable ISO-8601), archiving each to .devteam/control/applied/
    regardless of outcome (a rejected block is still archived, never
    reprocessed). Returns [(filename, applied, detail), ...]."""
    control_dir = repo / CONTROL_DIR_REL
    if not control_dir.is_dir():
        return []
    results = []
    files = sorted(p for p in control_dir.glob("*.json") if p.is_file())
    applied_dir = repo / CONTROL_APPLIED_DIR_REL
    for p in files:
        ok, detail = apply_control_file(repo, p, ts)
        results.append((p.name, ok, detail))
        applied_dir.mkdir(parents=True, exist_ok=True)
        try:
            p.rename(applied_dir / p.name)
        except OSError:
            pass
    return results


def drain_unreported_queue(repo: Path, ts: str) -> list[tuple[str, str, bool]]:
    """Process every .devteam/control/*.unreported marker (written by
    dispatch.sh/.ps1 when a run ended with no parseable CONTROL block).
    Returns [(task_id, detail, changed), ...] for the caller (supervisor) to
    track consecutive-unreported counts and escalate P2 after 2."""
    control_dir = repo / CONTROL_DIR_REL
    if not control_dir.is_dir():
        return []
    results = []
    files = sorted(p for p in control_dir.glob("*.unreported") if p.is_file())
    applied_dir = repo / CONTROL_APPLIED_DIR_REL
    for p in files:
        try:
            log_rel = p.read_text(encoding="utf-8").strip() or p.stem
        except OSError:
            log_rel = p.stem
        # filename convention: <task>-<ts>.unreported
        task_id = p.stem.rsplit("-", 6)[0] if "-" in p.stem else p.stem
        m = re.match(r"^(TASK-[A-Z0-9-]+)-\d{4}", p.stem)
        if m:
            task_id = m.group(1)
        tgc.git_pull(repo)
        plan_path = repo / "PLAN.md"
        plan_text = plan_path.read_text(encoding="utf-8")
        result = apply_unreported_to_plan(plan_text, task_id, ts, log_rel)
        if result.changed:
            plan_path.write_text(result.text, encoding="utf-8")
            tgc.git_commit_and_push(repo, f"chore(plan): {result.detail} [SV]")
        results.append((task_id, result.detail, result.changed))
        applied_dir.mkdir(parents=True, exist_ok=True)
        try:
            p.rename(applied_dir / p.name)
        except OSError:
            pass
    return results


# --------------------------------------------------------- claim-at-dispatch
def _deps_done(task: "Task", by_id: dict) -> bool:
    """Mirrors supervisor.py's _deps_done / instincts.py's local copy —
    deliberately reimplemented rather than imported for the same reason
    instincts.py gives: this is a small, dependency-light CLI, and
    supervisor.py pulls in the full autopilot stack."""
    raw = task.get("Depends_On")
    if task.is_empty("Depends_On"):
        return True
    for dep in re.split(r"[,\s]+", raw):
        if re.match(r"^TASK-[A-Z0-9-]+$", dep):
            d = by_id.get(dep)
            if d is None or d.get("Status") != "done":
                return False
    return True


def claim_for_unit(repo: Path, unit: str, ts: str, dry_run: bool = False) -> ClaimResult:
    """§3: the dispatcher performs the claim as its own micro-transaction
    BEFORE launching the builder. Resume-first: an existing in_progress/
    claimed task for this unit is resumed (no re-claim, no re-branch —
    mirrors §10a's ghost-task warning). Otherwise claims the
    highest-priority pending task with dependencies done. Writes
    .devteam/inflight/<unit>.json so the firewall/applier can cross-check
    which task this unit is authorized to report against.

    dry_run=True predicts the exact same result (same resume/claim/none
    outcome and task_id) WITHOUT writing PLAN.md, committing, pushing, or
    touching .devteam/inflight/ — a preview for `dispatch.sh --dry-run`
    that must never itself have a side effect.
    """
    plan_path = repo / "PLAN.md"
    if not plan_path.exists():
        return ClaimResult("none", detail="PLAN.md not found")

    text = plan_path.read_text(encoding="utf-8")
    rep = Report()
    all_tasks = parse_tasks(text, rep)
    by_id = {t.task_id: t for t in all_tasks}
    mine = [t for t in all_tasks if t.get("Assigned_To") == unit]

    resuming = [t for t in mine if t.get("Status") in ("in_progress", "claimed")]
    if resuming:
        target = resuming[0]
        if not dry_run:
            _write_inflight(repo, unit, target.task_id)
        return ClaimResult("resume", target.task_id, f"resuming {target.task_id}")

    pending = [t for t in mine if t.get("Status") == "pending" and _deps_done(t, by_id)]
    if not pending:
        return ClaimResult("none", detail=f"no eligible task for {unit}")
    pending.sort(key=lambda t: (_PRIORITY_ORDER.get(t.get("Priority"), 4), t.task_id))
    target = pending[0]
    suffix = {"GB": "gb", "CX": "cx"}.get(unit, unit.lower())
    branch = f"task/{target.task_id}-{suffix}"

    if dry_run:
        return ClaimResult("claimed", target.task_id,
                           f"would claim {target.task_id} on {branch} (dry-run — no write)")

    lines = text.split("\n")
    span = tgc._task_span(lines, target.task_id)
    if span is None:
        return ClaimResult("none", detail=f"{target.task_id}: span resolution failed")
    start, end = span
    lines = tgc._set_field(lines, start, end, "Status", "claimed")
    start, end = tgc._task_span(lines, target.task_id)
    lines = tgc._set_field(lines, start, end, "Branch", branch)
    start, end = tgc._task_span(lines, target.task_id)
    lines = tgc._set_field(lines, start, end, "Started_At", ts)
    start, end = tgc._task_span(lines, target.task_id)
    lines = tgc._set_field(lines, start, end, "Updated_By", "SV")
    start, end = tgc._task_span(lines, target.task_id)
    lines = tgc._set_field(lines, start, end, "Updated_At", ts)

    tgc.git_pull(repo)
    plan_path.write_text("\n".join(lines), encoding="utf-8")
    tgc.git_commit_and_push(repo, f"chore(plan): claim {target.task_id} [SV origin={unit}]")

    _write_inflight(repo, unit, target.task_id)
    return ClaimResult("claimed", target.task_id, f"claimed {target.task_id} on {branch}")


def _write_inflight(repo: Path, unit: str, task_id: str) -> None:
    d = repo / INFLIGHT_DIR_REL
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{unit}.json").write_text(json.dumps({"task_id": task_id}), encoding="utf-8")


# ------------------------------------------------------------------- CLI ----
def main(argv: list[str] | None = None) -> int:
    import argparse
    import time

    ap = argparse.ArgumentParser(description="Wave I CONTROL-block helper")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("claim", help="claim-at-dispatch: resume or claim a task for a unit")
    c.add_argument("--unit", required=True, choices=["GB", "CX"])
    c.add_argument("--repo", default=".")
    c.add_argument("--dry-run", action="store_true",
                   help="predict the outcome without writing PLAN.md/.devteam/inflight")

    d = sub.add_parser("drain", help="apply queued CONTROL blocks + unreported markers")
    d.add_argument("--repo", default=".")

    e = sub.add_parser("extract", help="scan a captured run log for the CONTROL fence (dispatch.sh/.ps1)")
    e.add_argument("--log", required=True, help="path to the captured stdout log")
    e.add_argument("--task", required=True)
    e.add_argument("--unit", required=True, choices=["GB", "CX"])
    e.add_argument("--repo", default=".")

    ns = ap.parse_args(argv)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    repo = Path(ns.repo)

    if ns.cmd == "claim":
        try:
            result = claim_for_unit(repo, ns.unit, ts, dry_run=ns.dry_run)
        except Exception as e:  # fail-open: dispatch must never hang on a claim bug
            print(f"NONE:{e}")
            return 0
        if result.kind == "resume":
            print(f"RESUME:{result.task_id}")
        elif result.kind == "claimed":
            print(f"CLAIMED:{result.task_id}")
        else:
            print(f"NONE:{result.detail}")
        return 0

    if ns.cmd == "drain":
        try:
            ctrl_results = drain_control_queue(repo, ts)
            unrep_results = drain_unreported_queue(repo, ts)
        except Exception as e:
            print(f"DRAIN error (fail-open): {e}", file=sys.stderr)
            return 0
        for name, ok, detail in ctrl_results:
            print(f"{'OK' if ok else 'REJECTED'}  {name}  {detail}")
        for task_id, detail, changed in unrep_results:
            print(f"{'OK' if changed else 'SKIP'}  UNREPORTED {task_id}  {detail}")
        return 0

    if ns.cmd == "extract":
        try:
            result = extract_from_log(repo, Path(ns.log), ns.task, ns.unit, ts)
        except Exception as e:
            print(f"EXTRACT error (fail-open, treated as unreported): {e}", file=sys.stderr)
            result = f"UNREPORTED:{ns.task}-{ts.replace(':', '-')}.unreported"
        print(result)
        return 0

    return 0


def extract_from_log(repo: Path, log_path: Path, task: str, unit: str, ts: str) -> str:
    """Called by dispatch.sh/.ps1 right after a builder session ends (strict
    mode only). Scans the captured log for the last devteam-control fence:
      - found  -> write .devteam/control/<task>-<ts>.json (queued for the
                  next supervisor tick's drain_control_queue); prints
                  "CONTROL:<filename>".
      - absent -> write .devteam/control/<task>-<ts>.unreported (queued for
                  drain_unreported_queue); prints "UNREPORTED:<filename>".
    ts is filesystem-safe (colons replaced) since it's used in a filename.
    """
    fs_ts = ts.replace(":", "-")
    control_dir = repo / CONTROL_DIR_REL
    control_dir.mkdir(parents=True, exist_ok=True)

    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log_text = ""

    block = parse_control_block(log_text)
    if block is not None:
        fname = f"{task}-{fs_ts}.json"
        (control_dir / fname).write_text(json.dumps(block), encoding="utf-8")
        return f"CONTROL:{fname}"

    fname = f"{task}-{fs_ts}.unreported"
    try:
        log_rel = str(log_path.resolve().relative_to(repo.resolve()))
    except ValueError:
        log_rel = str(log_path)
    (control_dir / fname).write_text(log_rel, encoding="utf-8")
    return f"UNREPORTED:{fname}"


if __name__ == "__main__":
    raise SystemExit(main())
