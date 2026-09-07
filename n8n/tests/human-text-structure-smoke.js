'use strict';
/**
 * Phase 1.3 structural smoke: engineer-facing Cyrillic string literals in Code nodes
 * must pass looksMachineText (no snake_case, key=value, JSON braces, a|b).
 * Does not scan Decision/Verify/Interpret SYSTEM prompts (chainLlm), only jsCode.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || path.resolve(__dirname, '../..');
const coreDir = path.join(workspace, 'n8n', 'workflows', 'core');

const MACHINE = /[a-z]+_[a-z_]+|[a-z_]+=[a-z0-9]+|[{}\[\]]|\w\|\w/;
const CYRILLIC = /[А-Яа-яЁё]{3,}/;

function extractQuoted(js) {
  const out = [];
  const re = /(['"`])((?:\\.|(?!\1)|.(?!\1))*?)\1/gs;
  let m;
  while ((m = re.exec(js))) {
    let body = m[2];
    if (m[1] === '`') body = body.replace(/\$\{[^}]*\}/g, ' ');
    body = body.replace(/\\n/g, '\n').replace(/\\'/g, "'").replace(/\\"/g, '"');
    out.push(body);
  }
  return out;
}

const files = fs.readdirSync(coreDir).filter((name) => name.endsWith('.workflow.json'));
assert.ok(files.includes('mas-orchestrator.workflow.json'));
const problems = [];
for (const file of files) {
  const wf = JSON.parse(fs.readFileSync(path.join(coreDir, file), 'utf8'));
  for (const node of wf.nodes || []) {
    if (node.type !== 'n8n-nodes-base.code') continue;
    const js = String((node.parameters && node.parameters.jsCode) || '');
    for (const text of extractQuoted(js)) {
      const stripped = text.trim();
      if (!CYRILLIC.test(stripped)) continue;
      if (!MACHINE.test(stripped)) continue;
      problems.push(`${file} :: ${node.name} :: ${JSON.stringify(stripped).slice(0, 180)}`);
    }
  }
}
assert.equal(problems.length, 0, `machine tokens in engineer-facing Code strings:\n${problems.join('\n')}`);
console.log(`human-text-structure-smoke: ${files.length} workflows, no machine tokens in Cyrillic Code literals`);
