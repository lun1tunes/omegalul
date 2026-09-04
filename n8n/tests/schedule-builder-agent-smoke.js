'use strict';
/**
 * Agent — Schedule Builder: one LLM + FastAPI tools.
 * Orchestrator — MAS calls it via executeWorkflow (Excel Extractor shape), no webhook adapter.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || path.resolve(__dirname, '../..');
const wf = JSON.parse(
  fs.readFileSync(path.join(workspace, 'n8n/workflows/core/schedule-builder-agent.workflow.json'), 'utf8'),
);
const orch = JSON.parse(
  fs.readFileSync(path.join(workspace, 'n8n/workflows/core/mas-orchestrator.workflow.json'), 'utf8'),
);

assert.equal(wf.name, 'Agent — Schedule Builder');
assert.equal(wf.active, false);
assert.equal(
  wf.nodes.some((n) => n.type === 'n8n-nodes-base.webhook'),
  false,
  'no webhook adapter — Orchestrator uses executeWorkflow',
);
const trigger = wf.nodes.find((n) => n.type === 'n8n-nodes-base.executeWorkflowTrigger');
assert.ok(trigger);
assert.equal(trigger.name, 'When executed by another workflow');
assert.equal(trigger.typeVersion, 1.2);
const agent = wf.nodes.find((n) => n.name === 'Schedule Builder AI Agent');
assert.ok(agent);
assert.equal(agent.type, '@n8n/n8n-nodes-langchain.agent');
assert.equal(agent.typeVersion, 3.1);
assert.equal(agent.parameters.hasOutputParser, false);
function sourceCode(name) {
  const node = wf.nodes.find((n) => n.name === name);
  assert.ok(node && node.parameters && node.parameters.jsCode, name);
  return node.parameters.jsCode;
}
const model = wf.nodes.find((n) => n.name === 'Schedule Builder Chat Model — Qwen');
assert.ok(model);
assert.equal(model.typeVersion, 1.3);
assert.equal(model.parameters.options.timeout, 300000);
const tools = wf.nodes.filter((n) => n.type === '@n8n/n8n-nodes-langchain.toolHttpRequest').map((n) => n.name);
for (const name of [
  'inspect_schedule',
  'search_keywords',
  'get_keyword',
  'apply_commissioning',
  'apply_group_rebind',
  'apply_operations',
  'build_schedule',
]) {
  assert.ok(tools.includes(name), name);
}
assert.ok(wf.connections['search_keywords'].ai_tool);
assert.ok(wf.connections['Schedule Builder Chat Model — Qwen'].ai_languageModel);
assert.equal(wf.connections['When executed by another workflow'].main[0][0].node, 'Runtime configuration');
const runtimeCfg = wf.nodes.find((n) => n.name === 'Runtime configuration');
assert.equal(runtimeCfg.type, 'n8n-nodes-base.executeWorkflow');
assert.equal(runtimeCfg.parameters.workflowId.value, 'REPLACE_MAS_RUNTIME_CONFIG_IN_UI');
assert.equal(runtimeCfg.parameters.workflowId.cachedResultName, 'MAS — Runtime Config');
const cap = wf.nodes.find((n) => n.name === 'Capability router');
assert.ok(cap);
assert.equal(cap.type, 'n8n-nodes-base.switch');
assert.equal(wf.connections['Session ready?'].main[0][0].node, 'Activity — Schedule Builder accepted');
assert.equal(wf.connections['Restore after Schedule Builder progress'].main[0][0].node, 'Capability router');
assert.equal(wf.connections['Capability router'].main[0][0].node, 'Apply commissioning');
assert.equal(wf.connections['Capability router'].main[1][0].node, 'Apply group rebind');
assert.equal(wf.connections['Capability router'].main[2][0].node, 'Prepare AI Agent input');
const applyComm = wf.nodes.find((n) => n.name === 'Apply commissioning');
assert.equal(applyComm.type, 'n8n-nodes-base.httpRequest');
assert.ok(String(applyComm.parameters.url).includes('apply_commissioning'));
assert.equal(applyComm.retryOnFail, true);
assert.equal(wf.connections['Apply commissioning'].main[0][0].node, 'Describe apply result');
assert.equal(wf.connections['Apply group rebind'].main[0][0].node, 'Describe apply result');
assert.equal(wf.connections['Describe apply result'].main[0][0].node, 'Activity — Schedule Builder apply');
assert.equal(wf.connections['Restore after apply event'].main[0][0].node, 'Apply finished?');
assert.equal(wf.connections['Apply finished?'].main[0][0].node, 'Format schedule result');
assert.equal(wf.connections['Apply finished?'].main[1][0].node, 'Fetch schedule result');
assert.equal(wf.connections['Schedule Builder AI Agent'].main[0][0].node, 'Summarize AI steps');
assert.equal(wf.connections['AI applied?'].main[0][0].node, 'Format schedule result');
assert.equal(wf.connections['AI applied?'].main[1][0].node, 'Fetch schedule result');
assert.equal(wf.connections['Format schedule result'].main[0][0].node, 'Close schedule session');
assert.equal(agent.parameters.options.maxIterations, 6);
assert.equal(wf.settings.executionTimeout, 900);
assert.ok(sourceCode('Describe apply result').includes('skip_fetch'));
assert.ok(sourceCode('Restore after Schedule Builder progress').includes('operations'));
const applyOps = wf.nodes.find((n) => n.name === 'apply_operations');
assert.ok(String(applyOps.parameters.toolDescription).includes('массив') || String(applyOps.parameters.toolDescription).includes('Массив'));
const activityProgress = wf.nodes.find((n) => n.name === 'Activity — Schedule Builder progress');
assert.equal(activityProgress.parameters.options.timeout, 2000);
assert.equal(activityProgress.parameters.options.response.response.neverError, true);

const cfg = orch.nodes.find((n) => n.name === 'Runtime endpoints');
assert.equal(cfg.type, 'n8n-nodes-base.executeWorkflow');
assert.equal(cfg.parameters.workflowId.value, 'REPLACE_MAS_RUNTIME_CONFIG_IN_UI');
const call = orch.nodes.find((n) => n.name === 'Call Schedule Builder');
assert.equal(call.type, 'n8n-nodes-base.executeWorkflow');
assert.equal(call.typeVersion, 1.3);
assert.equal(call.onError, 'continueRegularOutput');
assert.equal(call.parameters.workflowId.value, 'REPLACE_SCHEDULE_BUILDER_AGENT_IN_UI');
assert.equal(call.parameters.workflowId.cachedResultName, 'Agent — Schedule Builder');
assert.equal(call.parameters.options.waitForSubWorkflow, true);
assert.deepEqual(Object.keys(call.parameters.workflowInputs.value), ['agent_task']);
console.log('schedule-builder-agent-smoke: ok');
