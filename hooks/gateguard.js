#!/usr/bin/env node
/**
 * gateguard.js — PreToolUse fact-forcing gate (Edit|Write|MultiEdit|NotebookEdit|Bash).
 *
 * Ported from ECC's `gateguard` skill (github.com/affaan-m/ECC, MIT) — design only.
 * ECC's own hook is ~1300 lines and depends on its internal shell-substitution and
 * heredoc libs; this is a from-scratch implementation on our hooks/lib.js conventions,
 * with the fact prompts specialized to the Coordination Protocol (Owned_Paths,
 * Acceptance_Criteria, Spec_References) rather than ECC's generic wording.
 *
 * WHY THIS EXISTS (and why it is not "are you sure?"):
 *   territory-firewall.js answers "MAY this unit touch this path?".
 *   It never answers "does this unit UNDERSTAND this file?".
 *   Asking a model to self-assess ("are you sure?") reliably returns "yes".
 *   Asking it to LIST EVERY IMPORTER forces an actual Grep/Read — and the
 *   investigation is what changes the output. Deny -> force -> allow on retry.
 *
 * The gate never verifies the facts. It cannot, and does not try. The single
 * denial is the entire mechanism: the model must go look before it may write.
 *
 * SCOPE: builders only by default. ORCH is interactive and human-supervised, and
 * gating it would fire on every PLAN.md status write. Opt ORCH in via GATEGUARD_UNITS.
 *
 * Exit codes per Claude Code hook contract: 0 = allow, 2 = BLOCK (stderr -> model).
 * Fail-open on ANY unexpected error, including unwritable state — a gate that
 * cannot record "already asked" would otherwise deny the same edit forever.
 *
 * Env controls (see docs/GATEGUARD.md):
 *   GATEGUARD=off|0|false|disabled     disable entirely
 *   GATEGUARD_UNITS=ORCH,GB,CX         which units to gate (default: all non-ORCH)
 *   GATEGUARD_EXEMPT_GLOBS=a,b         skip first-touch gating for these paths
 *   GATEGUARD_FULL_DENIALS=3           full fact-block denials before condensing
 *   GATEGUARD_BASH_ROUTINE_DISABLED=1  drop routine-Bash gate; destructive stays
 *   GATEGUARD_EXTRA_DESTRUCTIVE=re     extra destructive-command regex source
 *   GATEGUARD_STATE_DIR=path           default .devteam/gateguard
 */
'use strict';

const fs = require('fs');
const path = require('path');
const lib = require('./lib.js');

const OFF_VALUES = new Set(['0', 'false', 'off', 'disabled', 'disable', 'no']);
const ON_VALUES = new Set(['1', 'true', 'on', 'enabled', 'enable', 'yes']);

const SESSION_TIMEOUT_MS = 30 * 60 * 1000; // stale state is discarded, not resumed
const MAX_ENTRIES = 500;                   // bound state growth in long sessions
const ROUTINE_BASH_KEY = '__routine_bash__';

function envOn(name) {
  const raw = String(process.env[name] || '').trim().toLowerCase();
  return ON_VALUES.has(raw);
}

function disabled() {
  const raw = String(process.env.GATEGUARD || '').trim().toLowerCase();
  return raw !== '' && OFF_VALUES.has(raw);
}

/**
 * Destructive Bash patterns. Split from the SQL/dd set because shell commands
 * carry flag-ordering variation (`rm -rf` vs `rm -f -r`) that a single stable
 * phrase regex handles badly.
 */
const DESTRUCTIVE_SHELL = [
  /\brm\s+(-[a-z]*\s+)*-[a-z]*[rf]/i,
  /\bgit\s+reset\s+--hard\b/i,
  /\bgit\s+clean\s+(-[a-z]*\s+)*-[a-z]*[fdx]/i,
  /\bgit\s+push\s+.*(--force\b|--force-with-lease\b|\s-f\b)/i,
  /\bgit\s+branch\s+(-D|--delete\s+--force)\b/i,
  /\bmkfs\b|\bshred\b|\bchmod\s+(-R\s+)?000\b/i,
  /\bRemove-Item\b[\s\S]*-Recurse/i,
];
const DESTRUCTIVE_SQL_DD = /\b(drop\s+table|drop\s+database|delete\s+from|truncate\s+table|dd\s+if=)\b/i;

