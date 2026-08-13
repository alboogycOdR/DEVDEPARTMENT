# Dossier — TASK-005 · ATLAS A4 — context-pack composer

**Brief:** Build `scripts/atlas_pack.py`: `pack --task TASK-NNN --budget N=3000 [--format prompt|json]` — the product of the whole system, a token-budgeted context pack for dispatch prompts. Registered via the TASK-002 façade hook; consumes core (`impact`, outlines, FRESH/STALE), episodes, and cards read-only; edits no other atlas module.

**Spec:** specs/DEVDEPARTMENT_ATLAS_SPEC.md — read in full; load-bearing: §4 (the four sections, their order, truncation rules — this is the heart of the task), §3 (contract), §6 row A4, §7 A4 exit criteria, R1 (footer sentence verbatim), R4 (degrade without cards).

**Intended approach:**
- Resolve the task's Owned_Paths against the live tree reusing `scripts/preflight_paths`' classification (import it; don't reimplement). Read the task block from PLAN.md via `validate_plan.parse_tasks`.
- Section order within budget: 1) territory core — per-file symbol outline + card body if FRESH, else first-N-lines head; 2) one-hop `impact` neighborhood — pointers + cards ONLY, never file bodies; 3) top-K episodes matches on Title/Description terms; 4) freshness footer — scan ts, stale-card count, and verbatim: "This pack is a map, not the ground: read live any file you edit."
- Hard cap: truncate lowest-priority-first (3 → 2 → card bodies in 1); ALWAYS state what was truncated.
- Token estimate: chars/4 heuristic is fine — document it.
- Test both paths: cards present (full) and cards absent (A1-only degradation, pack says so). §7: synthetic PLAN.md task fixture stays under budget with outline + pointers + R1 footer.

**Territory note:** scripts/** is builder-protected; scripts/atlas_pack.py is a deliberate per-task grant. You run alone in wave 3.

## Work Log
