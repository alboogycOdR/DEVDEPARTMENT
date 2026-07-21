#!/usr/bin/env node
/**
 * session-start.js — SessionStart hook.
 *
 * Automates the "sync & orient" half of Protocol §10: on every new session,
 * inject a compact resume brief (stdout from SessionStart hooks is added to
 * context) so no unit ever starts blind:
 *   - unit identity, STOP-file status
 *   - this unit's active tasks + their last Progress_Note line
 *   - frontmatter orchestrator_notes (ORCH checkpoint from a prior session)
 *   - unresolved checkpoint file from a pre-compact snapshot, if present
 *
 * Never blocks (always exit 0). Keeps output tight — this consumes context.
 */
'use strict';

const fs = require('fs');
const path = require('path');
const lib = require('./lib.js');

function lastProgressNote(task) {
  const notes = (task.fields.Progress_Notes || '').split('\n').map((s) => s.trim()).filter((s) => s.startsWith('-'));
  return notes.length ? notes[notes.length - 1] : null;
}

function main() {
  const root = lib.repoRoot();
  const u = lib.unit() || 'UNKNOWN'; // null (unrecognized unit) is fine here — these hooks only label output
  const lines = [`[devteam session-start] Unit: ${u}. Re-read AGENTS.md and PLAN.md fresh from disk before acting.`];

  if (fs.existsSync(path.join(root, 'STOP'))) {
    lines.push('[devteam session-start] STOP file present — autopilot halted; do not dispatch or auto-merge until it is removed.');
  }

  const checkpointPath = path.join(root, '.devteam', 'CHECKPOINT.md');
  if (fs.existsSync(checkpointPath)) {
    lines.push('[devteam session-start] Unresolved context checkpoint exists at .devteam/CHECKPOINT.md — read it FIRST; it is the §10 resume state from a compacted/ended session. Delete it once resumed.');
  }

  const planPath = path.join(root, 'PLAN.md');
  if (fs.existsSync(planPath)) {
    const text = fs.readFileSync(planPath, 'utf-8');
    const fm = text.match(/^---\s*\n([\s\S]*?)\n---/);
    if (fm) {
      const notes = fm[1].match(/^orchestrator_notes:\s*"?(.*?)"?\s*$/m);
      if (notes && notes[1] && notes[1].trim()) {
        lines.push(`[devteam session-start] orchestrator_notes: ${notes[1].trim().slice(0, 400)}`);
      }
    }
    const tasks = lib.parsePlan(text);
    const mine = u === 'ORCH'
      ? tasks.filter((t) => ['needs_review', 'blocked'].includes((t.fields.Status || '').trim()))
      : lib.activeTasksFor(tasks, u);
    if (mine.length) {
      const label = u === 'ORCH' ? 'Tasks awaiting ORCH (needs_review/blocked)' : 'Your active task(s) — RESUME FIRST per §10a';
      lines.push(`[devteam session-start] ${label}:`);
      for (const t of mine.slice(0, 6)) {
        const note = lastProgressNote(t);
        lines.push(`  - ${t.task_id} [${(t.fields.Status || '').trim()}] ${(t.fields.Title || '').trim().slice(0, 80)}${note ? ` | last note: ${note.slice(0, 160)}` : ''}`);
      }
    }
  }

  process.stdout.write(lines.join('\n') + '\n');
  return 0;
}

try { process.exit(main()); } catch (e) {
  process.stderr.write(`[session-start] non-fatal: ${e.message}\n`);
  process.exit(0);
}
