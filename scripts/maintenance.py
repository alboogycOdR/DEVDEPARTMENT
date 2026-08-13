#!/usr/bin/env python3
"""maintenance.py — Nightly self-audit routine (Wave B, Pillar 3).

The system keeps itself healthy without a human watching: once per configured
UTC hour, run_nightly_audit() runs six steps — harness audit, validator +
test suites, hygiene, backup, result handling, and a digest summary line —
each independently wrapped so one failure never blocks the rest. Idempotent
via scripts/scheduling.py's daily marker, so a supervisor restart mid-day
never re-runs it, and calling it twice in the same UTC day is a safe no-op.

Usage:
    python scripts/maintenance.py --repo .                 # run now if due
    python scripts/maintenance.py --repo . --force          # run now regardless of schedule
    python scripts/maintenance.py --repo . --check-only     # print the schedule decision, don't run
"""
from __future__ import annotations

import argparse
import json
import os as _os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import scheduling  # noqa: E402
import tg_commands as tgc  # noqa: E402 — reuse git_pull/git_commit_and_push, don't reinvent
from validate_plan import validate, parse_tasks, Report  # noqa: E402
try:
    from team_stats import compute as compute_team  # noqa: E402
except Exception:  # pragma: no cover
    compute_team = None

UTC_FMT = "%Y-%m-%dT%H:%M:%SZ"

DEFAULT_MAINTENANCE_CFG = {"hour_utc": 2, "backup_retain_days": 7}

# Which Owned_Paths a failed step implicates, for the auto-filed TASK-MAINT
# block's territory — kept conservative/broad since these are DIAGNOSTIC
# findings for a human or ORCH to triage, not a builder territory grant.
FAILURE_OWNED_PATHS: dict[str, list[str]] = {
    "harness_audit": ["scripts/**", "hooks/**"],
    "validate_plan": ["PLAN.md"],
    "pytest": ["scripts/**", "tests/**"],
    "node_tests": ["hooks/**"],
    "hygiene": ["scripts/**"],
    "backup": ["backups/**"],
    "atlas": [".devteam/**"],
}


@dataclass
class StepResult:
    name: str
    passed: bool
    detail: str


@dataclass
class MaintenanceResult:
    ran: bool                       # False if it wasn't yet due (schedule/idempotency gate)
    passed: bool                    # True iff ran and every step passed
    steps: list[StepResult] = field(default_factory=list)
    task_id: str | None = None      # TASK-MAINT-<date> if one was filed
    digest_line: str = ""           # one line for the next P0 digest


def _oneline(text: str, max_len: int = 400) -> str:
    """Collapse arbitrary (possibly multi-line, possibly huge) subprocess
    output into one safe PLAN.md field line — same structural-safety concern
    as Wave A's free-text sanitisation: a raw newline here could otherwise
    land at the start of a line and be misread as a new field/task header."""
    clean = " ".join((text or "").split())
    if len(clean) > max_len:
        clean = clean[:max_len].rstrip() + "\u2026[truncated]"
    return clean


# ============================================================ step 1: harness
def _step_harness_audit(repo: Path) -> StepResult:
    """Full harness-audit.sh/.ps1 WITHOUT --no-shield/-NoShield — network is
    assumed available at night, so this is the one time AgentShield actually runs."""
    if _os.name == "nt":
        script = repo / "scripts" / "harness-audit.ps1"
        cmd = ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    else:
        script = repo / "scripts" / "harness-audit.sh"
        cmd = ["bash", str(script)]
    if not script.exists():
        return StepResult("harness_audit", False, f"{script.name} not found")
    try:
        r = subprocess.run(cmd, cwd=repo, capture_output=True, encoding="utf-8", errors="replace", timeout=1800)
        ok = r.returncode == 0
        tail = _oneline((r.stdout or "") + " " + (r.stderr or ""), max_len=600)
        return StepResult("harness_audit", ok, "harness-audit PASS" if ok else f"harness-audit FAILED (exit {r.returncode}): {tail}")
    except subprocess.TimeoutExpired:
        return StepResult("harness_audit", False, "harness-audit timed out after 1800s")
    except Exception as exc:  # noqa: BLE001
        return StepResult("harness_audit", False, f"harness-audit crashed: {exc}")


