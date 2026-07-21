#!/usr/bin/env node
/**
 * run-tests.js — Zero-dependency test suite for the DEVDEPARTMENT hooks.
 *
 * Tests the shared library logic (parsing, territory matching, secret patterns)
 * AND the hooks end-to-end as child processes with synthetic stdin payloads,
 * a temp repo, and DEVTEAM_UNIT/CLAUDE_PROJECT_DIR env — exactly how Claude
 * Code invokes them.
 *
 * Usage: node hooks/run-tests.js   (exit 0 = all green)
 */
'use strict';

const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const HOOKS_DIR = __dirname;
const lib = require(path.join(HOOKS_DIR, 'lib.js'));

let passed = 0;
const failures = [];
function test(name, fn) {
  try { fn(); passed++; console.log(`  ok  ${name}`); }
  catch (e) { failures.push({ name, e }); console.error(`FAIL  ${name}: ${e.message}`); }
}

// ---------------------------------------------------------------- fixtures --
const PLAN = `---
plan_version: 1.0
last_updated: 2026-07-13T10:00:00Z
overall_status: in_progress
orchestrator_notes: "Wave 3 running. Next: review TASK-020."
---
# Plan

### TASK-020
**Title:** Feature A
**Status:** in_progress
**Assigned_To:** GB
**Priority:** high
**Spec_References:** specs/x.md
**Owned_Paths:** lib/features/auth/**, test/auth/**
**Depends_On:** —
**Description:** d
**Acceptance_Criteria:**
- [ ] c
**Branch:** task/TASK-020-gb
**Started_At:** 2026-07-13T09:00:00Z
**Progress_Notes:**
- [2026-07-13T09:30:00Z] [GB] login flow scaffolded; next: token refresh.
**Artifacts:** lib/features/auth/login.dart
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** GB
**Updated_At:** 2026-07-13T09:30:00Z

### TASK-021
**Title:** Feature B
**Status:** pending
**Assigned_To:** CX
**Priority:** high
**Spec_References:** specs/y.md
**Owned_Paths:** functions/**
**Depends_On:** —
**Description:** d
**Acceptance_Criteria:**
- [ ] c
**Branch:** —
**Started_At:** —
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** ORCH
**Updated_At:** 2026-07-13T08:00:00Z
`;

function makeTempRepo(opts) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'devteam-hooks-'));
  fs.writeFileSync(path.join(dir, 'PLAN.md'), (opts && opts.plan) || PLAN, 'utf-8');
  if (opts && opts.strict) {
    fs.writeFileSync(path.join(dir, 'autopilot.json'),
      JSON.stringify({ control: { mode: 'strict' } }), 'utf-8');
  }
  return dir;
}

/** Run a hook as a child process; returns {code, stderr, stdout}. */
function runHook(script, payload, env) {
  try {
    const stdout = execFileSync(process.execPath, [path.join(HOOKS_DIR, script)], {
      input: JSON.stringify(payload),
      env: { ...process.env, ...env },
      encoding: 'utf-8',
    });
    return { code: 0, stdout, stderr: '' };
  } catch (e) {
    return { code: e.status ?? 1, stdout: e.stdout || '', stderr: e.stderr || '' };
  }
}

// -------------------------------------------------------------- lib tests --
test('parsePlan extracts tasks and fields', () => {
  const tasks = lib.parsePlan(PLAN);
  assert.strictEqual(tasks.length, 2);
  assert.strictEqual(tasks[0].task_id, 'TASK-020');
  assert.strictEqual(tasks[0].fields.Assigned_To, 'GB');
});

test('ownedPathsOf splits comma territories', () => {
  const t = lib.parsePlan(PLAN)[0];
  assert.deepStrictEqual(lib.ownedPathsOf(t), ['lib/features/auth/**', 'test/auth/**']);
});

