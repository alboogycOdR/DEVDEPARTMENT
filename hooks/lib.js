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
const { execFileSync } = require('child_process');

/** Repo root: Claude Code sets CLAUDE_PROJECT_DIR; fall back to cwd. */
function repoRoot() {
  return process.env.CLAUDE_PROJECT_DIR || process.cwd();
}

/**
 * Main checkout (LOOP_HYGIENE E-A.1). Linked worktrees share one git dir;
 * `git rev-parse --git-common-dir` points at it, and its parent is the
 * checkout that holds the live PLAN.md / autopilot.json / .devteam.
 * Fallback is repoRoot() when git cannot answer (fixture repos, no git).
 * Memoized per process and per repoRoot() so a hook that reads PLAN.md,
 * autopilot.json and .devteam does not spawn git three times.
 */
const _mainRootMemo = new Map();

function computeMainRoot() {
  try {
    let out = execFileSync('git', ['-C', repoRoot(), 'rev-parse', '--git-common-dir'], {
      encoding: 'utf-8',
      stdio: ['ignore', 'pipe', 'ignore'],
      timeout: 5000,
      windowsHide: true,
    });
    out = String(out || '').replace(/\r/g, '').trim();
    if (!out) return repoRoot();
    const common = path.isAbsolute(out) ? out : path.resolve(repoRoot(), out);
    const main = path.resolve(common, '..');
    if (!main || !fs.existsSync(main)) return repoRoot();
    return main;
  } catch (_e) {
    return repoRoot();
  }
}

function mainRoot() {
  const key = repoRoot();
  if (_mainRootMemo.has(key)) return _mainRootMemo.get(key);
  const value = computeMainRoot();
  _mainRootMemo.set(key, value);
  return value;
}

/** autopilot.json and autopilot.local.json are human-session files (E-A.3). */
function isHumanOnlyConfig(rel) {
  return rel === 'autopilot.json' || rel === 'autopilot.local.json';
}

/** True when this process is a builder or a delegated headless session. */
function sessionIsDelegated() {
  const unitSet = typeof process.env.DEVTEAM_UNIT === 'string' && process.env.DEVTEAM_UNIT.trim() !== '';
  return unitSet || process.env.DEVTEAM_DELEGATED === '1';
}

/** Known unit IDs: ORCH + every unit DEFINED in autopilot.json's builders
 * registry (both shapes: legacy flat array -> that list; registry object ->
 * Object.keys(defined)). Fail-safe to the legacy roster on any read/parse
 * error — same per-call readFileSync pattern as controlMode() below. */
