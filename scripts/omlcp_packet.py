#!/usr/bin/env python3
"""OMLCP briefs, lane classification, generation packets and continuation prompts.

The OMLCP paper's workflow (§3.2) is: specification → architecture → ONE comprehensive
generation prompt → long-form generation → deterministic repair. In DEVDEPARTMENT the
first two phases already exist — `/devteam-decompose` writes the task block and the
dossier. This module adds the third: it compiles those into a *packet* with a hard
input budget (paper §3.1 "constrained input context"), instead of letting a builder
session re-read the whole PLAN.md blackboard (373 KB / ~93k tokens on this repo at
park time) plus briefings on every turn.

Two input shapes produce the same ``Brief``:

* **Pack mode** — ``Brief.from_plan(repo, "TASK-NNN")`` reads the PLAN.md task block,
  its dossier (which must carry an ``omlcp-manifest`` JSON block) and the referenced
  spec sections.
* **Standalone mode** — ``Brief.from_file(path)`` reads one markdown "generation brief"
  for small repos that will never install the full pack (field synthesis §0.10:
  LekkerSwot never installed it). See ``docs/OMLCP.md`` for the template.
"""

from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from omlcp_stream import MARKER, new_nonce  # noqa: E402

CHARS_PER_TOKEN = 3.5  # code-dense text; the README states this is an estimate
DEFAULTS = {
    "enabled": False,
    "adapter": "claude-cli",
    "cli": "claude",
    "model": "claude-sonnet-5",
    "effort": "medium",
    "max_output_tokens": 64000,
    "practical_ceiling_tokens": 60000,
    "max_continuations": 2,
    "tokens_per_line": 12,
    "input_warn_tokens": 30000,
    "input_max_tokens": 150000,
    "continuation_mode": "resume",
    "api_base": "https://api.anthropic.com",
    "api_version": "2023-06-01",
    "command": [],
    "extra_stub_patterns": [],
}

DISCOVERY_TERMS = re.compile(
    r"\b(investigate|debug|reproduce|root[- ]cause|profil(?:e|ing)|flaky|spike|explore|"
    r"bisect|diagnos(?:e|is)|figure out|find out why|regression hunt)\b", re.I)


def est_tokens(text: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN)


def load_config(repo: Path) -> dict:
    """``autopilot.json`` → ``omlcp``, overlaid by ``autopilot.local.json`` → ``omlcp``
    (the project's untracked override layer), over the code defaults."""
    cfg = dict(DEFAULTS)
    for name in ("autopilot.json", "autopilot.local.json"):
        p = repo / name
        if p.is_file():
            try:
                block = json.loads(p.read_text(encoding="utf-8")).get("omlcp") or {}
            except (json.JSONDecodeError, AttributeError) as exc:
                raise ValueError(f"{name}: cannot read omlcp config: {exc}") from exc
            cfg.update({k: v for k, v in block.items() if not k.startswith("_")})
    return cfg


def judgment_model(repo: Path) -> str | None:
    """The reviewer model, wherever this pack version keeps it (Wave F `models` or the
    legacy `judgment_model` key). Used to enforce maker != checker for the generator."""
    p = repo / "autopilot.json"
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    roles = (data.get("models") or {}).get("roles") or {}
    return (roles.get("judgment") or {}).get("model") or data.get("judgment_model")


# ------------------------------------------------------------------- manifest --
@dataclass
class ManifestFile:
    path: str
    purpose: str
    exports: list[str] = field(default_factory=list)
    est_lines: int | None = None


