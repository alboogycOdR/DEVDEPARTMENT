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
  // Scrub DEVTEAM_UNIT from the inherited environment: builder sessions run
  // with it exported (dispatch sets it), and tests that assert the unset-var
  // default would otherwise fail only when a builder runs the suite — which
  // is exactly when the suite gates a needs_review submission (TASK-002 hit
  // this live). Tests that need a unit set it explicitly via `env`.
  const base = { ...process.env, ...env };
  // Scrub session pins the same way as DEVTEAM_UNIT: a builder dispatch now
  // exports all three, and a test that does not set them must see them unset.
  for (const key of ['DEVTEAM_UNIT', 'DEVTEAM_TASK', 'DEVTEAM_DELEGATED']) {
    if (!env || !(key in env)) delete base[key];
  }
  try {
    const stdout = execFileSync(process.execPath, [path.join(HOOKS_DIR, script)], {
      input: JSON.stringify(payload),
      env: base,
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

test('ownedPathsOf strips the (new) annotation', () => {
  const t = { fields: { Owned_Paths: 'scripts/a.py (new), tests/b/** (new), c.md' } };
  assert.deepStrictEqual(lib.ownedPathsOf(t), ['scripts/a.py', 'tests/b/**', 'c.md']);
});

test('grantWithinOwned matches validate_plan.grant_within_owned cases', () => {
  const owned = ['hooks/**', 'scripts/validate_plan.py'];
  assert.ok(lib.grantWithinOwned('hooks/lib.js', owned));
  assert.ok(lib.grantWithinOwned('scripts/validate_plan.py', owned));
  assert.ok(lib.grantWithinOwned('scripts/validate_plan.py', ['scripts/validate_plan.py (new)'.replace(/\s+\(new\)$/i, '')]));
  assert.ok(!lib.grantWithinOwned('scripts/other.py', owned));
  assert.ok(!lib.grantWithinOwned('scripts/**', ['scripts/validate_plan.py']));
  assert.ok(!lib.grantWithinOwned('CLAUDE.md', owned));
  assert.ok(!lib.grantWithinOwned('**', owned), 'bare-wildcard grant is not a subset');
  assert.ok(lib.grantWithinOwned('CLAUDE.md', ['**']), 'bare-wildcard owned covers every grant');
  assert.ok(lib.grantWithinOwned('hooks/lib.js', ['hooks/**']));
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
    { tool_input: {
        file_path: path.join(repo, 'PLAN.md'),
        old_string: '**Status:** in_progress',
        new_string: '**Status:** needs_review',
      } },
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
  const content = PLAN.replace(
    '**Updated_At:** 2026-07-13T09:30:00Z',
    '**Updated_At:** 2026-07-13T10:00:00Z'
  );
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'PLAN.md'), content } },
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


// -------------------------------------- PROTECTED_EXCEPTIONS (per-task grants) ----
// The mechanism ships EMPTY by design: the pack provides the lever, not anyone's
// carve-outs. These tests pin both halves — that the default really is deny, and
// that the matcher does what the comment claims when a project populates it.
test('non-excepted scripts/** stays blocked regardless of active grants (deny-by-default)', () => {
  // Was: assert PROTECTED_EXCEPTIONS.length === 0. That pinned the PACK's
  // ship-state but broke the documented runtime mechanism: any live per-task
  // grant (e.g. TASK-004's atlas_cards.py) turned this suite red for every
  // builder gating needs_review on it. The load-bearing invariant is
  // BEHAVIOR: a protected path NOT in the exceptions list is denied even
  // while other grants are active. Ship-state hygiene ("keep it short,
  // delete at done") is enforced by review, not by this suite.
  assert.ok(!lib.pathInAnyException('scripts/anything.py', lib.PROTECTED_EXCEPTIONS),
    'scripts/anything.py must never be covered by a real grant — if this fires, a grant is drawn too wide');
  const repo = makeTempRepo();
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'scripts/anything.py'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 2, 'non-excepted scripts/ path must stay blocked');
});

test('exception matcher grants a specific file without weakening the glob', () => {
  // The documented use: product scripts living alongside ORCH machinery.
  const grants = ['scripts/extract_caps_taxonomy*.py'];
  assert.ok(lib.pathInAnyException('scripts/extract_caps_taxonomy.py', grants));
  assert.ok(lib.pathInAnyException('scripts/extract_caps_taxonomy_v2.py', grants));
  // ORCH machinery in the same directory must NOT be swept in:
  assert.ok(!lib.pathInAnyException('scripts/dispatch.sh', grants));
  assert.ok(!lib.pathInAnyException('scripts/plan_commit.sh', grants));
  assert.ok(!lib.pathInAnyException('scripts/validate_plan.py', grants));
});

test('exception matcher is literal, not prefix-based like pathInGlob', () => {
  // This is precisely why it does not reuse pathInGlob: that one truncates at
  // the first wildcard and prefix-matches, which would grant the whole directory.
  const grants = ['scripts/product_*.py'];
  assert.ok(lib.pathInAnyException('scripts/product_a.py', grants));
  assert.ok(!lib.pathInAnyException('scripts/product_a.py.bak', grants), 'must anchor at the end');
  assert.ok(!lib.pathInAnyException('scripts/nested/product_a.py', grants),
    '* must not cross a directory separator');
  assert.ok(!lib.pathInAnyException('scripts/', grants));
});

test('exception matcher does not treat regex metacharacters as patterns', () => {
  assert.ok(!lib.pathInAnyException('scriptsXanything.py', ['scripts.anything.py']),
    'a dot in the glob must match a literal dot only');
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

// ---------------------------------------------------- E-A (TASK-027) --------
test('DEVTEAM_TASK reports that task, not the first in_progress block', () => {
  const plan = PLAN.replace(
    '### TASK-020',
    `### TASK-338
**Title:** Wrong first block
**Status:** in_progress
**Assigned_To:** GB
**Priority:** high
**Spec_References:** specs/x.md
**Owned_Paths:** lib/other/**
**Depends_On:** —
**Description:** d
**Acceptance_Criteria:**
- [ ] c
**Branch:** task/TASK-338-gb
**Started_At:** 2026-07-13T09:00:00Z
**Progress_Notes:** —
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** GB
**Updated_At:** 2026-07-13T09:30:00Z

### TASK-332
**Title:** The dispatched task
**Status:** in_progress
**Assigned_To:** GB
**Priority:** high
**Spec_References:** specs/x.md
**Owned_Paths:** lib/features/auth/**
**Depends_On:** —
**Description:** d
**Acceptance_Criteria:**
- [ ] c
**Branch:** task/TASK-332-gb
**Started_At:** 2026-07-13T09:00:00Z
**Progress_Notes:**
- [2026-07-13T09:30:00Z] [GB] pinned note.
**Artifacts:** —
**Test_Evidence:** —
**Review_Findings:** —
**Blocked_Reason:** —
**Updated_By:** GB
**Updated_At:** 2026-07-13T09:30:00Z

### TASK-020`
  );
  const repo = makeTempRepo({ plan });
  const r = runHook('session-start.js', {}, {
    CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB', DEVTEAM_TASK: 'TASK-332',
  });
  assert.strictEqual(r.code, 0, r.stderr);
  assert.ok(r.stdout.includes('TASK-332'), r.stdout);
  assert.ok(r.stdout.includes('pinned note'), r.stdout);
  assert.ok(!r.stdout.includes('TASK-338'), 'must not banner the earlier in_progress block');
});

test('autopilot.json write denied when DEVTEAM_DELEGATED=1 and allowed with neither variable', () => {
  const repo = makeTempRepo();
  const denied = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'autopilot.json'), content: '{"control":{"mode":"strict"}}' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_DELEGATED: '1' });
  assert.strictEqual(denied.code, 2, denied.stderr);
  assert.ok(denied.stderr.includes('human session'), denied.stderr);
  const localDenied = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'autopilot.local.json'), content: '{}' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(localDenied.code, 2, localDenied.stderr);
  const allowed = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'autopilot.json'), content: '{}' } },
    { CLAUDE_PROJECT_DIR: repo });
  assert.strictEqual(allowed.code, 0, allowed.stderr);
  const other = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'anything.txt'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_DELEGATED: '1' });
  assert.strictEqual(other.code, 0, other.stderr);
});

