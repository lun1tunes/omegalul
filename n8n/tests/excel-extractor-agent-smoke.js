'use strict';
/**
 * Agent — Excel Extractor: one LLM + excel-tools FastAPI.
 * Orchestrator — MAS calls it via executeWorkflow, no webhook adapter.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || path.resolve(__dirname, '../..');
const wf = JSON.parse(
  fs.readFileSync(path.join(workspace, 'n8n/workflows/core/excel-extractor-agent.workflow.json'), 'utf8'),
);
const orch = JSON.parse(
  fs.readFileSync(path.join(workspace, 'n8n/workflows/core/mas-orchestrator.workflow.json'), 'utf8'),
);

assert.equal(wf.name, 'Agent — Excel Extractor');
assert.equal(wf.active, false);
assert.equal(
  wf.nodes.some((n) => n.type === 'n8n-nodes-base.webhook'),
  false,
  'no webhook adapter — Orchestrator uses executeWorkflow',
);
assert.equal(JSON.stringify(wf).includes('Hybrid Retrieval'), false);
assert.equal(JSON.stringify(wf).includes('formBinaryData'), false);
assert.equal(JSON.stringify(wf).includes('.first().binary'), false);
const trigger = wf.nodes.find((n) => n.type === 'n8n-nodes-base.executeWorkflowTrigger');
assert.ok(trigger);
assert.equal(trigger.name, 'When executed by another workflow');
assert.equal(trigger.typeVersion, 1.2);
const agent = wf.nodes.find((n) => n.name === 'Excel Extractor AI Agent');
assert.ok(agent);
assert.equal(agent.type, '@n8n/n8n-nodes-langchain.agent');
assert.equal(agent.typeVersion, 3.1);
assert.equal(agent.parameters.hasOutputParser, false);
const model = wf.nodes.find((n) => n.name === 'Excel Extractor Chat Model — Qwen');
assert.ok(model);
assert.equal(model.typeVersion, 1.3);
assert.equal(model.parameters.options.timeout, 300000);
const tools = wf.nodes.filter((n) => n.type === '@n8n/n8n-nodes-langchain.toolHttpRequest').map((n) => n.name);
for (const name of [
  'workbook_introspect',
  'sheet_preview',
  'detect_tables',
  'match_tables',
  'describe_table',
  'list_column_values',
  'query_table',
  'extract_commissioning',
]) {
  assert.ok(tools.includes(name), name);
}
assert.ok(wf.connections['detect_tables'].ai_tool);
assert.ok(wf.connections['Excel Extractor Chat Model — Qwen'].ai_languageModel);
assert.equal(wf.connections['When executed by another workflow'].main[0][0].node, 'Runtime configuration');
const cap = wf.nodes.find((n) => n.name === 'Capability router');
assert.ok(cap);
assert.equal(cap.type, 'n8n-nodes-base.switch');
assert.equal(wf.connections['Session ready?'].main[0][0].node, 'Capability router');
assert.equal(wf.connections['Capability router'].main[0][0].node, 'Extract commissioning');
assert.equal(wf.connections['Capability router'].main[1][0].node, 'Prepare AI Agent input');
const extract = wf.nodes.find((n) => n.name === 'Extract commissioning');
assert.equal(extract.type, 'n8n-nodes-base.httpRequest');
assert.ok(String(extract.parameters.url).includes('extract_commissioning'));
assert.equal(wf.connections['Extract commissioning'].main[0][0].node, 'Fetch excel result');
assert.equal(wf.connections['Excel Extractor AI Agent'].main[0][0].node, 'Fetch excel result');
const sessionFields = wf.nodes
  .filter((n) => n.type === '@n8n/n8n-nodes-langchain.toolHttpRequest')
  .flatMap((n) => (n.parameters.parametersBody?.values || []).filter((f) => f.name === 'session_id'));
assert.ok(sessionFields.length >= 8);
for (const field of sessionFields) {
  assert.equal(field.valueProvider, 'fieldValue');
  assert.match(String(field.value), /Open excel session/);
  assert.equal(String(field.value).includes('$json.session_id'), false);
}

const cfg = orch.nodes.find((n) => n.name === 'Runtime endpoints');
const url = (cfg.parameters.assignments.assignments || []).find((a) => a.name === 'excel_extractor_url');
assert.equal(url, undefined, 'no HTTP /agent/run URL — specialist is executeWorkflow');
const call = orch.nodes.find((n) => n.name === 'Call Excel Extractor');
assert.equal(call.type, 'n8n-nodes-base.executeWorkflow');
assert.equal(call.typeVersion, 1.3);
assert.equal(call.onError, 'continueRegularOutput');
assert.equal(call.parameters.workflowId.value, 'REPLACE_EXCEL_EXTRACTION_AGENT_IN_UI');
assert.equal(call.parameters.workflowId.cachedResultName, 'Agent — Excel Extractor');
assert.equal(call.parameters.options.waitForSubWorkflow, true);
assert.deepEqual(Object.keys(call.parameters.workflowInputs.value), ['agent_task']);
console.log('excel-extractor-agent-smoke: ok');