test('pathInGlob prefix containment semantics', () => {
  assert.ok(lib.pathInGlob('lib/features/auth/login.dart', 'lib/features/auth/**'));
  assert.ok(lib.pathInGlob('lib/features/auth', 'lib/features/auth/**'));
  assert.ok(!lib.pathInGlob('lib/features/authx/f.dart', 'lib/features/auth/**'));
  assert.ok(!lib.pathInGlob('lib/core/util.dart', 'lib/features/auth/**'));
  assert.ok(lib.pathInGlob('anything/at/all', '**'));
});

test('findSecrets catches key patterns and ignores clean text', () => {
  assert.ok(lib.findSecrets('const k = "sk-ABCDEFGHIJKLMNOPQRSTUVWX";').length > 0);
  assert.ok(lib.findSecrets('token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345').length > 0);
  assert.ok(lib.findSecrets('AKIAIOSFODNN7EXAMPLE').length > 0);
  assert.ok(lib.findSecrets('-----BEGIN RSA PRIVATE KEY-----').length > 0);
  assert.strictEqual(lib.findSecrets('const skill = "sk-learn is a library";').length, 0);
  assert.strictEqual(lib.findSecrets('normal code with no creds').length, 0);
});

// ---------------------------------------------------- firewall E2E tests --
test('firewall allows GB write inside territory', () => {
  const repo = makeTempRepo();
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'lib/features/auth/login.dart'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 0, r.stderr);
});

test('firewall blocks GB write outside territory (exit 2, names territories)', () => {
  const repo = makeTempRepo();
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'lib/core/util.dart'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 2);
  assert.ok(r.stderr.includes('outside your Owned_Paths'));
  assert.ok(r.stderr.includes('TASK-020'));
});

test('firewall blocks GB write to hooks/ and .codex/ (self-protection)', () => {
  const repo = makeTempRepo();
  for (const f of ['hooks/lib.js', '.codex/config.toml']) {
    const r = runHook('territory-firewall.js',
      { tool_input: { file_path: path.join(repo, f), content: 'x' } },
      { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
    assert.strictEqual(r.code, 2, `${f} should be protected`);
  }
});

test('firewall blocks GB write to protected path (specs)', () => {
  const repo = makeTempRepo();
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'specs/x.md'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 2);
  assert.ok(r.stderr.includes('protected path'));
});

test('firewall blocks GB write to protected path (deploy) — Wave B', () => {
  const repo = makeTempRepo();
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'deploy/ecosystem.config.js'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 2);
  assert.ok(r.stderr.includes('protected path'));
});

test('firewall blocks GB write to INSTINCTS.md — Wave C', () => {
  const repo = makeTempRepo();
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'INSTINCTS.md'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 2);
  assert.ok(r.stderr.includes('protected path'));
});

test('firewall blocks GB write to .devteam/pending_amendments/** — Wave C', () => {
  const repo = makeTempRepo();
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, '.devteam/pending_amendments/AMEND-001.md'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 2);
  assert.ok(r.stderr.includes('protected path'));
});

test('firewall allows GB write to PLAN.md in control.mode=legacy (default) — Wave I', () => {
  const repo = makeTempRepo();  // no strict flag -> legacy, same as today
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'PLAN.md'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 0, r.stderr);
});

test('firewall BLOCKS GB write to PLAN.md in control.mode=strict — Wave I', () => {
  const repo = makeTempRepo({ strict: true });
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'PLAN.md'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 2);
  assert.ok(r.stderr.includes('control.mode=strict'), r.stderr);
});

test('firewall allows GB write to its OWN active task dossier in strict mode — Wave I', () => {
  const repo = makeTempRepo({ strict: true });  // TASK-020 is GB's active task in the PLAN fixture
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'dossiers/TASK-020.md'), content: 'work log entry' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 0, r.stderr);
});

