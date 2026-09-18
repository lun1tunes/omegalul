'use strict';
/**
 * Agent — Demo Agent: the template agent generated from an AgentSpec (n8n/templates/agents/demo_agent.py)
 * for the FastAPI service in agents-template/demo_agent (mas-agent-kit).
 *
 * Proves the "add an agent" recipe end to end at the JSON level:
 *  - the workflow has the same shape as the production agents (trigger → open_session → HTTP chat loop → result),
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
assert.equal(wf.nodes.some((n) => n.type === '@n8n/n8n-nodes-langchain.agent'), false, 'http_loop: no LangChain agent');
assert.equal(wf.nodes.some((n) => n.type === '@n8n/n8n-nodes-langchain.lmChatOpenAi'), false, 'http_loop: no Chat Model');
assert.equal(wf.nodes.some((n) => n.type === 'n8n-nodes-base.httpRequestTool'), false);
assert.equal(wf.nodes.some((n) => n.type === '@n8n/n8n-nodes-langchain.toolHttpRequest'), false);
const chat = wf.nodes.find((n) => n.name === 'Demo Agent Agent chat');
assert.ok(chat);
assert.equal(chat.type, 'n8n-nodes-base.httpRequest');
assert.equal(chat.parameters.nodeCredentialType, 'openAiApi');
assert.equal(chat.parameters.options.timeout, 600000);
assert.equal(wf.settings.executionTimeout, 1800);
function sourceCode(name) {
  const node = wf.nodes.find((n) => n.name === name);
  assert.ok(node && node.parameters && node.parameters.jsCode, name);
  return node.parameters.jsCode;
}
const buildJs = sourceCode('Build chat request');
assert.match(buildJs, /count_wells/);
assert.match(buildJs, /start_long_job/);
assert.match(buildJs, /retrieve_knowledge/);
assert.equal(/excel_extractor|schedule_builder/.test(buildJs), false, 'the demo prompt knows nothing about other agents');
assert.equal(wf.connections['Restore after Demo Agent RAG'].main[0][0].node, 'Build chat request');
assert.equal(wf.connections['Agent loop router'].main[3][0].node, 'Summarize AI steps');
assert.equal(wf.connections['Restore after AI tools'].main[0][0].node, 'Fetch demo result');
assert.equal(wf.connections['Fetch demo result'].main[0][0].node, 'Format demo result');
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
async function run(name, json, nodes = {}) {
  const lookup = (nodeName) => {
    if (!Object.prototype.hasOwnProperty.call(nodes, nodeName)) throw new Error(`node not executed: ${nodeName}`);
    const items = [{ json: nodes[nodeName] }];
    return { first: () => items[0], last: () => items[0], all: () => items, item: items[0] };
  };
  const fn = new AsyncFunction('$json', '$', '$input', sourceCode(name));
  const result = await fn(json, lookup, { first: () => ({ json }), last: () => ({ json }), all: () => [{ json }], item: { json } });
  assert.ok(Array.isArray(result) && result[0]?.json);
  return result[0].json;
}

(async () => {
  const opened = { task_id: 'TASK-7', session_id: 'demo_1' };
  const longJob = await run(
    'Summarize AI steps',
    { llm_final_text: 'Запустил демонстрационный расчёт.', tool_log: ['start_long_job'], iteration: 2 },
    { 'Open demo session': opened },
  );
  assert.equal(longJob.total_calls, 1);
  assert.ok(longJob.tools_used.includes('start_long_job'));
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
    'Format demo result',
    {},
    { 'Open demo session': opened, 'Summarize AI steps': { llm_final_text: 'Опишите задачу одним-двумя предложениями.' } },
  );
  assert.equal(noResult.status, 'needs_input');
  assert.match(noResult.requests[0].question, /Опишите задачу/);
  assert.equal(/[a-z]+_[a-z_]+/.test(noResult.requests[0].question), false, 'engineer question has no snake_case');
  assert.deepEqual(noResult.requests[0].accepts.files, []);

  const MACHINE = /[a-z]+_[a-z_]+|[a-z_]+=[a-z0-9]+|[{}\[\]]|\w\|\w/;
  const down = await run(
    'Format missing demo',
    { error: { message: 'connect ECONNREFUSED' } },
    { 'Runtime configuration': { demo_agent_url: 'http://127.0.0.1:8300' } },
  );
  assert.equal(down.status, 'failed');
  assert.equal(down.issues[0].code, 'service_unreachable');
  assert.match(down.message, /Demo Agent/);
  assert.equal(MACHINE.test(down.message), false, down.message);

  const llmDown = await run(
    'Format demo result',
    {},
    { 'Open demo session': opened, 'Summarize AI steps': { llm_final_text: '', llm_unavailable: true } },
  );
  assert.equal(llmDown.status, 'failed');
  assert.equal(llmDown.issues[0].code, 'llm_unavailable');
  assert.match(llmDown.message, /Модель чата не ответила/);
  assert.equal(MACHINE.test(llmDown.message), false, llmDown.message);

  console.log('demo-agent-smoke: ok');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
