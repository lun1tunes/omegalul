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
assert.equal(wf.nodes.some((n) => n.type === '@n8n/n8n-nodes-langchain.agent'), false, 'http_loop: no LangChain agent');
assert.equal(wf.nodes.some((n) => n.type === '@n8n/n8n-nodes-langchain.lmChatOpenAi'), false, 'http_loop: no Chat Model');
assert.equal(wf.nodes.some((n) => n.type === 'n8n-nodes-base.httpRequestTool'), false, 'http_loop: tools are one HTTP Call agent tool');
function sourceCode(name) {
  const node = wf.nodes.find((n) => n.name === name);
  assert.ok(node && node.parameters && node.parameters.jsCode, name);
  return node.parameters.jsCode;
}
const chat = wf.nodes.find((n) => n.name === 'Schedule Builder Agent chat');
assert.ok(chat);
assert.equal(chat.type, 'n8n-nodes-base.httpRequest');
assert.equal(chat.typeVersion, 4.4);
assert.equal(chat.parameters.url, '={{ $json.chat_url }}');
assert.equal(chat.parameters.jsonBody, '={{ $json.chat_request }}');
assert.equal(chat.parameters.nodeCredentialType, 'openAiApi');
assert.equal(chat.parameters.options.timeout, 600000);
const buildJs = sourceCode('Build chat request');
assert.match(buildJs, /retrieve_knowledge/);
assert.match(buildJs, /apply_commissioning/);
assert.match(buildJs, /apply_dataset/);
assert.match(buildJs, /chat_template_kwargs/);
assert.equal(buildJs.includes('max_tokens'), false);
for (const name of [
  'inspect_schedule',
  'search_keywords',
  'get_keyword',
  'inspect_dataset',
  'apply_dataset',
  'apply_commissioning',
  'apply_group_rebind',
  'ask_engineer',
  'apply_operations',
  'render_ir',
  'build_schedule',
]) {
  assert.ok(buildJs.includes(`"name": "${name}"`) || buildJs.includes(`"name":"${name}"`), name);
}
assert.match(buildJs, /render_ir/);
assert.match(buildJs, /details.parameters/);
assert.match(buildJs, /schedule_mvp/);
assert.equal(buildJs.includes('suggested_capability'), false);
assert.match(buildJs, /Нет поля даты в наборе/);
assert.match(buildJs, /retrieve_knowledge не пропускай/);
assert.match(buildJs, /apply_commissioning для дат ввода — без retrieve/);
assert.match(buildJs, /spec_incomplete — это тебе, не инженеру/);
assert.match(buildJs, /не строками \.INC/);
assert.match(buildJs, /apply_dataset/);
assert.match(buildJs, /field_map/);
assert.equal(buildJs.includes('INTENT_ALIASES'), false);
assert.equal(sourceCode('Prepare AI Agent input').includes('mapped.push'), false);
assert.equal(sourceCode('Prepare AI Agent input').includes('дат[аые]'), false);
assert.match(sourceCode('Prepare AI Agent input'), /dataset_count/);
assert.match(buildJs, /"gas_rate": \{"type": "number"/);
assert.match(buildJs, /JSON-строкой/);
assert.ok(wf.connections['Call agent tool']);
assert.equal(wf.nodes.find((n) => n.name === 'Call agent tool').type, 'n8n-nodes-base.httpRequest');
assert.equal(wf.connections['When executed by another workflow'].main[0][0].node, 'Runtime configuration');
const runtimeCfg = wf.nodes.find((n) => n.name === 'Runtime configuration');
assert.equal(runtimeCfg.type, 'n8n-nodes-base.executeWorkflow');
assert.equal(runtimeCfg.parameters.workflowId.value, 'REPLACE_MAS_RUNTIME_CONFIG_IN_UI');
assert.equal(runtimeCfg.parameters.workflowId.cachedResultName, 'MAS — Runtime Config');
for (const gone of ['Capability router', 'Apply commissioning', 'Apply group rebind', 'Describe apply result', 'Apply finished?', 'Schedule Builder AI Agent', 'Schedule Builder Chat Model — Qwen', 'Result stored?']) {
  assert.equal(wf.nodes.some((n) => n.name === gone), false, `${gone} must be gone`);
}
assert.equal(wf.connections['Session ready?'].main[0][0].node, 'Activity — Schedule Builder accepted');
assert.equal(wf.connections['Restore after Schedule Builder progress'].main[0][0].node, 'Prepare AI Agent input');
assert.equal(JSON.stringify(wf).includes('suggested_capability'), false);
assert.equal(wf.connections['Prepare AI Agent input'].main[0][0].node, 'Call Knowledge Retrieval');
assert.equal(wf.connections['Call Knowledge Retrieval'].main[0][0].node, 'Attach schedule RAG evidence');
assert.equal(wf.connections['Attach schedule RAG evidence'].main[0][0].node, 'Activity — Schedule Builder RAG');
assert.equal(wf.connections['Activity — Schedule Builder RAG'].main[0][0].node, 'Restore after Schedule Builder RAG');
assert.equal(wf.connections['Restore after Schedule Builder RAG'].main[0][0].node, 'Build chat request');
assert.equal(wf.connections['Build chat request'].main[0][0].node, 'Schedule Builder Agent chat');
assert.equal(wf.connections['Schedule Builder Agent chat'].main[0][0].node, 'Parse agent chat');
assert.equal(wf.connections['Parse agent chat'].main[0][0].node, 'Activity — Schedule Builder LLM');
assert.equal(wf.connections['Restore after Schedule Builder LLM'].main[0][0].node, 'Agent loop router');
assert.equal(wf.connections['Agent loop router'].main[0][0].node, 'Build chat request');
assert.equal(wf.connections['Agent loop router'].main[1][0].node, 'Prepare tool call');
assert.equal(wf.connections['Agent loop router'].main[2][0].node, 'Prepare retrieve request');
assert.equal(wf.connections['Agent loop router'].main[3][0].node, 'Summarize AI steps');
assert.equal(wf.connections['Skip unknown tool?'].main[0][0].node, 'Append tool result');
assert.equal(wf.connections['Skip unknown tool?'].main[1][0].node, 'Call agent tool');
assert.equal(wf.connections['Restore after AI tools'].main[0][0].node, 'Fetch schedule result');
assert.equal(wf.connections['Fetch schedule result'].main[0][0].node, 'Format schedule result');
assert.equal(wf.connections['Format schedule result'].main[0][0].node, 'Close schedule session');
assert.match(buildJs, /const MAX_ITER=8/);
assert.equal(wf.settings.executionTimeout, 1800);
assert.ok(sourceCode('Summarize AI steps').includes('retrieve_knowledge'));
assert.ok(sourceCode('Summarize AI steps').includes('skip_fetch') === false);
assert.ok(buildJs.includes('массив') || buildJs.includes('Массив'));
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
    return { first: () => items[0], last: () => items[items.length - 1], all: () => items, item: items[0] };
  };
  const fn = new AsyncFunction('$json', '$', '$input', sourceCode(name));
  const result = await fn(json, lookup, { first: () => ({ json }), last: () => ({ json }), all: () => [{ json }], item: { json } });
  assert.ok(Array.isArray(result) && result[0]?.json);
  return result[0].json;
}