# ==================================================== step 2: validator+tests
def _step_validate_plan(repo: Path) -> StepResult:
    plan = repo / "PLAN.md"
    if not plan.exists():
        return StepResult("validate_plan", False, "PLAN.md not found")
    try:
        rep = validate(plan.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return StepResult("validate_plan", False, f"validator crashed: {exc}")
    if rep.ok:
        return StepResult("validate_plan", True, "PLAN.md protocol-legal")
    return StepResult("validate_plan", False, "PLAN.md illegal: " + " | ".join(rep.errors[:5]))


def _step_pytest(repo: Path) -> StepResult:
    tests_dir = repo / "tests"
    if not tests_dir.exists():
        return StepResult("pytest", True, "no tests/ directory \u2014 skipped")
    try:
        r = subprocess.run([sys.executable, "-m", "pytest", "tests/", "-q"], cwd=repo,
                           capture_output=True, encoding="utf-8", errors="replace", timeout=600)
        ok = r.returncode == 0
        tail = _oneline((r.stdout or "") + " " + (r.stderr or ""), max_len=500)
        return StepResult("pytest", ok, "pytest PASS" if ok else f"pytest FAILED: {tail}")
    except subprocess.TimeoutExpired:
        return StepResult("pytest", False, "pytest timed out after 600s")
    except Exception as exc:  # noqa: BLE001
        return StepResult("pytest", False, f"pytest crashed: {exc}")


def _step_node_tests(repo: Path) -> StepResult:
    runner = repo / "hooks" / "run-tests.js"
    if not runner.exists():
        return StepResult("node_tests", True, "no hooks/run-tests.js \u2014 skipped")
    try:
        r = subprocess.run(["node", str(runner)], cwd=repo, capture_output=True, encoding="utf-8", errors="replace", timeout=300)
        ok = r.returncode == 0
        tail = _oneline((r.stdout or "") + " " + (r.stderr or ""), max_len=500)
        return StepResult("node_tests", ok, "node hook tests PASS" if ok else f"node hook tests FAILED: {tail}")
    except subprocess.TimeoutExpired:
        return StepResult("node_tests", False, "node hook tests timed out after 300s")
    except Exception as exc:  # noqa: BLE001
        return StepResult("node_tests", False, f"node hook tests crashed: {exc}")


# ================================================================ step: atlas
_ATLAS_CORRUPTION_MARKERS = ("malformed", "not a database", "disk image is malformed", "database disk image")


def _atlas_run(atlas_script: Path, args: list[str], cwd: Path, timeout: int) -> tuple[bool, str]:
    """Run one `atlas.py` subcommand, returning (ok, combined-output-oneline).

    PYTHONIOENCODING is forced to utf-8 in the child's env: on Windows a
    piped/redirected child's stdout otherwise defaults to the OS ANSI
    codepage, and atlas.py's own em-dashes/arrows crash it with
    UnicodeEncodeError before a single byte is written — which would
    surface here as an ordinary-looking "scan failed" note every single
    night rather than the real scan actually running.
    """
    env = {**_os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        r = subprocess.run([sys.executable, str(atlas_script), *args], cwd=cwd, env=env,
                           capture_output=True, encoding="utf-8", errors="replace", timeout=timeout)
        ok = r.returncode == 0
        tail = _oneline((r.stdout or "") + " " + (r.stderr or ""), max_len=400)
        return ok, tail
    except subprocess.TimeoutExpired:
        return False, f"{' '.join(args)} timed out after {timeout}s"
    except Exception as exc:  # noqa: BLE001
        return False, f"{' '.join(args)} crashed: {exc}"


def _step_atlas(repo: Path) -> StepResult:
    """ATLAS A5 (spec §5): nightly scan + episodes --reindex + optional
    capped card refresh. Deliberately NOT part of the normal fail=escalate
    contract every other step follows: an ordinary atlas failure (model
    unreachable, a transient parse error) is logged in this step's detail
    and the audit moves on — ATLAS is a convenience layer, not something
    that should page a human at 2am. The one exception the spec carves out
    is db corruption, which DOES escalate (passed=False), because the
    prescribed remedy (delete + full rescan) is destructive enough that it
    should go through a filed task rather than run unattended here.

    Takes only `repo` (not `cfg`) to match every other step function's
    signature — `run_nightly_audit` dispatches all `_ORDERED_STEPS` the
    same way except `_step_backup`, and reading autopilot.json directly
    here keeps that uniform instead of special-casing this step's call."""
    cfg_path = repo / "autopilot.json"
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    except Exception:
        cfg = {}
    atlas_cfg = cfg.get("atlas") or {}
    if not atlas_cfg.get("enabled", False):
        return StepResult("atlas", True, "atlas disabled — skipped")
    atlas_script = repo / "scripts" / "atlas.py"
    if not atlas_script.exists():
        return StepResult("atlas", True, "scripts/atlas.py not found — skipped")

    notes: list[str] = []
    ok, tail = _atlas_run(atlas_script, ["scan", "--repo", str(repo)], repo, 300)
    if not ok:
        low = tail.lower()
        if any(marker in low for marker in _ATLAS_CORRUPTION_MARKERS):
            db = repo / ".devteam" / "atlas.db"
            return StepResult(
                "atlas", False,
                f"atlas.db appears corrupt: {tail}; remedy: delete {db} and re-run "
                f"'python scripts/atlas.py scan --full --repo .'",
            )
        notes.append(f"scan failed: {tail}")
    else:
        notes.append("scan OK")
        ok2, tail2 = _atlas_run(atlas_script, ["episodes", "--reindex", "--repo", str(repo)], repo, 300)
        notes.append("episodes --reindex OK" if ok2 else f"episodes --reindex failed: {tail2}")

        if atlas_cfg.get("cards_auto_refresh", False):
            max_n = atlas_cfg.get("max_cards_per_night", 30)
            ok3, tail3 = _atlas_run(atlas_script, ["cards", "--generate", "--max", str(max_n)], repo, 1800)
            notes.append(f"cards --generate (max {max_n}) OK" if ok3 else f"cards --generate failed: {tail3}")

    return StepResult("atlas", True, "; ".join(notes))


# =============================================================== step 3: hygiene
_MERGED_TASK_BRANCH_RE = re.compile(r"^task/(TASK-[A-Z0-9-]+)-(?:gb|cx)$")


def _done_task_ids(repo: Path) -> set[str]:
    plan_path = repo / "PLAN.md"
    if not plan_path.exists():
        return set()
    try:
        rep = Report()
        tasks = parse_tasks(plan_path.read_text(encoding="utf-8"), rep)
        return {t.task_id for t in tasks if t.get("Status") == "done"}
    except Exception:  # noqa: BLE001
        return set()


def _step_hygiene(repo: Path) -> StepResult:
    """Four sub-actions, bundled into one step per spec: worktree prune,
    delete done+merged task branches, rotate an oversized AUTOPILOT_LOG.md,
    and drop a stale checkpoint. Benign "nothing to do" outcomes are not
    failures; only an actual crash marks this step failed."""
    notes: list[str] = []
    ok = True

    try:
        r = subprocess.run(["git", "worktree", "prune"], cwd=repo, capture_output=True, encoding="utf-8", errors="replace", timeout=60)
        if r.returncode != 0:
            notes.append(f"worktree prune: {_oneline(r.stderr, 150)}")
    except FileNotFoundError:
        notes.append("git not available \u2014 worktree prune skipped")
    except Exception as exc:  # noqa: BLE001
        ok = False
        notes.append(f"worktree prune crashed: {exc}")

    done_ids = _done_task_ids(repo)
    try:
        merged = subprocess.run(["git", "branch", "--merged", "main"], cwd=repo,
                                capture_output=True, encoding="utf-8", errors="replace", timeout=60)
        if merged.returncode == 0:
            deleted = []
            for line in merged.stdout.splitlines():
                branch = line.strip().lstrip("*").strip()
                if not branch or branch == "main":
                    continue
                m = _MERGED_TASK_BRANCH_RE.match(branch)
                if m and m.group(1) in done_ids:
                    dr = subprocess.run(["git", "branch", "-d", branch], cwd=repo,
                                        capture_output=True, encoding="utf-8", errors="replace", timeout=30)
                    if dr.returncode == 0:
                        deleted.append(branch)
            if deleted:
                notes.append(f"pruned merged task branches: {', '.join(deleted)}")
    except FileNotFoundError:
        pass  # git not available — already noted above
    except Exception as exc:  # noqa: BLE001
        ok = False
        notes.append(f"branch cleanup crashed: {exc}")

    try:
        log_path = repo / "AUTOPILOT_LOG.md"
        if log_path.exists() and log_path.stat().st_size > 1_000_000:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            archive = repo / f"AUTOPILOT_LOG.{date_str}.md"
            log_path.rename(archive)
            log_path.write_text("", encoding="utf-8")
            notes.append(f"rotated AUTOPILOT_LOG.md \u2192 {archive.name}")
    except Exception as exc:  # noqa: BLE001
        ok = False
        notes.append(f"log rotation crashed: {exc}")

    try:
        checkpoint = repo / ".devteam" / "CHECKPOINT.md"
        if checkpoint.exists():
            text = checkpoint.read_text(encoding="utf-8")
            m = re.search(r"TASK-\d+", text)
            if m and m.group(0) in done_ids:
                checkpoint.unlink()
                notes.append(f"removed stale checkpoint for {m.group(0)} (now done)")
    except Exception as exc:  # noqa: BLE001
        ok = False
        notes.append(f"checkpoint cleanup crashed: {exc}")

    return StepResult("hygiene", ok, "; ".join(notes) if notes else "no hygiene actions needed")


# ================================================================ step 4: backup
def _step_backup(repo: Path, retain_days: int) -> StepResult:
    try:
        backups_dir = repo / "backups"
        backups_dir.mkdir(exist_ok=True)
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        bundle_path = backups_dir / f"{date_str}.bundle"
        r = subprocess.run(["git", "bundle", "create", str(bundle_path), "--all"],
                           cwd=repo, capture_output=True, encoding="utf-8", errors="replace", timeout=300)
        if r.returncode != 0:
            return StepResult("backup", False, f"git bundle create failed: {_oneline(r.stderr, 300)}")

        cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)
        removed = []
        for f in sorted(backups_dir.glob("*.bundle")):
            try:
                d = datetime.strptime(f.stem, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                continue  # not one of ours (unexpected filename) — leave it alone
            if d < cutoff:
                f.unlink()
                removed.append(f.name)

        detail = f"bundle created: {bundle_path.name}"
        if removed:
            detail += f"; pruned {len(removed)} bundle(s) older than {retain_days}d: {', '.join(removed)}"
        return StepResult("backup", True, detail)
    except Exception as exc:  # noqa: BLE001
        return StepResult("backup", False, f"backup crashed: {exc}")


# ======================================================= step 5: result handling
def _pick_assignee(repo: Path) -> str:
    """Per spec: 'per team_stats hint or GB by default'."""
    review_path = repo / "REVIEW.md"
    if compute_team is None or not review_path.exists():
        return "GB"
    try:
        stats = compute_team(review_path.read_text(encoding="utf-8"))
        hint = stats.get("assignment_hint", "") or ""
        if hint.startswith("GB"):
            return "GB"
        if hint.startswith("CX"):
            return "CX"
    except Exception:  # noqa: BLE001
        pass
    return "GB"


def _compose_maint_task(repo: Path, failed_steps: list[StepResult], now: datetime) -> tuple[str, str]:
    date_str = now.strftime("%Y-%m-%d")
    task_id = f"TASK-MAINT-{date_str}"

    owned: list[str] = []
    for s in failed_steps:
        for p in FAILURE_OWNED_PATHS.get(s.name, []):
            if p not in owned:
                owned.append(p)
    owned_str = ", ".join(owned) if owned else "scripts/**"

    assignee = _pick_assignee(repo)
    description = _oneline(" | ".join(f"{s.name}: {s.detail}" for s in failed_steps), max_len=800)
    failed_names = ", ".join(s.name for s in failed_steps)
    ts = now.strftime(UTC_FMT)

    block = f"""
### {task_id}
**Title:** Nightly self-audit failure ({date_str})
**Status:** pending
**Assigned_To:** {assignee}
**Priority:** high
**Spec_References:** self-generated \u2014 nightly audit failure
**Owned_Paths:** {owned_str}
**Depends_On:** \u2014
**Description:** {description}
**Acceptance_Criteria:**
- [ ] All nightly audit steps pass: {failed_names}
**Branch:** \u2014
**Started_At:** \u2014
**Progress_Notes:** \u2014
**Artifacts:** \u2014
**Test_Evidence:** \u2014
**Review_Findings:** \u2014
**Blocked_Reason:** \u2014
**Updated_By:** ORCH
**Updated_At:** {ts}
"""
    return task_id, block


def _file_maint_task(repo: Path, failed_steps: list[StepResult], now: datetime) -> str:
    """Append a TASK-MAINT-<date> block to PLAN.md via the same shared
    pull/write/commit micro-transaction Wave A already built (tg_commands),
    committed [MAINT] \u2014 symmetrical with [ORCH]/[GB]/[CX]/[TG]."""
    task_id, block = _compose_maint_task(repo, failed_steps, now)
    tgc.git_pull(repo)
    plan_path = repo / "PLAN.md"
    existing = plan_path.read_text(encoding="utf-8") if plan_path.exists() else ""
    plan_path.write_text(existing.rstrip("\n") + "\n" + block, encoding="utf-8")
    tgc.git_commit_and_push(repo, f"chore(plan): file {task_id} \u2014 nightly self-audit failure [MAINT]")
    return task_id


# ========================================================= orchestration ====
_ORDERED_STEPS = (
    "_step_harness_audit",
    "_step_validate_plan",
    "_step_pytest",
    "_step_node_tests",
    "_step_hygiene",
    "_step_backup",
    "_step_atlas",
)


def run_nightly_audit(repo: Path, cfg: dict, now: datetime | None = None, force: bool = False) -> MaintenanceResult:
    """Run the six-step nightly self-audit if due (or always, if force=True).

    Idempotent: internally re-checks scheduling.should_run_daily() against
    the SAME .devteam/last_audit_date.txt marker the supervisor's outer
    scheduler check consults, so a second call the same UTC day — whether
    from a supervisor restart, a duplicate tick, or a direct CLI invocation —
    is a safe no-op (unless force=True).
    """
    now = now or datetime.now(timezone.utc)
    m_cfg = {**DEFAULT_MAINTENANCE_CFG, **(cfg.get("maintenance") or {})}
    marker_path = repo / ".devteam" / "last_audit_date.txt"

    if not force and not scheduling.should_run_daily(marker_path, m_cfg["hour_utc"], now):
        return MaintenanceResult(ran=False, passed=True, steps=[], task_id=None, digest_line="")

    module = sys.modules[__name__]
    steps: list[StepResult] = []
    for fn_name in _ORDERED_STEPS:
        fn = getattr(module, fn_name)
        try:
            if fn_name == "_step_backup":
                steps.append(fn(repo, m_cfg["backup_retain_days"]))
            else:
                steps.append(fn(repo))
        except Exception as exc:  # noqa: BLE001 — a step function must never crash the audit
            steps.append(StepResult(fn_name.replace("_step_", ""), False, f"step crashed: {exc}"))

    failed = [s for s in steps if not s.passed]
    task_id: str | None = None
    if failed:
        try:
            task_id = _file_maint_task(repo, failed, now)
        except Exception as exc:  # noqa: BLE001 — filing the escalation must never crash the audit
            steps.append(StepResult("file_task", False, f"failed to file TASK-MAINT-{now.strftime('%Y-%m-%d')}: {exc}"))
            failed = [s for s in steps if not s.passed]

    scheduling.mark_done_daily(marker_path, now)

    passed = not failed
    digest_line = ("Self-audit: PASS" if passed else
                   f"Self-audit: FAIL \u2014 {len(failed)} finding(s) \u2192 "
                   f"{task_id or ('TASK-MAINT-' + now.strftime('%Y-%m-%d'))} filed")

    return MaintenanceResult(ran=True, passed=passed, steps=steps, task_id=task_id, digest_line=digest_line)


# ------------------------------------------------------------------- CLI ----
def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="DEVDEPARTMENT nightly self-maintenance audit")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--force", action="store_true", help="run now, ignoring the schedule/idempotency marker")
    ap.add_argument("--check-only", action="store_true", help="print whether it WOULD run now; don't run it")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    cfg_path = repo / "autopilot.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    m_cfg = {**DEFAULT_MAINTENANCE_CFG, **(cfg.get("maintenance") or {})}
    now = datetime.now(timezone.utc)

    if args.check_only:
        marker_path = repo / ".devteam" / "last_audit_date.txt"
        would = scheduling.should_run_daily(marker_path, m_cfg["hour_utc"], now)
        print(f"Would run now: {would} (hour_utc={m_cfg['hour_utc']}, current UTC hour={now.hour})")
        return 0

    result = run_nightly_audit(repo, cfg, now=now, force=args.force)
    if not result.ran:
        print("Not yet time for the nightly audit (or it already ran today). Use --force to run anyway.")
        return 0

    for s in result.steps:
        print(f"{'OK  ' if s.passed else 'FAIL'}  {s.name}: {s.detail}")
    print(result.digest_line)
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
