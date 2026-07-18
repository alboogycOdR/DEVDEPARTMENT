#!/usr/bin/env node
/**
 * secret-scan.js — PreToolUse hook (Edit|Write|MultiEdit|NotebookEdit).
 *
 * Blocks writes whose content matches known credential patterns (API keys,
 * tokens, private key blocks, hardcoded password assignments). Applies to ALL
 * units including ORCH — no unit ever writes credentials into the repo.
 * Credentials belong in environment variables (per DEVDEPARTMENT convention,
 * e.g. DEVTEAM_TG_TOKEN) or a secret manager, never in tracked files.
 *
 * Exit 0 = allow, 2 = block (stderr fed back to the model).
 * Fail-open on unexpected errors.
 */
'use strict';

const lib = require('./lib.js');

// Paths where example/placeholder patterns are expected and allowed.
const ALLOWLIST_PATHS = ['.env.example', 'docs/**', 'tests/**', '**/*.md'];

function isAllowlisted(rel) {
  // .md files and docs/tests may contain redacted examples; still block real private key blocks.
  return (
    rel.endsWith('.env.example') ||
    lib.pathInGlob(rel, 'docs/**') ||
    lib.pathInGlob(rel, 'tests/**')
  );
}

function main() {
  const input = lib.readStdinJson();
  const toolInput = input.tool_input || {};
  const content = lib.writtenContentOf(toolInput);
  if (!content) return 0;

  const hits = lib.findSecrets(content);
  if (hits.length === 0) return 0;

  const rel = lib.relPath(lib.filePathOf(toolInput));
  const privateKey = hits.includes('Private key block');
  if (isAllowlisted(rel) && !privateKey) return 0;

  process.stderr.write(
    `[secret-scan] BLOCKED: write to ${rel || '(unknown path)'} contains credential-like content: ` +
    `${hits.join('; ')}. Never write credentials into the repository — use environment variables ` +
    `(DEVDEPARTMENT convention) or a secret manager, and reference them by name. If this is a ` +
    `deliberate redacted example, use an obvious placeholder like YOUR_KEY_HERE instead of a ` +
    `realistic-looking value.`
  );
  return 2;
}

try {
  process.exit(main());
} catch (e) {
  process.stderr.write(`[secret-scan] non-fatal hook error (allowing): ${e.message}\n`);
  process.exit(0);
}
