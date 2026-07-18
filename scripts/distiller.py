#!/usr/bin/env python3
"""distiller.py — DEVDEPARTMENT Wave C (v4.3)

Post-review-batch distillation: mines new REVIEW.md findings into INSTINCTS.md
instincts via a headless sonnet-5 call, and routes anything targeting
AGENTS.md / CLAUDE.md / briefings through the AMEND-NNN constitutional gate.

Guarantees (tested):
  * Never mutates AGENTS.md, CLAUDE.md, or briefings/** — amendment proposals
    are written ONLY under .devteam/pending_amendments/.
  * Skips when < min_new_findings new findings since the last run (no noise).
  * Atomic INSTINCTS.md writes; malformed model output leaves the file
    byte-identical.
  * Fail-open end to end: any exception logs and returns a DistillResult with
    ok=False; nothing propagates to the supervisor tick.

NOTE ON NOTIFICATION: this module does NOT send the P2 Telegram alert for a
newly-written amendment itself — the real scripts/notify.py exposes a CLI
(subprocess, `--priority/--message/--channels`), not an importable
send()/notify() function, and only supervisor.py has the RuntimeState needed
to honour an active /mute. The caller (supervisor.py's tick loop) reads
DistillResult.amendments after run() returns and sends the P2 itself via the
same notify()/is_muted() path every other P2 escalation already uses.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import instincts as inst_mod  # noqa: E402

MARKER_REL = ".devteam/last_distill_ts.txt"
AMEND_DIR_REL = ".devteam/pending_amendments"
DEFAULT_MIN_NEW_FINDINGS = 3
AMEND_HEADER = "## PROPOSED AMENDMENT"

DISTILLER_PROMPT_TEMPLATE = (
    "Read the following new Review_Findings entries from REVIEW.md since {ts}. "
    "For each recurring or high-cost failure pattern, draft a new instinct or "
    "strengthen an existing one in the exact INSTINCTS.md block format. Never "
    "delete an existing instinct — propose Status: retired instead if "
    "warranted. If you believe the ROOT CAUSE is a gap in AGENTS.md or a "
    "briefing rather than something a per-task instinct can fix, write a "
    "## PROPOSED AMENDMENT section instead, describing the exact diff and "
    "citing the evidence. Respond only in the required format — no preamble, "
    "no explanation outside the blocks.\n\n"
    "Current INSTINCTS.md (for IDs and existing rules — new IDs must continue "
    "the sequence, never reuse):\n{instincts}\n\n"
    "New findings:\n{findings}\n"
)


@dataclass
class DistillResult:
    ok: bool
    skipped: bool = False
    reason: str = ""
    new_instincts: list[str] = field(default_factory=list)
    updated_instincts: list[str] = field(default_factory=list)
    amendments: list[str] = field(default_factory=list)


def _log(repo: Path, line: str) -> None:
    try:
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(repo / "AUTOPILOT_LOG.md", "a", encoding="utf-8") as fh:
            fh.write(f"- [{ts}] {line}\n")
    except OSError:
        pass


# ------------------------------------------------------- findings mining ----
def read_marker(repo: Path) -> str:
    try:
        return (repo / MARKER_REL).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_marker(repo: Path, ts: str) -> None:
    p = repo / MARKER_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(ts + "\n", encoding="utf-8")


TS_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)\b")
TASK_REF_RE = re.compile(r"\bTASK-[A-Z0-9-]+\b")


def extract_new_findings(review_text: str, since_ts: str) -> list[str]:
    """Tolerant extraction: any REVIEW.md line that references a TASK id and a
    UTC timestamp newer than `since_ts` (or all such lines when no marker yet).
    Works for both table-row and bullet formats — we deliberately avoid
    coupling to one exact REVIEW.md layout; the model receives raw lines."""
    out = []
    for line in review_text.splitlines():
        if not TASK_REF_RE.search(line):
            continue
        m = TS_RE.search(line)
        line_ts = m.group(1) if m else ""
        if not since_ts or (line_ts and line_ts > since_ts):
            out.append(line.rstrip())
        elif not line_ts and not since_ts:
            out.append(line.rstrip())
    return out


def latest_ts(lines: list[str]) -> str:
    stamps = [m.group(1) for ln in lines for m in [TS_RE.search(ln)] if m]
    return max(stamps) if stamps else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------- model output ----
def split_model_output(text: str) -> tuple[str, str]:
    """Return (instinct_blocks_text, amendment_text). Amendment section starts
    at the first '## PROPOSED AMENDMENT' header, runs to end of output."""
    idx = text.find(AMEND_HEADER)
    if idx == -1:
        return text, ""
    return text[:idx], text[idx:]


def call_model(prompt: str, cfg: dict) -> str:
    """Shell out using the same headless pattern as cfg['review_cmd'] (claude
    -p ...). Overridable via cfg['learning']['distill_cmd'] for testing."""
    learning = cfg.get("learning", {})
    cmd = learning.get("distill_cmd") or [
        "claude", "-p", "--model", learning.get("model", "claude-sonnet-5"),
        "--dangerously-skip-permissions",
    ]
    proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                          timeout=int(learning.get("distill_timeout_seconds", 600)))
    if proc.returncode != 0:
        raise RuntimeError(f"distill model call failed rc={proc.returncode}: "
                           f"{proc.stderr[:500]}")
    return proc.stdout


# ------------------------------------------------------------ amendments ----
def next_amend_id(repo: Path) -> str:
    d = repo / AMEND_DIR_REL
    hi = 0
    if d.is_dir():
        for f in d.glob("AMEND-*.md"):
            m = re.match(r"AMEND-(\d+)\.md$", f.name)
            if m:
                hi = max(hi, int(m.group(1)))
    return f"AMEND-{hi + 1:03d}"


def write_amendment(repo: Path, body: str) -> str:
    """Write a proposal file under .devteam/pending_amendments/ ONLY."""
    amend_id = next_amend_id(repo)
    d = repo / AMEND_DIR_REL
    d.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (d / f"{amend_id}.md").write_text(
        f"# {amend_id}\n**Proposed:** {ts}\n**Status:** pending\n\n{body.strip()}\n",
        encoding="utf-8", newline="\n")
    return amend_id


# -------------------------------------------------- rationalization rows ----
def rationalization_candidates(cfg: dict, repo: Path) -> list[str]:
    """When team_stats shows a rework category recurring >= 3x in the window,
    return category names — the distiller prompt is extended so the model can
    draft a 'Common Rationalizations' row, which ALSO routes via AMEND."""
    try:
        import team_stats
        review = (repo / "REVIEW.md").read_text(encoding="utf-8")
        stats = team_stats.compute(review)
        threshold = int(cfg.get("learning", {}).get("rationalization_threshold", 3))
        cats: dict[str, int] = {}
        for unit_stats in stats.values():
            if not isinstance(unit_stats, dict):
                continue
            for cat, n in (unit_stats.get("rework_causes") or {}).items():
                cats[cat] = cats.get(cat, 0) + int(n)
        return sorted(c for c, n in cats.items() if n >= threshold)
    except Exception:
        return []


# -------------------------------------------------- deterministic lifecycle -
REWORK_KEYWORDS = ("rework", "rejected", "needs_rework", "fail")
CLEAN_KEYWORDS = ("first-pass", "first_pass", "approved", "clean", "pass")
PATHS_HINT_RE = re.compile(r"(?:Territory|Paths|Owned_Paths)\s*[:=]\s*([^|]+)")
CATEGORY_RE = re.compile(r"\b(tests|territory|spec|quality|protocol)\b", re.I)


def mine_outcomes(findings: list[str], plan_text: str = "") -> list[dict]:
    """Tolerantly derive (task_id, is_rework, paths, category) per finding
    line. Paths come from an inline Territory/Paths hint when present, else
    from the task's Owned_Paths in PLAN.md."""
    plan_paths: dict[str, list[str]] = {}
    if plan_text:
        try:
            from validate_plan import Report, parse_tasks
            for t in parse_tasks(plan_text, Report()):
                raw = t.fields.get("Owned_Paths", "")
                plan_paths[t.task_id] = [p.strip() for p in re.split(r"[,\n]", raw)
                                         if p.strip() and p.strip() not in ("—", "-")]
        except Exception:
            pass
    out = []
    for line in findings:
        tm = TASK_REF_RE.search(line)
        if not tm:
            continue
        low = line.lower()
        is_rework = any(k in low for k in REWORK_KEYWORDS)
        is_clean = (not is_rework) and any(k in low for k in CLEAN_KEYWORDS)
        pm = PATHS_HINT_RE.search(line)
        paths = ([p.strip() for p in pm.group(1).split(",") if p.strip()]
                 if pm else plan_paths.get(tm.group(0), []))
        cm = CATEGORY_RE.search(line)
        out.append({"task_id": tm.group(0), "rework": is_rework,
                    "clean": is_clean, "paths": paths,
                    "category": cm.group(1).lower() if cm else ""})
    return out


