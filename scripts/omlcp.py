#!/usr/bin/env python3
"""omlcp — the DEVDEPARTMENT generation lane (Output-Maximizing Long-Context Programming).

One long, coherent generation for greenfield territory instead of a long agent loop;
deterministic materialization, verification and repair accounting around it.
Full guide: docs/OMLCP.md. Assessment of the paper against this pack: docs/OMLCP_ASSESSMENT.md.

    python scripts/omlcp.py classify TASK-051            # generate or iterate, with reasons
    python scripts/omlcp.py packet   TASK-051            # compile the packet, show its budget
    python scripts/omlcp.py stage    TASK-051            # dispatch-time: generate onto the task branch
    python scripts/omlcp.py run      TASK-051 --target ../wt-s5-proj
    python scripts/omlcp.py verify   TASK-051 --target ../wt-s5-proj
    python scripts/omlcp.py materialize TASK-051 --stream pasted.txt   # manual adapter
    python scripts/omlcp.py continue TASK-051            # print the continuation prompt
    python scripts/omlcp.py log-repair TASK-051 --input-tokens 40000 --output-tokens 3000
    python scripts/omlcp.py report                       # paper §6.4 metrics from the ledger
    python scripts/omlcp.py economics --lines 4000       # paper vs priced vs DEVDEPARTMENT model
    python scripts/omlcp.py brief-template > brief.md    # standalone mode, no PLAN.md needed

Every task-taking subcommand also accepts ``--brief FILE`` instead of a task ID.
Exit status: 0 success, 1 failure (incomplete run, failed verification, bad input).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import omlcp_economics as econ  # noqa: E402
from omlcp_generate import (GenerationError, append_ledger, make_adapter, materialize_run,  # noqa: E402
                            outcome_json, run_generation, utc_now)
from omlcp_packet import (Brief, build_packet, classify, continuation_prompt, judgment_model,  # noqa: E402
                          load_config)
from omlcp_stream import PathPolicy, RunState  # noqa: E402
from omlcp_verify import verify  # noqa: E402

BRIEF_TEMPLATE = """\
# <Feature name>

**Task:** FEAT-001

## Goal
<What this generation must deliver, in two to five sentences.>

## Specification
<The fixed requirements. Behaviour, inputs/outputs, error handling, edge cases.
Everything the generator needs and nothing it does not.>

## Approach
<Optional: the architecture you decided on — layering, data flow, key algorithms.>

## Acceptance
- <Testable criterion 1>
- <Testable criterion 2>