test('Protected_Grants on an active task allow that path; a done task grant does not', () => {
  const activePlan = PLAN.replace(
    '**Owned_Paths:** lib/features/auth/**, test/auth/**',
    '**Owned_Paths:** lib/features/auth/**, scripts/granted.py\n**Protected_Grants:** scripts/granted.py'
  );
  const repo = makeTempRepo({ plan: activePlan });
  const ok = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'scripts/granted.py'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(ok.code, 0, ok.stderr);

  const donePlan = activePlan.replace('**Status:** in_progress', '**Status:** done');
  const doneRepo = makeTempRepo({ plan: donePlan });
  const blocked = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(doneRepo, 'scripts/granted.py'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: doneRepo, DEVTEAM_UNIT: 'GB', DEVTEAM_TASK: 'TASK-020' });
  assert.strictEqual(blocked.code, 2, blocked.stderr);
});

test('ORCH-authored grant inside Owned_Paths allows the write', () => {
  const plan = PLAN.replace(
    '**Owned_Paths:** lib/features/auth/**, test/auth/**',
    '**Owned_Paths:** lib/features/auth/**, scripts/granted.py\n**Protected_Grants:** scripts/granted.py'
  );
  const repo = makeTempRepo({ plan });
  const r = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'scripts/granted.py'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(r.code, 0, r.stderr);
});

