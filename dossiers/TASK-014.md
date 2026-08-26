# Dossier — TASK-014 · tower_sync.py (P1: snapshot push + queue pull, module only)

**Brief:** The pack's half of Tower's data plane: assemble the schema-v1 snapshot, POST it to Tower, pull the command queue, and materialise commands into `.devteam/inbox/` for the consumer. Pure module — **you do not touch supervisor.py**; TASK-018 wires the tick and needs clean entry points (`build_snapshot(repo, cfg, state)`, `sync_tick(repo, cfg)`).

**Spec:** TOWER §1 P1 (the schema is verbatim — implement it field-for-field), H3 (push, never pull from Tower's side), H4 (bearer token from the env var named by `tower._token_env`; one round-trip pair per tick, always project-initiated), H5 (fail-open, one warning line).

**Intended approach:**
- Reuse, never re-parse: tasks[] via `validate_plan.parse_tasks`; usage via `usage_probe.load_cache` (**cache-only — calling probe() from a tick is forbidden**, same reasoning as board_publisher: a slow probe must never stall a tick); recent_events from the AUTOPILOT_LOG.md tail; supervisor/wave fields from `.autopilot_state.json` + PLAN.md frontmatter where derivable.
- The spec says P1 is "assembly and transport, not new analysis." Some schema fields aren't derivable module-side (live tick number, prev_wave_minutes). **Emit null and document every null in the module docstring** — H2 forbids invented numbers; TASK-018 can enrich from live state later if trivial.
- Transport: stdlib urllib, 10s timeout. POST `{url}/ingest` → GET `{url}/queue/{project_id}` → write each pending command as its own JSON file in `.devteam/inbox/` → DELETE each acked entry. The inbox directory is the interface to TASK-017's consumer — neither module imports the other.
- Fail-open: `tower.enabled` false or url empty → silent no-op. Unreachable/non-200/timeout → ONE warning line, clean return. Never an exception into the caller.
- Config: the `tower` block already exists in autopilot.json (ORCH added it in the planning commit). **autopilot.json is not in your territory.**

**Tests:** stub the transport (no network); cover schema shape, both fail-open paths, inbox materialisation + ack-delete.

**Territory note:** TASK-013 (GB) and TASK-015 (S5) run CONCURRENTLY. Your two files only.

## Work Log
