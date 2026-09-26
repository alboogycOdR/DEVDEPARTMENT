# TASK-026 — Wave E E-0d — no-manifest adopt path (sync_from_pack --adopt)

## Brief
`sync_from_pack.py --adopt` for a pre-v4.6 install with no sync-manifest.json (rwc-mobile-connect, ~v1.2): fingerprint known framework files against pack git history, write a manifest with `role: project`, record each file as `matches vX` / `diverged` / `absent`, then run TASK-025's legacy sync path. Never overwrite a diverged file. Absent layers (hooks, .claude/agents, control.py) are proposed as adds with a checklist, never silently installed. Build the fixture from real pack v1.2-era files (`git show <v1.2 sha>:<path>`). Then run `--adopt --dry-run` (add the flag if absent) against a COPY of C:/CLAUDECODE_kingdom.work/rwc-mobile-connect made into a tmp dir — never the real repo — and paste the report into Test_Evidence.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §2 (E-0.6)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/sync_from_pack.py, tests/test_sync_adopt.py (new), tests/fixtures/adopt/** (new)
- Protected-path grants: scripts/sync_from_pack.py
- Depends_On: TASK-025

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log
