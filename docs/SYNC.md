# SYNC.md — keeping onboarded projects current with the pack (v4.6)

## The problem this solves

`onboard.md` is deliberately **add-only** — it never overwrites an existing
file. Correct for first onboarding (it must not clobber a project's own
content), but it means an onboarded project is frozen at the pack version it
was born from. Observed live: a project running the v1.0.0 coordination
protocol while the pack was at v4.5, missing every fix in between — including
ones for failure modes that had already bitten other projects.

## The design

**Ownership is data, not judgment.** `sync-manifest.json` in the pack root
declares every path as one of:

- `framework_owned` — the pack is the source of truth (scripts, hooks, docs,
  briefings, tests, commands, board frontend). Sync updates these.
- `project_owned` — the project's own state (`PLAN.md`, `REVIEW.md`,
  `INSTINCTS.md`, `specs/`, `dossiers/`, `.devteam/`, logs, backups). Sync
  **never** touches these, under any flag.
- `merge_special` — part-pack, part-project:
  - `CLAUDE.md` — everything above the `## Multi-Agent Orchestration` marker
    is the project's own and is preserved; the marker section is replaced
    with the pack's current content.
  - `autopilot.json` — **add-only keys**: new config keys from the pack
    template are added; every existing value (your `interval_seconds`, your
    `builders`, your `control.mode`, your allowlists) is preserved.
  - `.claude/settings.json`, `.codex/config.toml` — **manual-only**: sync
    reports, never edits. Hook wiring can contain a project's own
    non-DEVDEPARTMENT hooks; too sensitive to automate.

**Three-way conflict detection.** `.devteam/sync_state.json` records the hash
of each framework file as last written by sync (or onboarding). On the next
sync, each file gets one of:

| Situation | Verdict |
|---|---|
| pack == project | `IN_SYNC` |
| project == baseline, pack moved on | `UPDATE` — pack improved it, safe |
| project != baseline | **`CONFLICT`** — you customized it locally; never silently clobbered |
| no baseline at all (legacy project) | `CONFLICT` for anything that differs — conservative by construction |
| absent in project | `ADD` |

Conflicts are resolved either by keeping your version (do nothing — it stays
flagged until hashes converge) or explicitly taking the pack's
(`--adopt-pack`).

## Usage

```bash
# ALWAYS start with the dry-run (it's the default — nothing is written):
python scripts/sync_from_pack.py --pack ../DEVDEPARTMENT

# Apply the safe changes (updates + adds; conflicts untouched):
python scripts/sync_from_pack.py --pack ../DEVDEPARTMENT --apply

# First sync of a legacy project, after reviewing the dry-run list:
python scripts/sync_from_pack.py --pack ../DEVDEPARTMENT --apply --adopt-pack

# Scoped to specific files:
python scripts/sync_from_pack.py --pack ../DEVDEPARTMENT --apply --only scripts/dispatch.sh
```

Windows: `python` instead of `python3`; pack path e.g.
`C:\CLAUDECODE_kingdom.work\DEVDEPARTMENT`.

Exit codes: `0` clean · `2` unresolved conflicts present · `1` usage error.

## After a sync

Sync never runs git for you. Review `git diff`, run the suites
(`python -m pytest tests/ -q`, `node hooks/run-tests.js`), then commit —
suggested message: `chore: sync DEVDEPARTMENT pack to <version> [ORCH]`.

If the sync brought in new autopilot.json keys, skim them — they arrive with
pack defaults, and your project may want different values.

## Guarantees

