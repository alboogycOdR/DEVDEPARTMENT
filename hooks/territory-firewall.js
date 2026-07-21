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

const fs = require('fs');
const path = require('path');
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
  if (u === 'ORCH') return 0;

  const rel = lib.relPath(target);
  const mode = lib.controlMode();

  // PLAN.md: legacy mode keeps today's behavior (block-level discipline
  // stays downstream). Strict mode (Wave I): PLAN.md joins the protected
  // set — the supervisor is the sole writer; builders report state via a
  // devteam-control block instead.
  if (rel === 'PLAN.md') {
    if (mode === 'legacy') return 0;
    process.stderr.write(
      `[territory-firewall] BLOCKED: PLAN.md is protected in control.mode=strict (Wave I). ` +
      `The supervisor is the sole writer — emit a devteam-control block as the last thing ` +
      `you print instead of editing PLAN.md directly. See docs/CONTROL.md.`
    );
    return 2;
  }

  // Hard-protected paths first.
  if (lib.pathInAnyGlob(rel, lib.PROTECTED_FOR_BUILDERS)) {
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
    let planTextForDossier = '';
    try {
      planTextForDossier = fs.readFileSync(path.join(lib.repoRoot(), 'PLAN.md'), 'utf-8');
    } catch (_e) { /* no plan yet — activeTaskIdFor's inflight fallback still works */ }
    const tasksForDossier = lib.parsePlan(planTextForDossier);
    const activeId = lib.activeTaskIdFor(tasksForDossier, u);
    if (activeId && rel === `dossiers/${activeId}.md`) return 0;
    process.stderr.write(
      `[territory-firewall] BLOCKED: ${rel} — in control.mode=strict you may only write your ` +
      `own active task's dossier (dossiers/${activeId || '<your-task-id>'}.md), not another task's.`
    );
    return 2;
  }

  // Territory check against this unit's active task(s).
  const planPath = path.join(lib.repoRoot(), 'PLAN.md');
  let planText;
  try {
    planText = fs.readFileSync(planPath, 'utf-8');
  } catch (_e) {
    return 0; // no plan → nothing to enforce (e.g. fresh repo); fail open
  }

  const tasks = lib.parsePlan(planText);
  const active = lib.activeTasksFor(tasks, u);
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