def apply_lifecycle(instincts_list: list, outcomes: list[dict]) -> list[str]:
    """Code-owned confidence math (spec: bump +0.1 on matching rework, decay
    -0.15 after 5 consecutive clean first-passes, probation/retire proposals
    from thresholds). Returns IDs touched."""
    touched: list[str] = []
    for o in outcomes:
        if not o["paths"]:
            continue
        for i in instincts_list:
            if i.status == "retired":
                continue
            if not inst_mod.matches_territory(o["paths"], i):
                continue
            if o["rework"]:
                inst_mod.bump_confidence(i, o["task_id"])
                touched.append(i.inst_id)
            elif o["clean"] and i.status == "active":
                before = i.confidence
                inst_mod.register_clean_pass(i)
                if i.confidence != before:
                    touched.append(i.inst_id)
    for i in instincts_list:
        new_status = inst_mod.proposed_status(i)
        if new_status != i.status:
            i.status = new_status
            touched.append(i.inst_id)
    return sorted(set(touched))



def run(repo: str | Path, cfg: dict) -> DistillResult:
    repo = Path(repo)
    try:
        return _run(repo, cfg)
    except Exception as e:  # absolute fail-open boundary
        _log(repo, f"DISTILL error (fail-open): {type(e).__name__}: {e}")
        return DistillResult(ok=False, reason=str(e))


