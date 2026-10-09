#!/usr/bin/env node
/**
 * territory-firewall.js — PreToolUse hook (Edit|Write|MultiEdit|NotebookEdit).
 *
 * Write-time enforcement of Coordination Protocol territorial isolation:
 *   - Unit GB/CX: writes allowed ONLY under the Owned_Paths of that unit's active
 *     task(s), plus PLAN.md (block-level discipline stays with validator + review).
 *     Protected paths are always blocked for builders.
 *   - Unit ORCH: unrestricted here (ORCH holds structural authority); ORCH's
 *     discipline is enforced by review conventions, not the firewall.
 *
 * Exit codes per Claude Code hook contract:
 *   0 = allow. 2 = BLOCK (stderr is fed back to the model as the reason).
 * Fail-open on unexpected errors (exit 0) so a hook bug can never brick a session —
 * post-hoc validator + review remain the backstop.
 */
'use strict';

const lib = require('./lib.js');

function main() {
  const input = lib.readStdinJson();
  const toolInput = input.tool_input || {};
  const target = lib.filePathOf(toolInput);
  if (!target) return 0; // nothing to check

  const u = lib.unit();
  if (u === null) {
    // v4.7 fail-closed: DEVTEAM_UNIT is set but not a known unit (typo, or
    // a builder added to scripts before being defined in autopilot.json's
    // registry). The old behavior silently granted unrestricted ORCH
    // permissions here. Deny through the normal verdict path — NOT a throw,
    // which the outer catch would convert to fail-open.
    process.stderr.write(
      `[territory-firewall] BLOCKED: DEVTEAM_UNIT='${process.env.DEVTEAM_UNIT}' is not a known ` +
      `unit (${lib.knownUnits().join('/')}) — refusing to treat an unrecognized unit as ` +
      `unrestricted ORCH. Fix DEVTEAM_UNIT or define the unit in autopilot.json's builders registry.`
    );
    return 2;
  }

  const rel = lib.relPath(target);
  // E-A.3: config is human-only. Checked before the ORCH short-circuit so a
  // delegated session (DEVTEAM_DELEGATED=1, unit unset → ORCH) is still denied.
  // Grants cannot override this.
  if (lib.isHumanOnlyConfig(rel) && lib.sessionIsDelegated()) {
    process.stderr.write(
      `[territory-firewall] BLOCKED: ${rel} is writable only by a human session. ` +
      `A process with DEVTEAM_UNIT set or DEVTEAM_DELEGATED=1 must not change autopilot config. ` +
      `Unset both variables (interactive ORCH) to edit it.`
    );
    return 2;
  }
  if (u === 'ORCH') return 0;

  const mode = lib.controlMode();

  // PLAN.md: strict mode — supervisor is the sole writer. Legacy mode
  // still lets a builder edit its own block, but Protected_Grants and
  // Owned_Paths are ORCH-only (a builder cannot self-grant a protected path).
  // isPlanFile also matches the main checkout's PLAN.md, which a worktree
  // builder edits by absolute path (rel is then '../<main>/PLAN.md').
  if (lib.isPlanFile(target)) {
    if (mode === 'strict') {
      process.stderr.write(
        `[territory-firewall] BLOCKED: PLAN.md is protected in control.mode=strict (Wave I). ` +
        `The supervisor is the sole writer — emit a devteam-control block as the last thing ` +
        `you print instead of editing PLAN.md directly. See docs/CONTROL.md.`
      );
      return 2;
    }
    if (lib.builderChangedOrchOnlyFields(toolInput)) {
      process.stderr.write(
        `[territory-firewall] BLOCKED: Protected_Grants and Owned_Paths are ORCH-only fields. ` +
        `A builder must not add or change those lines in PLAN.md.`
      );
      return 2;
    }
    return 0;
  }

  // PLAN.md and grants come from the main checkout, not this worktree (E-A.1).
  const planText = lib.readPlanText();
  const tasks = planText == null ? [] : lib.parsePlan(planText);
  const active = lib.sessionTasksFor(tasks, u);
  const grantGlobs = active.flatMap(lib.effectiveProtectedGrantsOf);

  // Hard-protected paths first. Permanent PROTECTED_EXCEPTIONS plus this
  // session's active-task Protected_Grants (a done task contributes none).
  if (lib.pathInAnyGlob(rel, lib.PROTECTED_FOR_BUILDERS) &&
      !lib.pathInAnyException(rel, lib.PROTECTED_EXCEPTIONS.concat(grantGlobs))) {
    process.stderr.write(
      `[territory-firewall] BLOCKED: ${rel} is a protected path (protocol hard prohibition for ${u}). ` +
      `Do not modify it. If you believe you need this file, set your task to blocked ` +
      `(Blocked_Reason: OWNERSHIP_CONFLICT) with a Progress_Note explaining exactly why.`
    );
    return 2;
  }

  // Strict mode positive rule: a builder MAY write dossiers/<their-active-task>.md
  // (their heartbeat/work-log file) — resolved via .devteam/inflight/<unit>.json,
  // falling back to a PLAN.md scan. Any OTHER dossier is still off-limits.
  if (mode === 'strict' && rel.startsWith('dossiers/')) {
    const activeId = lib.activeTaskIdFor(tasks, u);
    if (activeId && rel === `dossiers/${activeId}.md`) return 0;
    process.stderr.write(
      `[territory-firewall] BLOCKED: ${rel} — in control.mode=strict you may only write your ` +
      `own active task's dossier (dossiers/${activeId || '<your-task-id>'}.md), not another task's.`
    );
    return 2;
  }

  // Territory check against this session's active task(s).
  if (planText == null) {
    return 0; // no plan → nothing to enforce (e.g. fresh repo); fail open
  }

  if (active.length === 0) {
    process.stderr.write(
      `[territory-firewall] BLOCKED: unit ${u} has no active (claimed/in_progress/needs_review) task in PLAN.md, ` +
      `so no write territory exists. Claim your assigned task first (atomic claim commit), then implement.`
    );
    return 2;
  }

  const territories = active.flatMap(lib.ownedPathsOf);
  if (territories.length > 0 && lib.pathInAnyGlob(rel, territories)) return 0;

  process.stderr.write(
    `[territory-firewall] BLOCKED: ${rel} is outside your Owned_Paths ` +
    `(${territories.join(', ') || 'none defined'}) for active task(s) ` +
    `${active.map((t) => t.task_id).join(', ')}. Per protocol: never edit outside your territory — ` +
    `not one line, not "just an import". If this file is genuinely required, set Status: blocked with ` +
    `Blocked_Reason: OWNERSHIP_CONFLICT and list the exact paths needed for ORCH to re-carve.`
  );
  return 2;
}

try {
  process.exit(main());
} catch (e) {
  // Fail open: never let a firewall bug block legitimate work; validator is the backstop.
  process.stderr.write(`[territory-firewall] non-fatal hook error (allowing): ${e.message}\n`);
  process.exit(0);
}