test('firewall BLOCKS GB write to ANOTHER task\'s dossier in strict mode — Wave I', () => {
  const repo = makeTempRepo({ strict: true });
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'dossiers/TASK-999.md'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 2);
  assert.ok(r.stderr.includes('own active task'), r.stderr);
});

test('firewall resolves active task from .devteam/inflight/ when present — Wave I', () => {
  const repo = makeTempRepo({ strict: true, plan: PLAN });  // PLAN fixture has no TASK-777
  fs.mkdirSync(path.join(repo, '.devteam', 'inflight'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.devteam', 'inflight', 'GB.json'),
    JSON.stringify({ task_id: 'TASK-777' }), 'utf-8');
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'dossiers/TASK-777.md'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 0, r.stderr);  // inflight record wins even though PLAN.md doesn't have TASK-777
});

test('firewall allows GB write to PLAN.md (block discipline is downstream)', () => {
  const repo = makeTempRepo();
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'PLAN.md'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 0, r.stderr);
});

test('firewall blocks CX with no active task (pending only)', () => {
  const repo = makeTempRepo();
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'functions/index.js'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'CX' });
  assert.strictEqual(r.code, 2);
  assert.ok(r.stderr.includes('no active'));
});

test('firewall is inert for ORCH', () => {
  const repo = makeTempRepo();
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'anything/else.txt'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'ORCH' });
  assert.strictEqual(r.code, 0, r.stderr);
});

test('firewall fails open on malformed stdin', () => {
  const repo = makeTempRepo();
  const r = runHook('territory-firewall.js', undefined, { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 0);
});


// ------------------------------------- builder registry / fail-closed ------
test('firewall FAIL-CLOSED: unrecognized DEVTEAM_UNIT is denied (exit 2), not treated as ORCH', () => {
  const repo = makeTempRepo();
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'lib/anything.dart'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'ZZ' });
  assert.strictEqual(r.code, 2);
  assert.ok(r.stderr.includes('not a known unit'), 'names the failure');
  assert.ok(!r.stderr.includes('non-fatal hook error'),
    'must deny via the verdict path, not via an exception the outer catch converts to fail-open');
});

test('firewall recognizes a registry-defined 4th unit (S5B) with its own territory', () => {
  const repo = makeTempRepo({ plan: PLAN.replace(/GB/g, 'S5B') });
  fs.writeFileSync(path.join(repo, 'autopilot.json'), JSON.stringify({
    builders: { active: ['S5B'], defined: { S5B: {
      cli: 'claude', worktree_suffix: 's5b', branch_suffix: 's5b',
      briefing: 'briefings/S5_BUILD_BRIEFING.md' } } },
  }), 'utf-8');
  const inside = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'lib/features/auth/a.dart'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'S5B' });
  assert.strictEqual(inside.code, 0, 'S5B allowed inside its territory: ' + inside.stderr);
  const outside = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'lib/core/util.dart'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'S5B' });
  assert.strictEqual(outside.code, 2, 'S5B blocked outside its territory');
});

test('firewall with registry object: unit in legacy default but NOT defined here is denied', () => {
  const repo = makeTempRepo();
  fs.writeFileSync(path.join(repo, 'autopilot.json'), JSON.stringify({
    builders: { active: ['CX'], defined: { CX: {
      cli: 'codex', worktree_suffix: 'codex', branch_suffix: 'cx',
      briefing: 'briefings/CODEX_BRIEFING.md' } } },
  }), 'utf-8');
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'lib/x.dart'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 2, 'GB is not in THIS project\'s registry -> denied');
});

test('unset DEVTEAM_UNIT still means ORCH (interactive sessions unaffected)', () => {
  const repo = makeTempRepo();
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'anything.md'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo });
  assert.strictEqual(r.code, 0);
});

