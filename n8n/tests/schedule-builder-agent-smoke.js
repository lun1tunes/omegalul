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
const cap = wf.nodes.find((n) => n.name === 'Capability router');
assert.ok(cap);
assert.equal(cap.type, 'n8n-nodes-base.switch');
assert.equal(wf.connections['Session ready?'].main[0][0].node, 'Capability router');
assert.equal(wf.connections['Capability router'].main[0][0].node, 'Apply commissioning');
assert.equal(wf.connections['Capability router'].main[1][0].node, 'Apply group rebind');
assert.equal(wf.connections['Capability router'].main[2][0].node, 'Prepare AI Agent input');
const applyComm = wf.nodes.find((n) => n.name === 'Apply commissioning');
assert.equal(applyComm.type, 'n8n-nodes-base.httpRequest');
assert.ok(String(applyComm.parameters.url).includes('apply_commissioning'));
assert.equal(wf.connections['Apply commissioning'].main[0][0].node, 'Fetch schedule result');
assert.equal(wf.connections['Apply group rebind'].main[0][0].node, 'Fetch schedule result');

const cfg = orch.nodes.find((n) => n.name === 'Runtime endpoints');
const url = (cfg.parameters.assignments.assignments || []).find((a) => a.name === 'schedule_builder_url');
assert.equal(url, undefined, 'no HTTP webhook URL — specialist is executeWorkflow');
const call = orch.nodes.find((n) => n.name === 'Call Schedule Builder');
assert.equal(call.type, 'n8n-nodes-base.executeWorkflow');
assert.equal(call.typeVersion, 1.3);
assert.equal(call.onError, 'continueRegularOutput');
assert.equal(call.parameters.workflowId.value, 'REPLACE_SCHEDULE_BUILDER_AGENT_IN_UI');
assert.equal(call.parameters.workflowId.cachedResultName, 'Agent — Schedule Builder');
assert.equal(call.parameters.options.waitForSubWorkflow, true);
assert.deepEqual(Object.keys(call.parameters.workflowInputs.value), ['agent_task']);
console.log('schedule-builder-agent-smoke: ok');
