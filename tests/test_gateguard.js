#!/usr/bin/env node
/**
 * test_gateguard.js — behavioural tests for hooks/gateguard.js.
 *
 * Runs the hook as a real subprocess (stdin JSON in, exit code + stderr out),
 * because the hook contract IS the process boundary — an in-process require()
 * would not exercise readStdinJson, process.exit, or the state file.
 *
 * Each case gets a fresh GATEGUARD_STATE_DIR so ordering is not load-bearing.
 *
 * Run: node tests/test_gateguard.js
 */
'use strict';

const { spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const REPO = path.resolve(__dirname, '..');
const HOOK = path.join(REPO, 'hooks', 'gateguard.js');

let pass = 0;
let fail = 0;

function run(payload, env, stateDir) {
  const res = spawnSync(process.execPath, [HOOK], {
    input: JSON.stringify(payload),
    encoding: 'utf-8',
    cwd: REPO,
    env: Object.assign({}, process.env, {
      CLAUDE_PROJECT_DIR: REPO,
      GATEGUARD_STATE_DIR: stateDir,
      DEVTEAM_UNIT: 'S5',
    }, env || {}),
  });
  return { code: res.status, err: res.stderr || '' };
}

function freshDir(label) {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'gg-' + label + '-'));
}

function check(name, cond, detail) {
  if (cond) {
    pass += 1;
    console.log('  PASS  ' + name);
  } else {
    fail += 1;
    console.log('  FAIL  ' + name + (detail ? '  -> ' + detail : ''));
  }
}

function edit(file) {
  return { tool_name: 'Edit', session_id: 's1', tool_input: { file_path: file } };
}
function bash(cmd) {
  return { tool_name: 'Bash', session_id: 's1', tool_input: { command: cmd } };
}

// --- first touch denies, retry allows -------------------------------------
{
  const d = freshDir('firsttouch');
  const a = run(edit('scripts/budget.py'), {}, d);
  check('first touch on existing file is BLOCKED', a.code === 2, 'exit=' + a.code);
  check('denial names the file', a.err.includes('scripts/budget.py'), a.err.slice(0, 120));
  check('denial demands importers', /EVERY file that imports/.test(a.err), a.err.slice(0, 120));

  const b = run(edit('scripts/budget.py'), {}, d);
  check('retry on same file is ALLOWED', b.code === 0, 'exit=' + b.code);

  const c = run(edit('scripts/notify.py'), {}, d);
  check('a different file is gated separately', c.code === 2, 'exit=' + c.code);
}

// --- new file gets the write prompt, not the edit prompt -------------------
{
  const d = freshDir('newfile');
  const a = run(edit('scripts/does_not_exist_xyz.py'), {}, d);
  check('new file is BLOCKED', a.code === 2, 'exit=' + a.code);
  check('new file uses the creation prompt', a.err.includes('BLOCKED (new file)'), a.err.slice(0, 120));
  check('new-file prompt asks who will call it', /will call into this new file/.test(a.err));
}

// --- ORCH is not gated by default -----------------------------------------
{
  const d = freshDir('orch');
  const a = run(edit('scripts/budget.py'), { DEVTEAM_UNIT: 'ORCH' }, d);
  check('ORCH is ungated by default', a.code === 0, 'exit=' + a.code);

  const b = run(edit('scripts/budget.py'), { DEVTEAM_UNIT: 'ORCH', GATEGUARD_UNITS: 'ORCH' }, d);
  check('ORCH is gated when opted in', b.code === 2, 'exit=' + b.code);
}

// --- kill switch -----------------------------------------------------------
{
  const d = freshDir('off');
  for (const v of ['off', '0', 'false', 'disabled']) {
    const a = run(edit('scripts/budget.py'), { GATEGUARD: v }, d);
    check('GATEGUARD=' + v + ' disables the gate', a.code === 0, 'exit=' + a.code);
  }
}

// --- exempt globs ----------------------------------------------------------
{
  const d = freshDir('exempt');
  const a = run(edit('tests/test_gateguard.js'), { GATEGUARD_EXEMPT_GLOBS: 'tests/**' }, d);
  check('exempt glob skips gating', a.code === 0, 'exit=' + a.code);

  const b = run(edit('scripts/budget.py'), { GATEGUARD_EXEMPT_GLOBS: 'tests/**' }, d);
  check('non-matching path still gated', b.code === 2, 'exit=' + b.code);

  const c = run(edit('scripts/budget.py'), { GATEGUARD_EXEMPT_GLOBS: '[[[bad(' }, freshDir('badglob'));
  check('malformed glob fails open to normal gating', c.code === 2, 'exit=' + c.code);
}

// --- destructive bash gates EVERY time ------------------------------------
{
  const d = freshDir('destructive');
  const a = run(bash('rm -rf build/'), {}, d);
  check('rm -rf is BLOCKED', a.code === 2, 'exit=' + a.code);
  check('destructive prompt asks for rollback', /rollback procedure/.test(a.err));

  const b = run(bash('rm -rf build/'), {}, d);
  check('rm -rf is BLOCKED again (not memoized)', b.code === 2, 'exit=' + b.code);

  for (const cmd of ['git reset --hard HEAD~1', 'git push --force origin main', 'DROP TABLE users;']) {
    const r = run(bash(cmd), {}, freshDir('d'));
    check('destructive: ' + cmd, r.code === 2, 'exit=' + r.code);
  }
}

