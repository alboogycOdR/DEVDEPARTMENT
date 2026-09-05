# GateGuard — the comprehension gate

`hooks/gateguard.js` is a PreToolUse hook that denies a builder's **first** write to
each file, and every destructive Bash command, until the unit presents concrete facts
about what it is about to touch. It then allows the retry.

Ported from the `gateguard` skill in [ECC](https://github.com/affaan-m/ECC) (MIT).
The design is theirs; the implementation is ours — ECC's hook is ~1300 lines against
its own shell-parsing libs, and our fact prompts are specialized to the Coordination
Protocol (Owned_Paths, Acceptance_Criteria) rather than generic wording.

## Why it exists

We already had a permission layer. We did not have a comprehension layer.

| Hook | Question it answers |
|---|---|
| `territory-firewall.js` | **May** this unit write to this path? |
| `gateguard.js` | Does this unit **understand** the file it is about to change? |

Asking a model to self-assess — "are you sure?", "did you check the callers?" — reliably
returns yes. Asking it to *list every importer of this file* does not: it has to run a
Grep. The investigation is the mechanism. The gate never checks the answer, and cannot;
the denial alone is what forces the model to go look.

This is why gateguard is **not** redundant with review. Review catches a bad edit after
it exists. Gateguard changes what gets written in the first place.

## The three stages

```
1. DENY   — block the first Edit/Write per file, or any destructive Bash
2. FORCE  — state exactly which facts to gather
3. ALLOW  — the retry passes; the file is not gated again this session
```

## Gates

**Edit on an existing file** (once per file per session) asks for: every importer of the
file; the public symbols this edit changes and their callers; the real field names and
date format of any data it reads or writes; the acceptance criterion this satisfies,
quoted; and confirmation that every importer is inside the unit's Owned_Paths — an
importer outside the territory is a boundary finding, not something to route around.

**Write of a new file** asks: what will call it, what existing file already does this,
which Owned_Path glob covers the new path, and which criterion requires a *new* file.

**Destructive Bash** — `rm -rf`, `git reset --hard`, `git push --force`, `git clean -fd`,
`DROP TABLE`, `dd if=`, `Remove-Item -Recurse` and friends — gates **every time**, never
memoized, and asks for an enumerated blast radius plus a one-line rollback. Quoted
strings and heredoc bodies are stripped first, so `git commit -m "drop table migration"`
does not trip it.

**Routine Bash** gates once per session, then never again.

## Scope

Builders only, by default. ORCH is interactive, human-supervised, and would be gated on
every PLAN.md status write. Opt ORCH in with `GATEGUARD_UNITS=ORCH,S5,GB,CX` if you want
it during a hands-off orchestration run.

## Configuration

| Variable | Default | Effect |
|---|---|---|
| `GATEGUARD` | unset (on) | `off`/`0`/`false`/`disabled`/`no` turns the gate off entirely |
| `GATEGUARD_UNITS` | unset | Comma-separated units to gate. Unset = every unit except ORCH |
| `GATEGUARD_EXEMPT_GLOBS` | unset | Comma-separated globs skipping first-touch gating (`**` crosses segments, `*` does not). For trees where "who imports this" carries no signal — fixtures, generated output, scratch dirs |
| `GATEGUARD_FULL_DENIALS` | `3` | How many denials print the full checklist before later ones condense to one line. Keeps a long session from filling its own context with near-identical blocks |
| `GATEGUARD_BASH_ROUTINE_DISABLED` | unset | Drops the routine-Bash gate only. **Destructive Bash checks keep running** |
| `GATEGUARD_EXTRA_DESTRUCTIVE` | unset | Extra destructive patterns, as regex source. A malformed regex is ignored (built-ins still apply) and reported once to stderr |
| `GATEGUARD_STATE_DIR` | `.devteam/gateguard` | Per-session state. If state cannot be written the gate **allows** rather than looping |

Note the asymmetry: only `GATEGUARD=off` disables destructive-Bash checking. Every other
variable narrows one behaviour and leaves the load-bearing part running.

## Failure behaviour

Fail-open, everywhere, deliberately:

- any unexpected exception → allow, with a `non-fatal hook error` line on stderr;
- state directory unwritable → allow. A denial we cannot record would otherwise fire
  again on the very retry it demanded, and the unit would loop forever;
- unknown `DEVTEAM_UNIT` → allow. `territory-firewall.js` already fails *closed* on that,
  and two hooks blocking the same call would bury its message.

A comprehension gate must never be able to brick a build. `validate_plan.py` and review
remain the backstop.

## Tests

`node tests/test_gateguard.js` — 34 cases, run against the hook as a real subprocess
(stdin JSON → exit code + stderr), because the process boundary *is* the hook contract.

## Tuning it

Watch REVIEW.md. If rework findings stop citing "changed a signature without updating
callers" and start citing something else, the gate is doing its job and the next
adjustment belongs elsewhere. If builders start pattern-matching the checklist with
plausible-sounding answers they did not actually gather, that is verifier-gaming — see
ECC's `loop-design-check` skill, which is the next thing worth porting.
