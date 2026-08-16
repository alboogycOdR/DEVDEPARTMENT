# Dossier — TASK-010 · Repository line-ending policy

**Brief:** Add a `.gitattributes`. Small file, real trap: `sync_from_pack.py` compares files by **hash of their exact bytes**, and every downstream project's "in sync / conflict" verdict rests on that. A `.gitattributes` that renormalises tracked content would change those bytes underneath the sync tool and turn clean files into conflicts across every project that consumes this pack.

**Spec:** `specs/PACK_HARDENING_2026-08.md` §4 (H-D) and §8 Q1.

**ORCH decision, already made — do not re-litigate:** spec Q1 option **(b)**. `* text=auto` going forward only, **no mass renormalisation commit**. Rationale: lower risk for a pack other projects sync from byte-exactly. Write that reasoning into the file's own comments; the next person to touch it will otherwise "helpfully" run `git add --renormalize` and undo the decision.

**Intended approach:**
- `* text=auto` for the general case; explicit `-text` / `binary` for known-binary paths (`*.png`, `*.db`, `*.bundle`, etc. — check what the repo actually contains rather than pasting a generic list).
- Consider whether any file must keep CRLF (Windows-only scripts consumed by tools that require it) — if none, say so in a comment rather than leaving it unstated.
- Do NOT run `git add --renormalize .` and do not commit a whitespace-only sweep of existing files.

**Verification:** after the change, edit `PLAN.md` and confirm no CRLF warning; run `python -m pytest tests/test_sync_from_pack.py` (byte-exactness intact); run `git diff --stat` and show it contains no mass renormalisation.

**Territory note:** `.gitattributes` is NOT a protected path, so no firewall grant is needed — but it is repo-wide in effect, which is why the acceptance criteria are about what must NOT change. TASK-008 and TASK-009 run CONCURRENTLY in their own worktrees.

## Work Log