// --- quoted text must not read as destructive -----------------------------
{
  const d = freshDir('quoted');
  // Routine gate is off so a block here can only come from the destructive matcher.
  const env = { GATEGUARD_BASH_ROUTINE_DISABLED: '1' };
  const a = run(bash('git commit -m "drop table migration and rm -rf cleanup"'), env, d);
  check('quoted destructive words do NOT trigger', a.code === 0, 'exit=' + a.code + ' ' + a.err.slice(0, 90));

  const b = run(bash("echo 'git reset --hard is dangerous'"), env, freshDir('q2'));
  check('single-quoted destructive words do NOT trigger', b.code === 0, 'exit=' + b.code);
}

// --- routine bash gates once ----------------------------------------------
{
  const d = freshDir('routine');
  const a = run(bash('npm test'), {}, d);
  check('first routine bash is BLOCKED', a.code === 2, 'exit=' + a.code);
  const b = run(bash('ls -la'), {}, d);
  check('second routine bash is ALLOWED', b.code === 0, 'exit=' + b.code);

  const c = run(bash('npm test'), { GATEGUARD_BASH_ROUTINE_DISABLED: '1' }, freshDir('r2'));
  check('routine gate can be disabled', c.code === 0, 'exit=' + c.code);

  const e = run(bash('rm -rf x'), { GATEGUARD_BASH_ROUTINE_DISABLED: '1' }, freshDir('r3'));
  check('disabling routine leaves destructive active', e.code === 2, 'exit=' + e.code);
}

// --- condensed denials after the budget -----------------------------------
{
  const d = freshDir('condense');
  const env = { GATEGUARD_FULL_DENIALS: '1' };
  const a = run(edit('scripts/budget.py'), env, d);
  check('denial 1 is the full block', a.err.includes('EVERY file that imports'), a.err.slice(0, 80));
  const b = run(edit('scripts/notify.py'), env, d);
  check('denial 2 is condensed', b.err.includes('condensed'), b.err.slice(0, 80));
  check('condensed denial carries the ordinal', /denial #2/.test(b.err), b.err.slice(0, 80));
}

// --- unwritable state allows rather than looping --------------------------
{
  // A directory path *underneath an existing regular file* — mkdirSync must fail,
  // which is exactly the "state cannot be persisted" branch we need to exercise.
  const bad = path.join(REPO, 'CLAUDE.md', 'state');
  const a = run(edit('scripts/budget.py'), {}, bad);
  check('unpersistable state ALLOWS (never loops)', a.code === 0, 'exit=' + a.code);
}

// --- per-unit denial counter (feeds supervisor.py's circuit breaker) ------
{
  const d = freshDir('denialcounter');
  const denialFile = path.join(REPO, '.devteam', 'gateguard', 'denials', 'S5.json');
  try { fs.rmSync(denialFile, { force: true }); } catch (_e) { /* ignore */ }

  run(edit('scripts/budget.py'), {}, d); // first-touch file denial
  let count = JSON.parse(fs.readFileSync(denialFile, 'utf-8')).count;
  check('file denial bumps the counter to 1', count === 1, 'count=' + count);

  run(edit('scripts/notify.py'), {}, d); // a different file, also denied
  count = JSON.parse(fs.readFileSync(denialFile, 'utf-8')).count;
  check('a second file denial bumps it to 2', count === 2, 'count=' + count);

  run(bash('ls -la'), {}, d); // routine bash — should NOT bump
  count = JSON.parse(fs.readFileSync(denialFile, 'utf-8')).count;
  check('routine-Bash denial does NOT bump the counter', count === 2, 'count=' + count);

  run(bash('rm -rf x'), {}, d); // destructive bash — SHOULD bump
  count = JSON.parse(fs.readFileSync(denialFile, 'utf-8')).count;
  check('destructive-Bash denial bumps the counter', count === 3, 'count=' + count);

  try { fs.rmSync(denialFile, { force: true }); } catch (_e) { /* cleanup */ }
}

{
  const d = freshDir('denialcounter-orch');
  const denialFile = path.join(REPO, '.devteam', 'gateguard', 'denials', 'ORCH.json');
  try { fs.rmSync(denialFile, { force: true }); } catch (_e) { /* ignore */ }
  run(edit('scripts/budget.py'), { DEVTEAM_UNIT: 'ORCH' }, d); // ungated by default
  check('an ungated unit never gets a denial file', !fs.existsSync(denialFile));
}

// --- unknown unit is left to territory-firewall ---------------------------
{
  const d = freshDir('unknown');
  const a = run(edit('scripts/budget.py'), { DEVTEAM_UNIT: 'NOPE' }, d);
  check('unknown unit is not double-blocked here', a.code === 0, 'exit=' + a.code);
}

console.log('\n' + pass + ' passed, ' + fail + ' failed');
process.exit(fail === 0 ? 0 : 1);
