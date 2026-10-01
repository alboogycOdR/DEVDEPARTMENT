# TASK-046 — CI matrix portability

## Implementation

- Resolve native Git as `git.exe` on Windows and `git` on Unix when copying PLAN blob bytes.
- Execute PowerShell tests with `powershell` or `pwsh`; skip only when neither exists. Shell availability no longer suppresses PowerShell tests.
- Use platform-appropriate Git shims for the real concurrent PowerShell CAS regression.
- Isolate the supervisor triage fixture with pytest's temporary directory and remove the workflow's `C:\tmp` workaround.
- Print all suite skip reasons in CI and parse `plan_commit.ps1` with Windows PowerShell 5.1.

## Work log

- [2026-09-30T23:05:00Z] [CX] Read task, spec §10, protocol and briefing; preflight inspected all five Owned_Paths before editing.
- [2026-09-30T23:16:30Z] [CX] First implementation d493417 exposed that `Get-Command -CommandType Application` can return multiple PATH matches. Correction 97e7f9d selects the first native executable. Full Windows and Ubuntu matrix now green; no outside-territory failures. No code was ported from another project.

## Verification

Tested code/test commit: `97e7f9d32e97f5b66940e8c485982829b757c831`.

CI run: https://github.com/alboogycOdR/DEVDEPARTMENT/actions/runs/36789689399

| Environment | Verification | Actual result |
|---|---|---|
| windows-latest, fresh task-branch checkout | `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/harness-audit.ps1 -NoShield` | Audit PASS, exit 0; invokes `python -m pytest tests\ -q`: **1210 passed in 288.53s**, no skips; `node hooks/run-tests.js`: **47 passed, 0 failed** |
| ubuntu-latest, fresh task-branch checkout | `bash scripts/harness-audit.sh --no-shield` | Audit PASS, exit 0; invokes `python3 -m pytest tests/ -q`: **1209 passed, 1 skipped in 78.29s**; Node **47 passed, 0 failed** |
| Windows PowerShell 5.1 CI parser | Scriptblock parsing of dispatch.ps1, worktree.ps1, plan_commit.ps1 | All three passed |
| Local Windows repair regression | `python -m pytest -q tests/test_plan_commit.py::TestPowerShellCasBytes tests/test_plan_commit.py::TestClockStampedUpdatedAt -k "powershell or cas_reapply or untouched"` | **7 passed, 5 deselected in 34.51s**, exit 0 |
| Local Windows Node suite | `node hooks/run-tests.js` | **47 passed, 0 failed**, exit 0 |
| Whitespace | `git diff --check` | Clean |

Ubuntu's only skip is `tests/test_worktree_ps1.py:25`, reason **Windows process/worktree lifecycle**. PowerShell CAS/stamping tests execute using `pwsh` on Ubuntu and Windows PowerShell on Windows; none were suppressed to obtain a green matrix. The workflow's `PYTEST_ADDOPTS=-ra` records every skip reason. Audit intentionally excludes AgentShield and real credentialed CLI smoke under its existing `--no-shield` / no `--smoke` invocation.

The initial local affected suite completed with **92 passed, 6 failed in 552.55s**, exit 1, at the initial Git-resolution implementation. Those same six PowerShell CAS/stamp failures also appeared in first CI run https://github.com/alboogycOdR/DEVDEPARTMENT/actions/runs/36789412849 and were fixed by 97e7f9d. The subsequent local repair regression and both full CI suites verified the fix. Full-suite acceptance evidence is from the fresh CI worktrees above; an identical additional local full run was omitted under the coordinator's verification instruction. This final dossier update changes no executable code or tests.
