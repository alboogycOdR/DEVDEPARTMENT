#!/usr/bin/env python3
"""retro.py — DEVDEPARTMENT Wave C (v4.3)

Weekly retro drafter. Reads REVIEW.md, AUTOPILOT_LOG.md, team_stats output,
PLAN.md cycle times, INSTINCTS.md, and pending amendments; drafts
RETRO-<isoweek>.md. Purely descriptive + proposal-referencing — never mutates
INSTINCTS.md or any protocol file. Fail-open throughout.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

sys.path.insert(0, str(Path(__file__).resolve().parent))
import instincts as inst_mod  # noqa: E402
from validate_plan import Report, parse_tasks  # noqa: E402

MARKER_REL = ".devteam/last_retro_week.txt"
AMEND_DIR_REL = ".devteam/pending_amendments"
TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _read(repo: Path, name: str) -> str:
    try:
        return (repo / name).read_text(encoding="utf-8")
    except OSError:
        return ""


def _parse_ts(v: str) -> datetime | None:
    try:
        return datetime.strptime(v.strip(), TS_FMT).replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


# ------------------------------------------------------------ cycle time ----
def cycle_times_hours(plan_text: str) -> dict[str, float]:
    """Started_At -> Updated_At, done tasks only, in hours."""
    out: dict[str, float] = {}
    for t in parse_tasks(plan_text, Report()):
        if t.fields.get("Status", "").strip() != "done":
            continue
        a = _parse_ts(t.fields.get("Started_At", ""))
        b = _parse_ts(t.fields.get("Updated_At", ""))
        if a and b and b >= a:
            out[t.task_id] = round((b - a).total_seconds() / 3600.0, 2)
    return out


# --------------------------------------------------------- review mining ----
TASK_REF_RE = re.compile(r"\bTASK-[A-Z0-9-]+\b")
REWORK_KEYWORDS = ("rework", "rejected", "needs_rework", "fail")
CLEAN_KEYWORDS = ("first-pass", "first_pass", "approved", "clean", "pass")


def review_outcomes(review_text: str) -> list[dict]:
    out = []
    for line in review_text.splitlines():
        m = TASK_REF_RE.search(line)
        if not m:
            continue
        low = line.lower()
        rework = any(k in low for k in REWORK_KEYWORDS)
        clean = (not rework) and any(k in low for k in CLEAN_KEYWORDS)
        if rework or clean:
            out.append({"task_id": m.group(0), "rework": rework})
    return out


def territory_churn(plan_text: str, outcomes: list[dict]) -> dict[str, int]:
    """Rework count per top-level territory directory."""
    paths_by_task: dict[str, list[str]] = {}
    for t in parse_tasks(plan_text, Report()):
        raw = t.fields.get("Owned_Paths", "")
        paths_by_task[t.task_id] = [p.strip() for p in re.split(r"[,\n]", raw) if p.strip()]
    churn: dict[str, int] = {}
    for o in outcomes:
        if not o["rework"]:
            continue
        for p in paths_by_task.get(o["task_id"], []):
            top = p.split("/")[0] or p
            churn[top] = churn.get(top, 0) + 1
    return dict(sorted(churn.items(), key=lambda kv: -kv[1]))


# ------------------------------------------- instinct effectiveness xref ----
def instinct_effectiveness(repo: Path, plan_text: str,
                           outcomes: list[dict]) -> dict:
    """Did tasks matching an injected (active/probation) instinct have a
    higher first-pass rate than the project average?"""
    instincts = [i for i in inst_mod.load(repo) if i.status in ("active", "probation")]
    paths_by_task = {t.task_id: [p.strip() for p in
                                 re.split(r"[,\n]", t.fields.get("Owned_Paths", ""))
                                 if p.strip()]
                     for t in parse_tasks(plan_text, Report())}
    total = {"n": 0, "clean": 0}
    matched = {"n": 0, "clean": 0}
    for o in outcomes:
        total["n"] += 1
        total["clean"] += 0 if o["rework"] else 1
        paths = paths_by_task.get(o["task_id"], [])
        if paths and any(inst_mod.matches_territory(paths, i) for i in instincts):
            matched["n"] += 1
            matched["clean"] += 0 if o["rework"] else 1
    rate = lambda d: round(d["clean"] / d["n"], 3) if d["n"] else None  # noqa: E731
    return {"project_first_pass_rate": rate(total),
            "instinct_matched_first_pass_rate": rate(matched),
            "matched_reviews": matched["n"], "total_reviews": total["n"]}


# ------------------------------------------------------------------- run ----
def run(repo: str | Path, cfg: dict) -> Path | None:
    repo = Path(repo)
    try:
        return _run(repo, cfg)
    except Exception as e:  # fail-open boundary
        try:
            ts = time.strftime(TS_FMT, time.gmtime())
            with open(repo / "AUTOPILOT_LOG.md", "a", encoding="utf-8") as fh:
                fh.write(f"- [{ts}] RETRO error (fail-open): {type(e).__name__}: {e}\n")
        except OSError:
            pass
        return None


def _run(repo: Path, cfg: dict) -> Path:
    now = datetime.now(timezone.utc)
    iso = now.isocalendar()
    week = f"{iso.year}-W{iso.week:02d}"

    plan = _read(repo, "PLAN.md")
    review = _read(repo, "REVIEW.md")
    log = _read(repo, "AUTOPILOT_LOG.md")

    cycles = cycle_times_hours(plan)
    outcomes = review_outcomes(review)
    churn = territory_churn(plan, outcomes)
    eff = instinct_effectiveness(repo, plan, outcomes)
    instincts = inst_mod.load(repo)

    pending = sorted((repo / AMEND_DIR_REL).glob("AMEND-*.md")) \
        if (repo / AMEND_DIR_REL).is_dir() else []
    pending = [p for p in pending
               if "**Status:** pending" in p.read_text(encoding="utf-8", errors="replace")]

    escalations = {"P0": log.count("P0"), "P1": log.count("P1"), "P2": log.count("P2")}
    slow = sorted(cycles.items(), key=lambda kv: -kv[1])[:5]

    team = {}
    try:
        import team_stats
        team = team_stats.compute(review)
    except Exception:
        pass

    lines = [
        f"# RETRO-{week}",
        f"_Drafted {now.strftime(TS_FMT)} by scripts/retro.py — descriptive only; "
        "this document never mutates INSTINCTS.md or any protocol file._",
        "",
        "## Cycle time (done tasks, Started_At → Updated_At)",
    ]
    if cycles:
        lines.append(f"- Tasks measured: {len(cycles)} · mean "
                     f"{round(mean(cycles.values()), 2)}h")
        lines.append("- Slowest: " + ", ".join(f"{t} ({h}h)" for t, h in slow))
    else:
        lines.append("- No completed tasks with valid timestamps this period.")

    lines += ["", "## Territory churn (rework findings per territory)"]
    lines += ([f"- `{k}/` — {v} rework finding(s)" for k, v in churn.items()]
              or ["- No rework churn recorded."])

    lines += ["", "## Instinct effectiveness"]
    if eff["total_reviews"]:
        lines.append(f"- Project first-pass rate: {eff['project_first_pass_rate']}")
        lines.append(f"- First-pass rate for instinct-matched territories: "
                     f"{eff['instinct_matched_first_pass_rate']} "
                     f"({eff['matched_reviews']} of {eff['total_reviews']} reviews)")
        a, b = eff["instinct_matched_first_pass_rate"], eff["project_first_pass_rate"]
        if a is not None and b is not None:
            verdict = "higher — instincts appear to be helping" if a > b else \
                      "not higher — review whether current instincts target the real failure modes"
            lines.append(f"- Comparison: instinct-matched rate is {verdict}.")
    else:
        lines.append("- No reviews in window.")
    lines.append(f"- Active instincts: "
                 f"{sum(1 for i in instincts if i.status == 'active')} · probation: "
                 f"{sum(1 for i in instincts if i.status == 'probation')} · retired: "
                 f"{sum(1 for i in instincts if i.status == 'retired')}")

    lines += ["", "## Escalations logged (AUTOPILOT_LOG.md keyword counts)",
              f"- P0: {escalations['P0']} · P1: {escalations['P1']} · P2: {escalations['P2']}"]

    if isinstance(team, dict) and team:
        lines += ["", "## Team stats snapshot", "```json",
                  json.dumps(team, indent=2, default=str)[:3000], "```"]

    lines += ["", "## Pending AMEND proposals awaiting a decision"]
    lines += ([f"- {p.stem} — reply `/approve {p.stem}` or `/rework {p.stem} <reason>`"
               for p in pending] or ["- None."])
    lines.append("")

    out = repo / f"RETRO-{week}.md"
    out.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return out


if __name__ == "__main__":
    cfg_path = Path("autopilot.json")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    p = run(".", cfg)
    print(p if p else "retro failed (see AUTOPILOT_LOG.md)")