function knownUnits() {
  const legacy = ['ORCH', 'GB', 'CX', 'S5'];
  try {
    const raw = fs.readFileSync(path.join(mainRoot(), 'autopilot.json'), 'utf-8');
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

function splitPathField(raw) {
  const text = (raw || '').trim();
  if (EMPTY.has(text.toLowerCase())) return [];
  // ' (new)' marks a path that must not exist yet (LOOP_HYGIENE E-F.5); it is an
  // annotation, never part of the glob, or a builder is denied its own new file.
  return text.split(/[,\n]/).map((s) => s.trim().replace(/\s+\(new\)$/i, ''))
    .filter((s) => s && !EMPTY.has(s.toLowerCase()));
}

function ownedPathsOf(task) {
  return splitPathField(task.fields.Owned_Paths || '');
}

/** ORCH-written per-task carve-outs. Empty unless the task is active. */
function protectedGrantsOf(task) {
  const status = (task.fields.Status || '').trim();
  if (!ACTIVE.has(status)) return [];
  return splitPathField(task.fields.Protected_Grants || '');
}

/**
 * Port of validate_plan.grant_within_owned. Identical semantics:
 * equal tokens pass; a more-specific grant passes when an owned glob
 * covers it (`hooks/lib.js` under `hooks/**`); a wider grant fails
 * (`scripts/**` is not inside `scripts/validate_plan.py`); a
 * bare-wildcard grant fails; a bare-wildcard owned entry covers every grant.
 */
function grantWithinOwned(grant, owned) {
  if (!Array.isArray(owned)) return false;
  if (owned.includes(grant)) return true;
  const gp = globPrefix(grant);
  if (!gp) return false;
  for (const entry of owned) {
    const op = globPrefix(entry);
    if (!op) return true;
    if (gp === op || gp.startsWith(op + '/')) return true;
  }
  return false;
}

/** Active-task grants that actually sit inside that task's Owned_Paths. */
function effectiveProtectedGrantsOf(task) {
  const owned = ownedPathsOf(task);
  return protectedGrantsOf(task).filter((g) => grantWithinOwned(g, owned));
}

/** Active tasks for a given unit. */
function activeTasksFor(tasks, unitId) {
  return tasks.filter(
    (t) => (t.fields.Assigned_To || '').trim() === unitId && ACTIVE.has((t.fields.Status || '').trim())
  );
}

/**
 * Tasks this session may write under. DEVTEAM_TASK, when set, is the only
 * active task (E-A.2) — never "whichever in_progress block comes first".
 * An unknown or inactive id yields no territory rather than a fallback.
 */
function sessionTasksFor(tasks, unitId) {
  const wanted = (process.env.DEVTEAM_TASK || '').trim();
  if (wanted) {
    return tasks.filter(
      (t) => t.task_id === wanted
        && (t.fields.Assigned_To || '').trim() === unitId
        && ACTIVE.has((t.fields.Status || '').trim())
    );
  }
  return activeTasksFor(tasks, unitId);
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
  // NOTE: 'scripts/**' is blanket-protected because it holds ORCH's own machinery
  // (dispatch, plan_commit, validate_plan, preflight). Some projects also keep PRODUCT
  // scripts there, which are ordinary code a builder may legitimately be asked to fix.
  // Rather than weaken the glob, grant those specific files per-task via
  // PROTECTED_EXCEPTIONS below, so the default stays deny and each grant is deliberate
  // and visible in this file's history.
  'autopilot.json', 'AUTOPILOT_LOG.md', 'deploy/**',
  'INSTINCTS.md', '.devteam/pending_amendments/**',
];

/**
 * Exception matcher. Deliberately NOT pathInGlob: that one is prefix-based
 * (it truncates at the first wildcard and prefix-matches), which cannot express
 * "these files inside this directory" — exactly the shape an exception needs.
 * pathInGlob is load-bearing for PROTECTED_FOR_BUILDERS and Owned_Paths, so it
 * is left alone and exceptions get their own literal-glob matcher instead.
 */
function pathMatchesGlobExact(filePath, glob) {
  const rx = new RegExp(
    '^' + glob.trim().split('*').map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('[^/]*') + '$'
  );
  return rx.test(filePath.replace(/\/+$/, ''));
}

function pathInAnyException(filePath, globs) {
  return globs.some((g) => pathMatchesGlobExact(filePath, g));
}

const PROTECTED_EXCEPTIONS = [
  // TOWER pack wave (plan v4.1+) COMPLETE -- TASK-013 through TASK-018 all
  // done and merged; those grants were removed. TASK-019/TASK-020 done,
  // their scripts/tower_sync.py grant removed. Active grant below.
  // TASK-021 done 2026-08-28 (notify.py + tower_sync.py grammar) — grants
  // removed. TASK-022 done 2026-08-29 (autopilot.local.json override layer),
  // its scripts/supervisor.py + docs/SYNC.md grant removed. List is EMPTY
  // again, which is the correct resting state.
  // EMPTY BY DEFAULT. The pack ships the mechanism, not anyone's carve-outs.
  // Add an entry only when a specific path is (a) matched by a protected glob and
  // (b) genuinely not ORCH machinery -- e.g. product scripts that happen to live in
  // scripts/. Grant it per-task, keep the list short, and delete the entry when the
  // task is done. If this list grows, the protected globs are drawn wrong and should
  // be re-cut instead of exempted around.
  //
  // Example (from the project this came out of, where builders had to fix product
  // CAPS extractors that live alongside dispatch/plan_commit in scripts/):
  //   'scripts/extract_caps_taxonomy*.py',
  //
  // Wave E first dispatch (plan v6.0, 2026-09-26). Remove each when its task is done;
  // TASK-027's Protected_Grants field retires this ritual.
  // TASK-026 (CX):
  'scripts/sync_from_pack.py',
  // TASK-027 (GB):
  'hooks/**', 'scripts/validate_plan.py', 'scripts/dispatch.sh', 'scripts/dispatch.ps1',
  // TASK-028 (CX, from S5):
  'scripts/supervisor.py', 'scripts/status_digest.py',
];

/**
 * Wave I (I1): control.mode from autopilot.json. Fail-safe: unreadable or
 * missing config -> "legacy" (today's behavior), never "strict" — a broken
 * or absent config must never silently start blocking builder PLAN.md
 * writes they don't know to expect.
 */
function controlMode() {
  try {
    const raw = fs.readFileSync(path.join(mainRoot(), 'autopilot.json'), 'utf-8');
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
  const wanted = (process.env.DEVTEAM_TASK || '').trim();
  if (wanted) return wanted;
  try {
    const raw = fs.readFileSync(
      path.join(mainRoot(), '.devteam', 'inflight', `${unitId}.json`), 'utf-8');
    const obj = JSON.parse(raw);
    if (obj && obj.task_id) return obj.task_id;
  } catch (_e) { /* fall through to PLAN.md scan */ }
  const active = activeTasksFor(tasks, unitId);
  return active.length ? active[0].task_id : null;
}

function readPlanText() {
  try {
    return fs.readFileSync(path.join(mainRoot(), 'PLAN.md'), 'utf-8');
  } catch (_e) {
    return null;
  }
}

function firstString(obj, keys) {
  if (!obj) return '';
  for (const k of keys) {
    if (typeof obj[k] === 'string') return obj[k];
  }
  return '';
}

function parsePlanLoose(text) {
  const raw = text || '';
  const src = /(?:^|\n)###\s+TASK-/.test(raw) ? raw : (`### TASK-SNIP\n${raw}`);
  return parsePlan(src);
}

/** Canonical Owned_Paths / Protected_Grants values for before/after comparison. */
function orchOnlySnapshot(text) {
  return parsePlanLoose(text).map((t) => ({
    id: t.task_id,
    owned: ownedPathsOf(t).slice().sort().join('\n'),
    grants: splitPathField(t.fields.Protected_Grants || '').slice().sort().join('\n'),
  }));
}

function applyPlanEdits(original, edits) {
  let text = original || '';
  for (const e of edits || []) {
    const oldS = firstString(e, ['old_string', 'old_str']);
    const newS = firstString(e, ['new_string', 'new_str']);
    if (!oldS) continue;
    const idx = text.indexOf(oldS);
    if (idx === -1) continue;
    text = text.slice(0, idx) + newS + text.slice(idx + oldS.length);
  }
  return text;
}

/**
 * True when a builder Edit/Write/MultiEdit of PLAN.md adds or changes a
 * Protected_Grants or Owned_Paths field. Edit: old_string vs new_string.
 * Write/MultiEdit: compare against the current main-checkout PLAN.md.
 */
function builderChangedOrchOnlyFields(toolInput) {
  const oldSnippet = firstString(toolInput, ['old_string', 'old_str']);
  const newSnippet = firstString(toolInput, ['new_string', 'new_str']);
  const isMulti = Array.isArray(toolInput && toolInput.edits);
  const isEdit = !isMulti && oldSnippet !== '';
  const snap = (t) => JSON.stringify(orchOnlySnapshot(t));

  if (isEdit) {
    return snap(oldSnippet) !== snap(newSnippet);
  }

  const current = readPlanText() || '';
  if (isMulti) {
    if (snap(current) !== snap(applyPlanEdits(current, toolInput.edits))) return true;
    for (const e of toolInput.edits) {
      const o = firstString(e, ['old_string', 'old_str']);
      const n = firstString(e, ['new_string', 'new_str']);
      if (snap(o) !== snap(n)) return true;
    }
    return false;
  }

  const written = firstString(toolInput, ['content', 'file_text', 'file_content']);
  return snap(current) !== snap(written);
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
  repoRoot, mainRoot, unit, readStdinJson, relPath, parsePlan, ownedPathsOf, protectedGrantsOf,
  grantWithinOwned, effectiveProtectedGrantsOf, builderChangedOrchOnlyFields,
  activeTasksFor, sessionTasksFor,
  globPrefix, pathInGlob, pathInAnyGlob, PROTECTED_FOR_BUILDERS, PROTECTED_EXCEPTIONS, pathInAnyException, findSecrets,
  writtenContentOf, filePathOf, EMPTY, ACTIVE, controlMode, activeTaskIdFor, knownUnits,
  isHumanOnlyConfig, sessionIsDelegated, readPlanText,
};
