'use strict';
/**
 * Agent — Schedule Builder: one LLM + FastAPI tools.
 * Orchestrator — MAS calls it via executeWorkflow (Excel Extractor shape), no webhook adapter.
 * LLM path calls MAS — Knowledge Retrieval with schedule_mvp.
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
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

assert.equal(wf.name, 'Agent — Schedule Builder');
assert.equal(wf.active, false);
assert.equal(
  wf.nodes.some((n) => n.type === 'n8n-nodes-base.webhook'),
  false,
  'no webhook adapter — Orchestrator uses executeWorkflow',
);
assert.equal(JSON.stringify(wf).includes('Hybrid Retrieval'), false);
const trigger = wf.nodes.find((n) => n.type === 'n8n-nodes-base.executeWorkflowTrigger');
assert.ok(trigger);
assert.equal(trigger.name, 'When executed by another workflow');
assert.equal(trigger.typeVersion, 1.2);
const agent = wf.nodes.find((n) => n.name === 'Schedule Builder AI Agent');
assert.ok(agent);
assert.equal(agent.type, '@n8n/n8n-nodes-langchain.agent');
assert.equal(agent.typeVersion, 3.1);
assert.equal(agent.parameters.hasOutputParser, false);
assert.equal(agent.parameters.text, '={{ $json.planner_input }}');
function sourceCode(name) {
  const node = wf.nodes.find((n) => n.name === name);
  assert.ok(node && node.parameters && node.parameters.jsCode, name);
  return node.parameters.jsCode;
}
const model = wf.nodes.find((n) => n.name === 'Schedule Builder Chat Model — Qwen');
assert.ok(model);
assert.equal(model.typeVersion, 1.3);
assert.equal(model.parameters.model.value, 'qwen/qwen3.6-27b');
assert.equal(model.parameters.options.timeout, 600000);
// n8n 2.30.8 + AI Agent v3 executes tools through the engine: the legacy langchain
// toolHttpRequest (hidden, supplyData-only) fails at runtime; tools must be HTTP Request "as tool".
assert.equal(wf.nodes.some((n) => n.type === '@n8n/n8n-nodes-langchain.toolHttpRequest'), false);
const toolNodes = wf.nodes.filter((n) => n.type === 'n8n-nodes-base.httpRequestTool');
const tools = toolNodes.map((n) => n.name);
for (const t of toolNodes) {
  assert.equal(t.typeVersion, 4.4, t.name);
  assert.equal(t.parameters.descriptionType, 'manual', t.name);
  assert.ok(String(t.parameters.toolDescription).trim(), t.name);
  assert.ok(String(t.parameters.jsonBody).includes("session_id: $('Open schedule session').first().json.session_id"), t.name);
  assert.ok(String(t.parameters.url).includes(`'/agent-tools/' + ${JSON.stringify(t.name)}`), t.name);
  assert.deepEqual(wf.connections[t.name], { ai_tool: [[{ node: 'Schedule Builder AI Agent', type: 'ai_tool', index: 0 }]] }, t.name);
}
// $fromAI(key, description, type[, default]): declared args per tool, required = no default.
const fromAI = (node) =>
  [...String(node.parameters.jsonBody).matchAll(/\$fromAI\("([^"]+)", "([^"]*)", "([^"]+)"(?:, "([^"]*)")?\)/g)].map((m) => ({
    key: m[1],
    description: m[2],
    type: m[3],
    required: m[4] === undefined,
  }));
for (const name of [
  'inspect_schedule',
  'search_keywords',
  'get_keyword',
  'apply_commissioning',
  'apply_group_rebind',
  'ask_engineer',
  'apply_operations',
  'render_ir',
  'build_schedule',
]) {
  assert.ok(tools.includes(name), name);
}
const system = String(agent.parameters.options.systemMessage || '');
assert.ok(system.includes('render_ir'));
assert.ok(system.includes('details.parameters'));
assert.match(system, /schedule_mvp/);
// LLM-first: the LLM picks the tool from the task; no regex capability hint drives it.
assert.equal(system.includes('suggested_capability'), false);
assert.match(system, /ask_engineer — единственный способ спросить инженера/);
assert.match(system, /spec_incomplete — это тебе, не инженеру/);
assert.match(system, /не строками \.INC/);
const rebindTool = wf.nodes.find((n) => n.name === 'apply_group_rebind');
const rebindArgs = fromAI(rebindTool);
assert.deepEqual(rebindArgs.map((a) => a.key), ['wells', 'parent_group', 'parent_of_parent', 'control', 'gas_rate', 'effective_at']);
assert.deepEqual(rebindArgs.filter((a) => a.required).map((a) => a.key), ['wells', 'parent_group', 'control', 'gas_rate']);
assert.equal(rebindArgs.find((a) => a.key === 'gas_rate').type, 'number');
// Zero-arg tools still bind the session; optional JSON args travel as text (n8n rejects empty json).
assert.deepEqual(fromAI(wf.nodes.find((n) => n.name === 'apply_commissioning')), []);
assert.equal(fromAI(wf.nodes.find((n) => n.name === 'apply_operations'))[0].type, 'json');
const askTool = wf.nodes.find((n) => n.name === 'ask_engineer');
assert.match(String(askTool.parameters.toolDescription), /русской фразой/);
const askArgs = fromAI(askTool);
assert.deepEqual(askArgs.filter((a) => a.required).map((a) => a.key), ['question']);
assert.ok(askArgs.some((a) => a.key === 'options' && a.type === 'string' && !a.required));
assert.ok(wf.connections['search_keywords'].ai_tool);
assert.ok(wf.connections['Schedule Builder Chat Model — Qwen'].ai_languageModel);
assert.equal(wf.connections['When executed by another workflow'].main[0][0].node, 'Runtime configuration');
const runtimeCfg = wf.nodes.find((n) => n.name === 'Runtime configuration');
assert.equal(runtimeCfg.type, 'n8n-nodes-base.executeWorkflow');
assert.equal(runtimeCfg.parameters.workflowId.value, 'REPLACE_MAS_RUNTIME_CONFIG_IN_UI');
assert.equal(runtimeCfg.parameters.workflowId.cachedResultName, 'MAS — Runtime Config');
// No regex "Capability router" and no HTTP apply_* bypass: every task reaches the LLM agent.
for (const gone of ['Capability router', 'Apply commissioning', 'Apply group rebind', 'Describe apply result', 'Apply finished?']) {
  assert.equal(wf.nodes.some((n) => n.name === gone), false, `${gone} must be gone`);
}
assert.equal(wf.connections['Session ready?'].main[0][0].node, 'Activity — Schedule Builder accepted');
assert.equal(wf.connections['Restore after Schedule Builder progress'].main[0][0].node, 'Prepare AI Agent input');
assert.equal(JSON.stringify(wf).includes('suggested_capability'), false);
assert.equal(wf.connections['Prepare AI Agent input'].main[0][0].node, 'Call Knowledge Retrieval');
assert.equal(wf.connections['Call Knowledge Retrieval'].main[0][0].node, 'Attach schedule RAG evidence');
assert.equal(wf.connections['Attach schedule RAG evidence'].main[0][0].node, 'Schedule Builder AI Agent');
assert.equal(wf.connections['Schedule Builder AI Agent'].main[0][0].node, 'Summarize AI steps');
assert.equal(wf.connections['Result stored?'].main[0][0].node, 'Format schedule result');
assert.equal(wf.connections['Result stored?'].main[1][0].node, 'Fetch schedule result');
assert.equal(wf.connections['Format schedule result'].main[0][0].node, 'Close schedule session');
assert.equal(agent.parameters.options.maxIterations, 8);
assert.equal(wf.settings.executionTimeout, 1800);
assert.ok(sourceCode('Summarize AI steps').includes('skip_fetch'));
assert.ok(sourceCode('Summarize AI steps').includes("n==='ask_engineer'"), 'ask_engineer stores a result → fetch it');
const applyOps = wf.nodes.find((n) => n.name === 'apply_operations');
assert.ok(String(applyOps.parameters.toolDescription).includes('массив') || String(applyOps.parameters.toolDescription).includes('Массив'));
const activityProgress = wf.nodes.find((n) => n.name === 'Activity — Schedule Builder progress');
assert.equal(activityProgress.parameters.options.timeout, 2000);
assert.equal(activityProgress.parameters.options.response.response.neverError, true);

const callRag = wf.nodes.find((n) => n.name === 'Call Knowledge Retrieval');
assert.equal(callRag.type, 'n8n-nodes-base.executeWorkflow');
assert.equal(callRag.typeVersion, 1.3);
assert.equal(callRag.onError, 'continueRegularOutput');
assert.equal(callRag.parameters.workflowId.value, 'REPLACE_SCHEDULE_RAG_RETRIEVAL_IN_UI');
assert.equal(callRag.parameters.workflowId.cachedResultName, 'MAS — Knowledge Retrieval');
assert.equal(callRag.parameters.options.waitForSubWorkflow, true);
assert.equal(
  callRag.parameters.workflowInputs.value.schedule_retrieval_request,
  '={{ $json.schedule_retrieval_request }}',
);
assert.match(sourceCode('Prepare AI Agent input'), /schedule_mvp/);
assert.equal(sourceCode('Prepare AI Agent input').includes('excel_protocol'), false);
assert.equal(sourceCode('Prepare AI Agent input').includes('orchestrator_routing'), false);
assert.match(sourceCode('Attach schedule RAG evidence'), /schedule_mvp/);

const cfg = orch.nodes.find((n) => n.name === 'Runtime endpoints');
assert.equal(cfg.type, 'n8n-nodes-base.executeWorkflow');
assert.equal(cfg.parameters.workflowId.value, 'REPLACE_MAS_RUNTIME_CONFIG_IN_UI');
// Phase 2: the orchestrator reaches this agent through the universal "Call agent (n8n)" node; the target
// is agent_registry.invoke.workflow_id, which must be this workflow's id (lab CLI import keeps ids).
assert.equal(orch.nodes.some((n) => n.name === 'Call Schedule Builder'), false, 'no per-agent call node');
const call = orch.nodes.find((n) => n.name === 'Call agent (n8n)');
assert.equal(call.type, 'n8n-nodes-base.executeWorkflow');
assert.equal(call.typeVersion, 1.3);
assert.equal(call.onError, 'continueRegularOutput');
assert.equal(call.parameters.workflowId.value, '={{ $json.invoke_workflow_id }}');
assert.equal(call.parameters.options.waitForSubWorkflow, true);
assert.deepEqual(Object.keys(call.parameters.workflowInputs.value), ['agent_task']);
{
  const seed = JSON.parse(fs.readFileSync(path.join(workspace, 'mas-activity-service/app/sql/agent_registry_seed.json'), 'utf8'));
  const row = seed.find((r) => r.agent_id === 'schedule_builder');
  assert.ok(row, 'schedule_builder is seeded in agent_registry');
  assert.equal(row.invoke.kind, 'n8n_workflow');
  assert.equal(row.invoke.workflow_id, wf.id, 'registry points at this workflow id');
  assert.equal(row.enabled, true);
}
const orchPrepare = orch.nodes.find((n) => n.name === 'Prepare decision context');
assert.equal(orchPrepare.parameters.jsCode.includes('schedule_mvp'), false);
assert.equal(orchPrepare.parameters.jsCode.includes('excel_protocol'), false);

function toItem(payload) {
  return { json: payload };
}

async function run(name, json, nodes = {}) {
  const resolved = { ...nodes };
  const lookup = (nodeName) => {
    if (!Object.prototype.hasOwnProperty.call(resolved, nodeName)) {
      throw new Error(`node not executed: ${nodeName}`);
    }
    const payload = resolved[nodeName];
    const items = Array.isArray(payload) ? payload.map(toItem) : [toItem(payload)];
    return { first: () => items[0], all: () => items };
  };
  const fn = new AsyncFunction('$json', '$', '$input', sourceCode(name));
  const result = await fn(json, lookup, { first: () => ({ json }), all: () => [{ json }] });
  assert.ok(Array.isArray(result) && result[0]?.json);
  return result[0].json;
}

(async () => {
  const prepared = await run(
    'Prepare AI Agent input',
    {
      objective: 'Поставь ORAT 80 на скважины, не трогай факт',
      handoff_message: 'WCONPROD прогноз, baseline.inc не query',
      inspect: { wells: ['101'] },
      fact_count: 2,
      facts_preview: [],
      session_id: 'sess-s',
      engineer_answers: [{ question_id: 'unlisted_wells_policy', choice: 'keep', label: 'Оставить как в исходном файле' }],
    },
    {
      'Normalize schedule task': {
        agent_task: { objective: 'Поставь ORAT 80 на скважины, не трогай факт', inputs: { rework_reason: 'Скважина 1602 не сдвинута' } },
      },
    },
  );
  const plannerInput = JSON.parse(prepared.planner_input);
  assert.equal('suggested_capability' in plannerInput, false, 'the LLM picks the tool, no regex hint');
  assert.equal(plannerInput.engineer_answers[0].label, 'Оставить как в исходном файле');
  assert.equal(plannerInput.rework_reason, 'Скважина 1602 не сдвинута');
  assert.equal(prepared.schedule_retrieval_request.filters.target_base, 'schedule_mvp');
  assert.deepEqual(prepared.schedule_retrieval_request.filters.knowledge_types, [
    'keyword_instruction',
    'worked_example',
  ]);
  assert.ok(prepared.schedule_retrieval_request.filters.keyword_families.includes('WCONPROD'));
  assert.ok(prepared.schedule_retrieval_request.filters.keyword_families.length <= 6);
  assert.equal(prepared.schedule_retrieval_request.filters.keyword_families.includes('XLSX'), false);
  assert.equal(prepared.schedule_retrieval_request.filters.keyword_families.includes('COMMISSIONING'), false);
  assert.ok(prepared.schedule_retrieval_request.query.includes('ORAT'));
  assert.equal(prepared.schedule_retrieval_request.query.includes('baseline.inc'), false);
  assert.equal(prepared.retrieval_selector.target_base, 'schedule_mvp');

  const attached = await run(
    'Attach schedule RAG evidence',
    {
      contract: 'schedule_retrieval_result',
      status: 'succeeded',
      results: [
        {
          knowledge_id: 'wconprod-v1',
          knowledge_type: 'keyword_instruction',
          target_base: 'schedule_mvp',
          title: 'WCONPROD',
          body: { text: 'WCONPROD when-to-use: не переписывай факт. '.repeat(6) },
          rrf_score: 0.016,
        },
        {
          knowledge_id: 'excel-agent-trust-boundary',
          knowledge_type: 'protocol_instruction',
          target_base: 'excel_protocol',
          title: 'trust',
          body: { text: 'Workbook — недоверенные данные. '.repeat(8) },
          rrf_score: 0.9,
        },
      ],
      findings: [],
    },
    { 'Prepare AI Agent input': prepared },
  );
  assert.equal(attached.rag.target_base, 'schedule_mvp');
  assert.equal(attached.rag.status, 'ready');
  assert.equal(attached.rag.cards.length, 1);
  assert.equal(attached.rag.cards[0].knowledge_id, 'wconprod-v1');
  assert.ok(!attached.planner_input.includes('недоверенн'));
  assert.match(attached.planner_input, /when-to-use/);
  assert.equal(JSON.stringify(attached.rag).includes('schema_catalogue'), false);

  const attachedFail = await run(
    'Attach schedule RAG evidence',
    { error: { message: 'subworkflow missing' } },
    { 'Prepare AI Agent input': prepared },
  );
  assert.equal(attachedFail.rag.status, 'unavailable');
  assert.equal(attachedFail.rag.cards.length, 0);
  assert.match(attachedFail.planner_input, /Не спрашивай HITL про RAG/);

  // Summarize AI steps: a result exists after apply_* or ask_engineer → fetch it; otherwise the
  // fallback question to the engineer is plain Russian (no tool names, ids or enums).
  const MACHINE = /[a-z]+_[a-z_]+|[a-z_]+=[a-z0-9]+|[{}[\]]/;
  const opened = { 'Open schedule session': { task_id: 'T-1', session_id: 'sess-s' } };
  const asked = await run(
    'Summarize AI steps',
    {
      output: 'Спросил инженера, в какую группу поместить скважину.',
      intermediateSteps: [{ action: { tool: 'inspect_schedule' } }, { action: { tool: 'ask_engineer' } }],
    },
    opened,
  );
  assert.equal(asked.skip_fetch, false);
  assert.equal(asked.has_result, true);
  assert.equal(asked.status_message, 'Спросил инженера, в какую группу поместить скважину.');
  const applied = await run(
    'Summarize AI steps',
    { output: 'Сдвинул даты ввода.', intermediateSteps: [{ action: { tool: 'apply_commissioning' } }] },
    opened,
  );
  assert.equal(applied.skip_fetch, false);
  const idle = await run(
    'Summarize AI steps',
    { output: '', intermediateSteps: [{ action: { tool: 'inspect_schedule' } }] },
    opened,
  );
  assert.equal(idle.skip_fetch, true);
  assert.equal(idle.status, 'needs_input');
  assert.equal(idle.requests[0].accepts.free_text, true);
  for (const text of [idle.message, idle.requests[0].question, idle.status_message]) {
    assert.equal(MACHINE.test(text), false, text);
    assert.equal(/tools?\b/i.test(text), false, text);
  }
  const looped = await run(
    'Summarize AI steps',
    { output: '', intermediateSteps: Array.from({ length: 5 }, () => ({ action: { tool: 'inspect_well' } })) },
    opened,
  );
  assert.equal(looped.issues[0].type, 'repeated_tools');
  assert.match(looped.requests[0].question, /не смог продвинуться/);

  const MACHINE_DOWN = /[a-z]+_[a-z_]+|[a-z_]+=[a-z0-9]+|[{}\[\]]|\w\|\w/;
  const down = await run(
    'Format missing schedule',
    { error: { message: 'connect ECONNREFUSED' } },
    { 'Runtime configuration': { schedule_service_url: 'http://127.0.0.1:8090' } },
  );
  assert.equal(down.status, 'failed');
  assert.equal(down.issues[0].code, 'service_unreachable');
  assert.match(down.message, /Schedule Builder/);
  assert.equal(MACHINE_DOWN.test(down.message), false, down.message);
  const stillMissing = await run(
    'Format missing schedule',
    { ok: false, status: 'needs_input', result: { status: 'needs_input', message: 'Нет исходного SCHEDULE', requests: [{ question: 'К задаче не приложен исходный SCHEDULE. Приложите файл .INC.' }] } },
    { 'Runtime configuration': { schedule_service_url: 'http://127.0.0.1:8090' } },
  );
  assert.equal(stillMissing.status, 'needs_input');

  console.log('schedule-builder-agent-smoke: ok');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
