"""CLI dispatch smoke checks, with isolated fixture projects and stub CLIs."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
from builder_registry import load_registry  # noqa: E402


class SmokeFailure(RuntimeError):
    pass


class SmokeUnavailable(SmokeFailure):
    """The shell smoke cannot run because its interpreter is absent."""


PREFLIGHT_UNAVAILABLE = 77


@dataclass
class SmokeResult:
    unit: str
    version: str
    output_file: Path
    dispatch_output: str
    dispatch_argv: tuple[str, ...] = ()


def shell_files_with_cr(root: Path) -> list[Path]:
    """Return shell scripts containing CR bytes, excluding VCS/venv trees."""
    ignored = {".git", ".venv", "venv", "node_modules"}
    return [path for path in root.rglob("*.sh")
            if not any(part in ignored for part in path.relative_to(root).parts)
            and b"\r" in path.read_bytes()]


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _write_stub(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _bash() -> str:
    """Use Git Bash on Windows, where bare `bash` commonly resolves to WSL."""
    if os.name == "nt":
        for candidate in (Path("C:/Program Files/Git/bin/bash.exe"),
                          Path("C:/Program Files/Git/usr/bin/bash.exe")):
            if candidate.is_file():
                return str(candidate)
        raise SmokeUnavailable("Git Bash is required for the Windows shell dispatcher")
    bash = shutil.which("bash")
    if not bash:
        raise SmokeUnavailable("Bash is required for the shell dispatcher")
    return bash


def _run_bounded(command: list[str], *, cwd: Path, env: dict[str, str],
                 timeout: float) -> subprocess.CompletedProcess:
    """Run a dispatch in its own process group and clean up only that group.

    A legacy dispatch backgrounds the builder before waiting on it.  Killing a
    broad ``*codex*`` match after a timeout can therefore kill the smoke runner
    itself; retain the exact launcher PID and terminate only its descendants.
    """
    kwargs: dict[str, object] = {
        "cwd": cwd, "env": env, "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE, "stderr": subprocess.PIPE, "text": True,
    }
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(command, **kwargs)
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=False)
        else:
            import signal
            os.killpg(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate()
        raise SmokeFailure(
            f"dispatch timed out after {timeout}s; stopped only launcher process group PID {process.pid}: "
            f"{stdout}\n{stderr}"
        )
    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)


def version_command(cli: str, fakebin: Path | None) -> list[str]:
    """Return a Windows-safe command that asks a builder CLI for its version.

    Do not wrap a real Windows CLI in ``bash -c``: WSL's bash.exe loses the
    positional argument used by the old probe, leaving ``$1`` empty.  Fixture
    CLIs are deliberately shell scripts, so invoke those explicitly with bash.
    """
    if fakebin is not None:
        return [_bash(), str(fakebin / cli), "--version"]
    if os.name == "nt":
        windows_command = shutil.which(f"{cli}.cmd")
        if windows_command:
            return [windows_command, "--version"]
    return [cli, "--version"]


def create_fixture_project(parent: Path, unit: str = "CX", cli: str = "codex",
                           *, stub: bool = True,
                           source_entry: dict | None = None) -> tuple[Path, Path | None, Path]:
    """Build a disposable master-based project that exercises real dispatch argv."""
    suffix = unit.lower()
    root = parent / f"fixture-{unit}"
    (root / "scripts").mkdir(parents=True)
    (root / "briefings").mkdir()
    (root / "smoke-output").mkdir()
    for name in ("dispatch.sh", "validate_plan.py", "instincts.py", "builder_registry.py"):
        shutil.copyfile(SCRIPTS / name, root / "scripts" / name)
    (root / "scripts" / "dispatch.sh").chmod(0o755)
    _write_stub(root / "scripts" / "plan_commit.sh", "#!/usr/bin/env bash\nexit 0\n")
    entry = {
        "cli": cli, "model": None, "auth": {"mode": "default"},
        "worktree_suffix": suffix, "branch_suffix": suffix,
        "briefing": f"briefings/{unit}.md", "auto_loads_ambient_context": False,
    }
    if source_entry is not None:
        entry.update(source_entry)
        entry["cli"] = cli
        entry["briefing"] = f"briefings/{unit}.md"
    (root / "autopilot.json").write_text(json.dumps({
        "control": {"mode": "legacy"}, "git": {"base_branch": "master"},
        "atlas": {"enabled": False},
        "builders": {"active": [unit], "defined": {unit: entry}},
    }, indent=2), encoding="utf-8")
    relative_output = f"smoke-output/{unit}/dispatch-smoke.txt"
    (root / entry["briefing"]).write_text(
        f"Create `{relative_output}` with the CLI version, then exit.\n", encoding="utf-8")
    (root / "AGENTS.md").write_text(
        f"This is a disposable smoke fixture. Create `{relative_output}` with the CLI version "
        "and exit without asking for input. The fixture plan_commit helper intentionally "
        "succeeds without publishing coordination state.\n",
        encoding="utf-8",
    )
    (root / "PLAN.md").write_text(f"""---
