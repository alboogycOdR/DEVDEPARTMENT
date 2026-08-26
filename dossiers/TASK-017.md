# Dossier — TASK-017 · inbox.py (P2: Tower inbox consumer, module only)

**Brief:** The consuming half of Tower's command path: TASK-014's queue pull materialises command files into `.devteam/inbox/`; your module validates and hands them to the supervisor in the exact shape the tg-queue drain produces. Pure module — **no supervisor.py edits** (TASK-018 calls you before `decide()`).

**Spec:** TOWER §1 P2 (command schema + rejection contract), H1 (single handler path — the constraint your module exists to honour), H5 (fail-open).

**Intended approach:**
- `drain_inbox(repo, cfg)` → list of validated command dicts. Validation goes through `scripts/commands.py` (TASK-013) — H1 says "through the same handler path commands.py exposes"; building any second validator here is automatic rework.
- Schema per §1 P2: `id/issued_at/source/actor/command/args`, vocabulary `approve|rework|answer|stop|resume|wave|dispatch`. Malformed JSON or failed validation → file **moved** to `.devteam/inbox/rejected/` with a `.reason` sidecar — never deleted, never guessed. Tower's `queued → delivered → done/rejected` audit honesty (H1 corollary) depends on rejected files surviving.
- **Two-phase consume, designed explicitly:** `drain` lists and validates without deleting; the caller calls `ack(path)` after the command is actually handled. A crash between drain and handling must not lose a command (test this — kill the flow mid-way in a test and re-drain).
- Duplicate command `id`s: second occurrence → rejected/ as duplicate. Track seen ids durably enough to survive a restart (a small `.devteam/inbox/.seen` journal is fine; keep it bounded).
- Output shape: **verify against the real tg-queue drain shape in supervisor.py by reading it** (read-only — supervisor is out of territory), and pin the parity in a test, so TASK-018's wiring is a pass-through.
- Fail-open (H5): empty/missing inbox → clean no-op; any unexpected per-file error → warning + skip, never an exception into the tick.

**Territory note:** Depends on TASK-013. TASK-016 (GB) runs CONCURRENTLY — your two files only. tower_sync.py (TASK-014) is the producer; the filesystem is your only interface to it — no imports either direction.

## Work Log
