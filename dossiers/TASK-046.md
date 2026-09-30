# TASK-046 — CI matrix portability

## Implementation

- Resolve native Git as `git.exe` on Windows and `git` on Unix when copying PLAN blob bytes.
- Execute PowerShell tests with `powershell` or `pwsh`; skip only when neither exists. Shell availability no longer suppresses PowerShell tests.
- Use platform-appropriate Git shims for the real concurrent PowerShell CAS regression.
- Isolate the supervisor triage fixture with pytest's temporary directory and remove the workflow's `C:\tmp` workaround.
- Print all suite skip reasons in CI and parse `plan_commit.ps1` with Windows PowerShell 5.1.

## Work log

- CX: Read task, spec §10, protocol and briefing; preflight inspected all five Owned_Paths before editing. Focused tests running; full local and CI evidence pending.

## Verification

Pending final test evidence and task-branch Windows/Linux matrix run.
