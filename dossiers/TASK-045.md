# TASK-045 Work Log

## Scope

Registry-driven Codex sandbox selection, dispatch preflight coverage, and
accurate dispatch failure reporting. All code remains within TASK-045's
Owned_Paths.

## Work Log

- [2026-09-30T21:24:00Z] [CX] Added tests for registry sandbox defaults/validation, exact legacy Codex argv, read-only smoke failure, CI harness coverage, preflight invocation, and nonzero CLI propagation. Initial run had expected implementation failures plus one CI assertion mismatch; corrected the latter to follow the workflow's harness-audit path.
- [2026-09-30T21:30:00Z] [CX] Implemented registry-configured Codex sandbox in POSIX and PowerShell dispatch, per-dispatch fixture preflight with recursion guard, and POSIX CLI exit-code propagation. Focused rerun pending.
- [2026-09-30T22:09:10Z] [CX] Runtime investigation: the original builder-registry + smoke subset took 66.60s; seven smoke tests each launched full throwaway Git worktree dispatches at roughly 7.5–10s apiece. Removed two redundant full-dispatch tests, made registry-preflight iteration a unit test, and kept an end-to-end test proving dispatch invokes preflight before dry-run/launch. Final targeted subset: 42 passed in 61.23s; its slowest call is the end-to-end preflight at 14.72s. Broader suite runtime still needs a separate performance task because most of it is outside TASK-045 Owned_Paths.
- [2026-09-30T22:09:10Z] [CX] Verification: full Python suite passed 1205 tests in 807.33s before the final test-only refactor/additional dry-run and preflight assertions; the rerun after adding those tests was stopped at ~65% at the user's request to investigate runtime, with no failures observed before stop. Final affected tests passed 42/42; Node 47/47; PowerShell parse, `bash -n`, `compileall`, `git diff --check`, and PLAN validator all pass (validator's existing 361 KB PLAN size warning only).