def _run(repo: Path, cfg: dict) -> DistillResult:
    learning = cfg.get("learning", {})
    min_new = int(learning.get("min_new_findings", DEFAULT_MIN_NEW_FINDINGS))

    try:
        review_text = (repo / "REVIEW.md").read_text(encoding="utf-8")
    except OSError:
        return DistillResult(ok=True, skipped=True, reason="no REVIEW.md")

    since = read_marker(repo)
    findings = extract_new_findings(review_text, since)
    if len(findings) < min_new:
        return DistillResult(ok=True, skipped=True,
                             reason=f"{len(findings)} new findings < min {min_new}")

    current = inst_mod.load(repo)

    # Deterministic lifecycle pass FIRST — bumps/decay/status thresholds are
    # code-owned, never delegated to the model.
    plan_text = ""
    try:
        plan_text = (repo / "PLAN.md").read_text(encoding="utf-8")
    except OSError:
        pass
    lifecycle_touched = apply_lifecycle(current, mine_outcomes(findings, plan_text))
    if lifecycle_touched:
        if not inst_mod.save_atomic(repo, current):
            return DistillResult(ok=False, reason="lifecycle atomic save failed")

    prompt = DISTILLER_PROMPT_TEMPLATE.format(
        ts=since or "(beginning)",
        instincts=inst_mod.render_file(current),
        findings="\n".join(findings),
    )
    rats = rationalization_candidates(cfg, repo)
    if rats:
        prompt += ("\nAdditionally, these rework categories recurred >=3 times: "
                   + ", ".join(rats)
                   + ". For each, draft a proposed 'Common Rationalizations' table row "
                     "(excuse + rebuttal) inside a ## PROPOSED AMENDMENT section "
                     "targeting the relevant briefing — never as an instinct.\n")

    output = call_model(prompt, cfg)
    instinct_text, amend_text = split_model_output(output)

    proposed = inst_mod.parse_instincts(instinct_text)
    result = DistillResult(ok=True)
    result.updated_instincts.extend(lifecycle_touched)

    if proposed:
        by_id = {i.inst_id: i for i in current}
        for p in proposed:
            existing = by_id.get(p.inst_id)
            if existing is None:
                # New instinct: force sequential ID + seed confidence; never
                # trust the model on either.
                p.inst_id = inst_mod.next_id(current)
                p.confidence = inst_mod.SEED_CONFIDENCE
                if p.status not in ("active",):
                    p.status = "active"
                current.append(p)
                by_id[p.inst_id] = p
                result.new_instincts.append(p.inst_id)
            else:
                # Update path: merge sources; bump per matching new source;
                # accept only forward status transitions (active->probation->retired).
                for src in p.source:
                    if src not in existing.source:
                        existing.source.append(src)
                        existing.confidence = min(inst_mod.BUMP_CAP,
                                                  round(existing.confidence + inst_mod.BUMP, 2))
                order = {"active": 0, "probation": 1, "retired": 2}
                if order.get(p.status, 0) > order.get(existing.status, 0):
                    # honour thresholds: retirement only from probation + low conf
                    if p.status == "retired" and not (
                        existing.status == "probation"
                        and existing.confidence < inst_mod.RETIRE_THRESHOLD
                    ):
                        pass  # premature retirement proposal rejected
                    else:
                        existing.status = p.status
                result.updated_instincts.append(existing.inst_id)
        if not inst_mod.save_atomic(repo, current):
            _log(repo, "DISTILL malformed merge — INSTINCTS.md left untouched")
            return DistillResult(ok=False, reason="atomic save validation failed")

    if amend_text.strip():
        # Split multiple amendment sections on repeated headers.
        parts = [AMEND_HEADER + p for p in amend_text.split(AMEND_HEADER) if p.strip()]
        for part in parts:
            amend_id = write_amendment(repo, part)
            result.amendments.append(amend_id)
            _log(repo, f"DISTILL amendment proposed: {amend_id} "
                       f"(caller sends P2 notification — see DistillResult.amendments)")

    write_marker(repo, latest_ts(findings))
    _log(repo, "DISTILL ok "
               f"new={len(result.new_instincts)} updated={len(result.updated_instincts)} "
               f"amend={len(result.amendments)}")
    return result


if __name__ == "__main__":
    cfg_path = Path("autopilot.json")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else {}
    r = run(".", cfg)
    print(json.dumps(r.__dict__, indent=2))
