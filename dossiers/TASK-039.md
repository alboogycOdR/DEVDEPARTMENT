# TASK-039 — Wave E E-I — learning loop earns its sessions or stays off

## Brief
(1) `learning.enabled` gate inside distiller (default false for NEW projects; existing projects keep current behaviour + one-time notice — ask-don't-auto-flip). (2) Amendments de-duplicated by (target file, rule-text hash); missing-target rejected at creation; pending amendments appear once in the P0 digest with /approve AMEND-NNN / /rework AMEND-NNN; auto-expire after `learning.amend_expiry_days`=14 with a log line. (3) retro.py: unit list from the registry (CX9 included), correct active-instinct count, bucket by file not directory. (4) Effectiveness gate: matched-task first-pass ≤ overall for 2 consecutive weeks → distillation pauses itself and says so in the digest. (5) /devteam-status shows distiller last-run, runs, instincts produced.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §11 (E-I.1–5)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/distiller.py, scripts/retro.py, scripts/instincts.py, tests/test_distiller.py, tests/test_retro.py, tests/test_instincts.py, tests/test_instincts_lifecycle.py, scripts/board_publisher.py, tests/test_board_publisher.py, scripts/status_digest.py, .claude/commands/devteam-status.md
- Protected-path grants: scripts/distiller.py, scripts/retro.py, scripts/instincts.py, scripts/board_publisher.py, scripts/status_digest.py, .claude/commands/devteam-status.md
- Depends_On: TASK-030

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log
- [2026-09-29T00:00:00Z] [CX/ORCH] Merged master into branch (picks up TASK-032's sync-manifest registrations). Full suite green: 1112 Python passed in 223.85s; 47 Node passed. All four rework findings addressed: learning.enabled gate + one-time notice, amendment de-dup/missing-target-reject/expiry, P0 digest /approve|/rework lines (once), effectiveness-gate pause with digest + retro announcement, registry-driven per-unit retro (CX9 fixture). Submitting for review.
