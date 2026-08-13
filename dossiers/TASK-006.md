# Dossier — TASK-006 · ATLAS A5 — pack-wide integration

**Brief:** Wire ATLAS into dispatch, nightly maintenance, autopilot.json, onboarding, all three briefings, and the decompose command — every touchpoint fail-open, shipping disabled by default. This is cross-cutting surgery on ORCH's own machinery: read each file fully before editing, keep diffs minimal, and preserve the byte-identical-when-disabled guarantee.

**Spec:** specs/DEVDEPARTMENT_ATLAS_SPEC.md — read in full; load-bearing: §5 (every integration point, one bullet each), §6 row A5, §7 A5 exit criteria, R2 (onboarding .gitignore block), R3 (briefings must present plain-CLI usage — GB/CX cannot see MCP).

**Intended approach:**
- dispatch.sh/.ps1: mirror the instincts-injection pattern exactly — after it, `if db exists && autopilot.json atlas.enabled: append "## PROJECT MAP (ATLAS) — a map, not the ground" section from atlas.py pack`; any error → one warning line, dispatch proceeds. Both scripts, identical semantics.
- maintenance.py: nightly scan + `episodes --reindex` + (if atlas.cards_auto_refresh) capped `cards --generate --max <atlas.max_cards_per_night>` (default 30). Failure = one logged audit line; only a corrupt db escalates, and the prescribed remedy is delete + full rescan.
- autopilot.json: add the §5 atlas block verbatim, `"enabled": false`.
- onboard.md: ask-step following the control.mode/roster "ask, don't auto-flip" pattern; include the R2 .gitignore block.
- briefings/GROK_BUILD_BRIEFING.md, CODEX_BRIEFING.md, S5_BUILD_BRIEFING.md: one short section each — what the ATLAS prompt section is, R1 verbatim, `atlas.py query/where/impact` as shell commands builders may run mid-session.
- .claude/commands/devteam-decompose.md: one prose instruction — consult `atlas.py impact` when carving Owned_Paths; record surprising couplings in Descriptions.
- board_publisher.py: optional `"atlas"` key — cosmetic, skip if risky.
- docs/ATLAS.md: append Integration section.
- §7 verification: dry-run dispatch shows the section when enabled; **byte-identical prompts when disabled** (diff them); nightly audit runs scan clean.

**Territory note:** almost everything here is builder-protected ORCH machinery — the Owned_Paths are a deliberate, reviewed, per-task grant (firewall exceptions added at dispatch, removed at done). Anything outside them is automatic rework.

## Work Log
