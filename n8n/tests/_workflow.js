'use strict';

const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || path.resolve(__dirname, '..', '..');

function workflowFile(name) {
  const base = path.basename(name);
  const candidates = [
    path.join(workspace, 'n8n', 'workflows', 'core', base),
    path.join(workspace, 'n8n', 'workflows', 'retired', base),
    path.join(workspace, 'n8n', 'workflows', 'support', base),
  ];
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  throw new Error(`workflow not found: ${base}`);
}

function readWorkflow(name) {
  return JSON.parse(fs.readFileSync(workflowFile(name), 'utf8'));
}

module.exports = { workspace, workflowFile, readWorkflow };
