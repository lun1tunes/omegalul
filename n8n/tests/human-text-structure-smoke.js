'use strict';
/**
 * Phase 1.3 structural smoke: engineer-facing Cyrillic string literals in Code nodes
 * must pass looksMachineText (no snake_case, key=value, JSON braces, a|b).
 * Does not scan Decision/Verify/Interpret SYSTEM prompts (Build decision chat /
 * Build verify chat / Build interpret chat) or the agent SYSTEM (Build chat request),
 * only engineer-facing jsCode.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || path.resolve(__dirname, '../..');
const coreDir = path.join(workspace, 'n8n', 'workflows', 'core');

const MACHINE = /[a-z]+_[a-z_]+|[a-z_]+=[a-z0-9]+|[{}\[\]]|\w\|\w/;
const CYRILLIC = /[А-Яа-яЁё]{3,}/;

function extractQuoted(js) {
  // Single-line only (same as `.` without `/s`). Matching across newlines glued nested
  // templates into JS bodies (106 false hits). `(?:\\.| (?!\1) | .)` ReDoS-hung Parse decision.
  const out = [];
  const re = /(['"`])(?:\\.|(?!\1)[^\n])*?\1/g;
  let m;
  while ((m = re.exec(js))) {
    let body = m[0].slice(1, -1);
    if (m[1] === '`') body = body.replace(/\$\{[^}]*\}/g, ' ');
    body = body.replace(/\\n/g, '\n').replace(/\\'/g, "'").replace(/\\"/g, '"');
    out.push(body);
  }
  return out;
}

const files = fs.readdirSync(coreDir).filter((name) => name.endsWith('.workflow.json'));
assert.ok(files.includes('mas-orchestrator.workflow.json'));
const skipNames = new Set([
  'Build decision chat',
  'Build verify chat',
  'Build interpret chat',
  'Build chat request',
  'Prepare tool call',
  'Normalize control-plane request',
]);
const problems = [];
for (const file of files) {
  const wf = JSON.parse(fs.readFileSync(path.join(coreDir, file), 'utf8'));
  for (const node of wf.nodes || []) {
    if (node.type !== 'n8n-nodes-base.code') continue;
    if (skipNames.has(node.name)) continue;
    if (/^Attach /.test(node.name) && /RAG|retrieve/i.test(node.name)) continue;
    const js = String((node.parameters && node.parameters.jsCode) || '');
    for (const text of extractQuoted(js)) {
      const stripped = text.trim();
      if (stripped.includes('${')) continue;
      if (/^[):;,]/.test(stripped)) continue;
      if (!CYRILLIC.test(stripped)) continue;
      if (!MACHINE.test(stripped)) continue;
      problems.push(`${file} :: ${node.name} :: ${JSON.stringify(stripped).slice(0, 180)}`);
    }
  }
}
assert.equal(problems.length, 0, `machine tokens in engineer-facing Code strings:\n${problems.join('\n')}`);

const masState = fs.readFileSync(path.join(workspace, 'n8n', 'templates', 'mas_state_utils.py'), 'utf8');
const agentWf = fs.readFileSync(path.join(workspace, 'n8n', 'templates', 'mas_agent_workflow.py'), 'utf8');
function looksMachineBody(src, name) {
  const m = src.match(new RegExp(`function ${name}\\(text\\)\\{\\s*const s=String\\(text\\|\\|''\\);([\\s\\S]*?)\\n\\}`));
  assert.ok(m, name);
  return m[1].replace(/\s+/g, ' ').trim();
}
assert.equal(looksMachineBody(masState, 'looksMachineText'), looksMachineBody(agentWf, 'looksMachine'));

console.log(`human-text-structure-smoke: ${files.length} workflows, no machine tokens in Cyrillic Code literals`);
