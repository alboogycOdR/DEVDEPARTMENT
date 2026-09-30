# TASK-045 Work Log

## Scope

Registry-driven Codex sandbox selection, dispatch preflight coverage, and
accurate dispatch failure reporting. All code remains within TASK-045's
Owned_Paths.

PowerShell treats the shell smoke's exit 77 as unavailable (missing Bash),
warns, and continues native dispatch; other failures still refuse launch.
The shell smoke does not verify PowerShell argv. Read-only fixture CLIs exit
successfully without writing, so artifact verification must detect the denial.
Both CONTROL prompts require a non-empty `CATEGORY: detail`, including CAPACITY.

## Work Log

- [2026-09-30T21:24:00Z] [CX] Added tests for registry sandbox defaults/validation, exact legacy Codex argv, read-only smoke failure, CI harness coverage, preflight invocation, and nonzero CLI propagation. Initial run had expected implementation failures plus one CI assertion mismatch; corrected the latter to follow the workflow's harness-audit path.
- [2026-09-30T21:30:00Z] [CX] Implemented registry-configured Codex sandbox in POSIX and PowerShell dispatch, per-dispatch fixture preflight with recursion guard, and POSIX CLI exit-code propagation. Focused rerun pending.
- [2026-09-30T22:09:10Z] [CX] Runtime investigation: the original builder-registry + smoke subset took 66.60s; seven smoke tests each launched full throwaway Git worktree dispatches at roughly 7.5–10s apiece. Removed two redundant full-dispatch tests, made registry-preflight iteration a unit test, and kept an end-to-end test proving dispatch invokes preflight before dry-run/launch. Final targeted subset: 42 passed in 61.23s; its slowest call is the end-to-end preflight at 14.72s. Broader suite runtime still needs a separate performance task because most of it is outside TASK-045 Owned_Paths.
- [2026-09-30T22:09:10Z] [CX] Verification: full Python suite passed 1205 tests in 807.33s before the final test-only refactor/additional dry-run and preflight assertions; the rerun after adding those tests was stopped at ~65% at the user's request to investigate runtime, with no failures observed before stop. Final affected tests passed 42/42; Node 47/47; PowerShell parse, `bash -n`, `compileall`, `git diff --check`, and PLAN validator all pass (validator's existing 361 KB PLAN size warning only).
- [2026-09-30T23:08:21Z] [CX] Resumed ORCH rework on the existing clean branch and merged current master for updated control/validation behavior. Corrected all three findings: missing Bash now reports a distinct unavailable status handled as warn/continue by PowerShell; the read-only Codex stub exits 0 without writing so the smoke's artifact check fails; both CONTROL prompts require CATEGORY: detail and include CAPACITY. Added native PowerShell gate tests for pass/unavailable/failure, actual missing-interpreter discovery, read-only artifact failure and CLI launch failure. Node suite passed 47/47; Bash syntax, PS5.1 parser and diff checks passed. Focused final rerun and scheduled full-suite verification pending; parent CX owns shared PLAN updates.
- [2026-09-30T23:12:00Z] [CX] Final focused verification: `python -m pytest -q tests/test_builder_registry.py tests/test_harness_smoke.py` exited 0 with 49 passed in 122.91s. All ORCH rework assertions pass. Full Python suite queued behind the other builder's suite to avoid competing resource-heavy test runs.
