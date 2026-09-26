# TASK-023 — Wave E E-0a — port oikonomos a14f8976 (review ledger, review.lock, escalation de-dup, status digest)

## Brief
Port oikonomos (github.com/alboogycOdR/oikonomos-gbot) commit a14f8976 ('review once per submission, escalate once a day, scripted status digest') into the pack. Source: `git -C C:/CLAUDECODE_TOOLSETS/oikonomos show a14f8976` (read-only; pulled to origin/master 76b0cb3 on 2026-09-26 — never write to that repo; if a SHA is missing, block with MISSING_DEPENDENCY — do NOT re-derive from spec text). Port = adapt to the pack's supervisor (which differs from oikonomos's copy), never paste oikonomos-specific names/paths. This is the foundation E-B (TASK-028/029) extends: keep the review ledger, review.lock, one-review-per-tick, backoff, escalate_repeat_hours de-dup (with digit-masked detail) and MISSING_DEPENDENCY triage cap as separable functions. The commit message cites the origin SHA; anything intentionally not ported is listed in the dossier with the reason.

## Spec pointers
- specs/LOOP_HYGIENE_2026-09.md §0, §2 (E-0.1), §4 (context only)
- Why: docs/reviews/FIELD_FEEDBACK_SYNTHESIS_2026-09-26.md, docs/reviews/DEVDEPARTMENT_REVIEW_2026-09-26.md
- Live CLI facts: docs/reviews/LIVE_CHECKS_2026-09.md

## Territory
- Owned_Paths: scripts/supervisor.py, scripts/status_digest.py (new), tests/test_token_efficiency.py (new), tests/test_supervisor.py
- Protected-path grants: scripts/supervisor.py, scripts/status_digest.py
- Depends_On: —

## Intended approach
Write the failing test first (it must fail against current master — spec §1 H7), then implement, then run BOTH full suites. Ported code cites its origin SHA and lists anything not ported, with the reason, in the Work Log.

## Work Log