```omlcp-manifest
{
  "language": "python",
  "conventions": "PEP 8, type hints, logging via the stdlib logging module.",
  "files": [
    {"path": "src/feature/core.py", "purpose": "Core logic.", "exports": ["run"], "est_lines": 220},
    {"path": "tests/test_core.py", "purpose": "pytest suite for core.", "exports": [], "est_lines": 160}
  ],
  "context": [],
  "dependencies": {"python": ["pytest==8.3.3"]},
  "wiring": [],
  "tests": "python -m pytest -q tests/test_core.py"
}
```
"""


def find_repo_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "PLAN.md").is_file() or (candidate / "autopilot.json").is_file():
            return candidate
    return start


def _load(args) -> tuple[Path, Path, Brief, dict]:
    repo = Path(args.repo).resolve() if args.repo else find_repo_root(Path.cwd())
    target = Path(args.target).resolve() if getattr(args, "target", None) else repo
    if args.brief:
        brief = Brief.from_file(Path(args.brief).resolve(), context_root=target)
    elif args.task:
        brief = Brief.from_plan(repo, args.task, context_root=target)
    else:
        raise SystemExit("[omlcp] give a TASK-ID or --brief FILE")
    return repo, target, brief, load_config(repo)


def _run_dir(repo: Path, brief: Brief) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,80}", brief.task_id) or ".." in brief.task_id:
        raise ValueError(f"task id {brief.task_id!r} is not a safe directory name")
    return repo / ".devteam" / "omlcp" / brief.task_id


def _policy(brief: Brief) -> PathPolicy:
    return PathPolicy(manifest=brief.manifest.paths if brief.manifest else [],
                      owned=brief.owned, grants=brief.grants)


def _packet(brief: Brief, cfg: dict, run_dir: Path, fresh: bool = False):
    state_path = run_dir / "state.json"
    nonce = None
    if state_path.is_file() and not fresh:
        nonce = RunState.load(state_path).nonce
    pkt = build_packet(brief, cfg, nonce=nonce)
    pkt.write(run_dir)
    (run_dir / "packet.json").write_text(json.dumps({
        "task_id": brief.task_id, "nonce": pkt.nonce, "est_input_tokens": pkt.est_input_tokens,
        "est_output_tokens": pkt.est_output_tokens, "warnings": pkt.warnings, "built_at": utc_now(),
    }, indent=2) + "\n", encoding="utf-8")
    return pkt


def _print_warnings(items) -> None:
    for w in items:
        print(f"  WARN {w}")


# ---------------------------------------------------------------- commands --
def cmd_classify(args) -> int:
    repo, target, brief, cfg = _load(args)
    d = classify(brief, cfg, target)
    if args.json:
        print(json.dumps(d.to_dict() | {"task_id": brief.task_id, "warnings": brief.warnings}, indent=2))
        return 0
    print(f"[omlcp classify] {brief.task_id}: lane = {d.lane.upper()}")
    for b in d.blockers:
        print(f"  BLOCK {b}")
    for s in d.signals:
        print(f"  note  {s}")
    _print_warnings(brief.warnings)
    return 0


def cmd_packet(args) -> int:
    repo, target, brief, cfg = _load(args)
    run_dir = _run_dir(repo, brief)
    try:
        pkt = _packet(brief, cfg, run_dir, fresh=args.fresh)
    except ValueError as exc:
        print(f"[omlcp packet] ERROR {exc}", file=sys.stderr)
        return 1
    if args.print:
        print(pkt.system)
        print(pkt.prompt)
        return 0
    out = pkt.est_output_tokens
    print(f"[omlcp packet] {brief.task_id}: {run_dir / 'packet.md'}")
    print(f"  nonce {pkt.nonce} · input ~{pkt.est_input_tokens:,} tokens · output "
          + (f"~{out:,} tokens" if out else "unknown (add est_lines)"))
    _print_warnings(pkt.warnings)
    return 0


def _generate(args, repo: Path, target: Path, brief: Brief, cfg: dict, label: str):
    """Shared core of `run` and `stage`. Returns (outcome, report) or (None, None) after
    printing why it refused."""
    for key in ("adapter", "model", "effort"):
        if getattr(args, key, None):
            cfg[key] = getattr(args, key)
    if getattr(args, "max_continuations", None) is not None:
        cfg["max_continuations"] = args.max_continuations
    run_dir = _run_dir(repo, brief)
    fresh = getattr(args, "fresh", False)
    resuming = (run_dir / "state.json").is_file() and not fresh
    decision = classify(brief, cfg, target)
    if decision.lane != "generate" and not getattr(args, "force_lane", False) and not resuming:
        print(f"[omlcp {label}] {brief.task_id} classifies as ITERATE — not generating:", file=sys.stderr)
        for b in decision.blockers:
            print(f"  BLOCK {b}", file=sys.stderr)
        print("  (use the normal builder loop, fix the blockers, or pass --force-lane)", file=sys.stderr)
        return None, None
    reviewer = judgment_model(repo)
    if reviewer and cfg.get("model") == reviewer:
        print(f"[omlcp {label}] REFUSED: generator model {reviewer!r} is the reviewer model. "
              "Maker != checker (CLAUDE.md model discipline; Wave F rule N5).", file=sys.stderr)
        return None, None
    if fresh and run_dir.exists():
        shutil.rmtree(run_dir)
    try:
        pkt = _packet(brief, cfg, run_dir, fresh=fresh)
        adapter = make_adapter(cfg)
        outcome = run_generation(repo=repo, run_dir=run_dir, target=target, packet=pkt,
                                 policy=_policy(brief), cfg=cfg, adapter=adapter,
                                 unit=getattr(args, "unit", None))
    except (ValueError, GenerationError) as exc:
        print(f"[omlcp {label}] ERROR {exc}", file=sys.stderr)
        return None, None
    report = verify(target, brief.manifest.files, cfg.get("extra_stub_patterns"))
    (run_dir / "verify.json").write_text(json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8")
    return outcome, report


def _render_outcome(label: str, brief: Brief, outcome, report) -> None:
    s = outcome.summary()
    print(f"[omlcp {label}] {brief.task_id}: {'COMPLETE' if outcome.complete else 'INCOMPLETE'} "
          f"({outcome.stop}) · {s['segments_run']} segment(s) this run · "
          f"{len(s['written']) + len(s['unchanged'])}/{len(brief.manifest.files)} files on disk · "
          f"{s['output_tokens']:,} out / {s['input_tokens']:,} in tokens · ${s['cost_usd']}")
    for p in s["pending"]:
        print(f"  PENDING {p}")
    for p, why in s["rejected"].items():
        print(f"  REJECT  {p}: {'; '.join(why)}")
    for p, why in s["extraneous"].items():
        print(f"  IGNORED {p}: {'; '.join(why)}")
    _print_warnings(s["warnings"])
    for e in s["errors"]:
        print(f"  ERROR   {e}")
    print(report.render())


def cmd_run(args) -> int:
    repo, target, brief, cfg = _load(args)
    outcome, report = _generate(args, repo, target, brief, cfg, "run")
    if outcome is None:
        return 1
    if args.json:
        print(json.dumps(json.loads(outcome_json(outcome)) | {"verify": report.to_dict()}, indent=2))
    else:
        _render_outcome("run", brief, outcome, report)
        if outcome.complete and not report.ok:
            print("  NEXT: repair the findings agentically inside Owned_Paths, then "
                  f"`omlcp log-repair {brief.task_id} ...` and run the project's tests.")
    return 0 if outcome.complete and report.ok else 1


def _git(cwd: Path, *argv: str, check: bool = True) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", "-C", str(cwd), *argv], capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(argv)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc


def _base_branch(repo: Path) -> str:
    try:
        cfg = json.loads((repo / "autopilot.json").read_text(encoding="utf-8"))
        return (cfg.get("git") or {}).get("base_branch") or "main"
    except (OSError, json.JSONDecodeError):
        return "main"


def cmd_stage(args) -> int:
    """Hybrid execution (paper Fig. 2) wired into dispatch: generate on the task branch in a
    throwaway worktree, commit the generation as one reviewable commit, remove the
    worktree, and leave the branch for the assigned builder to claim, repair and hand off.
    dispatch.sh/.ps1 keep an existing task branch (they only pre-create missing ones)."""
    repo = Path(args.repo).resolve() if args.repo else find_repo_root(Path.cwd())
    brief = Brief.from_plan(repo, args.task)
    cfg = load_config(repo)
    unit = (args.unit or brief.assigned_to or "").strip()
    try:
        import builder_registry
        suffixes = builder_registry.branch_suffixes(repo)
    except Exception as exc:  # registry problems are reported, not guessed around
        print(f"[omlcp stage] ERROR cannot read the builder registry: {exc}", file=sys.stderr)
        return 1
    if unit not in suffixes:
        print(f"[omlcp stage] ERROR {args.task} is assigned to {unit or 'nobody'!r}; stage needs a "
              f"builder unit ({', '.join(sorted(suffixes))}) — pass --unit", file=sys.stderr)
        return 1
    branch = f"task/{args.task}-{suffixes[unit]}"
    base = _base_branch(repo)
    wt = Path(args.worktree).resolve() if args.worktree else repo.parent / f"wt-omlcp-{repo.name}-{args.task}"
    try:
        if wt.exists():
            listed = _git(repo, "worktree", "list", "--porcelain").stdout
            registered = {Path(line[len("worktree "):]).resolve()
                          for line in listed.splitlines() if line.startswith("worktree ")}
            if wt.resolve() not in registered:
                print(f"[omlcp stage] ERROR {wt} exists and is not a worktree of this repo", file=sys.stderr)
                return 1
        elif _git(repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}", check=False).returncode == 0:
            _git(repo, "worktree", "add", str(wt), branch)
        else:
            _git(repo, "worktree", "add", "-b", branch, str(wt), base)
        args.unit = unit
        outcome, report = _generate(args, repo, wt, brief, cfg, "stage")
        if outcome is None:
            return 1
        _render_outcome("stage", brief, outcome, report)
        if not outcome.complete:
            print(f"  Branch {branch} is still checked out in {wt}: re-run `omlcp stage {args.task}` to "
                  "continue, or fix the packet. The builder cannot switch to the branch until staging ends.")
            return 1
        paths = [p for p in brief.manifest.paths if (wt / p).is_file()]
        _git(wt, "add", "--", *paths)
        if _git(wt, "diff", "--cached", "--quiet", check=False).returncode != 0:
            msg = (f"feat(omlcp): generate {args.task} {brief.title} [{args.task}]\n\n"
                   f"Single-pass OMLCP generation ({cfg.get('model')}, effort {cfg.get('effort')}, "
                   f"{outcome.segments_run} segment(s) this run). Verify: "
                   f"{'PASS' if report.ok else f'{len(report.errors)} finding(s) for the builder to repair'}.\n\n"
                   f"Generated-By: scripts/omlcp.py stage")
            _git(wt, "commit", "-m", msg)
        sha = _git(wt, "rev-parse", "--short", "HEAD").stdout.strip()
        removed = _git(repo, "worktree", "remove", str(wt), check=False)
        if removed.returncode != 0:
            print(f"  WARN could not remove {wt}: {removed.stderr.strip()} — remove it before dispatch "
                  "(git refuses to check out a branch held by another worktree)")
        print(f"[omlcp stage] {args.task}: generation committed on {branch} at {sha}. "
              f"Dispatch {unit} as usual; it claims, switches to {branch}, repairs and hands off.")
        return 0
    except RuntimeError as exc:
        print(f"[omlcp stage] ERROR {exc}", file=sys.stderr)
        return 1


def cmd_materialize(args) -> int:
    repo, target, brief, cfg = _load(args)
    run_dir = _run_dir(repo, brief)
    state_path = run_dir / "state.json"
    if state_path.is_file():
        state = RunState.load(state_path)
    else:
        pkt_meta = run_dir / "packet.json"
        if not pkt_meta.is_file():
            print("[omlcp materialize] no packet yet: run `omlcp packet` first so the nonce is fixed",
                  file=sys.stderr)
            return 1
        state = RunState(task_id=brief.task_id, nonce=json.loads(pkt_meta.read_text())["nonce"],
                         target_root=str(target))
    if args.stream:
        name = f"segment-{len(state.segments)}.raw"
        shutil.copyfile(args.stream, run_dir / name)
        state.segments.append(name)
        state.save(state_path)
    if not state.segments:
        print("[omlcp materialize] nothing to materialize (no segments)", file=sys.stderr)
        return 1
    res = materialize_run(run_dir, state, _policy(brief), target, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
    else:
        print(f"[omlcp materialize] {brief.task_id}: {'COMPLETE' if res.complete else 'INCOMPLETE'}"
              f"{' (dry run)' if args.dry_run else ''} · written {len(res.written)} · "
              f"unchanged {len(res.unchanged)} · pending {len(res.pending)}")
        for p in res.written:
            print(f"  WROTE   {p}")
        for p in res.pending:
            print(f"  PENDING {p}" + ("  <- resume here" if p == res.resume_from else ""))
        for p, why in {**res.rejected, **res.extraneous}.items():
            print(f"  REJECT  {p}: {'; '.join(why)}")
        _print_warnings(res.warnings)
    return 0 if res.complete else 1


def cmd_continue(args) -> int:
    repo, target, brief, cfg = _load(args)
    run_dir = _run_dir(repo, brief)
    state_path = run_dir / "state.json"
    if not state_path.is_file():
        print("[omlcp continue] no run state for this task", file=sys.stderr)
        return 1
    state = RunState.load(state_path)
    complete = [p for p in brief.manifest.paths if p in state.written_hashes]
    contents = None
    if args.stateless:
        contents = {p: (target / p).read_text(encoding="utf-8") for p in complete if (target / p).is_file()}
    print(continuation_prompt(state.nonce, brief.manifest.paths, complete, state.resume_from, contents))
    return 0


def cmd_verify(args) -> int:
    repo, target, brief, cfg = _load(args)
    if not brief.manifest:
        print("[omlcp verify] no omlcp-manifest for this task", file=sys.stderr)
        return 1
    rep = verify(target, brief.manifest.files, cfg.get("extra_stub_patterns"))
    print(json.dumps(rep.to_dict(), indent=2) if args.json else rep.render())
    return 0 if rep.ok else 1


def cmd_log_repair(args) -> int:
    repo = Path(args.repo).resolve() if args.repo else find_repo_root(Path.cwd())
    cfg = load_config(repo)
    model = args.model or cfg.get("model")
    cost = args.cost
    if cost is None and model:
        try:
            cost = round(econ.usage_cost(model, args.input_tokens, args.output_tokens), 4)
        except KeyError:
            cost = None
    append_ledger(repo, {"ts": utc_now(), "role": "repair", "lane": "generate", "task_id": args.task,
                         "unit": args.unit, "model": model, "input_tokens": args.input_tokens,
                         "output_tokens": args.output_tokens, "cost_usd": cost, "note": args.note,
                         "exit": 0})
    print(f"[omlcp log-repair] {args.task}: {args.input_tokens:,} in / {args.output_tokens:,} out logged")
    return 0


def cmd_report(args) -> int:
    repo = Path(args.repo).resolve() if args.repo else find_repo_root(Path.cwd())
    tasks = econ.ledger_report(econ.read_ledger(repo))
    if args.json:
        print(json.dumps(tasks, indent=2))
        return 0
    if not tasks:
        print("[omlcp report] no generate-lane ledger lines yet (.devteam/ledger.jsonl)")
        return 0
    print(f"{'task':<16}{'segs':>5}{'cont':>5}{'gen in':>10}{'gen out':>10}{'repair':>9}"
          f"{'out share':>10}{'$ gen':>8}{'$ rep':>8}  model/effort")
    for tid, t in sorted(tasks.items()):
        rep_tok = t["repair_input"] + t["repair_output"]
        share = f"{t['output_share']:.0%}" if t["output_share"] is not None else "—"
        print(f"{tid:<16}{t['segments']:>5}{t['continuations']:>5}{t['gen_input']:>10,}"
              f"{t['gen_output']:>10,}{rep_tok:>9,}{share:>10}{t['gen_usd']:>8.2f}{t['repair_usd']:>8.2f}"
              f"  {t['model']}/{t['effort']}")
    return 0


def cmd_economics(args) -> int:
    repo = Path(args.repo).resolve() if args.repo else find_repo_root(Path.cwd())
    lines = args.lines
    tpl = args.tokens_per_line
    measured = econ.measure_repo(repo)
    overrides = {"lines": lines, "cache_hit": args.cache_hit}
    if tpl:
        overrides["tokens_per_line"] = tpl
    if args.builder_model:
        overrides["builder_model"] = args.builder_model
    if args.prefix_doc_tokens is not None:
        overrides["prefix_doc_tokens"] = args.prefix_doc_tokens
    pp = econ.params_from_repo(repo, **overrides)
    tpl_eff = pp.tokens_per_line
    paper_a = econ.paper_agentic_tokens(lines)
    paper_o = econ.paper_omlcp_tokens(lines)
    priced_paper_t = econ.priced_comparison(lines, args.model, 3.0, args.cache_hit)
    priced_real_t = econ.priced_comparison(lines, args.model, tpl_eff, args.cache_hit)
    dd = econ.devdept_compare(pp)
    result = {"lines": lines, "model": args.model, "prices_verified_at": econ.PRICES_VERIFIED_AT,
              "measured": measured, "paper": {"agentic": paper_a, "omlcp": paper_o,
                                              "ratio": paper_a["total"] / paper_o["total"]},
              "priced_paper_T3": priced_paper_t, "priced_measured_T": priced_real_t, "devdept": dd}
    if args.json:
        print(json.dumps(result, indent=2, default=float))
        return 0
    print(f"OMLCP economics for a {lines:,}-line artifact · prices verified {econ.PRICES_VERIFIED_AT}\n")
    print("1. Paper model, as published (eq. 2/4, T = 3 tokens/line)")
    print(f"   agentic {paper_a['total']:>12,.0f} tok   omlcp {paper_o['total']:>10,.0f} tok   "
          f"ratio {result['paper']['ratio']:.1f}x\n")
    print(f"2. Same flows priced on {args.model}, cache hit {args.cache_hit:.0%}")
    for label, r in (("T = 3 (paper)", priced_paper_t), (f"T = {tpl_eff:g} (measured)", priced_real_t)):
        print(f"   {label:<20} tokens {r['token_ratio']:>5.1f}x   dollars {r['usd_ratio']:>5.1f}x   "
              f"(${r['agentic_usd']:.2f} vs ${r['omlcp_usd']:.2f}; input is "
              f"{r['agentic_input_share_of_usd']:.0%} of agentic $)")
    print(f"\n3. DEVDEPARTMENT task model (builder {pp.builder_model}, review {pp.review_model})")
    print(f"   session prefix ~{dd['session_prefix_tokens']:,} tok re-sent per turn "
          f"(measured: {', '.join(pp.measured) or 'none'})")
    a, o = dd["agentic"], dd["omlcp"]
    print(f"   agentic lane  {a['total_tokens']:>13,.0f} tok  ${a['usd']:.2f}  (builder {a['builder_tokens']:,.0f}, "
          f"review {a['review_tokens']:,.0f})")
    print(f"   generate lane {o['total_tokens']:>13,.0f} tok  ${o['usd']:.2f}  (generation {o['generation_tokens']:,.0f} "
          f"in {o['segments']} seg, repair {o['repair_tokens']:,.0f}, review {o['review_tokens']:,.0f})")
    print(f"   ratio: tokens {dd['token_ratio']:.1f}x · dollars {dd['usd_ratio']:.1f}x · "
          f"builder-side tokens only {dd['builder_only_token_ratio']:.1f}x")
    print("\n   Assumption-driven rows are labelled in omlcp_economics.DevDeptParams; "
          "`omlcp report` replaces them with ledger data.")
    return 0


# -------------------------------------------------------------------- main --
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="omlcp", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def task_cmd(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.add_argument("task", nargs="?", help="TASK-ID in PLAN.md")
        p.add_argument("--brief", help="standalone generation brief (markdown) instead of a task")
        p.add_argument("--repo", help="repo holding PLAN.md/dossiers/specs/.devteam (default: auto)")
        p.add_argument("--target", help="where files are written/checked (default: repo; use the worktree)")
        p.add_argument("--json", action="store_true")
        p.set_defaults(fn=fn)
        return p

    task_cmd("classify", cmd_classify, "decide generate vs iterate lane, with reasons")
    p = task_cmd("packet", cmd_packet, "compile the generation packet and report its budget")
    p.add_argument("--print", action="store_true", help="print system + packet to stdout")
    p.add_argument("--fresh", action="store_true", help="new nonce even if a run state exists")
    p = task_cmd("run", cmd_run, "generate, materialize (with bounded continuation), verify")
    p.add_argument("--fresh", action="store_true", help="discard previous run state and start over")
    p.add_argument("--adapter", choices=["claude-cli", "anthropic-api", "command"])
    p.add_argument("--model")
    p.add_argument("--effort")
    p.add_argument("--max-continuations", type=int)
    p.add_argument("--unit", help="builder unit ID for the ledger (GB/CX/S5/...)")
    p.add_argument("--force-lane", action="store_true", help="generate even if classify says iterate")
    p = sub.add_parser("stage", help="generate on the task branch for the assigned builder (dispatch-time)")
    p.add_argument("task")
    p.add_argument("--unit", help="builder unit (default: the task's Assigned_To)")
    p.add_argument("--repo")
    p.add_argument("--worktree", help="staging worktree path (default: ../wt-omlcp-<project>-<task>)")
    p.add_argument("--fresh", action="store_true")
    p.add_argument("--adapter", choices=["claude-cli", "anthropic-api", "command"])
    p.add_argument("--model")
    p.add_argument("--effort")
    p.add_argument("--max-continuations", type=int)
    p.add_argument("--force-lane", action="store_true")
    p.set_defaults(fn=cmd_stage)
    p = task_cmd("materialize", cmd_materialize, "parse raw segments and write files (manual adapter)")
    p.add_argument("--stream", help="append this raw stream file as the next segment first")
    p.add_argument("--dry-run", action="store_true")
    p = task_cmd("continue", cmd_continue, "print the continuation prompt for the next segment")
    p.add_argument("--stateless", action="store_true", help="include completed files' contents")
    task_cmd("verify", cmd_verify, "manifest, syntax, contract and anti-stub checks")

    p = sub.add_parser("log-repair", help="record repair-session tokens (paper §6.4 item 6)")
    p.add_argument("task")
    p.add_argument("--input-tokens", type=int, required=True)
    p.add_argument("--output-tokens", type=int, required=True)
    p.add_argument("--cost", type=float)
    p.add_argument("--model")
    p.add_argument("--unit")
    p.add_argument("--note", default="")
    p.add_argument("--repo")
    p.set_defaults(fn=cmd_log_repair)

    p = sub.add_parser("report", help="per-task generate-lane metrics from the ledger")
    p.add_argument("--repo")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("economics", help="paper model vs priced vs DEVDEPARTMENT session model")
    p.add_argument("--lines", type=int, default=4000)
    p.add_argument("--model", default="claude-sonnet-5", help="model used for the priced comparison")
    p.add_argument("--builder-model")
    p.add_argument("--tokens-per-line", type=float, help="default: measured from the repo")
    p.add_argument("--cache-hit", type=float, default=0.9)
    p.add_argument("--prefix-doc-tokens", type=int,
                   help="override the measured CLAUDE.md+AGENTS.md+briefing+PLAN.md prefix "
                        "(e.g. to model PLAN.md after plan_archive.py)")
    p.add_argument("--repo")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_economics)

    p = sub.add_parser("brief-template", help="print a standalone generation brief template")
    p.set_defaults(fn=lambda a: (print(BRIEF_TEMPLATE), 0)[1])
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.fn(args) or 0)
    except (FileNotFoundError, KeyError, ValueError) as exc:
        print(f"[omlcp] ERROR {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