let extraKey = null;
let extraRe = null;
let extraWarned = false;
/** Operator-supplied extra patterns. Malformed regex => treated as unset (built-ins
 *  still apply) and reported once; a config typo must never crash a tool call. */
function extraDestructive() {
  const raw = process.env.GATEGUARD_EXTRA_DESTRUCTIVE || '';
  if (raw === extraKey) return extraRe;
  extraKey = raw;
  extraWarned = false;
  if (!raw) { extraRe = null; return null; }
  try {
    extraRe = new RegExp(raw, 'i');
  } catch (e) {
    extraRe = null;
    if (!extraWarned) {
      process.stderr.write('[gateguard] ignoring invalid GATEGUARD_EXTRA_DESTRUCTIVE: ' + e.message + '\n');
      extraWarned = true;
    }
  }
  return extraRe;
}

/**
 * Strip quoted strings and heredoc bodies before destructive matching, so
 * `git commit -m "drop table migration"` is not read as a destructive command.
 * Deliberately crude: this is a heuristic gate, and over-stripping only ever
 * makes it quieter, never more permissive about an actually-unquoted `rm -rf`.
 */
function stripQuoted(cmd) {
  return String(cmd || '')
    .replace(/<<-?\s*'?([A-Za-z_][A-Za-z0-9_]*)'?[\s\S]*?^\s*\1\s*$/gm, ' ')
    .replace(/'[^']*'/g, ' ')
    .replace(/"[^"]*"/g, ' ');
}

function isDestructive(cmd) {
  const c = stripQuoted(cmd);
  if (DESTRUCTIVE_SQL_DD.test(c)) return true;
  if (DESTRUCTIVE_SHELL.some(function (re) { return re.test(c); })) return true;
  const x = extraDestructive();
  return x ? x.test(c) : false;
}

/** Glob matcher for exemptions: `**` across segments, `*` within one, `?` one char.
 *  Fail-open — a malformed pattern is dropped rather than thrown. */
function exemptMatchers() {
  return String(process.env.GATEGUARD_EXEMPT_GLOBS || '')
    .split(',').map(function (s) { return s.trim(); }).filter(Boolean)
    .map(function (g) {
      try {
        const src = g.replace(/[.+^${}()|[\]\\]/g, '\\$&')
          .split('**').map(function (seg) { return seg.replace(/\*/g, '[^/]*').replace(/\?/g, '.'); })
          .join('.*');
        return new RegExp(src);
      } catch (_e) { return null; }
    })
    .filter(Boolean);
}

function isExempt(rel) {
  const p = rel.replace(/\\/g, '/').toLowerCase();
  return exemptMatchers().some(function (re) { return re.test(p); });
}

// ---------------------------------------------------------------------------
// Session state
// ---------------------------------------------------------------------------

function stateFile(sessionId) {
  const dir = process.env.GATEGUARD_STATE_DIR ||
    path.join(lib.repoRoot(), '.devteam', 'gateguard');
  const safe = String(sessionId || 'default').replace(/[^A-Za-z0-9_-]/g, '_').slice(0, 64);
  return path.join(dir, safe + '.json');
}

function loadState(file) {
  try {
    const st = JSON.parse(fs.readFileSync(file, 'utf-8'));
    if (Date.now() - (st.updated || 0) > SESSION_TIMEOUT_MS) return { checked: {}, denials: 0 };
    return { checked: st.checked || {}, denials: st.denials || 0 };
  } catch (_e) {
    return { checked: {}, denials: 0 };
  }
}

/** Returns false if state could not be persisted — caller must then ALLOW,
 *  because an unrecordable denial would repeat forever on every retry. */
