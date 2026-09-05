#!/usr/bin/env python3
"""instincts.py — DEVDEPARTMENT Wave C (v4.3)

Parse/write INSTINCTS.md, confidence lifecycle, territory-glob matching,
and the dispatch-time injection helper.

Design rules (per DEVDEPARTMENT_V4_COMPLETION_SPEC.md, Wave C):
  * INSTINCTS.md block schema round-trips byte-stably.
  * Territory matching REUSES validate_plan.globs_intersect — never a second
    glob implementation.
  * IDs are sequential INST-NNN and never reused (next_id scans ALL blocks,
    including retired ones).
  * Writes are atomic: temp file -> parse-validate -> os.replace.
  * Everything is fail-open: a broken INSTINCTS.md yields zero instincts,
    never an exception that could reach the supervisor tick.

CLI (used by dispatch.sh / dispatch.ps1):
    python3 scripts/instincts.py inject --paths "python/orb/**,scripts/x.py" \
        [--file INSTINCTS.md] [--limit 5]
Prints the "## PROJECT INSTINCTS — treat as acceptance criteria" section to
stdout, or nothing at all if no active/probation instinct matches (so the
dispatch scripts can blindly append the output).

INTEGRATION NOTE: the real dispatch.sh/dispatch.ps1 compose one generic
prompt for a unit and let the builder itself decide (via its own
resume-first/claim logic) which task it ends up working on — they do not
pre-resolve a single task's Owned_Paths before launch. So the CLI also
accepts `--unit GB|CX --repo <path>` instead of `--paths`: it predicts the
same task the builder's own resume-first rule would pick (first an
in_progress/claimed task for that unit, else the highest-priority pending
task for that unit whose dependencies are done) and resolves that task's
Owned_Paths itself. `--paths` remains available directly (and is what the
test suite exercises) for callers that already know the target task.
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_plan import Report, Task, globs_intersect, parse_tasks  # noqa: E402  (single glob source of truth)

_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _deps_done(task: "Task", by_id: dict) -> bool:
    """Mirrors supervisor.py's _deps_done — deliberately reimplemented rather
    than imported: supervisor.py pulls in the full autopilot stack (tg
    listener, maintenance, budget) which is far too heavy for this small
    dispatch-time CLI to import just for one dependency check."""
    raw = task.get("Depends_On")
    if task.is_empty("Depends_On"):
        return True
    for dep in re.split(r"[,\s]+", raw):
        if re.match(r"^TASK-[A-Z0-9-]+$", dep):
            d = by_id.get(dep)
            if d is None or d.get("Status") != "done":
                return False
    return True


def resolve_owned_paths_for_unit(repo: str | Path, unit: str) -> list[str]:
    """Predict which task `unit`'s dispatch.sh/.ps1 launch is about to work on
    (same resume-first priority the builder's own briefing follows: resume an
    in_progress/claimed task first, else claim the highest-priority pending
    task with dependencies done) and return that task's Owned_Paths. Returns
    [] on anything unexpected — fail-open, never blocks a dispatch."""
    try:
        plan_path = Path(repo) / "PLAN.md"
        text = plan_path.read_text(encoding="utf-8")
        rep = Report()
        tasks = [t for t in parse_tasks(text, rep) if t.get("Assigned_To") == unit]
        by_id = {t.task_id: t for t in parse_tasks(text, rep)}

        resuming = [t for t in tasks if t.get("Status") in ("in_progress", "claimed")]
        if resuming:
            target = resuming[0]
        else:
            pending = [t for t in tasks if t.get("Status") == "pending" and _deps_done(t, by_id)]
            if not pending:
                return []
            pending.sort(key=lambda t: (_PRIORITY_ORDER.get(t.get("Priority"), 4), t.task_id))
            target = pending[0]

        return _split_csv(target.get("Owned_Paths"))
    except Exception:
        return []

INSTINCTS_FILENAME = "INSTINCTS.md"
VALID_STATUSES = ("active", "probation", "retired")

SEED_CONFIDENCE = 0.6
BUMP = 0.1
BUMP_CAP = 1.0
DECAY = 0.15
DECAY_CLEAN_STREAK = 5
PROBATION_THRESHOLD = 0.3
RETIRE_THRESHOLD = 0.15

HEADER_RE = re.compile(r"^###\s+(INST-\d+)\s*$")
FIELD_RE = re.compile(r"^\*\*(Rule|Territory|Confidence|Source|Status|Retired):\*\*\s*(.*)$")

# --- lifecycle (v4.8) -------------------------------------------------------
# Retired instincts accumulate forever: next_id must keep scanning them so IDs
# are never reused, but INSTINCTS.md itself should not grow without bound. The
# `prune` command moves long-retired blocks to ARCHIVE_FILENAME, where next_id
# can still see them (load_all reads both files).
ARCHIVE_FILENAME = "INSTINCTS.archive.md"
PRUNE_GRACE_DAYS = 30
# Must stay identical to distiller.AMEND_DIR_REL — see write_amendment below.
AMEND_DIR_REL = ".devteam/pending_amendments"

# Promotion bar: an instinct is a candidate for a constitutional amendment only
# when it has proven itself repeatedly, not merely scored well once.
PROMOTE_MIN_CONFIDENCE = 0.9
PROMOTE_MIN_SOURCES = 2
# Clustering bar for `evolve`: this many territory-overlapping candidates in one
# cluster suggests a single durable rule rather than N separate instincts.
EVOLVE_MIN_CLUSTER = 2

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

FILE_PREAMBLE = (
    "# INSTINCTS.md — distilled project instincts\n"
    "\n"
    "Maintained by scripts/distiller.py (Wave C learning loop). Instinct\n"
    "entries are DATA: additions and confidence updates are auto-applied and\n"
    "git-reviewable. Changes to AGENTS.md / CLAUDE.md / briefings always go\n"
    "through the AMEND-NNN constitutional gate instead — see docs/LEARNING.md.\n"
)


@dataclass
class Instinct:
    inst_id: str
    rule: str = ""
    territory: list[str] = field(default_factory=list)
    confidence: float = SEED_CONFIDENCE
    source: list[str] = field(default_factory=list)
    status: str = "active"
    # Date (YYYY-MM-DD) this instinct was first OBSERVED in status 'retired'.
    # Empty for every non-retired instinct. Serialized only when set, so files
    # written before v4.8 still round-trip byte-identically.
    retired_on: str = ""
    # Internal lifecycle bookkeeping (not serialized; derived by callers):
    clean_streak: int = 0

    @property
    def num(self) -> int:
        try:
            return int(self.inst_id.split("-")[1])
        except (IndexError, ValueError):
            return 0

    def render(self) -> str:
        out = (
            f"### {self.inst_id}\n"
            f"**Rule:** {self.rule}\n"
            f"**Territory:** {', '.join(self.territory)}\n"
            f"**Confidence:** {format_confidence(self.confidence)}\n"
            f"**Source:** {', '.join(self.source)}\n"
            f"**Status:** {self.status}\n"
        )
        # Emitted only when set: a pre-v4.8 file with no retirement dates
        # renders back byte-identically, which save_atomic's round-trip
        # validation and the distiller's stability tests both depend on.
        if self.retired_on:
            out += f"**Retired:** {self.retired_on}\n"
        return out


def format_confidence(c: float) -> str:
    """Round to 2dp, strip trailing zero so 0.9 stays '0.9' (round-trip)."""
    s = f"{round(c + 1e-9, 2):.2f}"
    if s.endswith("0") and not s.endswith(".00"):
        s = s[:-1]
    if s == "1.00":
        s = "1.0"
    if s == "0.00":
        s = "0.0"
    return s


def _split_csv(raw: str) -> list[str]:
    return [p.strip() for p in raw.split(",") if p.strip()]


def parse_instincts(text: str) -> list[Instinct]:
    """Parse INSTINCTS.md text into Instinct objects. Tolerant: malformed
    blocks are skipped rather than raising."""
    out: list[Instinct] = []
    cur: Instinct | None = None
    for line in text.splitlines():
        stripped = line.strip()
        h = HEADER_RE.match(stripped)
        if h:
            cur = Instinct(inst_id=h.group(1))
            out.append(cur)
            continue
        if cur is None:
            continue
        f = FIELD_RE.match(stripped)
        if not f:
            continue
        key, val = f.group(1), f.group(2).strip()
        if key == "Rule":
            cur.rule = val
        elif key == "Territory":
            cur.territory = _split_csv(val)
        elif key == "Confidence":
            try:
                cur.confidence = float(val)
            except ValueError:
                cur.confidence = SEED_CONFIDENCE
        elif key == "Source":
            cur.source = _split_csv(val)
        elif key == "Status":
            cur.status = val if val in VALID_STATUSES else "active"
        elif key == "Retired":
            # Garbage in a hand-edited date must not silently become "today"
            # (which would restart the grace period); drop it and let the
            # next prune re-stamp it deliberately.
            cur.retired_on = val if DATE_RE.match(val) else ""
    # Drop blocks missing a rule entirely (unusable).
    return [i for i in out if i.rule]


def render_file(instincts: list[Instinct]) -> str:
    blocks = "\n".join(i.render() for i in sorted(instincts, key=lambda x: x.num))
    return FILE_PREAMBLE + ("\n" + blocks if blocks else "")


def load(repo: str | Path, filename: str = INSTINCTS_FILENAME) -> list[Instinct]:
    p = Path(repo) / filename
    try:
        return parse_instincts(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return []


def save_atomic(repo: str | Path, instincts: list[Instinct],
                filename: str = INSTINCTS_FILENAME) -> bool:
    """Write via temp file; validate the rendered text parses back to the same
    number of blocks before os.replace. Returns False (and leaves the existing
    file untouched) on any failure — never raises."""
    target = Path(repo) / filename
    try:
        text = render_file(instincts)
        reparsed = parse_instincts(text)
        if len(reparsed) != len(instincts):
            return False
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        return True
    except Exception:
        return False


def next_id(instincts: list[Instinct]) -> str:
    """Sequential, never reused — scans ALL blocks including retired.

    CALLERS MUST PASS load_all(), not load(): once prune has archived a
    retired block, load() alone no longer sees its number and next_id would
    hand out an ID that already exists in the archive."""
    hi = max((i.num for i in instincts), default=0)
    return f"INST-{hi + 1:03d}"


def load_all(repo: str | Path, filename: str = INSTINCTS_FILENAME,
             archive: str = ARCHIVE_FILENAME) -> list[Instinct]:
    """Live instincts plus archived ones. Use for ID allocation and audits;
    never for injection (archived instincts are retired by definition)."""
    return load(repo, filename) + load(repo, archive)


# ------------------------------------------------------------- lifecycle ----
def bump_confidence(inst: Instinct, source_task_id: str) -> None:
    inst.confidence = min(BUMP_CAP, round(inst.confidence + BUMP, 2))
    tag = f"{source_task_id} rework"
    if tag not in inst.source and source_task_id not in inst.source:
        inst.source.append(tag)
    inst.clean_streak = 0


def decay_confidence(inst: Instinct) -> None:
    inst.confidence = max(0.0, round(inst.confidence - DECAY, 2))
    inst.clean_streak = 0


def proposed_status(inst: Instinct) -> str:
    """What status the distiller should PROPOSE given current confidence.
    (Within INSTINCTS.md these proposals are auto-applied — the constitutional
    gate covers AGENTS.md/CLAUDE.md/briefings, not this data file.)"""
    if inst.status == "probation" and inst.confidence < RETIRE_THRESHOLD:
        return "retired"
    if inst.status == "active" and inst.confidence < PROBATION_THRESHOLD:
        return "probation"
    return inst.status


def register_clean_pass(inst: Instinct) -> None:
    """Call once per clean first-pass approval in the instinct's territory.
    After DECAY_CLEAN_STREAK consecutive clean passes since the last bump,
    apply the decay and reset the streak."""
    inst.clean_streak += 1
    if inst.clean_streak >= DECAY_CLEAN_STREAK:
        decay_confidence(inst)


# --------------------------------------------------- prune / evolve (v4.8) ---
# Ported in concept from ECC's continuous-learning-v2 (MIT): an instinct store
# needs an EXIT path, not just an entry one. ECC prunes pending instincts after
# 30 days and clusters survivors into skills. Ours differs in two ways that the
# constitution forces:
#   * we prune RETIRED instincts (the distiller already demotes via confidence
#     thresholds), not unreviewed ones — nothing here re-judges an instinct;
#   * evolve/promote NEVER edit AGENTS.md, CLAUDE.md or briefings. They emit an
#     AMEND-NNN proposal exactly like the distiller does, and a human decides.


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime())


def _days_since(date_str: str, today: str) -> int:
    """Whole days between two YYYY-MM-DD strings. Returns -1 on anything
    unparseable, which every caller treats as 'not yet eligible'."""
    try:
        d0 = datetime.date.fromisoformat(date_str)
        d1 = datetime.date.fromisoformat(today)
        return (d1 - d0).days
    except ValueError:
        return -1


@dataclass
class PruneResult:
    ok: bool = True
    stamped: list[str] = field(default_factory=list)   # retired, date recorded now
    archived: list[str] = field(default_factory=list)  # moved out of INSTINCTS.md
    waiting: list[str] = field(default_factory=list)   # retired, still inside grace
    error: str = ""


def prune(repo: str | Path, today: str | None = None, apply: bool = False,
          grace_days: int = PRUNE_GRACE_DAYS,
          filename: str = INSTINCTS_FILENAME,
          archive: str = ARCHIVE_FILENAME) -> PruneResult:
    """Two-phase retirement sweep.

    Phase 1 (stamp): a retired instinct with no **Retired:** date gets today's
    date. It is NOT archived in the same run. This is deliberate — the grace
    period must start from an observation we recorded, otherwise the very first
    prune after this feature ships would archive every historically-retired
    instinct at once, and a wrong retirement would vanish before anyone noticed.

    Phase 2 (archive): a retired instinct stamped more than `grace_days` ago
    moves to INSTINCTS.archive.md. IDs are never reused — next_id() reads
    load_all(), which spans both files.

    With apply=False (the default) nothing is written; the result describes
    what would happen. Fail-open: any error returns ok=False and touches
    nothing."""
    res = PruneResult()
    today = today or _today()
    try:
        live = load(repo, filename)
        archived = load(repo, archive)

        keep: list[Instinct] = []
        moving: list[Instinct] = []
        for i in live:
            if i.status != "retired":
                keep.append(i)
                continue
            if not i.retired_on:
                i.retired_on = today
                res.stamped.append(i.inst_id)
                keep.append(i)
                continue
            age = _days_since(i.retired_on, today)
            if age >= grace_days:
                moving.append(i)
                res.archived.append(i.inst_id)
            else:
                res.waiting.append(i.inst_id)
                keep.append(i)

        if not apply or (not res.stamped and not res.archived):
            return res

        # Archive first. If the live write then fails, the worst case is a
        # duplicated block (archived AND still live) — recoverable, and far
        # better than the reverse ordering, which loses the instinct outright.
        if moving:
            if not save_atomic(repo, archived + moving, archive):
                res.ok = False
                res.error = f"could not write {archive}; nothing pruned"
                return res
        if not save_atomic(repo, keep, filename):
            res.ok = False
            res.error = (f"could not write {filename}; "
                         f"{len(moving)} block(s) may now be duplicated in {archive}")
        return res
    except Exception as exc:  # fail-open, per module contract
        return PruneResult(ok=False, error=str(exc))


def promotion_candidates(instincts: list[Instinct],
                         min_confidence: float = PROMOTE_MIN_CONFIDENCE,
                         min_sources: int = PROMOTE_MIN_SOURCES) -> list[Instinct]:
    """Active instincts that have earned a shot at becoming a standing rule:
    high confidence AND evidence from more than one task. Confidence alone is
    not enough — a single task can bump one instinct to 0.9 on its own, and a
    rule that only ever fired in one territory is not yet a project-wide law."""
    out = [i for i in instincts
           if i.status == "active"
           and i.confidence >= min_confidence
           and len({s.split()[0] for s in i.source if s.strip()}) >= min_sources]
    out.sort(key=lambda i: (-i.confidence, i.num))
    return out


def cluster_by_territory(instincts: list[Instinct]) -> list[list[Instinct]]:
    """Group instincts whose territories overlap (transitively), so a cluster
    is a connected component over globs_intersect. Reuses the single glob
    implementation in validate_plan, same as matches_territory does."""
    items = [i for i in instincts if i.territory]
    parent = list(range(len(items)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a in range(len(items)):
        for b in range(a + 1, len(items)):
            if globs_intersect(items[a].territory, items[b].territory):
                ra, rb = find(a), find(b)
                if ra != rb:
                    parent[rb] = ra

    groups: dict[int, list[Instinct]] = {}
    for idx, inst in enumerate(items):
        groups.setdefault(find(idx), []).append(inst)
    out = [sorted(g, key=lambda i: i.num) for g in groups.values()]
    out.sort(key=lambda g: (-len(g), g[0].num))
    return out


def render_promotion_amendment(cluster: list[Instinct]) -> str:
    """The AMEND body. Deliberately a PROPOSAL with an explicit rejection
    option — the whole point of the constitutional gate is that promoting an
    instinct into AGENTS.md is a human decision, so this must never read like
    an instruction to apply it."""
    ids = ", ".join(i.inst_id for i in cluster)
    territory = sorted({t for i in cluster for t in i.territory})
    lines = [
        "## PROPOSED AMENDMENT",
        "",
        f"**Target:** AGENTS.md (standing rule) — promoted from {ids}",
        f"**Territory:** {', '.join(territory)}",
        "",
        "### Evidence",
        "",
    ]
    for i in cluster:
        lines.append(f"- **{i.inst_id}** (confidence {format_confidence(i.confidence)}, "
                     f"sources: {', '.join(i.source) or 'none recorded'}): {i.rule}")
    lines += [
        "",
        "### Why promote",
        "",
        f"These {len(cluster)} instinct(s) share a territory and have each cleared the "
        f"promotion bar (confidence >= {PROMOTE_MIN_CONFIDENCE}, evidence from "
        f">= {PROMOTE_MIN_SOURCES} distinct tasks). A rule that keeps re-earning its "
        "confidence in dispatch-time injection is cheaper as one standing rule than as "
        "N instincts competing for the injection budget.",
        "",
        "### Decision required",
        "",
        "- **Accept:** ORCH writes the consolidated rule into AGENTS.md, then sets the "
        "source instincts to `Status: retired` (prune archives them after the grace "
        "period).",
        "- **Reject:** delete this file. The instincts stay exactly as they are.",
        "",
        "Nothing has been changed in AGENTS.md, CLAUDE.md or briefings by generating "
        "this proposal.",
    ]
    return "\n".join(lines)


def write_amendment(repo: str | Path, body: str) -> str:
    """Write an AMEND-NNN proposal under .devteam/pending_amendments/ ONLY.

    Duplicated from distiller.write_amendment rather than imported: distiller
    already does `import instincts`, so importing it back here would be
    circular — and distiller pulls in the whole autopilot stack besides. Same
    reasoning as _deps_done above. The on-disk format must stay identical to
    distiller's; if that changes there, change it here."""
    repo = Path(repo)
    d = repo / AMEND_DIR_REL
    hi = 0
    if d.is_dir():
        for f in d.glob("AMEND-*.md"):
            m = re.match(r"AMEND-(\d+)\.md$", f.name)
            if m:
                hi = max(hi, int(m.group(1)))
    amend_id = f"AMEND-{hi + 1:03d}"
    d.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (d / f"{amend_id}.md").write_text(
        f"# {amend_id}\n**Proposed:** {ts}\n**Status:** pending\n\n{body.strip()}\n",
        encoding="utf-8", newline="\n")
    return amend_id