test('grant outside Owned_Paths is ignored by the firewall', () => {
  const plan = PLAN.replace(
    '**Owned_Paths:** lib/features/auth/**, test/auth/**',
    '**Owned_Paths:** lib/features/auth/**, test/auth/**\n**Protected_Grants:** CLAUDE.md, AGENTS.md'
  );
  const repo = makeTempRepo({ plan });
  const claude = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'CLAUDE.md'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(claude.code, 2, claude.stderr);
  assert.ok(claude.stderr.includes('protected path'), claude.stderr);
  const agents = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'AGENTS.md'), content: 'x' } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(agents.code, 2, agents.stderr);
});

test('builder self-grant attempt on PLAN.md is denied in legacy mode', () => {
  const repo = makeTempRepo();
  const editGrant = runHook('territory-firewall.js',
    { tool_input: {
        file_path: path.join(repo, 'PLAN.md'),
        old_string: '**Owned_Paths:** lib/features/auth/**, test/auth/**',
        new_string: '**Owned_Paths:** lib/features/auth/**, test/auth/**\n**Protected_Grants:** CLAUDE.md',
      } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(editGrant.code, 2, editGrant.stderr);
  assert.ok(editGrant.stderr.includes('ORCH-only'), editGrant.stderr);

  const editOwned = runHook('territory-firewall.js',
    { tool_input: {
        file_path: path.join(repo, 'PLAN.md'),
        old_string: '**Owned_Paths:** lib/features/auth/**, test/auth/**',
        new_string: '**Owned_Paths:** lib/features/auth/**, test/auth/**, CLAUDE.md',
      } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(editOwned.code, 2, editOwned.stderr);

  const writeGrant = PLAN.replace(
    '**Owned_Paths:** lib/features/auth/**, test/auth/**',
    '**Owned_Paths:** lib/features/auth/**, test/auth/**\n**Protected_Grants:** hooks/**'
  );
  const write = runHook('territory-firewall.js',
    { tool_input: { file_path: path.join(repo, 'PLAN.md'), content: writeGrant } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(write.code, 2, write.stderr);

  const multi = runHook('territory-firewall.js',
    { tool_input: {
        file_path: path.join(repo, 'PLAN.md'),
        edits: [{
          old_string: '**Owned_Paths:** lib/features/auth/**, test/auth/**',
          new_string: '**Owned_Paths:** lib/features/auth/**, test/auth/**\n**Protected_Grants:** CLAUDE.md',
        }],
      } },
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'GB' });
  assert.strictEqual(multi.code, 2, multi.stderr);
});

test('ORCH and interactive sessions may edit Protected_Grants and Owned_Paths', () => {
  const repo = makeTempRepo();
  const payload = {
    tool_input: {
      file_path: path.join(repo, 'PLAN.md'),
      old_string: '**Owned_Paths:** lib/features/auth/**, test/auth/**',
      new_string: '**Owned_Paths:** lib/features/auth/**, test/auth/**\n**Protected_Grants:** CLAUDE.md',
    },
  };
  const orch = runHook('territory-firewall.js', payload,
    { CLAUDE_PROJECT_DIR: repo, DEVTEAM_UNIT: 'ORCH' });
  assert.strictEqual(orch.code, 0, orch.stderr);
  const interactive = runHook('territory-firewall.js', payload,
    { CLAUDE_PROJECT_DIR: repo });
  assert.strictEqual(interactive.code, 0, interactive.stderr);
});

test('claim visible only in the main checkout is allowed; gateguard denials land there', () => {
  const parent = fs.mkdtempSync(path.join(os.tmpdir(), 'devteam-ea-'));
  const main = path.join(parent, 'main');
  const wt = path.join(parent, 'wt');
  fs.mkdirSync(main);
  const git = (args, cwd) => execFileSync('git', args, { cwd, stdio: 'ignore' });
  try {
    git(['init', '-q', '-b', 'master'], main);
    git(['config', 'user.email', 't@example.com'], main);
    git(['config', 'user.name', 'T'], main);
    const stale = PLAN.replace('**Status:** in_progress', '**Status:** pending')
      .replace('**Assigned_To:** GB', '**Assigned_To:** TBD');
    fs.writeFileSync(path.join(main, 'PLAN.md'), stale, 'utf-8');
    fs.writeFileSync(path.join(main, 'autopilot.json'), '{}\n', 'utf-8');
    git(['add', 'PLAN.md', 'autopilot.json'], main);
    git(['commit', '-q', '-m', 'init'], main);
    git(['worktree', 'add', '--detach', wt, 'HEAD'], main);
    // Uncommitted claim exists only in the main checkout working tree.
    fs.writeFileSync(path.join(main, 'PLAN.md'), PLAN, 'utf-8');
    assert.ok(fs.readFileSync(path.join(wt, 'PLAN.md'), 'utf-8').includes('**Status:** pending'));

    const allowed = runHook('territory-firewall.js',
      { tool_input: { file_path: path.join(wt, 'lib/features/auth/login.dart'), content: 'x' } },
      { CLAUDE_PROJECT_DIR: wt, DEVTEAM_UNIT: 'GB', GATEGUARD_STATE_DIR: '' });
    assert.strictEqual(allowed.code, 0, allowed.stderr);

    const denied = runHook('gateguard.js',
      { tool_name: 'Edit', session_id: 'ea', tool_input: { file_path: path.join(wt, 'lib/features/auth/login.dart') } },
      { CLAUDE_PROJECT_DIR: wt, DEVTEAM_UNIT: 'GB', GATEGUARD_STATE_DIR: '' });
    assert.strictEqual(denied.code, 2, denied.stderr);
    const counter = path.join(main, '.devteam', 'gateguard', 'denials', 'GB.json');
    assert.ok(fs.existsSync(counter), 'denial counter must land in the main checkout');
    assert.ok(!fs.existsSync(path.join(wt, '.devteam', 'gateguard', 'denials', 'GB.json')),
      'worktree must not receive the denial counter');
  } finally {
    try { git(['worktree', 'remove', '--force', wt], main); } catch (_e) { /* best effort */ }
    fs.rmSync(parent, { recursive: true, force: true });
  }
});

test('dispatch exports DEVTEAM_TASK and DEVTEAM_DELEGATED beside DEVTEAM_UNIT', () => {
  const root = path.join(HOOKS_DIR, '..');
  const sh = fs.readFileSync(path.join(root, 'scripts', 'dispatch.sh'), 'utf-8');
  const ps = fs.readFileSync(path.join(root, 'scripts', 'dispatch.ps1'), 'utf-8');
  assert.ok(sh.includes('export DEVTEAM_TASK="$TASK_ID"'), 'dispatch.sh DEVTEAM_TASK');
  assert.ok(sh.includes('export DEVTEAM_DELEGATED=1'), 'dispatch.sh DEVTEAM_DELEGATED');
  assert.ok(ps.includes('$env:DEVTEAM_TASK = $TaskId'), 'dispatch.ps1 DEVTEAM_TASK');
  assert.ok(ps.includes('$env:DEVTEAM_DELEGATED = "1"'), 'dispatch.ps1 DEVTEAM_DELEGATED');
  assert.ok(ps.includes("`$env:DEVTEAM_TASK = '$TaskId'"), 'detached runner DEVTEAM_TASK');
  assert.ok(ps.includes("`$env:DEVTEAM_DELEGATED = '1'"), 'detached runner DEVTEAM_DELEGATED');
});

// ------------------------------------------------------------------ report --
console.log(`\n${passed} passed, ${failures.length} failed`);
process.exit(failures.length ? 1 : 0);
