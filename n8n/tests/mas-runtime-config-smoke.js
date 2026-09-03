'use strict';
/**
 * MAS — Runtime Config: one Set of service URLs, no secrets, no $env/$vars.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || path.resolve(__dirname, '../..');
const read = (rel) => JSON.parse(fs.readFileSync(path.join(workspace, rel), 'utf8'));
const wf = read('n8n/workflows/core/mas-runtime-config.workflow.json');
const excel = read('n8n/workflows/core/excel-extractor-agent.workflow.json');
const schedule = read('n8n/workflows/core/schedule-builder-agent.workflow.json');
const orch = read('n8n/workflows/core/mas-orchestrator.workflow.json');

assert.equal(wf.name, 'MAS — Runtime Config');
assert.equal(wf.active, false);
assert.equal(wf.settings.saveDataSuccessExecution, 'none');
assert.equal(wf.settings.callerPolicy, 'workflowsFromSameOwner');
assert.equal(wf.settings.errorWorkflow || '', '');
assert.equal(wf.nodes.some((n) => n.type === 'n8n-nodes-base.webhook'), false);

const trigger = wf.nodes.find((n) => n.type === 'n8n-nodes-base.executeWorkflowTrigger');
assert.ok(trigger);
assert.equal(wf.connections[trigger.name].main[0][0].node, 'Runtime URLs');

const urls = wf.nodes.find((n) => n.name === 'Runtime URLs');
assert.equal(urls.type, 'n8n-nodes-base.set');
assert.equal(urls.parameters.includeOtherFields, false);
const fields = Object.fromEntries(
  (urls.parameters.assignments.assignments || []).map((a) => [a.name, a.value]),
);
assert.deepEqual(fields, {
  activity_base_url: 'http://mas-activity:8200',
  excel_tools_url: 'http://excel-tools:8000',
  schedule_service_url: 'http://schedule-builder:8090',
  math_url: 'http://math-service:8100',
});
const blob = JSON.stringify(wf);
assert.equal(blob.includes('excel_tools_api_key'), false);
assert.equal(blob.includes('$env'), false);
assert.equal(blob.includes('$vars'), false);

for (const [owner, nodeName] of [
  [excel, 'Runtime configuration'],
  [schedule, 'Runtime configuration'],
  [orch, 'Runtime endpoints'],
]) {
  const node = owner.nodes.find((n) => n.name === nodeName);
  assert.ok(node, nodeName);
  assert.equal(node.type, 'n8n-nodes-base.executeWorkflow');
  assert.equal(node.parameters.workflowId.value, 'REPLACE_MAS_RUNTIME_CONFIG_IN_UI');
  assert.equal(node.parameters.workflowId.cachedResultName, 'MAS — Runtime Config');
  assert.equal(node.onError, undefined);
}

assert.equal(JSON.stringify(excel).includes('excel_tools_api_key'), false);
assert.equal(JSON.stringify(orch).includes('excel_tools_api_key'), false);
console.log('mas-runtime-config-smoke: ok');
