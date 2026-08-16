#!/usr/bin/env python3
"""sync_from_pack.py — bring an already-onboarded project up to date with the
DEVDEPARTMENT pack.

Why this exists: onboard.md is deliberately add-only ("never overwrite
existing files"), which is correct for FIRST onboarding but means an
already-onboarded project never receives pack improvements — it stays frozen
at whatever pack version it was born from, silently accumulating drift
(observed live: a project running the v1.0.0 coordination protocol while the
pack was at v4.5+ with entire failure classes fixed).

Ownership is data, not judgment: sync-manifest.json in the pack declares
every path as framework_owned (pack is source of truth), project_owned
(never touched), or merge_special (part-pack part-project, dedicated merge
logic). This script never improvises beyond the manifest.

Three-way logic per framework_owned file, using .devteam/sync_state.json
(hash of each file as last written by sync/onboarding):

  pack == project                        -> IN_SYNC     (nothing to do)
  project == baseline, pack differs      -> UPDATE      (pack improved it; safe)
  project != baseline (local edits!)     -> CONFLICT    (flag, do NOT clobber
                                                         unless --adopt-pack)
  no baseline (legacy project)           -> same as above but every differing
                                            file is a CONFLICT by construction
                                            — conservative on purpose. Use
                                            --adopt-pack for the first sync of
                                            a legacy project after reviewing
                                            the dry-run diff list.
  file absent in project                 -> ADD

Usage:
  python scripts/sync_from_pack.py --pack ../DEVDEPARTMENT            # dry-run is the DEFAULT
  python scripts/sync_from_pack.py --pack ../DEVDEPARTMENT --apply
  python scripts/sync_from_pack.py --pack ../DEVDEPARTMENT --apply --adopt-pack
  python scripts/sync_from_pack.py --pack ../DEVDEPARTMENT --apply --only scripts/dispatch.sh

Exit codes: 0 = clean (nothing to do, or applied without conflicts);
2 = conflicts present (unresolved); 1 = usage/environment error.

All writes are byte-exact (read_bytes/write_bytes) — no encoding or newline
translation, per the CRLF lesson in the pack's git history. Nothing here
shells out; pure filesystem. Commit the result yourself after reviewing
`git diff` — sync never runs git for you.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

SYNC_STATE_REL = ".devteam/sync_state.json"
MANIFEST_NAME = "sync-manifest.json"
STATE_VERSION = 1

# Verdicts
IN_SYNC = "IN_SYNC"
UPDATE = "UPDATE"
ADD = "ADD"
CONFLICT = "CONFLICT"
CONFLICT_ADOPTED = "CONFLICT_ADOPTED"
MISSING_IN_PACK = "MISSING_IN_PACK"


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str | None:
    try:
        return sha256_bytes(path.read_bytes())
    except (OSError, FileNotFoundError):
        return None


# --------------------------------------------------------------------------- #
#  State (the per-project baseline of what sync last wrote)
# --------------------------------------------------------------------------- #

def load_state(project: Path) -> dict:
    path = project / SYNC_STATE_REL
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("version") != STATE_VERSION:
            return {"version": STATE_VERSION, "files": {}}
        if not isinstance(data.get("files"), dict):
            return {"version": STATE_VERSION, "files": {}}
        return data
    except (OSError, FileNotFoundError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "files": {}}


def save_state(project: Path, state: dict) -> None:
    path = project / SYNC_STATE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)


# --------------------------------------------------------------------------- #
#  Manifest
# --------------------------------------------------------------------------- #

def load_manifest(pack: Path) -> dict:
    path = pack / MANIFEST_NAME
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found — the pack at {pack} predates sync support "
            f"(needs the pack itself updated first).")
    return json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------- #
#  Per-file decision
# --------------------------------------------------------------------------- #

@dataclass
class Decision:
    rel: str
    verdict: str
    detail: str = ""


@dataclass
class Report:
    decisions: list[Decision] = field(default_factory=list)
    merge_notes: list[str] = field(default_factory=list)

    def add(self, rel: str, verdict: str, detail: str = "") -> None:
        self.decisions.append(Decision(rel, verdict, detail))

    def by(self, verdict: str) -> list[Decision]:
        return [d for d in self.decisions if d.verdict == verdict]

    @property
    def has_conflicts(self) -> bool:
        return bool(self.by(CONFLICT))


def decide_file(rel: str, pack: Path, project: Path, state: dict,
                adopt_pack: bool) -> Decision:
    pack_hash = sha256_file(pack / rel)
    proj_hash = sha256_file(project / rel)
    base_hash = state.get("files", {}).get(rel)

    if pack_hash is None:
        return Decision(rel, MISSING_IN_PACK,
                        "listed in manifest but absent from the pack — pack bug, skipped")
    if proj_hash is None:
        return Decision(rel, ADD, "absent in project")
    if proj_hash == pack_hash:
        return Decision(rel, IN_SYNC)
    # Differs. Was the project's copy locally modified since sync last wrote it?
    if base_hash is not None and proj_hash == base_hash:
        return Decision(rel, UPDATE, "pack changed; project copy untouched since last sync")
    # Local modification (or legacy project with no baseline): conflict.
    verdict = CONFLICT_ADOPTED if adopt_pack else CONFLICT
    why = ("no sync baseline (legacy project)" if base_hash is None
           else "project copy modified since last sync")
    return Decision(rel, verdict, why)


# --------------------------------------------------------------------------- #
#  merge_special strategies
# --------------------------------------------------------------------------- #

def _find_line(text: str, needles: list[str], start: int = 0) -> int:
    """Index of the start of the first line at/after ``start`` whose stripped
    content equals one of ``needles`` (-1 if none).

    Anchored to line starts because a shorter markdown heading is a SUBSTRING
    of a longer one: a plain ``find()`` of ``## Builder territory mapping``
    matches inside ``### Builder territory mapping`` at offset+1, silently
    leaving a stray ``#`` behind. Observed on oikonomos, where that one
    character made the section compare unequal forever — the file could never
    reach a clean sync, and a merge would have written the stray '#' back out.
    """
    pos, n = start, len(text)
    while pos <= n:
        eol = text.find("\n", pos)
        line = text[pos:eol if eol >= 0 else n]
        if line.strip() in needles:
            return pos
        if eol < 0:
            return -1
        pos = eol + 1
    return -1


def _heading_level(line: str) -> int:
    """Number of leading '#' on a markdown heading (0 if not a heading)."""
    stripped = line.lstrip()
    return len(stripped) - len(stripped.lstrip("#")) if stripped.startswith("#") else 0


def _heading_level_of_pack(pack_text: str, markers: list[str]) -> int:
    for m in markers:
        if m in pack_text:
            return _heading_level(m)
    return 0


def _normalize_section(text: str) -> str:
    """Section content compared for equality modulo the two things a legal
    onboarding shape is allowed to differ by: heading level (an appended H2
    section demotes every subsection) and surrounding whitespace. Anything
    still different after this is a genuine local edit."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith(">"):
            continue
        out.append(s.lstrip("#").strip() if s.startswith("#") else s)
    return "\n".join(out)


