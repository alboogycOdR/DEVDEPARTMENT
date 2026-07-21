#!/usr/bin/env node
/**
 * lib.js — Shared utilities for DEVDEPARTMENT hooks.
 *
 * Design notes:
 *  - Zero dependencies (Node stdlib only) for maximum portability (Windows/macOS/Linux).
 *  - PLAN.md parsing mirrors scripts/validate_plan.py field grammar — if the grammar
 *    changes there, change it here too.
 *  - Glob semantics mirror validate_plan.py globs_intersect(): prefix-before-wildcard,
 *    path-prefix containment. Conservative: unknown → not owned.
 */
'use strict';

const fs = require('fs');
const path = require('path');

/** Repo root: Claude Code sets CLAUDE_PROJECT_DIR; fall back to cwd. */
function repoRoot() {
  return process.env.CLAUDE_PROJECT_DIR || process.cwd();
}

/** Known unit IDs: ORCH + every unit DEFINED in autopilot.json's builders
 * registry (both shapes: legacy flat array -> that list; registry object ->
 * Object.keys(defined)). Fail-safe to the legacy roster on any read/parse
 * error — same per-call readFileSync pattern as controlMode() below. */
function knownUnits() {
  const legacy = ['ORCH', 'GB', 'CX', 'S5'];
  try {
    const raw = fs.readFileSync(path.join(repoRoot(), 'autopilot.json'), 'utf-8');
    const b = (JSON.parse(raw) || {}).builders;
    if (Array.isArray(b)) return ['ORCH', ...b.map((x) => String(x).toUpperCase())];
    if (b && typeof b === 'object' && b.defined && typeof b.defined === 'object') {
      return ['ORCH', ...Object.keys(b.defined).map((x) => x.toUpperCase())];
    }
    return legacy;
  } catch (_e) {
    return legacy;
  }
}

/** Current unit identity via DEVTEAM_UNIT env (default ORCH).
 *
 * v4.7 SECURITY FIX — this used to silently coerce any UNRECOGNIZED unit to
 * 'ORCH', i.e. a typo'd or not-yet-registered DEVTEAM_UNIT got UNRESTRICTED
 * permissions: the exact opposite of what a permission layer should fail
 * toward. Now: an unset DEVTEAM_UNIT still means ORCH (interactive
 * sessions), but a SET-yet-unknown one returns null, and the firewall
 * treats null as deny (exit 2 through its normal verdict path).
 *
 * Deliberately NOT a thrown exception: the firewall's outer try/catch
 * converts any exception into fail-open ("non-fatal hook error (allowing)")
 * by design, so a throw here would silently re-create the fail-open bug
 * this fix removes. The deny must flow through the normal return-2 path. */
function unit() {
  const raw = process.env.DEVTEAM_UNIT;
  if (!raw) return 'ORCH';
  const u = String(raw).toUpperCase();
  return knownUnits().includes(u) ? u : null;
}

/** Read hook input JSON from stdin (Claude Code hook contract). */
function readStdinJson() {
  try {
    const raw = fs.readFileSync(0, 'utf-8');
    return raw.trim() ? JSON.parse(raw) : {};
  } catch (_e) {
    return {};
  }
}

/** Normalize a filesystem path to a repo-relative, forward-slash path. */
function relPath(p) {
  if (!p) return '';
  let abs = path.isAbsolute(p) ? p : path.join(repoRoot(), p);
  let rel = path.relative(repoRoot(), abs);
  return rel.split(path.sep).join('/');
}

/** Parse PLAN.md into an array of task objects {task_id, fields:{}}. */
function parsePlan(planText) {
  const tasks = [];
  let current = null;
  let currentField = null;
  const headerRe = /^###\s+(TASK-[A-Za-z0-9-]+)\s*$/;
  const fieldRe = /^\*\*([A-Za-z_]+):\*\*\s*(.*)$/;

  for (const line of planText.split(/\r?\n/)) {
    const h = line.trim().match(headerRe);
    if (h) {
      current = { task_id: h[1], fields: {} };
      tasks.push(current);
      currentField = null;
      continue;
    }
    if (!current) continue;
    const f = line.trim().match(fieldRe);
    if (f) {
      currentField = f[1];
      current.fields[currentField] = f[2].trim();
    } else if (currentField && line.trim()) {
      current.fields[currentField] += '\n' + line.trimEnd();
    }
  }
  return tasks;
}

const EMPTY = new Set(['', '—', '-', '--', 'n/a', 'none']);
const ACTIVE = new Set(['claimed', 'in_progress', 'needs_review']);

function ownedPathsOf(task) {
  const raw = (task.fields.Owned_Paths || '').trim();
  if (EMPTY.has(raw.toLowerCase())) return [];
  return raw.split(/[,\n]/).map((s) => s.trim()).filter((s) => s && !EMPTY.has(s.toLowerCase()));
}

/** Active tasks for a given unit. */
function activeTasksFor(tasks, unitId) {
  return tasks.filter(
    (t) => (t.fields.Assigned_To || '').trim() === unitId && ACTIVE.has((t.fields.Status || '').trim())
  );
}

/** Prefix of a glob before the first wildcard char, trimmed of trailing slash. */
function globPrefix(glob) {
  const i = glob.split('').findIndex((ch) => '*?['.includes(ch));
  const pre = i === -1 ? glob : glob.slice(0, i);
  return pre.replace(/\/+$/, '');
}