@dataclass
class Manifest:
    files: list[ManifestFile]
    context: list[str] = field(default_factory=list)
    dependencies: dict[str, list[str]] = field(default_factory=dict)
    conventions: str = ""
    wiring: list[str] = field(default_factory=list)
    tests: str = ""
    language: str = ""

    @property
    def paths(self) -> list[str]:
        return [f.path for f in self.files]

    @classmethod
    def parse(cls, raw: str) -> "Manifest":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"omlcp-manifest is not valid JSON: {exc}") from exc
        if not isinstance(data, dict) or not isinstance(data.get("files"), list) or not data["files"]:
            raise ValueError("omlcp-manifest needs a non-empty 'files' list")
        files, seen = [], set()
        for i, f in enumerate(data["files"]):
            if not isinstance(f, dict) or not f.get("path") or not f.get("purpose"):
                raise ValueError(f"omlcp-manifest files[{i}] needs 'path' and 'purpose'")
            if f["path"] in seen:
                raise ValueError(f"omlcp-manifest lists {f['path']!r} twice")
            seen.add(f["path"])
            exports = f.get("exports") or []
            if not isinstance(exports, list) or not all(isinstance(e, str) for e in exports):
                raise ValueError(f"omlcp-manifest files[{i}].exports must be a list of names")
            est = f.get("est_lines")
            if est is not None and (not isinstance(est, int) or est <= 0):
                raise ValueError(f"omlcp-manifest files[{i}].est_lines must be a positive integer")
            files.append(ManifestFile(f["path"], f["purpose"], exports, est))
        deps = data.get("dependencies") or {}
        if not isinstance(deps, dict):
            raise ValueError("omlcp-manifest 'dependencies' must map ecosystem -> [pins]")
        return cls(files=files, context=list(data.get("context") or []), dependencies=deps,
                   conventions=str(data.get("conventions") or ""),
                   wiring=list(data.get("wiring") or []), tests=str(data.get("tests") or ""),
                   language=str(data.get("language") or ""))


MANIFEST_BLOCK = re.compile(r"```omlcp-manifest\s*\n(.*?)\n```", re.S)


def extract_manifest(text: str) -> Manifest | None:
    m = MANIFEST_BLOCK.search(text)
    return Manifest.parse(m.group(1)) if m else None


# ---------------------------------------------------------------- spec excerpts --
SPEC_PATH = re.compile(r"(specs/[A-Za-z0-9_./-]+\.md)")
SECTION = re.compile(r"§\s*(\d+)(?:\s*[–-]\s*§?\s*(\d+))?")


def parse_spec_refs(raw: str) -> list[tuple[str, list[int]]]:
    """'specs/A.md §0–§3, §6 (A1); specs/B.md' -> [('specs/A.md',[0,1,2,3,6]), ('specs/B.md',[])]."""
    out: list[tuple[str, list[int]]] = []
    matches = list(SPEC_PATH.finditer(raw))
    for i, m in enumerate(matches):
        tail = raw[m.end(): matches[i + 1].start() if i + 1 < len(matches) else len(raw)]
        sections: list[int] = []
        for s in SECTION.finditer(tail):
            a, b = int(s.group(1)), int(s.group(2) or s.group(1))
            sections.extend(range(min(a, b), max(a, b) + 1))
        out.append((m.group(1), sorted(set(sections))))
    return out


HEADING = re.compile(r"^(#{1,6})\s+(.*)$")


def extract_sections(text: str, sections: list[int]) -> str:
    """Return the requested numbered sections (``## 3. Title`` or ``## §3 Title``), each
    up to the next heading of the same or higher level. No sections -> whole text."""
    if not sections:
        return text
    lines = text.splitlines()
    picked: list[str] = []
    i = 0
    while i < len(lines):
        h = HEADING.match(lines[i])
        if h:
            num = re.match(r"(?:§\s*)?(\d+)[.)\s]", h.group(2) + " ")
            if num and int(num.group(1)) in sections:
                level = len(h.group(1))
                j = i + 1
                while j < len(lines):
                    h2 = HEADING.match(lines[j])
                    if h2 and len(h2.group(1)) <= level:
                        break
                    j += 1
                picked.extend(lines[i:j])
                picked.append("")
                i = j
                continue
        i += 1
    return "\n".join(picked).strip() + "\n" if picked else ""


