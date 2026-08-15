# Dossier — TASK-007 · ATLAS A6 — judgeable staleness + visible opt-in cards

**Brief:** Field defects from oikonomos (2026-08-15): `status` output let an 8-merged-tasks-stale index look healthy (bare timestamp) and reported `cards: 0` with no hint that generation is opt-in. Implement spec §9: scan records its HEAD position, status reports deltas a reader can judge (git-vs-indexed count, commits since last scan) with R4 degradation when git is absent, the cards opt-in hint, and the onboarding cards ask-step.

**Spec:** specs/DEVDEPARTMENT_ATLAS_SPEC.md — read §9 (A6) in full; it is precise about output strings and degradation. §3 invariants (UTF-8, forward slashes, exit 0/1 never 2) and R4 still bind. The v1.2 changelog at the top gives the field context.

**Intended approach:**
- A6-1: in `scan`'s finalization, `git rev-parse HEAD` (subprocess, cwd=repo, catch-all → empty string) into meta key `last_scan_head`. meta is already key/value — INSERT OR REPLACE, no migration.
- A6-2 delta line: N = `git ls-files` output filtered through the SAME ignore logic the scanner uses (`is_ignored` + atlas.exclude) so the comparison is apples-to-apples; M = `SELECT COUNT(*) FROM files`. Render `— in sync` when D=0.
- A6-2 commits line: `git rev-list --count <last_scan_head>..HEAD`; ANY failure (no git on PATH, empty head, unknown hash after history rewrite) → `n/a`, exit stays 0.
- A6-2 cards hint: exact string from the spec when `SELECT COUNT(*) FROM cards` = 0; leave existing output untouched otherwise.
- A6-3: onboard.md Step 1 ATLAS bullet — add the cards question (asked only when ATLAS is enabled): generate now (deliberate spend, mention ~file count) and/or enable nightly `cards_auto_refresh` capped at `max_cards_per_night`; silence = neither.
- Tests: temp repo fixtures already exist in tests/test_atlas_core.py — extend them. No-git degradation: build a PATH without git for the subprocess env in the test, don't uninstall anything.

**Territory notes:** onboard.md is a protected path — your write there is a deliberate per-task ORCH grant, review-gated; grok does not load the Claude hooks so nothing blocks you mechanically — territory discipline is on you and verified at review. Do NOT touch atlas.py / atlas_cards.py / atlas_pack.py / dispatch.* / maintenance.py: the dispatch scan-before-pack fix already shipped (5af80c7). Work in YOUR worktree on task/TASK-007-gb; PLAN.md via plan_commit only.

## Work Log