plan_version: 1.0
last_updated: 2026-09-29T00:00:00Z
overall_status: in_progress
---
### TASK-001
**Title:** CLI smoke fixture
**Status:** pending
**Assigned_To:** {unit}
**Priority:** high
**Spec_References:** specs/smoke.md
**Owned_Paths:** smoke-output/**
**Depends_On:** —
**Description:** Create `{relative_output}` containing the CLI version, then exit without asking for input.
**Acceptance_Criteria:**
- [ ] The smoke file is written
**Branch:** —
**Started_At:** —
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** ORCH
**Updated_At:** 2026-09-29T00:00:00Z
""", encoding="utf-8", newline="\n")
    _git(root, "init", "-q", "-b", "master")
    _git(root, "config", "user.email", "smoke@example.com")
    _git(root, "config", "user.name", "Smoke")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed smoke fixture")
    worktree = parent / f"wt-{suffix}-{root.name}"
    output = worktree / relative_output
    fakebin = None
    if stub:
        fakebin = parent / f"bin-{unit}"
        fakebin.mkdir()
        fake_cli = fakebin / cli
        _write_stub(fake_cli, """#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then echo "stub-""" + cli + """ 1.0"; exit 0; fi
if [[ " $* " == *" --max-turns "* ]]; then echo "accepted --max-turns"; exit 0; fi
mkdir -p "$(dirname "$DEVTEAM_SMOKE_TARGET")"
if [[ -n "${DEVTEAM_SMOKE_ARGV:-}" ]]; then printf '%s\\0' "$@" > "$DEVTEAM_SMOKE_ARGV"; fi
""" + ('''if [[ " $* " == *" -s read-only "* ]]; then exit 0; fi
''' if cli == "codex" else "") + """
if [[ "${SMOKE_DENY_WRITE:-0}" == "1" ]]; then echo "write denied" >&2; exit 0; fi
if [[ -t 0 ]]; then echo "unexpected TTY" >&2; exit 9; fi
printf 'stub-""" + cli + """ 1.0\\n' > "$DEVTEAM_SMOKE_TARGET"
echo "stub-""" + cli + """ 1.0"
""")
    return root, fakebin, output


def run_dispatch_smoke(root: Path, unit: str, cli: str, fakebin: Path | None,
                       output_file: Path, *, deny_write: bool = False,
                       artifact_timeout: float = 90) -> SmokeResult:
    env = dict(os.environ)
    entry = load_registry(root)["defined"][unit]
    auth = entry.get("auth") or {}
    if auth.get("mode") == "config_dir":
        env["CLAUDE_CONFIG_DIR"] = str(Path(auth["value"]).expanduser())
    if fakebin:
        env["PATH"] = f"{fakebin}{os.pathsep}{env.get('PATH', '')}"
    argv_file = output_file.with_suffix(".argv")
    env["DEVTEAM_SMOKE_ARGV"] = str(argv_file)
    env["DEVTEAM_PREFLIGHT_ACTIVE"] = "1"
    version = subprocess.run(version_command(cli, fakebin), env=env,
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=10)
    if version.returncode != 0 or not version.stdout.strip():
        raise SmokeFailure(f"{unit}: CLI version probe failed: {version.stderr.strip()}")
    if cli == "claude":
        flag_probe = subprocess.run(
            [_bash(), "-c", 'exec "$1" -p "smoke" --max-turns 1 --dangerously-skip-permissions', "smoke", cli],
            env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
        if flag_probe.returncode != 0:
            raise SmokeFailure(f"{unit}: CLI rejected hidden --max-turns flag: {flag_probe.stderr.strip()}")
    env["DEVTEAM_SMOKE_TARGET"] = str(output_file)
    env["SMOKE_DENY_WRITE"] = "1" if deny_write else "0"
    result = _run_bounded([_bash(), "scripts/dispatch.sh", unit], cwd=root, env=env, timeout=45)
    if result.returncode != 0:
        raise SmokeFailure(f"{unit}: dispatch exited {result.returncode}: {result.stdout}\n{result.stderr}")
    deadline = time.monotonic() + artifact_timeout
    while not output_file.is_file() and time.monotonic() < deadline:
        # Legacy dispatch deliberately backgrounds the builder.  Wait for its
        # required artifact instead of mistaking a successful launcher exit
        # for a completed CLI session.
        time.sleep(0.25)
    if not output_file.is_file():
        raise SmokeFailure(
            f"{unit}: CLI exited 0 but did not write its Owned_Paths smoke file:\\n"
            f"{result.stdout}\\n{result.stderr}"
        )
    argv_parts = argv_file.read_bytes().split(bytes([0])) if argv_file.is_file() else []
    if argv_parts and argv_parts[-1] == b"":
        argv_parts.pop()
    dispatch_argv = (cli, *(part.decode("utf-8") for part in argv_parts))
    return SmokeResult(unit, version.stdout.strip(), output_file, result.stdout + result.stderr,
                       dispatch_argv)


def run_fixture_smoke(parent: Path, unit: str = "CX", cli: str = "codex",
                      *, deny_write: bool = False, stub: bool = True,
                      source_entry: dict | None = None) -> SmokeResult:
    root, fakebin, output_file = create_fixture_project(
        parent, unit, cli, stub=stub, source_entry=source_entry)
    return run_dispatch_smoke(root, unit, cli, fakebin, output_file, deny_write=deny_write)


def run_live_smoke(repo: Path, units: list[str] | None = None) -> list[SmokeResult]:
    """Run selected active CLIs; default to the entire active registry."""
    cr_files = shell_files_with_cr(repo)
    if cr_files:
        raise SmokeFailure("CR byte in shell script(s): " + ", ".join(str(p) for p in cr_files))
    registry = load_registry(repo)
    active = registry["active"]
    selected = set(active if units is None else units)
    unknown = selected.difference(active)
    if unknown:
        raise SmokeFailure("smoke unit(s) are not active: " + ", ".join(sorted(unknown)))
    results: list[SmokeResult] = []
    for unit in active:
        if unit not in selected:
            print(f"{unit}: deferred (not selected)")
            continue
        entry = registry["defined"][unit]
        cli = entry["cli"]
        with tempfile.TemporaryDirectory(prefix=f"devteam-smoke-{unit}-") as temp:
            result = run_fixture_smoke(Path(temp), unit, cli, stub=False,
                                       source_entry=entry)
            results.append(result)
            print(f"{unit}: exit=0 version={result.version} file={result.output_file}")
    return results


def run_registry_preflight(repo: Path, units: list[str] | None = None) -> list[SmokeResult]:
    """Run a bounded stub CLI smoke for the selected registry entries.

    Dispatch calls this before launch. The fixture exercises the exact
    registry-derived argv and must be able to write under the worktree.
    """
    _bash()  # Report an unavailable smoke distinctly, before creating fixtures.
    registry = load_registry(repo)
    selected = registry["active"] if units is None else units
    unknown = set(selected).difference(registry["defined"])
    if unknown:
        raise SmokeFailure("smoke unit(s) are not defined: " + ", ".join(sorted(unknown)))
    results = []
    for unit in selected:
        entry = registry["defined"][unit]
        with tempfile.TemporaryDirectory(prefix=f"devteam-preflight-{unit}-") as temp:
            result = run_fixture_smoke(Path(temp), unit, entry["cli"],
                                       source_entry=entry)
            results.append(result)
            print(f"{unit}: fixture argv smoke passed ({' '.join(result.dispatch_argv[:4])})")
    return results


def test_fixture_dispatch_smoke_records_version_and_writes_owned_file(tmp_path):
    result = run_fixture_smoke(tmp_path)
    assert result.version == "stub-codex 1.0"
    assert result.output_file.read_text(encoding="utf-8").strip() == "stub-codex 1.0"
    assert "Creating worktree" in result.dispatch_output
    # This is the old, hard-coded codex argv prefix. Omitting the new registry
    # field must be byte-identical; only an explicitly configured entry can
    # change the sandbox argument.
    assert result.dispatch_argv[:4] == ("codex", "exec", "-s", "danger-full-access")
    assert len(result.dispatch_argv) == 5  # prompt remains one unchanged argv item


def test_codex_default_registry_dry_run_keeps_the_legacy_argv(tmp_path):
    root, fakebin, _ = create_fixture_project(tmp_path)
    env = dict(os.environ)
    env["PATH"] = f"{fakebin}{os.pathsep}{env.get('PATH', '')}"
    env["DEVTEAM_PREFLIGHT_ACTIVE"] = "1"

    result = _run_bounded([_bash(), "scripts/dispatch.sh", "CX", "--dry-run"],
                          cwd=root, env=env, timeout=45)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "codex exec -s danger-full-access" in result.stdout


def test_dispatch_runs_registry_preflight_before_a_real_launch(tmp_path):
    root, fakebin, _ = create_fixture_project(tmp_path)
    (root / "tests").mkdir()
    shutil.copyfile(Path(__file__).resolve(), root / "tests" / "test_harness_smoke.py")
    env = dict(os.environ)
    env["PATH"] = f"{fakebin}{os.pathsep}{env.get('PATH', '')}"
    env.pop("DEVTEAM_PREFLIGHT_ACTIVE", None)

    result = _run_bounded([_bash(), "scripts/dispatch.sh", "CX", "--dry-run"],
                          cwd=root, env=env, timeout=60)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Running registry fixture preflight for CX" in result.stdout
    assert "CX: fixture argv smoke passed (codex exec -s danger-full-access)" in result.stdout
    assert "DRY RUN" in result.stdout


def test_read_only_codex_registry_fixture_fails_the_write_smoke(tmp_path):
    fixture = ROOT / "tests" / "fixtures" / "smoke" / "codex-read-only-registry.json"
    source = json.loads(fixture.read_text(encoding="utf-8"))["builders"]["defined"]["CX"]
    root, fakebin, output_file = create_fixture_project(tmp_path, source_entry=source)

    with pytest.raises(SmokeFailure, match="CLI exited 0 but did not write its Owned_Paths smoke file"):
        run_dispatch_smoke(root, "CX", "codex", fakebin, output_file,
                           artifact_timeout=0)

    assert not output_file.exists()


def test_builder_start_failure_is_not_reported_as_dispatch_success(tmp_path):
    root, fakebin, output_file = create_fixture_project(tmp_path)
    _write_stub(fakebin / "codex", '#!/usr/bin/env bash\n'
                'if [[ "${1:-}" == "--version" ]]; then echo "stub-codex 1.0"; exit 0; fi\n'
                'echo "builder failed to start" >&2\nexit 73\n')
    with pytest.raises(SmokeFailure, match=r"(?s)dispatch exited 73:.*builder failed to start"):
        run_dispatch_smoke(root, "CX", "codex", fakebin, output_file,
                           artifact_timeout=0)
    assert not output_file.exists()


def test_codex_registry_change_is_covered_by_ci_harness_suite():
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    audit = (ROOT / "scripts" / "harness-audit.sh").read_text(encoding="utf-8")
    assert "on:\n  push:\n  pull_request:" in workflow.replace("\r", "")
    assert "harness-audit" in workflow and "tests/test_harness_smoke.py" in audit
    assert "python3 -m pytest tests/ -q" in audit


def test_preflight_command_runs_a_registry_fixture_smoke(tmp_path, monkeypatch, capsys):
    root, _, _ = create_fixture_project(tmp_path)
    calls = []

    def fake_smoke(parent, unit, cli, *, source_entry=None, **kwargs):
        calls.append((unit, cli, source_entry))
        return SmokeResult(unit, "stub-codex 1.0", parent / "smoke.txt", "",
                           (cli, "exec", "-s", "danger-full-access"))

    monkeypatch.setattr(sys.modules[__name__], "run_fixture_smoke", fake_smoke)
    monkeypatch.setattr(sys.modules[__name__], "_bash", lambda: "bash")

    run_registry_preflight(root, ["CX"])

    assert calls[0][0:2] == ("CX", "codex")
    assert calls[0][2]["codex_sandbox"] == "danger-full-access"
    assert "CX: fixture argv smoke passed (codex exec -s danger-full-access)" in capsys.readouterr().out


def test_preflight_missing_bash_is_distinct_from_a_failed_write(tmp_path, monkeypatch, capsys):
    # Exercise actual interpreter discovery without removing an installation.
    monkeypatch.setattr(Path, "is_file", lambda _: False)
    monkeypatch.setattr(shutil, "which", lambda _: None)
    assert main(["--preflight", "--repo", str(tmp_path)]) == PREFLIGHT_UNAVAILABLE
    captured = capsys.readouterr()
    assert "smoke unavailable:" in captured.out and "Bash is required" in captured.out
    assert not captured.err
    assert not list(tmp_path.iterdir())


def test_preflight_failed_write_is_a_failure(monkeypatch, capsys):
    def failed_write(*args):
        raise SmokeFailure("CLI exited 0 but did not write its Owned_Paths smoke file")

    monkeypatch.setattr(sys.modules[__name__], "run_registry_preflight", failed_write)
    assert main(["--preflight"]) == 1
    assert "did not write its Owned_Paths smoke file" in capsys.readouterr().err


@pytest.mark.parametrize("preflight_exit", [0, PREFLIGHT_UNAVAILABLE, 1])
def test_powershell_preflight_warns_only_when_unavailable(tmp_path, preflight_exit):
    powershell = shutil.which("powershell") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell unavailable")
    # Execute the actual production preflight block in isolation: no real
    # builder, credentials, or unrelated worktree setup is needed to test
    # whether it permits launch and restores the recursion guard.
    dispatch = (SCRIPTS / "dispatch.ps1").read_text(encoding="utf-8")
    block = dispatch.split('$PreflightScript = Join-Path', 1)[1]
    block = '$PreflightScript = Join-Path' + block.split('Write-Host "[dispatch] Launching', 1)[0]
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_harness_smoke.py").write_text(
        f"raise SystemExit({preflight_exit})\n", encoding="utf-8")
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    script = ("$ErrorActionPreference = 'Stop'\n"
              f"$RepoRoot = {quote(tmp_path)}\n$Py = {quote(sys.executable)}\n"
              "$Id = 'CX'\n$env:DEVTEAM_PREFLIGHT_ACTIVE = 'prior'\n" + block +
              '\nWrite-Host "LAUNCH_ALLOWED guard=$env:DEVTEAM_PREFLIGHT_ACTIVE"\nexit 0\n')
    result = subprocess.run([powershell, "-NoProfile", "-NonInteractive",
                             "-Command", script], capture_output=True, text=True, timeout=20)
    output = result.stdout + result.stderr
    if preflight_exit == 1:
        assert result.returncode != 0
        assert "refusing to launch" in output
        assert "LAUNCH_ALLOWED" not in result.stdout
    else:
        assert result.returncode == 0, output
        assert "LAUNCH_ALLOWED guard=prior" in output
        assert ("continuing native PowerShell dispatch" in output) == (preflight_exit == PREFLIGHT_UNAVAILABLE)


def test_control_prompt_blocked_reason_format_matches_in_both_dispatchers():
    for name in ("dispatch.sh", "dispatch.ps1"):
        dispatch = (SCRIPTS / name).read_text(encoding="utf-8")
        assert "blocked_reason must use CATEGORY: detail; ': detail' is required and non-empty" in dispatch
        assert "TOOLING_FAILURE, CAPACITY, or OTHER" in dispatch
        assert "blocked_reason must start with" not in dispatch


def test_codex_stub_exiting_without_worktree_write_fails_smoke(tmp_path):
    root, fakebin, output_file = create_fixture_project(tmp_path)
    with pytest.raises(SmokeFailure, match="did not write its Owned_Paths smoke file"):
        run_dispatch_smoke(root, "CX", "codex", fakebin, output_file,
                           deny_write=True, artifact_timeout=1)


def test_claude_fixture_accepts_hidden_max_turns_flag(tmp_path):
    result = run_fixture_smoke(tmp_path, unit="S5", cli="claude")
    assert result.version == "stub-claude 1.0"
    assert result.output_file.is_file()


def test_crlf_dispatch_shell_is_rejected(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "dispatch.sh").write_bytes(b"#!/bin/sh\r\necho broken\r\n")
    assert shell_files_with_cr(tmp_path) == [tmp_path / "scripts" / "dispatch.sh"]


def test_live_entrypoint_rejects_crlf_dispatch_before_launch(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "dispatch.sh").write_bytes(b"#!/bin/sh\r\necho broken\r\n")
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--live", "--repo", str(tmp_path)],
        capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert "CR byte" in result.stderr
    assert "dispatch.sh" in result.stderr


def test_live_selection_defers_others_and_copies_registry_entry(tmp_path, monkeypatch, capsys):
    registry = {"active": ["GB", "CX"], "defined": {
        "GB": {"cli": "grok"},
        "CX": {"cli": "codex", "model": "pinned-model", "auth": {"mode": "config_dir", "value": "~/auth"}},
    }}
    monkeypatch.setattr(sys.modules[__name__], "load_registry", lambda _: registry)
    calls = []

    def fake_run(parent, unit, cli, **kwargs):
        calls.append((unit, cli, kwargs["source_entry"]))
        return SmokeResult(unit, "v1", parent / "smoke.txt", "")

    monkeypatch.setattr(sys.modules[__name__], "run_fixture_smoke", fake_run)
    run_live_smoke(tmp_path, ["CX"])
    assert calls == [("CX", "codex", registry["defined"]["CX"])]
    assert "GB: deferred" in capsys.readouterr().out


def test_fixture_preserves_live_model_and_auth(tmp_path):
    source = {"model": "pinned-model", "auth": {"mode": "config_dir", "value": "~/auth"}}
    root, _, _ = create_fixture_project(tmp_path, source_entry=source)
    entry = json.loads((root / "autopilot.json").read_text(encoding="utf-8"))["builders"]["defined"]["CX"]
    assert entry["model"] == "pinned-model"
    assert entry["auth"] == source["auth"]


def test_fixture_keeps_prompt_away_from_windows_leading_slash_path_conversion():
    dispatch = (SCRIPTS / "dispatch.sh").read_text(encoding="utf-8")
    assert 'PROMPT="${IDENTITY_OVERRIDE}You are' in dispatch
    assert "codex exec" in dispatch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="run selected active real builder CLIs")
    parser.add_argument("--preflight", action="store_true",
                        help="run a stub CLI smoke against the selected registry entries")
    parser.add_argument("--repo", default=str(ROOT))
    parser.add_argument("--units", default=os.environ.get("DEVTEAM_SMOKE_UNITS"),
                        help="comma-separated active units; default: every active unit")
    args = parser.parse_args(argv)
    selected = [unit.strip() for unit in args.units.split(",") if unit.strip()] if args.units else None
    try:
        if args.preflight:
            run_registry_preflight(Path(args.repo).resolve(), selected)
        elif args.live:
            run_live_smoke(Path(args.repo).resolve(), selected)
        else:
            parser.error("pass --live for real CLIs, --preflight for registry fixture coverage, or run pytest")
    except SmokeUnavailable as exc:
        # stdout avoids PowerShell 5.1 treating native stderr as a terminating
        # error before dispatch can inspect the distinct unavailable exit code.
        print(f"smoke unavailable: {exc}")
        return PREFLIGHT_UNAVAILABLE
    except SmokeFailure as exc:
        print(f"smoke failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
