'use strict';
/**
 * Agent — tNav Cluster Agent: workflow генерируется из AgentSpec (n8n/templates/agents/tnav_cluster.py)
 * для сервиса tnav-cluster-service (mas-agent-kit + paramiko до ГД-кластера).
 *
 * Что доказывает на уровне JSON:
 *  - форма как у боевых агентов (trigger → open_session → HTTP chat loop → result), без LangChain-нод;
 *  - оркестратор не тронут: единственная привязка — строка ``agent_registry`` с id workflow;
 *  - строка реестра посеяна enabled=false (в поле включает инженер, когда прописан доступ к кластеру);
 *  - Runtime Config несёт tnav_cluster_url, адресов Docker в runtime-нодах нет;
 *  - долгий расчёт проходит наружу как in_progress с watch.kind=cluster_run (паркинг кейса),
 *    а мёртвый сервис и молчащая модель дают failed с русским текстом, а не ложный HITL.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || path.resolve(__dirname, '../..');
const read = (rel) => JSON.parse(fs.readFileSync(path.join(workspace, rel), 'utf8'));
const wf = read('n8n/workflows/core/tnav-cluster-agent.workflow.json');
const orch = read('n8n/workflows/core/mas-orchestrator.workflow.json');
const runtime = read('n8n/workflows/core/mas-runtime-config.workflow.json');
const health = read('n8n/workflows/core/mas-deployment-health-check.workflow.json');
const seed = read('mas-activity-service/app/sql/agent_registry_seed.json');
const manifest = read('n8n/import-manifest.json');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const MACHINE = /[a-z]+_[a-z_]+|[a-z_]+=[a-z0-9]+|[{}\[\]]|\w\|\w/;

// -- форма: как у Excel / Schedule агентов ---------------------------------------------------------
assert.equal(wf.name, 'Agent — tNav Cluster Agent');
assert.equal(wf.active, false);
assert.equal(wf.nodes.some((n) => n.type === 'n8n-nodes-base.webhook'), false, 'sub-workflow, no webhook');
const trigger = wf.nodes.find((n) => n.type === 'n8n-nodes-base.executeWorkflowTrigger');
assert.ok(trigger && trigger.typeVersion === 1.2);
assert.equal(wf.nodes.some((n) => n.type === '@n8n/n8n-nodes-langchain.agent'), false, 'http_loop: no LangChain agent');
assert.equal(wf.nodes.some((n) => n.type === '@n8n/n8n-nodes-langchain.lmChatOpenAi'), false, 'http_loop: no Chat Model');
assert.equal(wf.nodes.some((n) => n.type === 'n8n-nodes-base.httpRequestTool'), false);
assert.equal(wf.nodes.some((n) => n.type === '@n8n/n8n-nodes-langchain.toolHttpRequest'), false);
const chat = wf.nodes.find((n) => n.name === 'tNav Cluster Agent Agent chat');
assert.ok(chat, 'chat node');
assert.equal(chat.type, 'n8n-nodes-base.httpRequest');
assert.equal(chat.parameters.nodeCredentialType, 'openAiApi');
assert.equal(chat.parameters.options.timeout, 600000);
assert.equal(wf.settings.executionTimeout, 1800);
// Тело запроса к модели собирает Code-нода (Build chat request), а не сам HTTP-узел.
assert.equal(String(chat.parameters.jsonBody || ''), '={{ $json.chat_request }}');

function sourceCode(name) {
  const node = wf.nodes.find((n) => n.name === name);
  assert.ok(node && node.parameters && node.parameters.jsCode, name);
  return node.parameters.jsCode;
}
const buildJs = sourceCode('Build chat request');
for (const tool of ['list_models', 'inspect_model', 'prepare_model_version', 'start_calculation', 'check_calculation', 'ask_engineer']) {
  assert.match(buildJs, new RegExp(tool), `tool ${tool} is in the chat request schema`);
}
assert.match(buildJs, /retrieve_knowledge/);
assert.match(buildJs, /function calling/, 'CASE-6aae41e4-a48ba9: Qwen wrote inspect_model as content; prompt must require tool_calls');
assert.equal(/excel_extractor|schedule_builder|demo_agent/.test(buildJs), false, 'the cluster prompt knows nothing about other agents');
assert.equal(/\brm\b|rmdir/.test(buildJs), false, 'the prompt never mentions deleting files');
assert.equal(wf.connections['Restore after tNav Cluster Agent RAG'].main[0][0].node, 'Build chat request');
assert.equal(wf.connections['Agent loop router'].main[3][0].node, 'Summarize AI steps');
assert.equal(wf.connections['Restore after AI tools'].main[0][0].node, 'Fetch cluster result');
assert.equal(wf.connections['Fetch cluster result'].main[0][0].node, 'Format cluster result');
for (const node of wf.nodes.filter((n) => n.type === 'n8n-nodes-base.httpRequest')) {
  assert.equal(String(node.parameters.url).includes('tnav-cluster:8400'), false, `${node.name}: no Compose DNS in runtime nodes`);
}
const blob = JSON.stringify(wf);
assert.equal(blob.includes('$env'), false);
assert.equal(blob.includes('$vars'), false);
// Пароль кластера живёт только в tnav-cluster.env на рабочей станции, не в workflow.
assert.equal(/TNAV_SSH_PASSWORD|cluster-pass/.test(blob), false, 'no cluster credentials in the workflow JSON');

// -- лог разработчика: каждая строка Activity называет свой execution -------------------------------
const errorWf = read('n8n/workflows/core/mas-error-traces.workflow.json');
assert.equal(wf.settings.errorWorkflow, errorWf.id, 'settings.errorWorkflow → Error — MAS Node Traces');
const activityNodes = wf.nodes.filter((n) => n.type === 'n8n-nodes-base.httpRequest' && String(n.parameters.url).includes("'/events'"));
assert.ok(activityNodes.length >= 3, 'accepted / progress / tools Activity lines');
for (const node of activityNodes) {
  const body = String(node.parameters.jsonBody);
  assert.match(body, /execution_id: String\(\$execution\.id \|\| ''\)/, `${node.name}: payload.execution_id`);
  assert.match(body, /workflow_id: String\(\$workflow\.id \|\| ''\)/, `${node.name}: payload.workflow_id`);
  assert.equal(node.onError, 'continueRegularOutput', `${node.name}: a dead Activity never fails the agent`);
}

// -- оркестратор не тронут: привязка — только строка реестра ----------------------------------------
assert.equal(JSON.stringify(orch).includes('tnav_cluster'), false, 'adding an agent must not edit the orchestrator');
const row = seed.find((r) => r.agent_id === 'tnav_cluster');
assert.ok(row, 'agent_registry seed carries the cluster agent');
assert.equal(row.enabled, false, 'off until the engineer fills tnav-cluster.env and enables it in Activity');
assert.equal(row.invoke.kind, 'n8n_workflow');
assert.equal(row.invoke.workflow_id, wf.id);
assert.equal(row.invoke.workflow_name, wf.name);
assert.equal(MACHINE.test(row.when_to_use), false, 'when_to_use is Russian prose for the planner');
assert.deepEqual(row.output_provides, ['model_version', 'run_status', 'run_results']);
assert.deepEqual(row.input_required, []);

// -- Runtime Config, Health Check, манифест импорта --------------------------------------------------
const runtimeSet = runtime.nodes.find((n) => n.name === 'Runtime URLs');
const urlField = runtimeSet.parameters.assignments.assignments.find((a) => a.name === 'tnav_cluster_url');
assert.ok(urlField, 'MAS — Runtime Config has tnav_cluster_url (field-editable)');
assert.equal(urlField.value, 'http://tnav-cluster:8400');
assert.equal(JSON.stringify(health).includes('tnav_cluster'), false, 'Health Check does not require a disabled agent');
assert.ok(manifest.full_clean_import_set.includes('workflows/core/tnav-cluster-agent.workflow.json'));
assert.ok(manifest.runtime_import_order.includes('workflows/core/tnav-cluster-agent.workflow.json'));
assert.ok(
  manifest.ui_configuration.some((line) => line.includes('tnav_cluster_url') && line.includes('tnav-cluster.env')),
  'the manifest tells the engineer where the cluster URL and credentials go',
);
const redeploy = fs.readFileSync(path.join(workspace, 'scripts/lab_soft_redeploy.py'), 'utf8');
assert.ok(redeploy.includes('"Agent — tNav Cluster Agent"'), 'lab import activates the cluster agent workflow');
const corpus = read('n8n/rag/excel-agent-operating-guide.documents.json');
const clusterCard = (corpus.documents || []).find((d) => d.knowledge_id === 'route-cluster-calculation');
assert.ok(clusterCard, 'orchestrator routing has the cluster policy card');
assert.deepEqual(clusterCard.topics, ['model_version', 'run_status', 'run_results']);
assert.equal((clusterCard.topics || []).includes('absent_agent'), false, 'cluster card is retrieved when the agent is enabled, not via absent_agent');

// -- Code-ноды: долгий расчёт уходит наружу как in_progress ------------------------------------------
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
  const opened = { task_id: 'TASK-9', session_id: 'clu_1' };
  const steps = await run(
    'Summarize AI steps',
    { llm_final_text: 'Запустил расчёт модели на кластере.', tool_log: ['inspect_model', 'prepare_model_version', 'start_calculation'], iteration: 3 },
    { 'Open cluster session': opened },
  );
  assert.equal(steps.total_calls, 3);
  assert.ok(steps.tools_used.includes('start_calculation'));

  const parked = await run(
    'Format cluster result',
    {
      agent_id: 'tnav_cluster',
      task_id: 'TASK-9',
      status: 'in_progress',
      message: 'Расчёт модели запущен на гидродинамическом кластере. Сообщу, когда он закончится.',
      data: {},
      artifacts: {},
      issues: [],
      assumptions: [],
      requests: [],
      watch: { kind: 'cluster_run', ref: 'run_abc123', poll_hint: '10m' },
    },
    { 'Open cluster session': opened },
  );
  assert.equal(parked.status, 'in_progress', 'in_progress reaches the orchestrator (case parks as waiting_agent)');
  assert.deepEqual(parked.watch, { kind: 'cluster_run', ref: 'run_abc123', poll_hint: '10m' }, 'watch survives Format result');
  assert.equal(MACHINE.test(parked.message), false, parked.message);

  const done = await run(
    'Format cluster result',
    {
      agent_id: 'tnav_cluster',
      task_id: 'TASK-9',
      status: 'completed',
      message: 'Расчёт модели на гидродинамическом кластере завершён: 14 расчётных шагов, время расчёта 2 часа 13 минут.',
      data: { run_status: 'finished', run_results: { state: 'finished', steps: 14 } },
      artifacts: {},
      issues: [],
      assumptions: [],
      requests: [],
    },
    { 'Open cluster session': opened },
  );
  assert.equal('watch' in done, false, 'no watch key when the agent did not send one');
  assert.equal(done.data.run_status, 'finished');

  const noResult = await run(
    'Format cluster result',
    {},
    { 'Open cluster session': opened, 'Summarize AI steps': { llm_final_text: 'Напишите, какую модель считать на кластере.' } },
  );
  assert.equal(noResult.status, 'needs_input');
  assert.match(noResult.requests[0].question, /модель/i);
  assert.equal(MACHINE.test(noResult.requests[0].question), false, 'engineer question has no snake_case');
  assert.deepEqual(noResult.requests[0].accepts.files, ['inc']);

  const down = await run(
    'Format missing cluster',
    { error: { message: 'connect ECONNREFUSED' } },
    { 'Runtime configuration': { tnav_cluster_url: 'http://127.0.0.1:8400' } },
  );
  assert.equal(down.status, 'failed');
  assert.equal(down.issues[0].code, 'service_unreachable');
  assert.match(down.message, /tNav Cluster Agent/);
  assert.equal(MACHINE.test(down.message), false, down.message);

  const llmDown = await run(
    'Format cluster result',
    {},
    { 'Open cluster session': opened, 'Summarize AI steps': { llm_final_text: '', llm_unavailable: true } },
  );
  assert.equal(llmDown.status, 'failed');
  assert.equal(llmDown.issues[0].code, 'llm_unavailable');
  assert.match(llmDown.message, /Модель чата не ответила/);

  console.log('tnav-cluster-agent-smoke: ok');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