def merge_marker_section(project_file: Path, pack_file: Path, markers: list[str],
                         apply: bool, report: Report,
                         preserve_after: list[str] | None = None,
                         adopt_pack: bool = False, state: dict | None = None) -> None:
    """Refresh the pack's section inside the project's file, preserving the
    project's own content on BOTH sides of it.

    Three boundaries, all learned from real onboarded projects (2026-08-15):

    * ``markers`` — onboard.md STEP 4 produces TWO legal shapes. A project with
      no CLAUDE.md receives the pack's file verbatim, so its marker is the
      pack's own H1. A project that already had one gets the section APPENDED
      under an H2 (``## Multi-Agent Orchestration — DEVDEPARTMENT (ORCH)``) —
      the common case, and the one a single-marker manifest could never sync.
      Candidates are tried in order; the first found in the project wins.
    * The project's own marker LINE is kept (not overwritten by the pack's),
      so an appended H2 stays an H2 and its provenance note survives. For the
      pack-H1 shape the two strings are identical, so behaviour is unchanged.
    * ``preserve_after`` — STEP 4 also appends the project's REAL territory map
      AFTER the pack section. Replacing marker→EOF would silently delete it
      (oikonomos had a substantial one). Anything from the first sentinel
      onward is the project's again and is preserved verbatim.

    Residual limit, stated plainly: hand-added trailing content that matches no
    sentinel is still inside the replaced span. That was true before this fix
    too; the sentinels close the case the pack itself creates.
    """
    rel = project_file.name
    if not pack_file.exists():
        report.merge_notes.append(f"{rel}: pack file missing — skipped")
        return
    if not project_file.exists():
        report.merge_notes.append(
            f"{rel}: absent in project — this is onboard.md's job, not sync's; skipped")
        return

    pack_text = pack_file.read_text(encoding="utf-8")
    proj_text = project_file.read_text(encoding="utf-8")

    # Which marker does THIS project actually use? Line-anchored (see _find_line).
    marker, pidx = None, -1
    for m in markers:
        idx = _find_line(proj_text, [m])
        if idx >= 0:
            marker, pidx = m, idx
            break
    if marker is None:
        report.merge_notes.append(
            f"{rel}: none of the known markers ({'; '.join(markers)}) found in the project copy — "
            f"cannot merge safely; flagged for manual attention")
        return

    # Pack side: take its content BELOW its own marker line (the project keeps
    # its own heading). Fall back to the whole pack file if it has no marker.
    pack_idx = next((i for i in (_find_line(pack_text, [m]) for m in markers) if i >= 0), -1)
    if pack_idx >= 0:
        line_end = pack_text.find("\n", pack_idx)
        pack_body = pack_text[line_end:] if line_end >= 0 else ""
    else:
        pack_body = "\n" + pack_text

    # Project tail: everything from the first preserve_after sentinel that
    # appears AFTER the marker stays exactly as the project wrote it.
    tail = ""
    tail_idx = len(proj_text)
    sidx = _find_line(proj_text, list(preserve_after or []), pidx + 1)
    if sidx >= 0:
        tail = proj_text[sidx:]
        tail_idx = sidx
        eol = proj_text.find("\n", sidx)
        name = proj_text[sidx:eol if eol >= 0 else len(proj_text)].strip().lstrip("# ").strip()
        report.merge_notes.append(
            f"{rel}: preserving the project's own '{name}' section below the pack content")

    # The project's current copy of the section, and the note line (if any)
    # onboarding wrote directly under the marker heading.
    marker_line_end = proj_text.find("\n", pidx)
    proj_body = proj_text[marker_line_end:tail_idx] if marker_line_end >= 0 else ""
    note = ""
    for line in proj_body.lstrip("\n").splitlines():
        if line.startswith(">"):
            note = line
        break

    # Heading-level adaptation: an appended H2 section demotes the pack's own
    # H2 subsections to H3 so they nest instead of becoming siblings of the
    # project's top-level sections. Onboarding does this; a refresh must too,
    # or every sync flattens the document (observed on oikonomos).
    demote = "#" * max(0, _heading_level(marker) - _heading_level_of_pack(pack_text, markers))
    if demote:
        pack_body = "\n".join(
            (demote + ln) if ln.startswith("#") else ln for ln in pack_body.splitlines())

    # LOCAL CUSTOMIZATION GUARD. marker_section had no conflict concept — it
    # simply overwrote — which is safe only while projects never edit inside
    # the section. oikonomos does, deliberately and valuably (a hard-won
    # "always run the FULL recursive suite" review rule with its incident
    # report, and a disambiguated protected-paths heading). Silently
    # discarding that is worse than never syncing.
    #
    # Conflict is judged against the BASELINE — the section as the pack had it
    # at the last successful merge, kept in sync_state under a synthetic
    # "<file>#section" key — exactly how decide_file() judges ordinary files.
    # Comparing against the CURRENT pack instead would call every legitimately
    # out-of-date project a conflict, which defeats the whole strategy.
    section_key = f"{rel}#section"
    baseline = (state or {}).get("files", {}).get(section_key)
    current = sha256_bytes(_normalize_section(proj_body).encode('utf-8'))
    if not adopt_pack:
        if baseline is None:
            if _normalize_section(proj_body) != _normalize_section(pack_body):
                report.merge_notes.append(
                    f"{rel}: section differs from the pack and this project has no section baseline "
                    f"(never marker-synced), so local edits cannot be told apart from pack drift — "
                    f"not touching it. Diff it against the pack's {rel}, then re-run with "
                    f"--adopt-pack once you are satisfied nothing project-specific is lost.")
                return
        elif current != baseline:
            report.merge_notes.append(
                f"{rel}: the project has LOCAL EDITS inside the pack section since the last sync — "
                f"not touching it. Reconcile by hand, or re-run with --adopt-pack to discard them.")
            return
    elif _normalize_section(proj_body) != _normalize_section(pack_body):
        report.merge_notes.append(f"{rel}: local edits inside the section DISCARDED (--adopt-pack)")

    # pack_body starts at the newline that ends the pack's marker line, so
    # splicing it straight after the project's marker preserves the pack's own
    # spacing (a blank line under the heading). Stripping it instead produced a
    # one-line diff on every sync — pure churn on a hot-path file.
    rest = pack_body[1:] if pack_body.startswith("\n") else pack_body
    merged = (proj_text[:pidx] + marker + "\n"
              + (note + "\n" if note else "")
              + rest.rstrip("\n") + "\n"
              + ("\n" + tail if tail else ""))
    # Record the section baseline on every clean pass — including the no-op
    # one, which is how an already-current project acquires a baseline without
    # ever needing --adopt-pack.
    new_baseline = sha256_bytes(_normalize_section(pack_body).encode("utf-8"))
    if merged == proj_text:
        if apply and state is not None:
            state.setdefault("files", {})[section_key] = new_baseline
        report.merge_notes.append(f"{rel}: marker section already current")
        return
    if apply:
        project_file.write_text(merged, encoding="utf-8", newline="")
        if state is not None:
            state.setdefault("files", {})[section_key] = new_baseline
        report.merge_notes.append(f"{rel}: marker section UPDATED from pack")
    else:
        report.merge_notes.append(f"{rel}: marker section would be updated (dry-run)")


