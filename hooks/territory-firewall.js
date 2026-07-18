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
  if (u === 'ORCH') return 0;

  const rel = lib.relPath(target);

  // PLAN.md itself is writable by builders (own-block discipline enforced downstream).
  if (rel === 'PLAN.md') return 0;

  // Hard-protected paths first.
  if (lib.pathInAnyGlob(rel, lib.PROTECTED_FOR_BUILDERS)) {
    process.stderr.write(
      `[territory-firewall] BLOCKED: ${rel} is a protected path (protocol hard prohibition for ${u}). ` +
      `Do not modify it. If you believe you need this file, set your task to blocked ` +
      `(Blocked_Reason: OWNERSHIP_CONFLICT) with a Progress_Note explaining exactly why.`
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
