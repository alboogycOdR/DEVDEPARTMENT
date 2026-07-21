#!/usr/bin/env node
/**
 * session-end.js — SessionEnd hook.
 *
 * Closes the §10 loop: on session end, append one audit line to
 * AUTOPILOT_LOG.md recording the unit and the plan state at exit, and refresh
 * the .devteam/CHECKPOINT.md snapshot (same content as pre-compact) so an
 * abruptly-ended session is resumable exactly like a compacted one.
 *
 * Never blocks (exit 0 always). Appends only — never rewrites the log.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const lib = require('./lib.js');

function main() {
  const root = lib.repoRoot();
  const u = lib.unit() || 'UNKNOWN'; // null (unrecognized unit) is fine here — these hooks only label output
  const ts = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');

  let summary = 'no PLAN.md';
  const planPath = path.join(root, 'PLAN.md');
  if (fs.existsSync(planPath)) {
    const tasks = lib.parsePlan(fs.readFileSync(planPath, 'utf-8'))
      .filter((t) => !((t.fields.Title || '').toUpperCase().includes('EXAMPLE')));
    const counts = {};
    for (const t of tasks) {
      const s = (t.fields.Status || 'unknown').trim();
      counts[s] = (counts[s] || 0) + 1;
    }
    summary = Object.entries(counts).map(([k, v]) => `${k}:${v}`).join(' ') || 'empty plan';
  }

  try {
    fs.appendFileSync(
      path.join(root, 'AUTOPILOT_LOG.md'),
      `- [${ts}] SESSION_END unit=${u} plan={${summary}}\n`,
      'utf-8'
    );
  } catch (_e) { /* log write failure is never fatal */ }

  // Refresh checkpoint via the pre-compact writer (same §10 resume shape).
  try {
    const { execFileSync } = require('child_process');
    execFileSync(process.execPath, [path.join(__dirname, 'pre-compact.js')], {
      env: process.env, input: '', stdio: ['pipe', 'ignore', 'ignore'],
    });
  } catch (_e) { /* best effort */ }

  return 0;
}

try { process.exit(main()); } catch (e) {
  process.stderr.write(`[session-end] non-fatal: ${e.message}\n`);
  process.exit(0);
}