function saveState(file, state) {
  try {
    const keys = Object.keys(state.checked);
    if (keys.length > MAX_ENTRIES) {
      // Drop oldest by recorded timestamp; keep the gate bounded, not exact.
      keys.sort(function (a, b) { return state.checked[a] - state.checked[b]; })
        .slice(0, keys.length - MAX_ENTRIES)
        .forEach(function (k) { delete state.checked[k]; });
    }
    state.updated = Date.now();
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, JSON.stringify(state), 'utf-8');
    return true;
  } catch (_e) {
    return false;
  }
}

/**
 * Best-effort per-unit denial counter at .devteam/gateguard/denials/<UNIT>.json,
 * read by supervisor.py's circuit breaker (scripts/circuit_breaker.py,
 * ported from ralph-claude-code) as one of two independent stagnation
 * signals — a unit repeatedly denied without ever landing a diff is a
 * stronger, faster stall signal than mere silence. Deliberately NOT this
 * hook's job to ever DECREASE it: only supervisor.py resets it, the instant
 * it observes real progress for the unit's task. Bumped only on destructive-
 * Bash and file comprehension denials — never on the once-per-session
 * routine-Bash gate, which is a trivial formality, not evidence of a stall.
 * Fail-open and silent: telemetry must never affect the gate's own verdict
 * or be allowed to throw.
 */
function bumpDenialCounter(unit) {
  try {
    const dir = path.join(lib.repoRoot(), '.devteam', 'gateguard', 'denials');
    const file = path.join(dir, unit + '.json');
    let count = 0;
    try { count = JSON.parse(fs.readFileSync(file, 'utf-8')).count || 0; } catch (_e) { /* start at 0 */ }
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(file, JSON.stringify({ count: count + 1, last: new Date().toISOString() }), 'utf-8');
  } catch (_e) { /* best-effort telemetry only — never affects the gate */ }
}

// ---------------------------------------------------------------------------
// Fact blocks
// ---------------------------------------------------------------------------

function editFacts(rel, unitId, taskId) {
  return [
    '[gateguard] BLOCKED (first touch): present these facts, then retry the same edit — it will be allowed.',
    '',
    'Before editing ' + rel + ':',
    '  1. List EVERY file that imports/requires/includes ' + rel + ' (Grep or Glob the tree — do not guess).',
    '  2. Name the public functions/classes/exports this edit changes, and who calls them.',
    '  3. If this file reads or writes data (JSON, CSV, DB, PLAN.md fields), show the actual',
    '     field names, structure and date format from a real sample — redacted values, real shape.',
    '  4. Quote the Acceptance_Criterion of ' + (taskId || 'your active task') + ' that this edit satisfies, verbatim.',
    '  5. Confirm every importer you listed in (1) is inside your Owned_Paths. If any is not,',
    '     that is a territory boundary: stop and set Status: blocked (OWNERSHIP_CONFLICT).',
    '',
    'This is not a permission check — ' + unitId + ' may write here. It is a comprehension check.',
    'Answer from tool output you actually ran, not from memory of the file.',
  ].join('\n');
}

function writeFacts(rel, taskId) {
  return [
    '[gateguard] BLOCKED (new file): present these facts, then retry — it will be allowed.',
    '',
    'Before creating ' + rel + ':',
    '  1. Name the exact file(s) and line(s) that will call into this new file.',
    '  2. Confirm no existing file already serves this purpose (search the tree; say what you searched).',
    '  3. State which Owned_Path glob of ' + (taskId || 'your active task') + ' covers this new path.',
    '  4. Quote the Acceptance_Criterion that requires a NEW file rather than a change to an existing one.',
  ].join('\n');
}

function destructiveFacts(cmd) {
  return [
    '[gateguard] BLOCKED (destructive command): present these facts, then retry.',
    '',
    'Command: ' + String(cmd).slice(0, 400),
    '  1. List every file, branch, or table this will modify or delete — enumerate them, do not describe them.',
    '  2. Give a one-line rollback procedure. If there is none, say so explicitly.',
    '  3. Quote the instruction (task Description or ORCH note) that requires this destruction, verbatim.',
    '',
    'Destructive commands are gated EVERY time, not once per session.',
  ].join('\n');
}