(async () => {
  const prepared = await run(
    'Prepare AI Agent input',
    {
      objective: 'Поставь ORAT 80 на скважины, не трогай факт',
      handoff_message: 'WCONPROD прогноз, baseline.inc не query',
      inspect: { wells: ['101'], keywords_present: ['DATES', 'WCONPROD', 'WEFAC'] },
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
  assert.deepEqual(prepared.schedule_retrieval_request.filters.keyword_families, ['DATES', 'WCONPROD', 'WEFAC']);
  assert.ok(prepared.schedule_retrieval_request.filters.keyword_families.length <= 6);
  assert.equal(prepared.schedule_retrieval_request.filters.keyword_families.includes('XLSX'), false);
  assert.equal(prepared.schedule_retrieval_request.filters.keyword_families.includes('COMMISSIONING'), false);
  assert.ok(prepared.schedule_retrieval_request.query.includes('ORAT'));

  const textOnlySched = await run(
    'Prepare AI Agent input',
    {
      objective: 'Поставь ORAT 80, keyword WCONPROD в тексте',
      handoff_message: 'WCONPROD прогноз, baseline.inc не query',
      inspect: { wells: ['101'] },
      session_id: 'sess-s',
    },
    { 'Normalize schedule task': { agent_task: { objective: 'Поставь ORAT 80, keyword WCONPROD в тексте' } } },
  );
  assert.deepEqual(textOnlySched.schedule_retrieval_request.filters.keyword_families, []);
  assert.ok(textOnlySched.schedule_retrieval_request.query.includes('WCONPROD'));

  const expectedKw = await run(
    'Prepare AI Agent input',
    {
      objective: 'Внеси коэффициенты эксплуатации',
      handoff_message: 'набор без имени keyword в тексте',
      inspect: { wells: ['101'], keywords_present: ['DATES'] },
      datasets: [{ name: 'wefac', fields: ['well', 'WEFAC'] }],
      expected_output: { datasets: [{ name: 'exploitation', keywords: ['WEFAC'] }] },
      session_id: 'sess-s',
    },
    { 'Normalize schedule task': { agent_task: { objective: 'Внеси коэффициенты эксплуатации' } } },
  );
  assert.deepEqual(expectedKw.schedule_retrieval_request.filters.keyword_families, ['WEFAC', 'DATES']);
  assert.equal(prepared.schedule_retrieval_request.query.includes('baseline.inc'), false);
  assert.equal(prepared.retrieval_selector.target_base, 'schedule_mvp');

  const prepRetrieve = await run(
    'Prepare retrieve request',
    {
      pending_tools: [{ id: 'c2', name: 'retrieve_knowledge', arguments: '{"query":"коэффициент эксплуатации","keywords":"WEFAC"}' }],
      schedule_retrieval_request: {
        query: 'даты ввода',
        filters: {
          target_base: 'schedule_mvp',
          keyword_families: ['INCLUDE', 'DATES', 'WELSPECS', 'WELLTRACK', 'ACTIONX', 'WCONPROD'],
        },
      },
    },
  );
  assert.equal(prepRetrieve.schedule_retrieval_request.query, 'коэффициент эксплуатации');
  assert.deepEqual(prepRetrieve.schedule_retrieval_request.filters.keyword_families, ['WEFAC']);
  const prepRetrieveQueryOnly = await run(
    'Prepare retrieve request',
    {
      pending_tools: [{ id: 'c3', name: 'retrieve_knowledge', arguments: '{"query":"коэффициент эксплуатации"}' }],
      schedule_retrieval_request: {
        query: 'даты ввода',
        filters: { target_base: 'schedule_mvp', keyword_families: ['INCLUDE', 'DATES', 'WELSPECS'] },
      },
    },
  );
  assert.deepEqual(prepRetrieveQueryOnly.schedule_retrieval_request.filters.keyword_families, []);

  const wefacFull = 'WEFAC — коэффициент эксплуатации. Когда применять. Антипаттерны: не путать с GEFAC. Расклад полей — get_keyword.details. '.repeat(8);
  const wefacSummary = 'Когда применять. Краткое summary без полного текста карточки и без слова pitfalls-only.';
  const retrieveOk = await run(
    'Attach retrieve evidence',
    {
      contract: 'schedule_retrieval_result',
      status: 'succeeded',
      results: [{
        knowledge_id: 'wefac-well-efficiency-v1',
        knowledge_type: 'keyword_instruction',
        target_base: 'schedule_mvp',
        title: 'WEFAC',
        rrf_score: 0.016,
        branches: ['tag'],
        body: { target_base: 'schedule_mvp', knowledge_type: 'keyword_instruction', summary: wefacSummary, text: wefacFull },
      }],
    },
    { 'Prepare retrieve request': { ...prepRetrieve, pending_tool: prepRetrieve.pending_tools[0], messages: [], tool_log: [] } },
  );
  assert.equal(retrieveOk.rag.status, 'ready');
  assert.equal(retrieveOk.activity_payload.phase, 'on_demand');
  assert.equal(retrieveOk.rag.cards[0].knowledge_id, 'wefac-well-efficiency-v1');
  assert.ok(retrieveOk.rag.cards[0].text.includes('get_keyword'));
  assert.equal(retrieveOk.rag.cards[0].text.includes('pitfalls-only'), false);
  assert.ok(retrieveOk.rag.cards[0].text.length > wefacSummary.length);
  const retrieveMsg = JSON.parse(retrieveOk.messages[0].content);
  assert.ok(retrieveMsg.cards[0].text.includes('Когда применять'));

  // CASE-6aac16ba-93c607: apply_dataset without retrieve_knowledge must skip FastAPI.
  // CASE-6aac16ba-93c607: apply_dataset without retrieve_knowledge must skip FastAPI.
  const blockedApply = await run(
    'Prepare tool call',
    {
      pending_tools: [{ id: 'a1', name: 'apply_dataset', arguments: '{"dataset":"exploitation_coefficients","keyword":"WEFAC"}' }],
      tool_log: ['inspect_dataset', 'get_keyword'],
      session_id: 'sess-s',
    },
    { 'Open schedule session': { session_id: 'sess-s' }, 'Runtime configuration': { schedule_service_url: 'http://127.0.0.1:8090' } },
  );
  assert.equal(blockedApply.skip_http, true);
  assert.equal(blockedApply.skip_result.code, 'knowledge_required');
  assert.equal(JSON.stringify(blockedApply.skip_result).includes('"error"'), false);
  const allowedApply = await run(
    'Prepare tool call',
    {
      pending_tools: [{ id: 'a2', name: 'apply_dataset', arguments: '{"dataset":"exploitation_coefficients","keyword":"WEFAC"}' }],
      tool_log: ['inspect_dataset', 'retrieve_knowledge', 'get_keyword'],
      session_id: 'sess-s',
    },
    { 'Open schedule session': { session_id: 'sess-s' }, 'Runtime configuration': { schedule_service_url: 'http://127.0.0.1:8090' } },
  );
  assert.equal(allowedApply.skip_http, false);
  assert.match(allowedApply.tool_url, /apply_dataset/);

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
  assert.equal(attached.activity_kind, 'trace.rag');
  assert.equal(attached.activity_payload.caller, 'schedule_builder');
  assert.ok(!attached.planner_input.includes('недоверенн'));
  assert.match(attached.planner_input, /when-to-use/);
  assert.equal(JSON.stringify(attached.rag).includes('schema_catalogue'), false);

  const attachedFail = await run(
    'Attach schedule RAG evidence',
    { error: { message: 'subworkflow missing' } },
    { 'Prepare AI Agent input': prepared },
  );
  assert.equal(attachedFail.rag.status, 'failed');
  assert.equal(attachedFail.rag.cards.length, 0);
  assert.equal(attachedFail.activity_kind, 'trace.rag');
  assert.equal(attachedFail.activity_payload.status, 'failed');
  assert.equal(attachedFail.activity_payload.phase, 'initial');
  assert.match(attachedFail.planner_input, /Не спрашивай HITL про RAG/);

  const attachedAbstain = await run(
    'Attach schedule RAG evidence',
    {
      contract: 'schedule_retrieval_result',
      status: 'abstain',
      results: [],
      findings: [{ code: 'SCHEMA_KEYWORD_SCOPE_REQUIRED' }],
    },
    { 'Prepare AI Agent input': prepared },
  );
  assert.equal(attachedAbstain.rag.status, 'abstain');
  assert.equal(attachedAbstain.activity_payload.status, 'abstain');
  assert.deepEqual(attachedAbstain.rag.findings, ['SCHEMA_KEYWORD_SCOPE_REQUIRED']);

  // X7: result is always GET /sessions/{id}/result; human LLM text replaces a generic no_apply HITL.
  const MACHINE = /[a-z]+_[a-z_]+|[a-z_]+=[a-z0-9]+|[{}[\]]/;
  const opened = { 'Open schedule session': { task_id: 'T-1', session_id: 'sess-s' } };
  const asked = await run(
    'Summarize AI steps',
    { llm_final_text: 'Спросил инженера, в какую группу поместить скважину.', tool_log: ['inspect_schedule', 'ask_engineer'], iteration: 2 },
    opened,
  );
  assert.equal(asked.total_calls, 2);
  assert.equal(asked.status_message, 'Спросил инженера, в какую группу поместить скважину.');
  const machineProgress = await run(
    'Summarize AI steps',
    { llm_final_text: 'Записал коэффициенты в набор exploitation_coefficients.', tool_log: ['apply_dataset'], iteration: 2 },
    opened,
  );
  assert.equal(machineProgress.status_message, 'Агент завершил шаг.');
  const applied = await run(
    'Summarize AI steps',
    { llm_final_text: 'Сдвинул даты ввода.', tool_log: ['apply_commissioning', 'retrieve_knowledge'], iteration: 2 },
    opened,
  );
  assert.equal(applied.total_calls, 1);
  const stored = await run(
    'Format schedule result',
    { status: 'completed', message: 'Сдвинул даты ввода.', data: {}, artifacts: { schedule_out: 'x' }, issues: [], requests: [] },
    { ...opened, 'Summarize AI steps': applied },
  );
  assert.equal(stored.status, 'completed');
  const idle = await run(
    'Format schedule result',
    {},
    { ...opened, 'Summarize AI steps': { llm_final_text: 'Уточните, в какую группу поместить скважину.' } },
  );
  assert.equal(idle.status, 'needs_input');
  assert.equal(idle.issues[0].type, 'no_apply');
  for (const text of [idle.message, idle.requests[0].question]) {
    assert.equal(MACHINE.test(text), false, text);
    assert.equal(/tools?\b/i.test(text), false, text);
  }
  const looped = await run(
    'Summarize AI steps',
    { llm_final_text: '', tool_log: Array.from({ length: 5 }, () => 'inspect_well') },
    opened,
  );
  assert.equal(looped.repeated, true);

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

  const llmDown = await run(
    'Format schedule result',
    {},
    { ...opened, 'Summarize AI steps': { llm_final_text: '', llm_unavailable: true } },
  );
  assert.equal(llmDown.status, 'failed');
  assert.equal(llmDown.issues[0].code, 'llm_unavailable');
  assert.match(llmDown.message, /Модель чата не ответила/);
  assert.equal(MACHINE_DOWN.test(llmDown.message), false, llmDown.message);

  console.log('schedule-builder-agent-smoke: ok');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