# -------------------------------------------------------------- matching ----
def matches_territory(owned_paths: list[str], inst: Instinct) -> bool:
    if not owned_paths or not inst.territory:
        return False
    return globs_intersect(owned_paths, inst.territory)


def top_matching(owned_paths: list[str], instincts: list[Instinct],
                 limit: int = 5) -> list[Instinct]:
    """Active + probation instincts whose territory intersects owned_paths,
    highest-confidence first, capped at `limit`. Retired: never injected."""
    hits = [i for i in instincts
            if i.status in ("active", "probation") and matches_territory(owned_paths, i)]
    hits.sort(key=lambda i: (-i.confidence, i.num))
    return hits[:limit]


def render_injection(matches: list[Instinct]) -> str:
    """The dispatch-prompt section. Empty string when nothing matches."""
    if not matches:
        return ""
    lines = ["## PROJECT INSTINCTS — treat as acceptance criteria", ""]
    for i in matches:
        flag = " [PROBATION — verify applicability]" if i.status == "probation" else ""
        lines.append(f"- **{i.inst_id}** (confidence {format_confidence(i.confidence)}"
                     f"{flag}): {i.rule}")
    lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------------- CLI ----
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="INSTINCTS.md helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    inj = sub.add_parser("inject", help="print dispatch-prompt instinct section")
    inj.add_argument("--paths", default=None,
                     help="comma-separated Owned_Paths of the task being dispatched")
    inj.add_argument("--unit", default=None, choices=["GB", "CX"],
                     help="resolve Owned_Paths from PLAN.md for this unit's next task "
                          "(alternative to --paths, for dispatch.sh/.ps1 which don't "
                          "pre-resolve a specific task before launch)")
    inj.add_argument("--file", default=INSTINCTS_FILENAME)
    inj.add_argument("--repo", default=".")
    inj.add_argument("--limit", type=int, default=5)

    st = sub.add_parser("status", help="summarize the instinct store")
    st.add_argument("--repo", default=".")
    st.add_argument("--file", default=INSTINCTS_FILENAME)

    pr = sub.add_parser("prune", help="stamp and archive long-retired instincts")
    pr.add_argument("--repo", default=".")
    pr.add_argument("--file", default=INSTINCTS_FILENAME)
    pr.add_argument("--grace-days", type=int, default=PRUNE_GRACE_DAYS)
    pr.add_argument("--apply", action="store_true",
                    help="actually write; without it, report only")

    ev = sub.add_parser("evolve", help="cluster promotion-ready instincts by territory")
    ev.add_argument("--repo", default=".")
    ev.add_argument("--file", default=INSTINCTS_FILENAME)
    ev.add_argument("--min-cluster", type=int, default=EVOLVE_MIN_CLUSTER)

    pm = sub.add_parser("promote", help="write an AMEND-NNN proposal for a cluster")
    pm.add_argument("--repo", default=".")
    pm.add_argument("--file", default=INSTINCTS_FILENAME)
    pm.add_argument("--ids", default=None,
                    help="comma-separated INST-NNN to promote; default is the "
                         "largest qualifying cluster from `evolve`")
    pm.add_argument("--apply", action="store_true",
                    help="actually write the proposal; without it, print it")

    ns = ap.parse_args(argv)

    if ns.cmd == "inject":
        try:
            instincts = load(ns.repo, ns.file)
            if ns.paths:
                owned = _split_csv(ns.paths)
            elif ns.unit:
                owned = resolve_owned_paths_for_unit(ns.repo, ns.unit)
            else:
                owned = []
            sys.stdout.write(render_injection(top_matching(owned, instincts, ns.limit)))
        except Exception:
            # Fail-open: a broken instinct store must never block a dispatch.
            return 0
        return 0

    # The commands below are ORCH-operated maintenance, not dispatch-path code.
    # They still never raise, but unlike `inject` they DO report failure through
    # the exit code — a silent no-op would look like "nothing to prune".
    if ns.cmd == "status":
        live = load(ns.repo, ns.file)
        arch = load(ns.repo, ARCHIVE_FILENAME)
        by_status = {s: [i for i in live if i.status == s] for s in VALID_STATUSES}
        print(f"live: {len(live)}   archived: {len(arch)}")
        for s in VALID_STATUSES:
            group = by_status[s]
            if not group:
                print(f"  {s:<9} 0")
                continue
            avg = sum(i.confidence for i in group) / len(group)
            print(f"  {s:<9} {len(group):<3} (mean confidence {format_confidence(avg)})")
        stale = [i for i in by_status["retired"] if not i.retired_on]
        if stale:
            print(f"  {len(stale)} retired instinct(s) unstamped — run `prune` to start "
                  f"their {PRUNE_GRACE_DAYS}-day clock: {', '.join(i.inst_id for i in stale)}")
        cands = promotion_candidates(live)
        if cands:
            print(f"  {len(cands)} promotion candidate(s): "
                  f"{', '.join(i.inst_id for i in cands)} — see `evolve`")
        return 0

    if ns.cmd == "prune":
        res = prune(ns.repo, apply=ns.apply, grace_days=ns.grace_days, filename=ns.file)
        if not res.ok:
            print(f"prune FAILED: {res.error}", file=sys.stderr)
            return 1
        verb = "stamped" if ns.apply else "would stamp"
        verb2 = "archived" if ns.apply else "would archive"
        print(f"{verb}: {', '.join(res.stamped) or 'none'}")
        print(f"{verb2}: {', '.join(res.archived) or 'none'}")
        print(f"within grace ({ns.grace_days}d): {', '.join(res.waiting) or 'none'}")
        if not ns.apply and (res.stamped or res.archived):
            print("(dry run — re-run with --apply to write)")
        return 0

    if ns.cmd == "evolve":
        live = load(ns.repo, ns.file)
        cands = promotion_candidates(live)
        if not cands:
            print("no instinct clears the promotion bar "
                  f"(confidence >= {PROMOTE_MIN_CONFIDENCE}, "
                  f">= {PROMOTE_MIN_SOURCES} distinct source tasks)")
            return 0
        clusters = [c for c in cluster_by_territory(cands) if len(c) >= ns.min_cluster]
        if not clusters:
            print(f"{len(cands)} promotion candidate(s), but no territory cluster of "
                  f"{ns.min_cluster}+: {', '.join(i.inst_id for i in cands)}")
            print("promote one directly with: instincts.py promote --ids INST-NNN")
            return 0
        for c in clusters:
            terr = sorted({t for i in c for t in i.territory})
            print(f"cluster ({len(c)}): {', '.join(i.inst_id for i in c)}")
            print(f"  territory: {', '.join(terr)}")
            for i in c:
                print(f"  - {i.inst_id}: {i.rule}")
        print("\npromote with: instincts.py promote --ids "
              f"{','.join(i.inst_id for i in clusters[0])} --apply")
        return 0

    if ns.cmd == "promote":
        live = load(ns.repo, ns.file)
        if ns.ids:
            wanted = {s.strip().upper() for s in ns.ids.split(",") if s.strip()}
            cluster = [i for i in live if i.inst_id.upper() in wanted]
            missing = wanted - {i.inst_id.upper() for i in cluster}
            if missing:
                print(f"unknown instinct id(s): {', '.join(sorted(missing))}", file=sys.stderr)
                return 1
        else:
            clusters = [c for c in cluster_by_territory(promotion_candidates(live))
                        if len(c) >= EVOLVE_MIN_CLUSTER]
            if not clusters:
                print("nothing qualifies for automatic promotion; pass --ids explicitly",
                      file=sys.stderr)
                return 1
            cluster = clusters[0]
        body = render_promotion_amendment(cluster)
        if not ns.apply:
            print(body)
            print("\n(dry run — re-run with --apply to write the AMEND file)")
            return 0
        amend_id = write_amendment(ns.repo, body)
        print(f"wrote {AMEND_DIR_REL}/{amend_id}.md — "
              f"AGENTS.md is UNCHANGED; review and decide.")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