def _md_section(text: str, title: str) -> str:
    """Body of a ``## <title>`` section in a dossier, or ''."""
    m = re.search(rf"^##\s+{re.escape(title)}\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    return m.group(1).strip() if m else ""


# ----------------------------------------------------------------------- brief --
@dataclass
class Brief:
    task_id: str
    title: str
    description: str
    acceptance: list[str]
    manifest: Manifest | None
    spec_excerpts: list[tuple[str, str]] = field(default_factory=list)
    approach: str = ""
    owned: list[str] = field(default_factory=list)
    owned_new: list[str] = field(default_factory=list)
    grants: list[str] = field(default_factory=list)
    context_files: list[tuple[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    mode: str = "pack"
    lane_field: str = ""  # the task block's optional **Lane:** value, as ORCH set it
    assigned_to: str = ""

    # -- constructors -------------------------------------------------------
    @classmethod
    def from_plan(cls, repo: Path, task_id: str, context_root: Path | None = None) -> "Brief":
        from validate_plan import Report, parse_owned_paths, parse_tasks  # local: optional dep

        plan = repo / "PLAN.md"
        if not plan.is_file():
            raise FileNotFoundError(f"no PLAN.md in {repo}")
        tasks = {t.task_id: t for t in parse_tasks(plan.read_text(encoding="utf-8"), Report())}
        if task_id not in tasks:
            raise KeyError(f"{task_id} not found in PLAN.md")
        t = tasks[task_id]
        raw_owned = t.get("Owned_Paths")
        owned = parse_owned_paths(raw_owned)
        owned_new = [re.sub(r"\s+\(new\)$", "", p.strip(), flags=re.I)
                     for p in re.split(r"[,\n]", raw_owned) if re.search(r"\(new\)\s*$", p, re.I)]
        grants = [] if t.is_empty("Protected_Grants") else parse_owned_paths(t.get("Protected_Grants"))
        acceptance = [re.sub(r"^-\s*\[[ xX]\]\s*", "", ln.strip())
                      for ln in t.get("Acceptance_Criteria").splitlines() if ln.strip().startswith("-")]
        warnings: list[str] = []
        dossier_path = repo / "dossiers" / f"{task_id}.md"
        dossier = dossier_path.read_text(encoding="utf-8") if dossier_path.is_file() else ""
        if not dossier:
            warnings.append(f"no dossier at dossiers/{task_id}.md")
        manifest = None
        try:
            manifest = extract_manifest(dossier)
        except ValueError as exc:
            warnings.append(str(exc))
        excerpts: list[tuple[str, str]] = []
        for ref, sections in parse_spec_refs(t.get("Spec_References")):
            p = repo / ref
            if not p.is_file():
                warnings.append(f"spec reference {ref} does not exist")
                continue
            body = extract_sections(p.read_text(encoding="utf-8"), sections)
            if sections and not body:
                warnings.append(f"{ref}: none of sections {sections} found; whole spec included")
                body = p.read_text(encoding="utf-8")
            label = ref + (" §" + ",".join(map(str, sections)) if sections else "")
            excerpts.append((label, body))
        brief = cls(task_id=task_id, title=t.get("Title"), description=t.get("Description"),
                    acceptance=acceptance, manifest=manifest, spec_excerpts=excerpts,
                    approach=_md_section(dossier, "Intended approach"), owned=owned,
                    owned_new=owned_new, grants=grants, warnings=warnings, mode="pack",
                    lane_field="" if t.is_empty("Lane") else t.get("Lane").split()[0].lower(),
                    assigned_to=t.get("Assigned_To"))
        brief.load_context(context_root or repo)
        return brief

    @classmethod
    def from_file(cls, path: Path, context_root: Path | None = None) -> "Brief":
        text = path.read_text(encoding="utf-8")
        title_m = re.search(r"^#\s+(.+)$", text, re.M)
        id_m = re.search(r"^\*\*Task:\*\*\s*(\S+)", text, re.M)
        task_id = id_m.group(1) if id_m else re.sub(r"[^A-Za-z0-9-]+", "-", path.stem).upper()
        acceptance = [re.sub(r"^-\s*(\[[ xX]\]\s*)?", "", ln.strip())
                      for ln in _md_section(text, "Acceptance").splitlines() if ln.strip().startswith("-")]
        manifest = extract_manifest(text)
        spec = _md_section(text, "Specification")
        brief = cls(task_id=task_id, title=title_m.group(1).strip() if title_m else path.stem,
                    description=_md_section(text, "Goal"), acceptance=acceptance, manifest=manifest,
                    spec_excerpts=[(path.name, spec)] if spec else [],
                    approach=_md_section(text, "Approach"), mode="standalone")
        brief.load_context(context_root or path.parent)
        return brief

    def load_context(self, root: Path) -> None:
        if not self.manifest:
            return
        for rel in self.manifest.context:
            p = root / rel
            if p.is_file():
                self.context_files.append((rel, p.read_text(encoding="utf-8", errors="replace")))
            else:
                self.warnings.append(f"context file {rel} does not exist under {root}")

    # -- sizing ---------------------------------------------------------------
    def est_output_tokens(self, tokens_per_line: int) -> int | None:
        if not self.manifest or any(f.est_lines is None for f in self.manifest.files):
            return None
        return sum(f.est_lines or 0 for f in self.manifest.files) * tokens_per_line


# ------------------------------------------------------------------- classify --
@dataclass
class LaneDecision:
    lane: str  # "generate" | "iterate"
    blockers: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    segments_needed: int | None = None

    def to_dict(self) -> dict:
        return {"lane": self.lane, "blockers": self.blockers, "signals": self.signals,
                "segments_needed": self.segments_needed}


def classify(brief: Brief, cfg: dict, target_root: Path) -> LaneDecision:
    """Paper §8.4 decision heuristic, made mechanical for a DEVDEPARTMENT task:
    generate when scope is fixed, territory is new and the output fits the budget;
    iterate when requirements are open, files already exist, or progress depends on
    runtime diagnostics."""
    d = LaneDecision(lane="iterate")
    if brief.lane_field == "iterate":
        d.blockers.append("ORCH set **Lane:** iterate on this task")
    elif brief.lane_field == "generate":
        d.signals.append("ORCH set **Lane:** generate on this task")
    if brief.manifest is None:
        d.blockers.append("no omlcp-manifest block (the file layout and contracts are not fixed yet)")
    if not brief.acceptance:
        d.blockers.append("no acceptance criteria to generate against")
    if brief.mode == "pack" and not brief.spec_excerpts:
        d.blockers.append("no readable Spec_References")
    text = f"{brief.title}\n{brief.description}"
    hit = DISCOVERY_TERMS.search(text)
    if hit:
        d.blockers.append(f"discovery work ('{hit.group(0)}'): runtime diagnostics drive progress — iterate")
    if brief.manifest:
        from omlcp_stream import PathPolicy, glob_match

        policy = PathPolicy(manifest=brief.manifest.paths, owned=brief.owned, grants=brief.grants)
        for f in brief.manifest.files:
            for problem in policy.violations(f.path):
                if "not in the generation manifest" not in problem:
                    d.blockers.append(problem)
            if (target_root / f.path).exists():
                d.blockers.append(f"{f.path} already exists: the generate lane creates new files; "
                                  "editing existing code stays agentic (paper §11.9)")
            elif brief.mode == "pack" and not any(glob_match(f.path, g) for g in brief.owned_new):
                d.signals.append(f"{f.path} is new but not marked '(new)' in Owned_Paths")
        missing_exports = [f.path for f in brief.manifest.files if not f.exports]
        if missing_exports:
            d.signals.append("no declared exports (contract check will be skipped) for: "
                             + ", ".join(missing_exports))
        else:
            d.signals.append("every file declares its exports (contract-checked after generation)")
        if brief.manifest.dependencies:
            unpinned = [p for pins in brief.manifest.dependencies.values() for p in pins
                        if not re.search(r"(==|@|:)\s*\d", p)]
            if unpinned:
                d.signals.append("unpinned dependencies (paper §11.8 recommends verified pins): "
                                 + ", ".join(unpinned))
        if not brief.manifest.tests:
            d.signals.append("no test command in the manifest; verification will be manual")
        est = brief.est_output_tokens(int(cfg["tokens_per_line"]))
        if est is None:
            d.signals.append("est_lines missing on some files; output size unknown")
        else:
            ceiling = int(cfg["practical_ceiling_tokens"])
            d.segments_needed = max(1, math.ceil(est / ceiling))
            budget = 1 + int(cfg["max_continuations"])
            d.signals.append(f"estimated output ~{est:,} tokens = {d.segments_needed} segment(s) "
                             f"at a {ceiling:,}-token practical ceiling")
            if d.segments_needed > budget:
                d.blockers.append(f"estimated {d.segments_needed} segments exceeds the budget of "
                                  f"{budget}: split the task (paper §11.2)")
    if not d.blockers:
        d.lane = "generate"
    return d


# --------------------------------------------------------------------- packet --
GENERATION_CONTRACT = """\
You are the generation stage of an OMLCP (output-maximizing long-context programming)
pipeline. Planning is finished: the specification, file layout and interface contracts
below are fixed. Your job is to emit the COMPLETE implementation in one continuous
output stream that a program will parse and write to disk. No human reads this output
before it is parsed.

OUTPUT PROTOCOL (exact; a parser enforces it):
- For every file, in manifest order, emit a line `{M} {N} FILE <path>`, then the file's
  full content verbatim, then a line `{M} {N} END <path>`.
- Marker lines start at column 0. Do not indent them. Do not wrap file content in
  markdown code fences. Do not emit anything between files except optional
  `{M} {N} NOTE <one line>` lines.
- After the last file, emit `{M} {N} DONE` on its own line.
- Emit ONLY files listed in the manifest, at exactly the listed paths.

COMPLETENESS RULES (the parser and a verifier check these):
- Every file is complete and production-ready: error handling, input validation,
  logging where the stack uses it. Declared exports must exist with those names.
- Never write placeholders, stubs, "TODO", "rest of implementation", "similar to above",
  "omitted for brevity", or `...` in place of code. Never summarise instead of writing.
- Tests in the manifest are real tests that would fail if the behaviour were wrong.
- If you are about to run out of output space, finish the CURRENT file properly, emit
  its END marker, and stop without DONE. A continuation will resume at the next file.
  Never leave a file half-written to squeeze more in.

Think about structure before you emit, but spend your output on the files themselves.
"""


def _fence(text: str) -> str:
    longest = max((len(m) for m in re.findall(r"`{3,}", text)), default=2)
    f = "`" * max(3, longest + 1)
    return f"{f}\n{text.rstrip()}\n{f}"


@dataclass
class Packet:
    nonce: str
    system: str
    prompt: str
    est_input_tokens: int
    est_output_tokens: int | None
    warnings: list[str] = field(default_factory=list)

    def write(self, run_dir: Path) -> None:
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "system.md").write_text(self.system, encoding="utf-8", newline="\n")
        (run_dir / "packet.md").write_text(self.prompt, encoding="utf-8", newline="\n")


def build_packet(brief: Brief, cfg: dict, nonce: str | None = None) -> Packet:
    if brief.manifest is None:
        raise ValueError(f"{brief.task_id}: cannot build a packet without an omlcp-manifest block")
    nonce = nonce or new_nonce()
    system = GENERATION_CONTRACT.replace("{M}", MARKER).replace("{N}", nonce)
    m = brief.manifest
    parts: list[str] = [f"# Generation packet — {brief.task_id}: {brief.title}\n"]
    # Static-ish material first, task-specific last: maximises prompt-cache reuse.
    if m.conventions:
        parts.append(f"## Conventions\n\n{m.conventions.strip()}\n")
    for label, body in brief.spec_excerpts:
        parts.append(f"## Specification excerpt — {label}\n\n{body.strip()}\n")
    for rel, body in brief.context_files:
        parts.append(f"## Read-only context — {rel} (do NOT re-emit this file)\n\n{_fence(body)}\n")
    parts.append(f"## Task\n\n**{brief.title}**\n\n{brief.description.strip()}\n")
    if brief.approach:
        parts.append(f"## Intended approach (from the planner)\n\n{brief.approach.strip()}\n")
    if brief.acceptance:
        parts.append("## Acceptance criteria (each must be satisfied by the files)\n\n"
                     + "\n".join(f"- {a}" for a in brief.acceptance) + "\n")
    if m.dependencies:
        dep_lines = [f"- {eco}: " + ", ".join(pins) for eco, pins in m.dependencies.items()]
        parts.append("## Dependencies (use exactly these; add no others)\n\n" + "\n".join(dep_lines) + "\n")
    if m.wiring:
        parts.append("## Integration notes (outside this generation — do not emit these files)\n\n"
                     + "\n".join(f"- {w}" for w in m.wiring) + "\n")
    rows = []
    for i, f in enumerate(m.files, 1):
        exp = ", ".join(f"`{e}`" for e in f.exports) or "—"
        est = f"~{f.est_lines} lines" if f.est_lines else "size unspecified"
        rows.append(f"{i}. `{f.path}` — {f.purpose} Exports: {exp}. ({est})")
    parts.append("## Manifest — emit exactly these files, in this order\n\n" + "\n".join(rows) + "\n")
    if m.tests:
        parts.append(f"## How the result will be tested\n\n`{m.tests}`\n")
    parts.append(f"## Begin\n\nStart now with `{MARKER} {nonce} FILE {m.files[0].path}`.\n")
    prompt = "\n".join(parts)
    est_in = est_tokens(system) + est_tokens(prompt)
    warnings = list(brief.warnings)
    if est_in > int(cfg["input_warn_tokens"]):
        warnings.append(f"packet is ~{est_in:,} input tokens, above the {cfg['input_warn_tokens']:,} "
                        "constrained-input target (paper §3.1): trim spec excerpts or context files")
    if est_in > int(cfg["input_max_tokens"]):
        raise ValueError(f"packet is ~{est_in:,} input tokens, above input_max_tokens "
                         f"{cfg['input_max_tokens']:,}: split the task")
    return Packet(nonce, system, prompt, est_in, brief.est_output_tokens(int(cfg["tokens_per_line"])),
                  warnings)


def continuation_prompt(nonce: str, manifest_paths: list[str], complete: list[str],
                        resume_from: str | None, completed_contents: dict[str, str] | None = None) -> str:
    """Bounded continuation (paper §3.2 step 4): same trajectory, explicit structural
    boundary, no re-planning. ``completed_contents`` is only passed in stateless mode,
    where the model has not seen its own earlier output."""
    remaining = [p for p in manifest_paths if p not in complete]
    if resume_from in remaining:
        remaining.remove(resume_from)
        remaining.insert(0, resume_from)
    lines = [
        "Continue the same OMLCP output stream under the same protocol and rules.",
        "",
        "Files already complete — do NOT emit these again:",
        *(f"- {p}" for p in complete),
        "",
        "Emit these remaining files, in this order, each from its first line:",
        *(f"- {p}" for p in remaining),
        "",
        f"Start immediately with `{MARKER} {nonce} FILE {remaining[0]}`" if remaining else
        f"All files are complete. Emit only `{MARKER} {nonce} DONE`.",
        f"and end with `{MARKER} {nonce} DONE` once every remaining file is complete." if remaining else "",
    ]
    if completed_contents:
        lines += ["", "For reference (read-only, already written to disk):", ""]
        for p in complete:
            if p in completed_contents:
                lines += [f"### {p}", _fence(completed_contents[p]), ""]
    return "\n".join(lines).rstrip() + "\n"
