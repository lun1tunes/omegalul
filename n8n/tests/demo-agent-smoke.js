'use strict';
/**
 * Agent — Demo Agent: the template agent generated from an AgentSpec (n8n/templates/agents/demo_agent.py)
 * for the FastAPI service in agents-template/demo_agent (mas-agent-kit).
 *
 * Proves the "add an agent" recipe end to end at the JSON level:
 *  - the workflow has the same shape as the production agents (trigger → open_session → AI Agent + tools → result),
 *  - the registry seed carries the row (enabled=false) with the workflow id — the orchestrator is not touched,
 *  - Runtime Config has the service URL field, the Health Check does not require a disabled agent,
 *  - a long job (tool start_long_job → status in_progress) is passed through to the orchestrator unchanged.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || path.resolve(__dirname, '../..');
const read = (rel) => JSON.parse(fs.readFileSync(path.join(workspace, rel), 'utf8'));
const wf = read('n8n/workflows/support/demo-agent.workflow.json');
const orch = read('n8n/workflows/core/mas-orchestrator.workflow.json');
const runtime = read('n8n/workflows/core/mas-runtime-config.workflow.json');
const health = read('n8n/workflows/core/mas-deployment-health-check.workflow.json');
const seed = read('mas-activity-service/app/sql/agent_registry_seed.json');
const manifest = read('n8n/import-manifest.json');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

// -- shape: same as Excel / Schedule agents ------------------------------------------------------
assert.equal(wf.name, 'Agent — Demo Agent');
assert.equal(wf.active, false);
assert.equal(wf.nodes.some((n) => n.type === 'n8n-nodes-base.webhook'), false, 'sub-workflow, no webhook');
const trigger = wf.nodes.find((n) => n.type === 'n8n-nodes-base.executeWorkflowTrigger');
assert.ok(trigger && trigger.typeVersion === 1.2);
const agent = wf.nodes.find((n) => n.name === 'Demo Agent AI Agent');
assert.equal(agent.type, '@n8n/n8n-nodes-langchain.agent');
assert.equal(agent.parameters.text, '={{ $json.planner_input }}');
assert.equal(wf.nodes.some((n) => n.type === '@n8n/n8n-nodes-langchain.toolHttpRequest'), false, 'tools are httpRequestTool 4.4');
const tools = wf.nodes.filter((n) => n.type === 'n8n-nodes-base.httpRequestTool').map((n) => n.name).sort();
assert.deepEqual(tools, ['ask_engineer', 'count_wells', 'start_long_job']);
for (const name of tools) {
  const node = wf.nodes.find((n) => n.name === name);
  assert.match(String(node.parameters.url), /demo_agent_url/, `${name} reads the service URL from Runtime Config`);
  assert.match(String(node.parameters.url), new RegExp(`/agent-tools/' \\+ "${name}"`), `${name} → POST /agent-tools/${name}`);
  assert.match(String(node.parameters.jsonBody), /session_id/, `${name} carries the session id`);
}
const system = agent.parameters.options.systemMessage;
assert.match(system, /count_wells/);
assert.match(system, /start_long_job/);
assert.equal(/excel_extractor|schedule_builder/.test(system), false, 'the demo prompt knows nothing about other agents');
for (const node of wf.nodes.filter((n) => n.type === 'n8n-nodes-base.httpRequest')) {
  assert.equal(String(node.parameters.url).includes('demo-agent:8300'), false, `${node.name}: no Compose DNS in runtime nodes`);
}

// -- developer log («Лог»): every Activity line names its n8n execution; node failures reach the case --
const errorWf = read('n8n/workflows/core/mas-error-traces.workflow.json');
for (const rel of [
  'n8n/workflows/support/demo-agent.workflow.json',
  'n8n/workflows/core/excel-extractor-agent.workflow.json',
  'n8n/workflows/core/schedule-builder-agent.workflow.json',
]) {
  const w = read(rel);
  assert.equal(w.settings.errorWorkflow, errorWf.id, `${rel}: settings.errorWorkflow → Error — MAS Node Traces (same id as the orchestrator)`);
  const activityNodes = w.nodes.filter((n) => n.type === 'n8n-nodes-base.httpRequest' && String(n.parameters.url).includes("'/events'"));
  assert.ok(activityNodes.length >= 3, `${rel}: accepted / progress / tools Activity lines`);
  for (const node of activityNodes) {
    const body = String(node.parameters.jsonBody);
    assert.match(body, /execution_id: String\(\$execution\.id \|\| ''\)/, `${node.name}: payload.execution_id lets Activity map this execution to the case`);
    assert.match(body, /workflow_id: String\(\$workflow\.id \|\| ''\)/, `${node.name}: payload.workflow_id builds the n8n link in the log`);
    assert.equal(node.onError, 'continueRegularOutput', `${node.name}: a dead Activity never fails the agent`);
  }
}
assert.equal(orch.settings.errorWorkflow, errorWf.id, 'orchestrator and agents share one error workflow');

// -- the orchestrator is untouched: the registry row is the only binding ----------------------------
assert.equal(JSON.stringify(orch).includes('demo_agent'), false, 'adding an agent must not edit the orchestrator');
const row = seed.find((r) => r.agent_id === 'demo_agent');
assert.ok(row, 'agent_registry seed carries the demo agent');
assert.equal(row.enabled, false, 'template agent is off until an engineer enables it');
assert.equal(row.invoke.kind, 'n8n_workflow');
assert.equal(row.invoke.workflow_id, wf.id);
assert.equal(row.invoke.workflow_name, wf.name);
assert.equal(/[a-z]+_[a-z_]+/.test(row.when_to_use), false, 'when_to_use is Russian prose for the planner');

// -- Runtime Config field, Health Check optionality, import manifest -------------------------------
const runtimeSet = runtime.nodes.find((n) => n.name === 'Runtime URLs');
const urlField = runtimeSet.parameters.assignments.assignments.find((a) => a.name === 'demo_agent_url');
assert.ok(urlField, 'MAS — Runtime Config has demo_agent_url (field-editable)');
assert.equal(urlField.value, 'http://demo-agent:8300');
assert.equal(JSON.stringify(health).includes('demo_agent'), false, 'Health Check does not require a disabled agent');
assert.equal(JSON.stringify(health).includes('demo_agent_url'), false);
assert.ok(manifest.full_clean_import_set.includes('workflows/support/demo-agent.workflow.json'));
assert.equal(manifest.runtime_import_order.some((p) => p.includes('demo-agent')), false, 'optional: not in the field runtime order');

// -- Code nodes: the long job flows through as in_progress ----------------------------------------
function sourceCode(name) {
  const node = wf.nodes.find((n) => n.name === name);
  assert.ok(node && node.parameters && node.parameters.jsCode, name);
  return node.parameters.jsCode;
}
async function run(name, json, nodes = {}) {
  const lookup = (nodeName) => {
    if (!Object.prototype.hasOwnProperty.call(nodes, nodeName)) throw new Error(`node not executed: ${nodeName}`);
    const items = [{ json: nodes[nodeName] }];
    return { first: () => items[0], all: () => items };
  };
  const fn = new AsyncFunction('$json', '$', '$input', sourceCode(name));
  const result = await fn(json, lookup, { first: () => ({ json }), all: () => [{ json }] });
  assert.ok(Array.isArray(result) && result[0]?.json);
  return result[0].json;
}

(async () => {
  const opened = { task_id: 'TASK-7', session_id: 'demo_1' };
  const longJob = await run(
    'Summarize AI steps',
    { output: 'Запустил демонстрационный расчёт.', intermediateSteps: [{ action: { tool: 'start_long_job' } }] },
    { 'Open demo session': opened },
  );
  assert.equal(longJob.skip_fetch, false, 'start_long_job stored a result — fetch it');
  const fetched = await run(
    'Format demo result',
    {
      agent_id: 'demo_agent',
      task_id: 'TASK-7',
      status: 'in_progress',
      message: 'Прогон запущен, ориентировочно 1 минута.',
      data: {},
      artifacts: {},
      issues: [],
      assumptions: [],
      requests: [],
      watch: { kind: 'timer', ref: 'demo_1', poll_hint: '15s' },
    },
    { 'Open demo session': opened },
  );
  assert.equal(fetched.status, 'in_progress', 'in_progress reaches the orchestrator (applyAgentResult parks the case as waiting_agent)');
  assert.equal(fetched.agent_id, 'demo_agent');
  assert.equal(fetched.message, 'Прогон запущен, ориентировочно 1 минута.');
  // CASE-6a9f3bc9-10cb9f: the parking event showed payload.watch = {} — Format result dropped the field.
  assert.deepEqual(fetched.watch, { kind: 'timer', ref: 'demo_1', poll_hint: '15s' }, 'watch survives Format result');
  const completed = await run(
    'Format demo result',
    { agent_id: 'demo_agent', task_id: 'TASK-7', status: 'completed', message: 'Готово.', data: { well_count: 3 }, artifacts: {}, issues: [], assumptions: [], requests: [] },
    { 'Open demo session': opened },
  );
  assert.equal('watch' in completed, false, 'no watch key when the agent did not send one');

  const noResult = await run(
    'Summarize AI steps',
    { output: 'Не понял задачу.', intermediateSteps: [] },
    { 'Open demo session': opened },
  );
  assert.equal(noResult.status, 'needs_input');
  assert.match(noResult.requests[0].question, /Опишите задачу/);
  assert.equal(/[a-z]+_[a-z_]+/.test(noResult.requests[0].question), false, 'engineer question has no snake_case');
  assert.deepEqual(noResult.requests[0].accepts.files, []);
  console.log('demo-agent-smoke: ok');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