function routineFacts() {
  return [
    '[gateguard] BLOCKED (first Bash of session): present these facts, then retry.',
    '',
    '  1. State your active task ID and its goal in one sentence.',
    '  2. State what this specific command verifies or produces, and what output would mean failure.',
    '',
    'Routine Bash is gated once per session only. This will not fire again.',
  ].join('\n');
}

function condensed(ordinal, what) {
  return '[gateguard] BLOCKED (denial #' + ordinal + ', condensed): investigate ' + what +
    ' — importers, affected API, data shape, governing acceptance criterion — then retry. ' +
    'Full checklist: docs/GATEGUARD.md';
}

// ---------------------------------------------------------------------------

function main() {
  if (disabled()) return 0;

  const input = lib.readStdinJson();
  const tool = input.tool_name || '';
  const toolInput = input.tool_input || {};

  const u = lib.unit();
  if (u === null) return 0; // territory-firewall owns the unknown-unit denial; don't double-block

  // Which units are gated. Default: every builder, never ORCH.
  const configured = String(process.env.GATEGUARD_UNITS || '').trim();
  if (configured) {
    const allow = configured.split(',').map(function (s) { return s.trim().toUpperCase(); }).filter(Boolean);
    if (!allow.includes(u)) return 0;
  } else if (u === 'ORCH') {
    return 0;
  }

  const file = stateFile(input.session_id);
  const state = loadState(file);
  const parsed = parseInt(process.env.GATEGUARD_FULL_DENIALS || '3', 10);
  const fullBudget = Number.isFinite(parsed) && parsed >= 0 ? parsed : 3;

  let key = null;
  let message = null;
  let subject = null;

  if (tool === 'Bash') {
    const cmd = toolInput.command || '';
    if (!cmd) return 0;
    if (isDestructive(cmd)) {
      // Never memoized: destructive commands gate every single time.
      state.denials += 1;
      if (!saveState(file, state)) return 0;
      bumpDenialCounter(u);
      process.stderr.write(
        state.denials > fullBudget ? condensed(state.denials, 'this destructive command') : destructiveFacts(cmd)
      );
      return 2;
    }
    if (envOn('GATEGUARD_BASH_ROUTINE_DISABLED')) return 0;
    if (state.checked[ROUTINE_BASH_KEY]) return 0;
    key = ROUTINE_BASH_KEY;
    subject = 'this command';
    message = routineFacts();
  } else {
    const target = lib.filePathOf(toolInput);
    if (!target) return 0;
    const rel = lib.relPath(target);
    if (isExempt(rel)) return 0;
    key = 'file:' + rel;
    if (state.checked[key]) return 0; // already gated this file this session

    let taskId = null;
    try {
      const planText = fs.readFileSync(path.join(lib.repoRoot(), 'PLAN.md'), 'utf-8');
      taskId = lib.activeTaskIdFor(lib.parsePlan(planText), u);
    } catch (_e) { /* no plan yet — prompts fall back to generic wording */ }

    const isNew = !fs.existsSync(path.join(lib.repoRoot(), rel));
    subject = rel;
    message = isNew ? writeFacts(rel, taskId) : editFacts(rel, u, taskId);
  }

  state.checked[key] = Date.now();
  state.denials += 1;
  if (tool !== 'Bash') bumpDenialCounter(u); // file comprehension denial — routine-Bash is not a stall signal
  // If we cannot persist "already asked", allow — otherwise the retry we just
  // demanded would hit the identical denial and the unit would loop forever.
  if (!saveState(file, state)) {
    process.stderr.write(
      '[gateguard] state not persistable (' + stateFile(input.session_id) + '); allowing rather than ' +
      'looping. Set GATEGUARD_STATE_DIR to a writable path.\n'
    );
    return 0;
  }

  process.stderr.write(state.denials > fullBudget ? condensed(state.denials, subject) : message);
  return 2;
}

try {
  process.exit(main());
} catch (e) {
  // Fail open, always. A comprehension gate must never be able to brick a build.
  process.stderr.write('[gateguard] non-fatal hook error (allowing): ' + e.message + '\n');
  process.exit(0);
}
