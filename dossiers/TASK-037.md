# TASK-037 — Wave E E-H1 — Windows runner lifecycle, CR stripping, line-ending defaults, Windows CI matrix

## Brief
(1) dispatch.ps1 records the runner window PID in `.devteam/launch/<unit>.pid`; headless runs drop -NoExit; `worktree.ps1 remove` kills recorded PIDs first, retries `Access is denied` with robocopy /MIR from an empty folder and \\?\ long paths. (2) GitHub Actions matrix windows-latest + ubuntu-latest, fixture repo with base branch `master`, both suites + harness-audit; PS 5.1 parser check on Windows. (3) Every shell parse of Python output strips CR (extends d3f5fc08 port). (5) .gitattributes: `*.sh text eol=lf`, `*.ps1 text eol=crlf`, `PLAN.md text eol=lf` (framework-owned).

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §10 (E-H.1–3, E-H.5)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/dispatch.ps1, scripts/worktree.ps1, .github/workflows/** (new), .gitattributes, tests/test_dispatch_worktree.py, tests/test_worktree_ps1.py (new)
- Protected-path grants: scripts/dispatch.ps1, scripts/worktree.ps1
- Depends_On: TASK-035

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log
