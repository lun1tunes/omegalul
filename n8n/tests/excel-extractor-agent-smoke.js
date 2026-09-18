'use strict';
/**
 * Agent — Excel Extractor: one LLM + excel-tools FastAPI.
 * Orchestrator — MAS calls it via executeWorkflow, no webhook adapter.
 * LLM path calls MAS — Knowledge Retrieval with excel_protocol.
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
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

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
assert.equal(wf.nodes.some((n) => n.type === '@n8n/n8n-nodes-langchain.agent'), false, 'http_loop: no LangChain agent');
assert.equal(wf.nodes.some((n) => n.type === '@n8n/n8n-nodes-langchain.lmChatOpenAi'), false, 'http_loop: no Chat Model');
assert.equal(wf.nodes.some((n) => n.type === 'n8n-nodes-base.httpRequestTool'), false, 'http_loop: tools are one HTTP Call agent tool');
function sourceCode(name) {
  const node = wf.nodes.find((n) => n.name === name);
  assert.ok(node && node.parameters && node.parameters.jsCode, name);
  return node.parameters.jsCode;
}
const chat = wf.nodes.find((n) => n.name === 'Excel Extractor Agent chat');
assert.ok(chat);
assert.equal(chat.type, 'n8n-nodes-base.httpRequest');
assert.equal(chat.typeVersion, 4.4);
assert.equal(chat.parameters.url, '={{ $json.chat_url }}');
assert.equal(chat.parameters.jsonBody, '={{ $json.chat_request }}');
assert.equal(chat.parameters.nodeCredentialType, 'openAiApi');
assert.equal(chat.parameters.options.timeout, 600000);
const buildJs = sourceCode('Build chat request');
assert.match(buildJs, /retrieve_knowledge/);
assert.match(buildJs, /Если протокола для выбранного инструмента нет в срезе — retrieve_knowledge/);
assert.match(buildJs, /extract_commissioning/);
assert.match(buildJs, /extract_table/);
assert.match(buildJs, /ask_engineer/);
assert.match(buildJs, /chat_template_kwargs/);
assert.match(buildJs, /enable_thinking/);
assert.equal(buildJs.includes('max_tokens'), false);
const specTools = [
  'workbook_introspect',
  'sheet_preview',
  'detect_tables',
  'match_tables',
  'describe_table',
  'list_column_values',
  'query_table',
  'extract_commissioning',
  'extract_well_parameters',
  'extract_table',
  'ask_engineer',
];
for (const name of specTools) {
  assert.ok(buildJs.includes(`"name": "${name}"`) || buildJs.includes(`"name":"${name}"`), name);
}
assert.ok(wf.connections['Call agent tool']);
const callTool = wf.nodes.find((n) => n.name === 'Call agent tool');
assert.equal(callTool.type, 'n8n-nodes-base.httpRequest');
assert.match(String(callTool.parameters.url), /tool_url/);
assert.equal(wf.connections['When executed by another workflow'].main[0][0].node, 'Runtime configuration');
const runtimeCfg = wf.nodes.find((n) => n.name === 'Runtime configuration');
assert.equal(runtimeCfg.type, 'n8n-nodes-base.executeWorkflow');
assert.equal(runtimeCfg.parameters.workflowId.value, 'REPLACE_MAS_RUNTIME_CONFIG_IN_UI');
assert.equal(runtimeCfg.parameters.workflowId.cachedResultName, 'MAS — Runtime Config');
assert.equal(JSON.stringify(wf).includes('excel_tools_api_key'), false);
const openSession = wf.nodes.find((n) => n.name === 'Open excel session');
assert.equal(openSession.parameters.authentication, undefined);
assert.equal(openSession.credentials, undefined);
assert.equal(openSession.retryOnFail, true);
for (const gone of ['Capability router', 'Extract commissioning', 'Describe extract result', 'Extract finished?', 'Restore after extract event', 'Excel Extractor AI Agent', 'Excel Extractor Chat Model — Qwen', 'Result stored?']) {
  assert.equal(wf.nodes.some((n) => n.name === gone), false, `${gone} must be gone`);
}
assert.equal(JSON.stringify(wf).includes('suggested_capability'), false);
assert.equal(wf.connections['Session ready?'].main[0][0].node, 'Activity — Excel Extractor accepted');
assert.equal(wf.connections['Session ready?'].main[1][0].node, 'Format missing excel');
assert.equal(wf.connections['Restore after Excel Extractor progress'].main[0][0].node, 'Prepare AI Agent input');
assert.equal(wf.connections['Prepare AI Agent input'].main[0][0].node, 'Call Knowledge Retrieval');
for (const must of ['extract_commissioning', 'extract_well_parameters', 'extract_table', 'expected_output', 'name_reserved', 'ask_engineer', 'spec_incomplete', 'column_not_found', 'question_not_human', 'engineer_answers']) {
  assert.ok(buildJs.includes(must), `system prompt mentions ${must}`);
}
assert.match(buildJs, /Имена файлов в вопрос не вставляй/);
assert.equal(wf.connections['Call Knowledge Retrieval'].main[0][0].node, 'Attach excel RAG evidence');
assert.equal(wf.connections['Attach excel RAG evidence'].main[0][0].node, 'Activity — Excel Extractor RAG');
assert.equal(wf.connections['Activity — Excel Extractor RAG'].main[0][0].node, 'Restore after Excel Extractor RAG');
assert.equal(wf.connections['Restore after Excel Extractor RAG'].main[0][0].node, 'Build chat request');
assert.equal(wf.connections['Build chat request'].main[0][0].node, 'Excel Extractor Agent chat');
assert.equal(wf.connections['Excel Extractor Agent chat'].main[0][0].node, 'Parse agent chat');
assert.equal(wf.connections['Parse agent chat'].main[0][0].node, 'Activity — Excel Extractor LLM');
assert.equal(wf.connections['Activity — Excel Extractor LLM'].main[0][0].node, 'Restore after Excel Extractor LLM');
assert.equal(wf.connections['Restore after Excel Extractor LLM'].main[0][0].node, 'Agent loop router');
const router = wf.nodes.find((n) => n.name === 'Agent loop router');
assert.ok(router);
assert.equal(router.type, 'n8n-nodes-base.switch');
assert.equal(router.typeVersion, 3.4);
assert.equal(wf.connections['Agent loop router'].main[0][0].node, 'Build chat request');
assert.equal(wf.connections['Agent loop router'].main[1][0].node, 'Prepare tool call');
assert.equal(wf.connections['Agent loop router'].main[2][0].node, 'Prepare retrieve request');
assert.equal(wf.connections['Agent loop router'].main[3][0].node, 'Summarize AI steps');
assert.equal(wf.connections['Skip unknown tool?'].main[0][0].node, 'Append tool result');
assert.equal(wf.connections['Skip unknown tool?'].main[1][0].node, 'Call agent tool');
assert.equal(wf.connections['Call agent tool'].main[0][0].node, 'Append tool result');
assert.equal(wf.connections['Append tool result'].main[0][0].node, 'Agent loop router');
assert.equal(wf.connections['Prepare retrieve request'].main[0][0].node, 'Retrieve knowledge');
assert.equal(wf.connections['Retrieve knowledge'].main[0][0].node, 'Attach retrieve evidence');
assert.equal(wf.connections['Attach retrieve evidence'].main[0][0].node, 'Activity — Excel Extractor retrieve');
assert.equal(wf.connections['Activity — Excel Extractor retrieve'].main[0][0].node, 'Restore after Excel Extractor retrieve');
assert.equal(wf.connections['Restore after Excel Extractor retrieve'].main[0][0].node, 'Agent loop router');
assert.equal(wf.connections['Summarize AI steps'].main[0][0].node, 'Activity — Excel Extractor tools');
assert.equal(wf.connections['Restore after AI tools'].main[0][0].node, 'Fetch excel result');
assert.equal(wf.connections['Fetch excel result'].main[0][0].node, 'Format excel result');
assert.equal(wf.connections['Format excel result'].main[0][0].node, 'Close excel session');
assert.match(buildJs, /const MAX_ITER=8/);
assert.equal(wf.settings.executionTimeout, 1800);
assert.ok(sourceCode('Summarize AI steps').includes('retrieve_knowledge'));
assert.equal(sourceCode('Summarize AI steps').includes('вызвал'), false, 'no "called N tools" template shown to engineers');
assert.ok(buildJs.includes('массив') || buildJs.includes('Массив'));
const activityProgress = wf.nodes.find((n) => n.name === 'Activity — Excel Extractor progress');
assert.equal(activityProgress.parameters.options.timeout, 2000);
assert.equal(activityProgress.parameters.options.response.response.neverError, true);
assert.equal(activityProgress.parameters.authentication, undefined);
const close = wf.nodes.find((n) => n.name === 'Close excel session');
assert.ok(String(close.parameters.url).includes('/close'));
assert.equal(close.parameters.authentication, undefined);
assert.equal(close.credentials, undefined);
assert.match(sourceCode('Prepare tool call'), /Open excel session/);
assert.match(sourceCode('Prepare tool call'), /session_id/);

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
assert.match(sourceCode('Prepare AI Agent input'), /excel_protocol/);
assert.equal(sourceCode('Prepare AI Agent input').includes('schedule_mvp'), false);
assert.equal(sourceCode('Prepare AI Agent input').includes('orchestrator_routing'), false);
assert.match(sourceCode('Attach excel RAG evidence'), /excel_protocol/);
assert.match(buildJs, /excel_protocol/);

const cfg = orch.nodes.find((n) => n.name === 'Runtime endpoints');
assert.equal(cfg.type, 'n8n-nodes-base.executeWorkflow');
assert.equal(cfg.parameters.workflowId.value, 'REPLACE_MAS_RUNTIME_CONFIG_IN_UI');
// Phase 2: the orchestrator reaches this agent through the universal "Call agent (n8n)" node; the target
// is agent_registry.invoke.workflow_id, which must be this workflow's id (lab CLI import keeps ids).
assert.equal(orch.nodes.some((n) => n.name === 'Call Excel Extractor'), false, 'no per-agent call node');
const call = orch.nodes.find((n) => n.name === 'Call agent (n8n)');
assert.equal(call.type, 'n8n-nodes-base.executeWorkflow');
assert.equal(call.typeVersion, 1.3);
assert.equal(call.onError, 'continueRegularOutput');
assert.equal(call.parameters.workflowId.value, '={{ $json.invoke_workflow_id }}');
assert.equal(call.parameters.options.waitForSubWorkflow, true);
assert.deepEqual(Object.keys(call.parameters.workflowInputs.value), ['agent_task']);
{
  const seed = JSON.parse(fs.readFileSync(path.join(workspace, 'mas-activity-service/app/sql/agent_registry_seed.json'), 'utf8'));
  const row = seed.find((r) => r.agent_id === 'excel_extractor');
  assert.ok(row, 'excel_extractor is seeded in agent_registry');
  assert.equal(row.invoke.kind, 'n8n_workflow');
  assert.equal(row.invoke.workflow_id, wf.id, 'registry points at this workflow id');
  assert.equal(row.enabled, true);
}
const orchPrepare = orch.nodes.find((n) => n.name === 'Prepare decision context');
assert.equal(orchPrepare.parameters.jsCode.includes('excel_protocol'), false);
assert.equal(orchPrepare.parameters.jsCode.includes('schedule_mvp'), false);

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
  const inventory = {
    files: ['rates.xlsx'],
    sheets: [{ name: 'Wells', rows: 3, columns: 2, file: 'rates.xlsx' }],
    table_count: 1,
    tables: [{ table_id: 'tbl_1', file: 'rates.xlsx', sheet: 'Wells', range: 'A1:B3', kind: 'data', columns: ['Скважина', 'Дебит'], sample: [{ Скважина: '1601', Дебит: 120 }] }],
  };
  const prepared = await run(
    'Prepare AI Agent input',
    {
      objective: 'Достань дебиты из таблицы',
      handoff_message: 'query_table по нефти',
      inspect: inventory,
      files: ['rates.xlsx'],
      file_name: 'rates.xlsx',
      session_id: 'sess-1',
      engineer_answers: [{ question_id: 'Q-date_column', label: 'Дата ввода' }],
      rework_reason: 'взята колонка baseline',
      expected_output: {
        datasets: [{ name: 'oil_rates', description: 'дебиты нефти', fields: [{ name: 'well', type: 'text' }, { name: 'rate', type: 'number' }] }],
      },
    },
    {
      'Normalize excel task': { agent_task: { objective: 'Достань дебиты из таблицы' } },
    },
  );
  assert.equal(prepared.schedule_retrieval_request.filters.target_base, 'excel_protocol');
  assert.deepEqual(prepared.schedule_retrieval_request.filters.knowledge_types, ['protocol_instruction']);
  assert.deepEqual(prepared.schedule_retrieval_request.filters.keyword_families, []);
  assert.ok(prepared.schedule_retrieval_request.filters.topics.includes('протокол'));
  assert.ok(prepared.schedule_retrieval_request.filters.task_patterns.includes('data'));
  assert.ok(prepared.schedule_retrieval_request.filters.task_patterns.includes('oil_rates'));
  assert.equal(prepared.schedule_retrieval_request.filters.task_patterns.includes('даты ввода'), false);
  assert.equal(prepared.schedule_retrieval_request.filters.task_patterns.includes('извлечь таблицу'), false);
  assert.ok(prepared.schedule_retrieval_request.query.includes('дебит'));
  assert.equal(prepared.schedule_retrieval_request.query.includes('rates.xlsx'), false);
  assert.equal(prepared.retrieval_selector.target_base, 'excel_protocol');
  const plannerInput = JSON.parse(prepared.planner_input);
  assert.equal(plannerInput.objective, 'Достань дебиты из таблицы');
  assert.deepEqual(plannerInput.files, ['rates.xlsx']);
  assert.equal(plannerInput.inspect.tables[0].columns[1], 'Дебит', 'inventory (tables, columns, sample) reaches the LLM');
  assert.equal(plannerInput.engineer_answers[0].label, 'Дата ввода', 'HITL answers reach the LLM');
  assert.equal(plannerInput.rework_reason, 'взята колонка baseline');
  assert.equal(plannerInput.expected_output.datasets[0].name, 'oil_rates', 'orchestrator shape reaches the Excel LLM');
  assert.equal('suggested_capability' in plannerInput, false, 'the LLM picks the tool, no regex hint');

  const textOnlyExcel = await run(
    'Prepare AI Agent input',
    {
      objective: 'Достань даты ввода скважин и query_table по дебиту',
      handoff_message: 'commission the wells',
      inspect: { tables: [] },
      session_id: 'sess-1',
    },
    { 'Normalize excel task': { agent_task: { objective: 'Достань даты ввода скважин' } } },
  );
  assert.deepEqual(textOnlyExcel.schedule_retrieval_request.filters.task_patterns, []);
  assert.ok(textOnlyExcel.schedule_retrieval_request.query.includes('даты ввода'));

  const runtime = { excel_tools_url: 'http://127.0.0.1:8000', chat_model: 'qwen/qwen3.6-27b', chat_base_url: 'https://openrouter.ai/api/v1' };
  const built = await run(
    'Build chat request',
    { planner_input: JSON.stringify({ objective: 'Достань дебиты' }), session_id: 'sess-1' },
    { 'Runtime configuration': runtime },
  );
  assert.equal(built.chat_url, 'https://openrouter.ai/api/v1/chat/completions');
  assert.equal(built.chat_request.temperature, 0.7);
  assert.equal(built.chat_request.top_p, 0.8);
  assert.equal(built.chat_request.presence_penalty, 1.5);
  assert.equal(built.chat_request.reasoning.enabled, false);
  const extraBuilt = await run(
    'Build chat request',
    { planner_input: JSON.stringify({ objective: 'Достань дебиты' }), session_id: 'sess-1' },
    { 'Runtime configuration': { ...runtime, chat_extra_params: '{"top_k":7,"tools":[],"temperature":0.1}' } },
  );
  assert.equal(extraBuilt.chat_request.top_k, 7);
  assert.equal(extraBuilt.chat_request.temperature, 0.1);
  assert.ok(extraBuilt.chat_request.tools.length > 0, 'chat_extra_params must not replace tools');
  assert.equal(extraBuilt.chat_request.reasoning.enabled, false);
  assert.equal(built.chat_request.enable_thinking, false);
  assert.equal(built.chat_request.chat_template_kwargs.enable_thinking, false);
  assert.equal(Object.prototype.hasOwnProperty.call(built.chat_request, 'max_tokens'), false);
  const toolNames = built.chat_request.tools.map((t) => t.function.name);
  assert.ok(toolNames.includes('extract_commissioning'));
  assert.ok(toolNames.includes('extract_table'));
  assert.equal(toolNames.at(-1), 'retrieve_knowledge');
  const extractFn = built.chat_request.tools.find((t) => t.function.name === 'extract_commissioning');
  assert.deepEqual(extractFn.function.parameters.required, ['table_id', 'well_column', 'date_column']);
  const tableFn = built.chat_request.tools.find((t) => t.function.name === 'extract_table');
  assert.equal(tableFn.function.parameters.properties.columns.type, 'string');

  const loopPrev = {
    messages: [{ role: 'system', content: 'S' }, { role: 'user', content: '{}' }],
    chat_request: { model: 'qwen/qwen3.6-27b', messages: [{ role: 'system', content: 'S' }, { role: 'user', content: '{}' }] },
    max_iterations: 8,
    iteration: 0,
    tool_log: [],
    session_id: 'sess-1',
  };
  const stopped = await run(
    'Parse agent chat',
    {
      choices: [{ message: { content: '<think>скрыто</think>Извлёк даты ввода для 14 скважин.' }, finish_reason: 'stop' }],
      usage: { prompt_tokens: 11, completion_tokens: 22, completion_tokens_details: { reasoning_tokens: 0 } },
    },
    { 'Build chat request': loopPrev },
  );
  assert.equal(stopped.loop_route, 'done');
  assert.equal(stopped.llm_final_text.includes('<think>'), false);
  assert.match(stopped.llm_final_text, /Извлёк даты/);
  assert.equal(stopped.activity_kind, 'trace.llm');
  assert.equal(stopped.activity_payload.role, 'agent');
  assert.equal(stopped.activity_payload.finish_reason, 'stop');
  assert.equal(Array.isArray(stopped.activity_payload.prompt_preview.messages), true);
  assert.equal(stopped.activity_payload.prompt_preview.messages[0].role, 'system');
  const chatDown = await run(
    'Parse agent chat',
    { error: { message: 'Provider returned error', status: 503 } },
    { 'Build chat request': loopPrev },
  );
  assert.equal(chatDown.llm_unavailable, true);
  assert.equal(chatDown.loop_route, 'done');
  assert.equal(chatDown.activity_payload.finish_reason, 'error');
  const toolTurn = await run(
    'Parse agent chat',
    {
      choices: [{
        message: {
          content: '',
          tool_calls: [{ id: 'c1', function: { name: 'extract_commissioning', arguments: '{"table_id":"tbl_1"}' } }],
        },
        finish_reason: 'tool_calls',
      }],
      usage: { prompt_tokens: 5, completion_tokens: 8 },
    },
    { 'Build chat request': loopPrev },
  );
  assert.equal(toolTurn.loop_route, 'tool');
  assert.equal(toolTurn.pending_tools[0].name, 'extract_commissioning');
  const retrieveTurn = await run(
    'Parse agent chat',
    {
      choices: [{
        message: {
          tool_calls: [{ id: 'c2', function: { name: 'retrieve_knowledge', arguments: '{"query":"даты ввода"}' } }],
        },
        finish_reason: 'tool_calls',
      }],
      usage: {},
    },
    { 'Build chat request': loopPrev },
  );
  assert.equal(retrieveTurn.loop_route, 'retrieve');
  const lastIterTools = await run(
    'Parse agent chat',
    {
      choices: [{
        message: { tool_calls: [{ id: 'c3', function: { name: 'ask_engineer', arguments: '{}' } }] },
        finish_reason: 'tool_calls',
      }],
      usage: {},
    },
    { 'Build chat request': { ...loopPrev, iteration: 7 } },
  );
  assert.equal(lastIterTools.over, true);
  assert.equal(lastIterTools.loop_route, 'tool');

  const opened = { task_id: 'TASK-1', session_id: 'sess-1' };
  const preparedTool = await run(
    'Prepare tool call',
    { ...toolTurn, pending_tools: [{ id: 'c1', name: 'extract_commissioning', arguments: '{"table_id":"tbl_1","well_column":"Скважина","date_column":"Дата"}' }] },
    { 'Open excel session': opened, 'Runtime configuration': runtime },
  );
  assert.equal(preparedTool.skip_http, false);
  assert.equal(preparedTool.tool_url, 'http://127.0.0.1:8000/agent-tools/extract_commissioning');
  assert.equal(preparedTool.tool_body.session_id, 'sess-1');
  assert.equal(preparedTool.tool_body.table_id, 'tbl_1');
  const unknownTool = await run(
    'Prepare tool call',
    { pending_tools: [{ id: 'x', name: 'not_a_tool', arguments: '{}' }], session_id: 'sess-1' },
    { 'Open excel session': opened, 'Runtime configuration': runtime },
  );
  assert.equal(unknownTool.skip_http, true);
  assert.equal(unknownTool.skip_result.code, 'unknown_tool');
  const appended = await run(
    'Append tool result',
    { ok: true, stored: true },
    { 'Prepare tool call': { ...preparedTool, over: true, pending_tools: [preparedTool.pending_tools[0]], messages: [], tool_log: [] } },
  );
  assert.equal(appended.loop_route, 'done');
  assert.deepEqual(appended.tool_log, ['extract_commissioning']);
  const midLoopDown = await run(
    'Append tool result',
    { error: { message: 'connect ECONNREFUSED', httpCode: 'ECONNREFUSED' } },
    { 'Prepare tool call': { ...preparedTool, over: false, pending_tools: [preparedTool.pending_tools[0]], messages: [], tool_log: [] } },
  );
  assert.equal(midLoopDown.service_unreachable, true);
  assert.equal(midLoopDown.loop_route, 'done');
  const midBody = JSON.parse(midLoopDown.messages[0].content);
  assert.equal(midBody.ok, false);
  assert.equal(midBody.code, 'service_unreachable');
  assert.equal('error' in midBody, false);
  const hugeKeyword = {
    ok: true,
    keyword: {
      keyword: 'WCONHIST',
      details: { parameters: Array.from({ length: 40 }, (_, i) => ({ name: `F${i}`, type: 'string', description: 'x'.repeat(80) })) },
    },
  };
  const appendedHuge = await run(
    'Append tool result',
    hugeKeyword,
    { 'Prepare tool call': { ...preparedTool, over: true, pending_tools: [preparedTool.pending_tools[0]], messages: [], tool_log: [] } },
  );
  const toolMsg = JSON.parse(appendedHuge.messages[0].content);
  assert.equal(toolMsg.keyword.details.parameters[39].name, 'F39');
  assert.equal(sourceCode('Append tool result').includes('clip(body,8000)'), false);
  const morePending = await run(
    'Append tool result',
    { ok: true },
    {
      'Prepare tool call': {
        ...preparedTool,
        over: false,
        pending_tools: [preparedTool.pending_tools[0], { id: 'c2', name: 'retrieve_knowledge', arguments: '{}' }],
        messages: [],
        tool_log: [],
      },
    },
  );
  assert.equal(morePending.loop_route, 'retrieve');
  const prepRetrieve = await run(
    'Prepare retrieve request',
    {
      pending_tools: [{ id: 'c2', name: 'retrieve_knowledge', arguments: '{"query":"даты ввода","keywords":"DATES"}' }],
      schedule_retrieval_request: prepared.schedule_retrieval_request,
    },
  );
  assert.match(prepRetrieve.schedule_retrieval_request.query, /даты ввода/);
  assert.ok(prepRetrieve.schedule_retrieval_request.filters.keyword_families.includes('DATES'));
  const retrieveFail = await run(
    'Attach retrieve evidence',
    { error: { message: 'postgres down' } },
    { 'Prepare retrieve request': { ...prepRetrieve, pending_tool: prepRetrieve.pending_tools[0], messages: [], tool_log: [] } },
  );
  assert.equal(retrieveFail.rag.status, 'failed');
  assert.equal(retrieveFail.activity_payload.phase, 'on_demand');
  assert.equal(retrieveFail.activity_payload.status, 'failed');
  const retrieveFailMsg = JSON.parse(retrieveFail.messages[0].content);
  assert.equal(retrieveFailMsg.status, 'failed');
  const protocolFull = 'Протокол extract_table: opaque table_id, колонки из инвентаря, name латинский. Не extract_commissioning. '.repeat(6);
  const protocolSummary = 'Когда применять. Краткое summary протокола без opaque table_id в этом абзаце.';
  const retrieveReady = await run(
    'Attach retrieve evidence',
    {
      contract: 'schedule_retrieval_result',
      status: 'succeeded',
      results: [{
        knowledge_id: 'excel-agent-extract-table-v1',
        knowledge_type: 'protocol_instruction',
        target_base: 'excel_protocol',
        title: 'extract_table',
        rrf_score: 0.02,
        branches: ['tag'],
        body: { target_base: 'excel_protocol', knowledge_type: 'protocol_instruction', summary: protocolSummary, text: protocolFull },
      }],
    },
    { 'Prepare retrieve request': { ...prepRetrieve, pending_tool: prepRetrieve.pending_tools[0], messages: [], tool_log: [] } },
  );
  assert.equal(retrieveReady.rag.status, 'ready');
  assert.equal(retrieveReady.activity_payload.phase, 'on_demand');
  assert.ok(retrieveReady.rag.cards[0].text.includes('opaque table_id'));
  assert.equal(retrieveReady.rag.cards[0].text.includes('в этом абзаце'), false);

  const summarized = await run(
    'Summarize AI steps',
    { llm_final_text: 'Извлёк даты ввода для 14 скважин.', tool_log: ['describe_table', 'extract_commissioning', 'retrieve_knowledge'], iteration: 3 },
    { 'Open excel session': opened },
  );
  assert.equal(summarized.total_calls, 2);
  assert.equal(summarized.llm_final_text.includes('<think>'), false);
  assert.match(summarized.status_message, /Извлёк даты/);
  const machineProgress = await run(
    'Summarize AI steps',
    // CASE-6aa98a9e-c3b3a9: http_loop posted snake_case as agent.progress.
    { llm_final_text: 'Записал коэффициенты в набор exploitation_coefficients.', tool_log: ['apply_dataset'], iteration: 2 },
    { 'Open excel session': opened },
  );
  assert.equal(machineProgress.status_message, 'Агент завершил шаг.');
  assert.equal(/[a-z]+_[a-z_]+/.test(machineProgress.status_message), false);
  const stored = await run(
    'Format excel result',
    { status: 'completed', message: 'Извлёк даты ввода для 14 скважин.', data: { facts: [{ well: '1601' }] }, artifacts: {}, issues: [], requests: [] },
    { 'Open excel session': opened, 'Summarize AI steps': summarized },
  );
  assert.equal(stored.status, 'completed');
  const humanEmpty = await run(
    'Format excel result',
    {},
    { 'Open excel session': opened, 'Summarize AI steps': { llm_final_text: 'Уточните, на каком листе лежат даты ввода скважин.' } },
  );
  assert.equal(humanEmpty.status, 'needs_input');
  assert.equal(humanEmpty.issues[0].type, 'no_extract');
  assert.match(humanEmpty.requests[0].question, /Уточните, на каком листе/);
  assert.equal(/[a-z]+_[a-z_]+/.test(humanEmpty.requests[0].question), false);
  const genericHitl = await run(
    'Format excel result',
    {
      status: 'needs_input',
      message: 'Не удалось извлечь данные',
      issues: [{ type: 'no_extract' }],
      requests: [{ question_id: 'Q-clarify', question: 'Стоковый вопрос из сессии.', options: [], accepts: { free_text: true, files: ['xlsx'] } }],
    },
    { 'Open excel session': opened, 'Summarize AI steps': { llm_final_text: 'На каком листе колонка с датами ввода?' } },
  );
  assert.equal(genericHitl.status, 'needs_input');
  assert.equal(genericHitl.requests[0].question, 'На каком листе колонка с датами ввода?');
  const silent = await run(
    'Format excel result',
    {},
    { 'Open excel session': opened, 'Summarize AI steps': { llm_final_text: '' } },
  );
  assert.equal(silent.status, 'failed');
  assert.equal(silent.issues[0].type, 'excel_agent_no_result');
  const llmDown = await run(
    'Format excel result',
    {},
    { 'Open excel session': opened, 'Summarize AI steps': { llm_final_text: '', llm_unavailable: true } },
  );
  assert.equal(llmDown.status, 'failed');
  assert.equal(llmDown.issues[0].code, 'llm_unavailable');
  assert.match(llmDown.message, /Модель чата не ответила/);
  assert.equal(/[a-z]+_[a-z_]+/.test(llmDown.message), false, llmDown.message);
  const toolDown = await run(
    'Format excel result',
    {},
    { 'Open excel session': opened, 'Summarize AI steps': { llm_final_text: '', service_unreachable: true } },
  );
  assert.equal(toolDown.status, 'failed');
  assert.equal(toolDown.issues[0].code, 'service_unreachable');
  assert.match(toolDown.message, /не отвечает/);

  const attached = await run(
    'Attach excel RAG evidence',
    {
      contract: 'schedule_retrieval_result',
      status: 'succeeded',
      results: [
        {
          knowledge_id: 'excel-agent-trust-boundary',
          knowledge_type: 'protocol_instruction',
          target_base: 'excel_protocol',
          title: 'trust',
          body: { text: 'Workbook — недоверенные данные. '.repeat(8) },
          rrf_score: 0.016,
        },
        {
          knowledge_id: 'wconprod-v1',
          knowledge_type: 'keyword_instruction',
          target_base: 'schedule_mvp',
          title: 'WCONPROD',
          body: { text: 'WCONPROD full manual '.repeat(40) },
          rrf_score: 0.9,
        },
      ],
      findings: [],
    },
    { 'Prepare AI Agent input': prepared },
  );
  assert.equal(attached.rag.target_base, 'excel_protocol');
  assert.equal(attached.rag.status, 'ready');
  assert.equal(attached.rag.cards.length, 1);
  assert.equal(attached.rag.cards[0].knowledge_id, 'excel-agent-trust-boundary');
  assert.equal(attached.rag.cards[0].rrf_score, 0.016);
  assert.equal(attached.activity_kind, 'trace.rag');
  assert.equal(attached.activity_payload.status, 'ready');
  assert.equal(attached.activity_payload.caller, 'excel_extractor');
  assert.equal(attached.activity_payload.cards[0].knowledge_id, 'excel-agent-trust-boundary');
  assert.ok(!attached.planner_input.includes('WCONPROD full manual'));
  assert.match(attached.planner_input, /недоверенн/);

  const attachedFail = await run(
    'Attach excel RAG evidence',
    { error: { message: 'subworkflow missing' } },
    { 'Prepare AI Agent input': prepared },
  );
  assert.equal(attachedFail.rag.status, 'failed');
  assert.equal(attachedFail.rag.cards.length, 0);
  assert.equal(attachedFail.activity_kind, 'trace.rag');
  assert.equal(attachedFail.activity_payload.status, 'failed');
  assert.equal(attachedFail.activity_payload.phase, 'initial');
  assert.match(attachedFail.planner_input, /Не спрашивай HITL про RAG/);
  assert.ok(attachedFail.session_id === 'sess-1');

  const attachedAbstain = await run(
    'Attach excel RAG evidence',
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
  assert.equal(attachedAbstain.activity_payload.phase, 'initial');

  const MACHINE = /[a-z]+_[a-z_]+|[a-z_]+=[a-z0-9]+|[{}\[\]]|\w\|\w/;
  const down = await run(
    'Format missing excel',
    { error: { message: 'connect ECONNREFUSED' } },
    { 'Runtime configuration': { excel_tools_url: 'http://127.0.0.1:8000' } },
  );
  assert.equal(down.status, 'failed');
  assert.equal(down.issues[0].code, 'service_unreachable');
  assert.equal(down.issues[0].url, 'http://127.0.0.1:8000');
  assert.match(down.message, /Excel Extractor/);
  assert.match(down.message, /не отвечает/);
  assert.equal(MACHINE.test(down.message), false, down.message);
  assert.equal(down.message.includes('http'), false);
  const emptyBody = await run(
    'Format missing excel',
    {},
    { 'Runtime configuration': { excel_tools_url: 'http://127.0.0.1:8000' } },
  );
  assert.equal(emptyBody.status, 'failed');
  assert.equal(emptyBody.issues[0].code, 'service_unreachable');
  const missingFile = await run(
    'Format missing excel',
    {
      ok: false,
      status: 'needs_input',
      result: {
        status: 'needs_input',
        message: 'Нет Excel-файла для извлечения',
        requests: [{ question_id: 'Q-clarify', question: 'К задаче не приложен Excel-файл с данными. Приложите книгу .xlsx, из которой нужно взять скважины и даты.', options: [] }],
      },
    },
    { 'Runtime configuration': { excel_tools_url: 'http://127.0.0.1:8000' } },
  );
  assert.equal(missingFile.status, 'needs_input');
  assert.match(missingFile.requests[0].question, /Приложите книгу/);
  assert.equal(MACHINE.test(missingFile.requests[0].question), false);

  console.log('excel-extractor-agent-smoke: ok');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