/** Does repo-relative filePath fall under glob territory? (prefix containment) */
function pathInGlob(filePath, glob) {
  const pre = globPrefix(glob.trim());
  if (!pre) return true; // bare wildcard owns everything
  const fp = filePath.replace(/\/+$/, '');
  return fp === pre || fp.startsWith(pre + '/');
}

function pathInAnyGlob(filePath, globs) {
  return globs.some((g) => pathInGlob(filePath, g));
}

/**
 * Protected paths per protocol (builders must never write these).
 * PLAN.md is handled separately: in control.mode=legacy builders may edit
 * it (their own block only — block-level enforcement stays with
 * validate_plan.py + review; file-level hooks cannot see which block is
 * edited reliably across Edit payload shapes). In control.mode=strict
 * (Wave I) PLAN.md joins this list — see territory-firewall.js.
 */
const PROTECTED_FOR_BUILDERS = [
  'specs/**', 'AGENTS.md', 'CLAUDE.md', 'docs/**', 'REVIEW.md',
  '.claude/**', '.codex/**', 'scripts/**', 'hooks/**', 'briefings/**', 'onboard.md',
  'autopilot.json', 'AUTOPILOT_LOG.md', 'deploy/**',
  'INSTINCTS.md', '.devteam/pending_amendments/**',
];

/**
 * Wave I (I1): control.mode from autopilot.json. Fail-safe: unreadable or
 * missing config -> "legacy" (today's behavior), never "strict" — a broken
 * or absent config must never silently start blocking builder PLAN.md
 * writes they don't know to expect.
 */
function controlMode() {
  try {
    const raw = fs.readFileSync(path.join(repoRoot(), 'autopilot.json'), 'utf-8');
    const cfg = JSON.parse(raw);
    const m = cfg.control && cfg.control.mode;
    return m === 'strict' ? 'strict' : 'legacy';
  } catch (_e) {
    return 'legacy';
  }
}

/**
 * Wave I: resolve the task_id this unit is authorized to report/write a
 * dossier against. .devteam/inflight/<unit>.json (written by dispatch.*'s
 * claim-at-dispatch step) is authoritative when present; falls back to a
 * PLAN.md scan (activeTasksFor) for states where inflight hasn't been
 * written yet (e.g. mid-migration from legacy). Returns null if neither
 * source has an answer.
 */
function activeTaskIdFor(tasks, unitId) {
  try {
    const raw = fs.readFileSync(
      path.join(repoRoot(), '.devteam', 'inflight', `${unitId}.json`), 'utf-8');
    const obj = JSON.parse(raw);
    if (obj && obj.task_id) return obj.task_id;
  } catch (_e) { /* fall through to PLAN.md scan */ }
  const active = activeTasksFor(tasks, unitId);
  return active.length ? active[0].task_id : null;
}

/** Secret patterns (superset of ECC's sk-/ghp_/AKIA idea, tuned to reduce noise). */
const SECRET_PATTERNS = [
  { name: 'Anthropic/OpenAI-style API key', re: /\bsk-[A-Za-z0-9_-]{16,}\b/ },
  { name: 'GitHub token', re: /\bgh[pousr]_[A-Za-z0-9]{20,}\b/ },
  { name: 'AWS access key ID', re: /\bAKIA[0-9A-Z]{16}\b/ },
  { name: 'Google API key', re: /\bAIza[0-9A-Za-z_-]{30,}\b/ },
  { name: 'Slack token', re: /\bxox[baprs]-[A-Za-z0-9-]{10,}\b/ },
  { name: 'Private key block', re: /-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----/ },
  { name: 'Telegram bot token', re: /\b\d{8,10}:AA[A-Za-z0-9_-]{30,}\b/ },
  { name: 'Generic assigned secret', re: /(?:password|passwd|secret|api[_-]?key|auth[_-]?token)\s*[:=]\s*['"][^'"\s]{12,}['"]/i },
];

function findSecrets(text) {
  if (!text) return [];
  const hits = [];
  for (const { name, re } of SECRET_PATTERNS) {
    if (re.test(text)) hits.push(name);
  }
  return hits;
}

/** Extract every text payload that could carry written content from a tool_input. */
function writtenContentOf(toolInput) {
  if (!toolInput) return '';
  const parts = [];
  if (typeof toolInput.content === 'string') parts.push(toolInput.content);
  if (typeof toolInput.new_str === 'string') parts.push(toolInput.new_str);
  if (typeof toolInput.file_text === 'string') parts.push(toolInput.file_text);
  if (Array.isArray(toolInput.edits)) {
    for (const e of toolInput.edits) if (typeof e.new_string === 'string') parts.push(e.new_string);
  }
  if (typeof toolInput.new_string === 'string') parts.push(toolInput.new_string);
  return parts.join('\n');
}

function filePathOf(toolInput) {
  if (!toolInput) return '';
  return toolInput.file_path || toolInput.path || toolInput.notebook_path || '';
}

module.exports = {
  repoRoot, unit, readStdinJson, relPath, parsePlan, ownedPathsOf, activeTasksFor,
  globPrefix, pathInGlob, pathInAnyGlob, PROTECTED_FOR_BUILDERS, findSecrets,
  writtenContentOf, filePathOf, EMPTY, ACTIVE, controlMode, activeTaskIdFor, knownUnits,
};