// ---------------------------------------------------- secret-scan E2E ------
test('secret-scan blocks API key in source write for any unit incl. ORCH', () => {
  const repo = makeTempRepo();
  const r = runHook('secret-scan.js',
    { tool_input: { file_path: path.join(repo, 'lib/config.dart'), content: 'final k = "sk-ABCDEFGHIJKLMNOPQRSTUVWX";' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'ORCH' });
  assert.strictEqual(r.code, 2);
  assert.ok(r.stderr.includes('credential-like'));
});

test('secret-scan allows clean write and doc examples', () => {
  const repo = makeTempRepo();
  let r = runHook('secret-scan.js',
    { tool_input: { file_path: path.join(repo, 'lib/config.dart'), content: 'final k = env("API_KEY");' } },
    { CLAUDE_PROJECT_DIR: repo });
  assert.strictEqual(r.code, 0, r.stderr);
  r = runHook('secret-scan.js',
    { tool_input: { file_path: path.join(repo, 'docs/EXAMPLE.md'), content: 'e.g. ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ012345' } },
    { CLAUDE_PROJECT_DIR: repo });
  assert.strictEqual(r.code, 0, r.stderr);
});

test('secret-scan blocks private key even in docs', () => {
  const repo = makeTempRepo();
  const r = runHook('secret-scan.js',
    { tool_input: { file_path: path.join(repo, 'docs/keys.md'), content: '-----BEGIN RSA PRIVATE KEY-----\nMIIB...' } },
    { CLAUDE_PROJECT_DIR: repo });
  assert.strictEqual(r.code, 2);
});

test('secret-scan inspects str_replace-style new_str payloads', () => {
  const repo = makeTempRepo();
  const r = runHook('secret-scan.js',
    { tool_input: { file_path: path.join(repo, 'lib/a.dart'), new_str: 'AKIAIOSFODNN7EXAMPLE' } },
    { CLAUDE_PROJECT_DIR: repo });
  assert.strictEqual(r.code, 2);
});

// ---------------------------------------------- session lifecycle E2E ------
test('session-start emits resume brief with active task + last note', () => {
  const repo = makeTempRepo();
  const r = runHook('session-start.js', {}, { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 0);
  assert.ok(r.stdout.includes('TASK-020'));
  assert.ok(r.stdout.includes('RESUME FIRST'));
  assert.ok(r.stdout.includes('token refresh'));
});

test('session-start flags STOP file and checkpoint', () => {
  const repo = makeTempRepo();
  fs.writeFileSync(path.join(repo, 'STOP'), '');
  fs.mkdirSync(path.join(repo, '.devteam'), { recursive: true });
  fs.writeFileSync(path.join(repo, '.devteam', 'CHECKPOINT.md'), 'x');
  const r = runHook('session-start.js', {}, { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'ORCH' });
  assert.ok(r.stdout.includes('STOP file present'));
  assert.ok(r.stdout.includes('CHECKPOINT.md'));
});

test('pre-compact writes §10 checkpoint with active snapshot', () => {
  const repo = makeTempRepo();
  const r = runHook('pre-compact.js', {}, { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 0);
  const cp = fs.readFileSync(path.join(repo, '.devteam', 'CHECKPOINT.md'), 'utf-8');
  assert.ok(cp.includes('TASK-020'));
  assert.ok(cp.includes('do NOT re-claim'));
});

test('session-end appends audit line and refreshes checkpoint', () => {
  const repo = makeTempRepo();
  const r = runHook('session-end.js', {}, { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'ORCH' });
  assert.strictEqual(r.code, 0);
  const log = fs.readFileSync(path.join(repo, 'AUTOPILOT_LOG.md'), 'utf-8');
  assert.ok(log.includes('SESSION_END unit=ORCH'));
  assert.ok(log.includes('in_progress:1'));
  assert.ok(fs.existsSync(path.join(repo, '.devteam', 'CHECKPOINT.md')));
});

// ------------------------------------------------------------------ report --
console.log(`\n${passed} passed, ${failures.length} failed`);
process.exit(failures.length ? 1 : 0);
