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
const { spawnSync } = require('child_process');
const lib = require('./lib.js');

function lastProgressNote(task) {
  const notes = (task.fields.Progress_Notes || '').split('\n').map((s) => s.trim()).filter((s) => s.startsWith('-'));
  return notes.length ? notes[notes.length - 1] : null;
}

function main() {
  const root = lib.repoRoot();
  const main = lib.mainRoot();
  const u = lib.unit() || 'UNKNOWN'; // null (unrecognized unit) is fine here — these hooks only label output
  const lines = [`[devteam session-start] Unit: ${u}. Re-read AGENTS.md and PLAN.md fresh from disk before acting.`];
  const sync = spawnSync(process.env.PYTHON || 'python',
    [path.join(root, 'scripts', 'sync_from_pack.py'), '--behind-pack', '--project', root],
    { encoding: 'utf8', timeout: 5000 });
  if (sync.status === 0 && sync.stdout.trim()) lines.push(`[devteam session-start] ${sync.stdout.trim()}`);

  // E-J.7/E-J.8: report PLAN.md freshness and untagged base-branch work at
  // session start. This is observational and bounded; it must never block a
  // session if Python or Git is unavailable.
  const health = spawnSync(process.env.PYTHON || 'python',
    [path.join(root, 'scripts', 'plan_health.py'), '--repo', main],
    { encoding: 'utf8', timeout: 10000 });
  if (health.status === 0 && health.stdout.trim()) {
    for (const line of health.stdout.trim().split(/\r?\n/)) lines.push(`[devteam session-start] ${line}`);
  }

  if (fs.existsSync(path.join(root, 'STOP'))) {
    lines.push('[devteam session-start] STOP file present — autopilot halted; do not dispatch or auto-merge until it is removed.');
  }

  const checkpointPath = path.join(main, '.devteam', 'CHECKPOINT.md');
  if (fs.existsSync(checkpointPath)) {
    lines.push('[devteam session-start] Unresolved context checkpoint exists at .devteam/CHECKPOINT.md — read it FIRST; it is the §10 resume state from a compacted/ended session. Delete it once resumed.');
  }

  const planPath = path.join(main, 'PLAN.md');
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
    const wanted = (process.env.DEVTEAM_TASK || '').trim();
    let mine = u === 'ORCH'
      ? tasks.filter((t) => ['needs_review', 'blocked'].includes((t.fields.Status || '').trim()))
      : lib.activeTasksFor(tasks, u);
    // E-A.2: the dispatched task wins over "first in_progress block for this unit".
    if (wanted) {
      const pinned = tasks.filter((t) => t.task_id === wanted);
      mine = pinned;
      if (!pinned.length) {
        lines.push(`[devteam session-start] DEVTEAM_TASK=${wanted} is not in the main-checkout PLAN.md — not substituting another task.`);
      }
    }
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
