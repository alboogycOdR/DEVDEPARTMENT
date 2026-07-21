#!/usr/bin/env node
/**
 * pre-compact.js — PreCompact hook.
 *
 * Automates Protocol §10b: the moment Claude Code is about to compact the
 * context window, snapshot the recoverable state to .devteam/CHECKPOINT.md so
 * a fresh or compacted session can resume without guesswork. The companion
 * session-start hook surfaces this file on the next session.
 *
 * Deliberately writes to .devteam/ (a scratch dir), NOT PLAN.md — hooks must
 * never race builders/ORCH on the blackboard. PLAN.md checkpoints remain a
 * deliberate ORCH action; this file is the safety net underneath it.
 *
 * Never blocks (exit 0 always).
 */
'use strict';

const fs = require('fs');
const path = require('path');
const lib = require('./lib.js');

function main() {
  const root = lib.repoRoot();
  const u = lib.unit() || 'UNKNOWN'; // null (unrecognized unit) is fine here — these hooks only label output
  const dir = path.join(root, '.devteam');
  fs.mkdirSync(dir, { recursive: true });

  const ts = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
  const lines = [
    `# DEVDEPARTMENT Context Checkpoint (auto, PreCompact)`,
    ``,
    `- **When:** ${ts}`,
    `- **Unit:** ${u}`,
    `- **Why:** context window compaction imminent (§10b safety net).`,
    ``,
    `## Resume procedure`,
    `1. Re-read AGENTS.md, then PLAN.md, fresh from disk (files are the truth).`,
    `2. ${u === 'ORCH'
      ? 'Check orchestrator_notes + needs_review/blocked tasks; continue the wave from there.'
      : 'Find your Assigned_To task with Status in_progress/claimed; re-read its last Progress_Note and continue on the existing branch (do NOT re-claim or re-branch).'}`,
    `3. Verify working tree state with git status / git log before writing anything.`,
    `4. Delete this file once resumed.`,
    ``,
  ];

  const planPath = path.join(root, 'PLAN.md');
  if (fs.existsSync(planPath)) {
    const tasks = lib.parsePlan(fs.readFileSync(planPath, 'utf-8'));
    const active = u === 'ORCH'
      ? tasks.filter((t) => ['claimed', 'in_progress', 'needs_review', 'blocked'].includes((t.fields.Status || '').trim()))
      : lib.activeTasksFor(tasks, u);
    if (active.length) {
      lines.push(`## Active state snapshot at compaction`);
      for (const t of active) {
        lines.push(`- ${t.task_id} | ${(t.fields.Status || '').trim()} | ${(t.fields.Assigned_To || '').trim()} | branch ${(t.fields.Branch || '—').trim()}`);
      }
      lines.push(``);
    }
  }

  fs.writeFileSync(path.join(dir, 'CHECKPOINT.md'), lines.join('\n'), 'utf-8');
  process.stdout.write(`[pre-compact] §10b checkpoint written to .devteam/CHECKPOINT.md\n`);
  return 0;
}

try { process.exit(main()); } catch (e) {
  process.stderr.write(`[pre-compact] non-fatal: ${e.message}\n`);
  process.exit(0);
}
