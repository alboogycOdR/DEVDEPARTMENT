# BACKLOG.md — evaluated, deliberately not built (yet)

Items assessed against a real Claude Code capability review (2026-08) and
consciously deferred, with the reason and the trigger that would change the
answer. Recorded so they are not silently forgotten, and not relitigated from
scratch every time someone notices the feature exists.

---

## 1. Per-account usage probing (increment 9 of the builder registry)

**State:** S5B ships with `usage_provider: null` — never usage-gated. The
compound form `"claude:<tag>"` is reserved in the schema but nothing probes it.

**Deferred because:** a second probe target is real work in `usage_probe.py`'s
CLI-invocation layer, not a registry read, and fail-open means the worst case
is S5B dispatching into an exhausted window — which the existing stale
detection already surfaces.

**Check before building it:** Claude Code now ships **per-agent, per-task usage
attribution** natively. That may supply richer data (per-task cost, not just
window %) for less machinery than a `CLAUDE_CONFIG_DIR`-scoped throwaway probe.
Investigate that first; only build the probe if native attribution can't be
read per-config-dir. Also needs a third board meter slot — `board/index.html`'s
top strip currently hardcodes exactly two.

---

## 2. Agent checkpointing → resume instead of REDISPATCH_STALE

**State:** a stale builder is redispatched from scratch, losing its context.

**Deferred because:** checkpointing is **beta** and its schema may change.
Building on it now buys a fragile dependency for a path that already works.

**Trigger:** checkpointing reaching GA. Then evaluate whether
`REDISPATCH_STALE` can resume a checkpoint rather than restart, and whether it
can replace the detached-console-window workaround in `dispatch.ps1` (added in
`deacac3` because builders died with their parent process) with something
native.

---

## 3. Streaming agent logs → live Mission Control board

**State:** the board reads committed state; builder progress appears when
PLAN.md/dossiers are written.

**Deferred because:** also **beta**. The current file-mediated model is
honest — the board shows what is durably recorded, not what is merely claimed.

**Trigger:** GA, plus a decision on whether live-but-uncommitted progress
belongs on a board whose whole value is that it reflects git-durable truth.

---

## 4. Native agent teams / `SendMessage` cross-session messaging

**State:** builders coordinate asynchronously through git (PLAN.md, dossiers,
CONTROL blocks). No live inter-session messaging.

**Deferred because:** DEVDEPARTMENT's value is **cross-CLI** (Grok + Codex +
Claude) with git-persistent, reviewable state. Native agent teams are
Claude-only and in-session; migrating the blackboard onto them would trade the
system's core property for real-time chat it doesn't need.

**Narrow exception worth revisiting:** the `peer-sessions` **Codex bridge**
pattern (a temporary Claude relay) would let CX escalate a mid-session
`SPEC_AMBIGUITY` and receive an answer without killing its context — today it
must write `blocked` to PLAN.md and exit. Bounded, additive, doesn't touch the
blackboard. Requires Claude Code ≥ 2.1.224, macOS/Linux, and `cmux`; will not
work headless on clawsrv without that layer present.

---

## 5. Graphify / codebase-memory-mcp knowledge graph

**State:** builders read files directly; `instincts.py inject --limit 5` is the
only memory-as-index mechanism.

**Deferred because:** an external dependency with its own operational surface,
and the current token profile is acceptable.

**Trigger:** builder sessions routinely exhausting context on codebase reading
in large projects. The reported reductions come from making code relationships
queryable *before* a session loads files — complementary to, not a replacement
for, the blackboard.

---

## 6. Mechanical enforcement for the shared-infrastructure rule

**State:** stated as prose in all three briefings (`45a6e89`). No mechanical
check — deliberately, because the territory firewall watches *file writes* and
cannot see a `psql`/`docker`/`gcloud`/`kubectl` call.

**Deferred because:** any Bash-pattern denylist is trivially evadable
(a wrapper script, a different client) and risks teaching false confidence in
a guard that doesn't really guard. Prose plus a `blocked`-escalation rule is
honest about what is actually enforced.

**Trigger:** per-agent `disallowedTools` scoping becoming viable for
cross-CLI dispatch — it would add a second layer for the Claude-CLI units at
least. Note it would remain partial: GB and CX would still be unprotected, so
the prose rule stays regardless.

---

## Standing rule

Anything added here must name the **trigger** that would change the answer.
"Not now" without a trigger is just forgetting slowly.
