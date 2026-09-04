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
function sourceCode(name) {
  const node = wf.nodes.find((n) => n.name === name);
  assert.ok(node && node.parameters && node.parameters.jsCode, name);
  return node.parameters.jsCode;
}
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
const runtimeCfg = wf.nodes.find((n) => n.name === 'Runtime configuration');
assert.equal(runtimeCfg.type, 'n8n-nodes-base.executeWorkflow');
assert.equal(runtimeCfg.parameters.workflowId.value, 'REPLACE_MAS_RUNTIME_CONFIG_IN_UI');
assert.equal(runtimeCfg.parameters.workflowId.cachedResultName, 'MAS — Runtime Config');
assert.equal(JSON.stringify(wf).includes('excel_tools_api_key'), false);
const openSession = wf.nodes.find((n) => n.name === 'Open excel session');
assert.equal(openSession.parameters.authentication, 'genericCredentialType');
assert.equal(openSession.parameters.genericAuthType, 'httpHeaderAuth');
assert.equal(openSession.credentials.httpHeaderAuth.name, 'REPLACE: Excel Tools X-API-Key');
assert.equal(openSession.retryOnFail, true);
const introspect = wf.nodes.find((n) => n.name === 'workbook_introspect');
assert.equal(introspect.parameters.authentication, 'genericCredentialType');
assert.equal(introspect.credentials.httpHeaderAuth.name, 'REPLACE: Excel Tools X-API-Key');
const cap = wf.nodes.find((n) => n.name === 'Capability router');
assert.ok(cap);
assert.equal(cap.type, 'n8n-nodes-base.switch');
assert.equal(wf.connections['Session ready?'].main[0][0].node, 'Activity — Excel Extractor accepted');
assert.equal(wf.connections['Restore after Excel Extractor progress'].main[0][0].node, 'Capability router');
assert.equal(wf.connections['Capability router'].main[0][0].node, 'Extract commissioning');
assert.equal(wf.connections['Capability router'].main[1][0].node, 'Prepare AI Agent input');
const extract = wf.nodes.find((n) => n.name === 'Extract commissioning');
assert.equal(extract.type, 'n8n-nodes-base.httpRequest');
assert.ok(String(extract.parameters.url).includes('extract_commissioning'));
assert.equal(extract.retryOnFail, true);
assert.equal(wf.connections['Extract commissioning'].main[0][0].node, 'Describe extract result');
assert.equal(wf.connections['Describe extract result'].main[0][0].node, 'Activity — Excel Extractor extract');
assert.equal(wf.connections['Restore after extract event'].main[0][0].node, 'Extract finished?');
assert.equal(wf.connections['Extract finished?'].main[0][0].node, 'Format excel result');
assert.equal(wf.connections['Extract finished?'].main[1][0].node, 'Fetch excel result');
assert.equal(wf.connections['Excel Extractor AI Agent'].main[0][0].node, 'Summarize AI steps');
assert.equal(wf.connections['AI extracted?'].main[0][0].node, 'Format excel result');
assert.equal(wf.connections['AI extracted?'].main[1][0].node, 'Fetch excel result');
assert.equal(wf.connections['Format excel result'].main[0][0].node, 'Close excel session');
assert.equal(agent.parameters.options.maxIterations, 6);
assert.equal(wf.settings.executionTimeout, 900);
assert.ok(sourceCode('Describe extract result').includes('skip_fetch'));
assert.ok(sourceCode('Restore after Excel Extractor progress').includes('operations'));
assert.ok(sourceCode('Summarize AI steps').includes('query_table'));
const queryTable = wf.nodes.find((n) => n.name === 'query_table');
assert.ok(String(queryTable.parameters.toolDescription).includes('массив') || String(queryTable.parameters.toolDescription).includes('Массив'));
const activityProgress = wf.nodes.find((n) => n.name === 'Activity — Excel Extractor progress');
assert.equal(activityProgress.parameters.options.timeout, 2000);
assert.equal(activityProgress.parameters.options.response.response.neverError, true);
assert.equal(activityProgress.parameters.authentication, undefined);
const close = wf.nodes.find((n) => n.name === 'Close excel session');
assert.ok(String(close.parameters.url).includes('/close'));
assert.equal(close.parameters.authentication, 'genericCredentialType');
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
assert.equal(cfg.type, 'n8n-nodes-base.executeWorkflow');
assert.equal(cfg.parameters.workflowId.value, 'REPLACE_MAS_RUNTIME_CONFIG_IN_UI');
const call = orch.nodes.find((n) => n.name === 'Call Excel Extractor');
assert.equal(call.type, 'n8n-nodes-base.executeWorkflow');
assert.equal(call.typeVersion, 1.3);
assert.equal(call.onError, 'continueRegularOutput');
assert.equal(call.parameters.workflowId.value, 'REPLACE_EXCEL_EXTRACTION_AGENT_IN_UI');
assert.equal(call.parameters.workflowId.cachedResultName, 'Agent — Excel Extractor');
assert.equal(call.parameters.options.waitForSubWorkflow, true);
assert.deepEqual(Object.keys(call.parameters.workflowInputs.value), ['agent_task']);
console.log('excel-extractor-agent-smoke: ok');