def merge_add_only_keys(project_file: Path, pack_file: Path, apply: bool,
                        report: Report) -> None:
    """Recursively add keys present in the pack template but absent in the
    project's copy. NEVER changes an existing value — the project's tuning
    (interval, builders, control.mode, allowlists) is its own."""
    rel = project_file.name
    if not pack_file.exists() or not project_file.exists():
        report.merge_notes.append(f"{rel}: one side missing — skipped")
        return
    try:
        pack_cfg = json.loads(pack_file.read_text(encoding="utf-8"))
        proj_cfg = json.loads(project_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        report.merge_notes.append(f"{rel}: JSON parse failed ({exc}) — skipped, fix manually")
        return

    added: list[str] = []

    # Keys whose VALUES are project-specific even though the pack's copy has
    # them: the pack repo doubles as its own live project, so its autopilot.json
    # carries DEVDEPARTMENT's own settings, not template defaults. Propagating
    # git.base_branch bit live on 2026-08-15: the sync injected the pack's
    # "master" into main-based rwc-admin-portal, which would have fail-closed
    # every plan_commit there. Each project sets these itself (onboarding asks).
    PROJECT_OWNED_KEYS = {"git"}

    def add_missing(dst: dict, src: dict, prefix: str) -> None:
        for key, value in src.items():
            if prefix == "" and key in PROJECT_OWNED_KEYS:
                if key not in dst:
                    report.merge_notes.append(
                        f"{rel}: key '{key}' is project-owned — NOT copied from the pack; "
                        f"set it per this project's own layout (e.g. git.base_branch)")
                continue
            if key not in dst:
                dst[key] = value
                added.append(prefix + key)
            elif isinstance(value, dict) and isinstance(dst[key], dict):
                add_missing(dst[key], value, prefix + key + ".")

    add_missing(proj_cfg, pack_cfg, "")
    if not added:
        report.merge_notes.append(f"{rel}: no missing keys")
        return
    if apply:
        project_file.write_text(json.dumps(proj_cfg, indent=2) + "\n",
                                encoding="utf-8", newline="\n")
        report.merge_notes.append(f"{rel}: ADDED missing keys: {', '.join(added)}")
    else:
        report.merge_notes.append(f"{rel}: would add missing keys: {', '.join(added)} (dry-run)")


# --------------------------------------------------------------------------- #
#  The sync run
# --------------------------------------------------------------------------- #

def run_sync(pack: Path, project: Path, apply: bool = False,
             adopt_pack: bool = False, only: list[str] | None = None) -> Report:
    manifest = load_manifest(pack)
    state = load_state(project)
    report = Report()

    files = manifest.get("framework_owned", [])
    if only:
        only_set = set(only)
        files = [f for f in files if f in only_set]
        unknown = only_set - set(files)
        for rel in sorted(unknown):
            report.add(rel, MISSING_IN_PACK, "--only path not in manifest's framework_owned list")

    for rel in files:
        decision = decide_file(rel, pack, project, state, adopt_pack)
        report.decisions.append(decision)
        if decision.verdict in (UPDATE, ADD, CONFLICT_ADOPTED) and apply:
            src = pack / rel
            dst = project / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(src.read_bytes())  # byte-exact, always
            state.setdefault("files", {})[rel] = sha256_file(dst)
        elif decision.verdict == IN_SYNC and apply:
            # Refresh/establish the baseline for in-sync files too, so a
            # legacy project's FIRST successful sync leaves a complete
            # baseline behind.
            state.setdefault("files", {})[rel] = sha256_file(project / rel)

    # merge_special
    ms = manifest.get("merge_special", {})
    if not only:  # merge-specials run on full syncs only
        spec = ms.get("CLAUDE.md")
        if spec and spec.get("strategy") == "marker_section":
            # markers[] is the current shape; a bare marker (older manifests,
            # and projects syncing from a pack older than 2026-08-15) still works.
            markers = spec.get("markers") or [spec["marker"]]
            merge_marker_section(project / "CLAUDE.md", pack / "CLAUDE.md",
                                 markers, apply, report,
                                 preserve_after=spec.get("preserve_after"),
                                 adopt_pack=adopt_pack, state=state)
        spec = ms.get("autopilot.json")
        if spec and spec.get("strategy") == "add_only_keys":
            merge_add_only_keys(project / "autopilot.json", pack / "autopilot.json",
                                apply, report)
        for name, spec in ms.items():
            if spec.get("strategy") == "manual_only":
                target = project / name
                report.merge_notes.append(
                    f"{name}: manual-only — sync never edits this; review drift yourself")

    if apply:
        state["version"] = STATE_VERSION
        save_state(project, state)
    return report


def render_report(report: Report, apply: bool) -> str:
    lines = ["", f"DEVDEPARTMENT SYNC {'(APPLIED)' if apply else '(DRY-RUN — nothing written)'}",
             "=" * 46]
    for verdict, label in [(UPDATE, "Updated" if apply else "Would update"),
                           (ADD, "Added" if apply else "Would add"),
                           (CONFLICT_ADOPTED, "Conflicts resolved pack-ward (--adopt-pack)"),
                           (CONFLICT, "CONFLICTS (project copy locally modified — NOT touched)"),
                           (MISSING_IN_PACK, "Manifest/pack inconsistencies")]:
        items = report.by(verdict)
        if items:
            lines.append(f"{label}: {len(items)}")
            for d in items:
                suffix = f"  ({d.detail})" if d.detail else ""
                lines.append(f"  - {d.rel}{suffix}")
    lines.append(f"In sync: {len(report.by(IN_SYNC))} files")
    if report.merge_notes:
        lines.append("Merge-special:")
        for note in report.merge_notes:
            lines.append(f"  - {note}")
    if report.has_conflicts:
        lines += ["", "Conflicts mean the project's copy differs from the pack AND either",
                  "was locally modified since the last sync, or this project has never",
                  "synced (no baseline). Review each, then either re-run with",
                  "--adopt-pack to take the pack's version, or keep yours (the file",
                  "stays flagged until the hashes converge)."]
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Sync an onboarded project with the DEVDEPARTMENT pack")
    ap.add_argument("--pack", required=True, help="path to the DEVDEPARTMENT pack folder")
    ap.add_argument("--project", default=".", help="project root (default: cwd)")
    ap.add_argument("--apply", action="store_true",
                    help="actually write changes (default is dry-run)")
    ap.add_argument("--adopt-pack", action="store_true",
                    help="resolve conflicts by taking the pack's version")
    ap.add_argument("--only", nargs="*", default=None,
                    help="restrict to specific manifest paths")
    args = ap.parse_args(argv)

    pack = Path(args.pack).resolve()
    project = Path(args.project).resolve()
    if not pack.is_dir():
        print(f"error: pack not found at {pack}", file=sys.stderr)
        return 1
    if pack == project:
        print("error: --pack and --project are the same directory", file=sys.stderr)
        return 1

    try:
        report = run_sync(pack, project, apply=args.apply,
                          adopt_pack=args.adopt_pack, only=args.only)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(render_report(report, args.apply))
    return 2 if report.has_conflicts else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