- **Byte-exact copies** (`read_bytes`/`write_bytes`) — no encoding or newline
  translation, ever (the CRLF lesson is in this repo's git history).
- Dry-run writes literally nothing, including sync state.
- `project_owned` paths are untouchable even under `--adopt-pack`.
- A locally-customized framework file is never overwritten without
  `--adopt-pack` — your customizations cannot be silently lost.
- Applying a sync (even a no-op one) establishes/refreshes the baseline, so a
  legacy project's first `--apply` leaves it fully tracked for all future
  syncs.

## Verifying merge_special markers (learned the hard way)

`CLAUDE.md`'s merge_special marker went stale once already: the manifest said
`## Multi-Agent Orchestration`, a string that existed in NEITHER the pack's
own `CLAUDE.md` nor therefore any project that ever synced from it. Every
project's CLAUDE.md merge silently reported "cannot merge safely" forever,
and nothing caught it because every test used a synthetic fixture with a
marker chosen to match -- none exercised the real pack file against its own
manifest. `tests/test_sync_from_pack.py::TestManifestMarkersMatchRealFiles`
now does exactly that on every test run: asserts each configured
`marker_section` marker actually appears in the pack's own file, and that
every `merge_special` entry corresponds to a strategy `sync_from_pack.py`
actually implements (a second, related bug: `AGENTS.md`'s entry described a
merge that was never wired into `run_sync()`, so it was silently whole-file
the entire time regardless of what the manifest claimed).

**Whenever a heading in `CLAUDE.md` or `AGENTS.md` changes, or a new
merge_special entry is added, run the pack's own test suite before shipping**
-- `TestManifestMarkersMatchRealFiles` will fail loudly if the marker no
longer matches, instead of failing silently on every downstream project
forever.

## Local, untracked overrides (`autopilot.local.json`)

`autopilot.json`'s `merge_special` handling (above) keeps a project's
*existing* values safe from sync — but it does nothing for the pack
repository itself. **DEVDEPARTMENT's own `autopilot.json` is simultaneously
the shipped template AND this repo's live project config**, so any value
flipped here for local use (to enable a feature just for DEVDEPARTMENT, say)
lands directly in the file every onboarded project inherits through the
`add_only_keys` merge the moment they add that key. This has happened twice:
once with an ATLAS setting, and again on 2026-08-29 with `tower.enabled` /
`tower.url` / `tower.project_id`, committed straight to the tracked file to
push DEVDEPARTMENT's first Tower snapshot (commit `aa1da4a`) — the pack
repo's own `test_sync_from_pack.py::TestPackTemplateShipsSafeDefaults` guard
caught it and stayed red on `master` until the value was reverted
(`6df0fa7`).

The fix is a small, genuinely optional read-side override:
`supervisor.load_config()` also reads an **untracked**, gitignored
`autopilot.local.json` in the repo root, if present, and deep-merges its
values over the tracked `autopilot.json`/`DEFAULT_CONFIG` result — a key
present in the local file always wins (nested dicts merge recursively, so
overriding just `tower.enabled` doesn't clobber `tower.url`); a key absent in
the local file falls through untouched to the tracked config. Absent
`autopilot.local.json` is a silent no-op — every project without one behaves
exactly as before.

This is the same pattern already used for secrets (`DEVTEAM_TOWER_TOKEN` and
friends): a value that is genuinely specific to *this* checkout does not
belong in a tracked file that other projects inherit — it belongs beside it,
outside git.

```jsonc
// autopilot.local.json — untracked, repo root, same shape as autopilot.json.
// Only the keys you want to override locally; everything else falls
// through to the tracked autopilot.json.
{
  "tower": { "enabled": true }
}
```

**When you need a project-specific value in DEVDEPARTMENT itself** (or any
project that also happens to be the pack it ships from): put it in
`autopilot.local.json`, not `autopilot.json`. If a change belongs in the
shared template for every project, it still goes in the tracked file, same
as always.

## For the pack maintainer

When a wave adds a new framework file, **add it to `sync-manifest.json`** in
the same commit — the manifest is itself framework_owned, so downstream
projects receive the updated manifest through the same sync that delivers the
new file. A manifest entry pointing at a file the pack doesn't ship is
reported as `MISSING_IN_PACK` (this exact mechanism caught its own first bug:
this file was listed in the manifest before it was written).
