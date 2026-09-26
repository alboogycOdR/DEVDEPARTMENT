# Wave E decomposition: plan v6.0 (2026-09-26, ORCH on claude-opus-5-5)

Source: `specs/LOOP_HYGIENE_2026-09.md` v2.1. There are 21 tasks, TASK-023..043. Status: **awaiting owner approval; nothing dispatched.**

## Increment → task map, and why each split

| Spec | Tasks | Split reason |
|---|---|---|
| E-0 | 023 (a14f8976), 024 (bceb8eb2, d3f5fc08, 7baeedf3, 3e8c3e79), 025 (items 2–5), 026 (item 6 adopt) | a14f8976 alone is +447 lines into supervisor.py, and the other four ports touch a disjoint file set, so they run in parallel. Sync items 2–6 exceed one session; adopt (fingerprinting against pack history) is separable and off the critical path. |
| E-A | 027 | One session. The optional Protected_Grants (A.4) is **in scope**: it ends the grant-edit ritual for the rest of the wave. |
| E-B | 028 (review ledger, lock, markers, atomic state + `tests/tick_harness.py`), 029 (escalation/triage ledgers, prompt form) | The multi-tick/multi-process harness is shared by 029/030/031/043 and must land first; six items plus the harness exceed one session. |
| E-C | 030 | One session. The "/resume from the file inbox" acceptance line moves to 031, since the inbox path doesn't exist until E-K. |
| E-K | 031 | One session. Adds `usage_probe.py` (named in K.3, missing from the spec's territory table). |
| E-D | 032 | One session. It also owns `validate_plan.py` (warnings, `--review`, archive stubs), which the spec table omits. The clock-stamped review verdict moves to 042 (a `.claude/commands` edit). |
| E-E | 033 | One session. It also fixes the known flake where `test_plan_commit.py` writes to the live checkout (TASK-022 review). |
| E-F | 034 (CAS, idempotent claim, legacy guard), 035 (verified claim, pinned base, grammar, strict-by-default) | Two disjoint file sets (plan_commit/plan_guard vs dispatch/validate/sync) that can run in parallel. |
| E-G | 036 | One session. Adds `scripts/push_policy.py (new)` and `supervisor.py` (merge/park push hooks). |
| E-H | 037 (runner lifecycle, CR, eol, CI), 038 (CLI smoke test) | Disjoint (dispatch/worktree/CI vs harness-audit), so they run in parallel. |
| E-I | 039 | One session. |
| E-J | 040 (roster, retire_unit, lint), 041 (statuses and lanes), 042 (review rules, freshness, untracked work) | Eight items; three coherent file sets. |
| §15 | 043 | The exit scenario is a real test deliverable, not an ORCH ad-hoc run. |

## Deviations from the spec's territory table

- `sync.pack_path`/`framework_version` writes and the behind-pack line: /devteam-status + session-start are in 025; the P0 digest line is in 028 (its owner then).
- `DEVTEAM_DELEGATED=1` from supervisor launches is in 029 (it owns supervisor.py then), not in E-A.
- The `devteam-decompose.md` model line changes to claude-opus-5-5 (owner decision: no Fable) in 042.
- Protocol, CLAUDE.md/AGENTS.md roster markers, README, onboard.md and docs/AUTOPILOT.md are ORCH-applied at wave close.

## Pre-dispatch ORCH machinery (uncommitted, pending approval)

` (new)` is stripped as an annotation by `validate_plan.parse_owned_paths`, `hooks/lib.js ownedPathsOf` and `preflight_paths.owned_paths_for`. Without this, the firewall would deny builders their own new files, and isolation checks would miss a `(new)`/bare pair. Tests were added; Python 1033 passed, Node 37/0.
