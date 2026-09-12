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
const agent = wf.nodes.find((n) => n.name === 'Excel Extractor AI Agent');
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
const model = wf.nodes.find((n) => n.name === 'Excel Extractor Chat Model — Qwen');
assert.ok(model);
assert.equal(model.typeVersion, 1.3);
assert.equal(model.parameters.model.value, 'qwen/qwen3.6-27b');
assert.equal(model.parameters.options.timeout, 600000);
// n8n 2.30.8 + AI Agent v3 executes tools through the engine: the legacy langchain
// toolHttpRequest (hidden, supplyData-only) fails at runtime; tools must be HTTP Request "as tool".
assert.equal(wf.nodes.some((n) => n.type === '@n8n/n8n-nodes-langchain.toolHttpRequest'), false);
const toolNodes = wf.nodes.filter((n) => n.type === 'n8n-nodes-base.httpRequestTool');
const tools = toolNodes.map((n) => n.name);
for (const name of [
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
]) {
  assert.ok(tools.includes(name), name);
}
// $fromAI(key, description, type[, default]): the LLM names the table and the columns; nothing is
// inferred from the task text by regex. required = no default.
function fromAI(node, key) {
  const args = [...String(node.parameters.jsonBody).matchAll(/\$fromAI\("([^"]+)", "([^"]*)", "([^"]+)"(?:, "([^"]*)")?\)/g)].map((m) => ({
    key: m[1],
    description: m[2],
    type: m[3],
    required: m[4] === undefined,
  }));
  const arg = args.find((a) => a.key === key);
  assert.ok(arg, `${node.name}: $fromAI("${key}") missing`);
  return arg;
}
const extractTool = wf.nodes.find((n) => n.name === 'extract_commissioning');
assert.ok(String(extractTool.parameters.url).includes("'/agent-tools/' + \"extract_commissioning\""));
for (const key of ['table_id', 'well_column', 'date_column']) {
  const arg = fromAI(extractTool, key);
  assert.equal(arg.type, 'string', key);
  assert.equal(arg.required, true, key);
}
assert.match(fromAI(extractTool, 'date_column').description, /не из исходного \.INC/);
const paramsTool = wf.nodes.find((n) => n.name === 'extract_well_parameters');
assert.equal(fromAI(paramsTool, 'table_id').required, true);
assert.equal(fromAI(paramsTool, 'well_column').required, true);
assert.equal(fromAI(paramsTool, 'mapping').required, false, 'mapping is optional');
assert.match(fromAI(paramsTool, 'mapping').description, /md_top/);
const tableTool = wf.nodes.find((n) => n.name === 'extract_table');
assert.ok(String(tableTool.parameters.url).includes("'/agent-tools/' + \"extract_table\""));
assert.equal(fromAI(tableTool, 'table_id').required, true);
assert.equal(fromAI(tableTool, 'name').required, true);
assert.equal(fromAI(tableTool, 'columns').required, false, 'columns is optional JSON-as-string');
assert.equal(fromAI(tableTool, 'columns').type, 'string');
assert.match(String(tableTool.parameters.toolDescription), /extract_commissioning/);
const askTool = wf.nodes.find((n) => n.name === 'ask_engineer');
assert.equal(fromAI(askTool, 'question').required, true);
assert.equal(fromAI(askTool, 'options').required, false);
assert.match(String(askTool.parameters.toolDescription), /инженер/i);
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
// No regex "Capability router" and no HTTP extract_commissioning bypass: every task reaches the LLM
// agent, which picks table and columns from the inventory returned by open_session.
for (const gone of ['Capability router', 'Extract commissioning', 'Describe extract result', 'Extract finished?', 'Restore after extract event']) {
  assert.equal(wf.nodes.some((n) => n.name === gone), false, `${gone} must be gone`);
}
assert.equal(wf.nodes.some((n) => n.type === 'n8n-nodes-base.switch'), false);
assert.equal(JSON.stringify(wf).includes('suggested_capability'), false);
assert.equal(wf.connections['Session ready?'].main[0][0].node, 'Activity — Excel Extractor accepted');
assert.equal(wf.connections['Session ready?'].main[1][0].node, 'Format missing excel');
assert.equal(wf.connections['Restore after Excel Extractor progress'].main[0][0].node, 'Prepare AI Agent input');
assert.equal(wf.connections['Prepare AI Agent input'].main[0][0].node, 'Call Knowledge Retrieval');
const system = String(agent.parameters.options.systemMessage || '');
for (const must of ['extract_commissioning', 'extract_well_parameters', 'extract_table', 'expected_output', 'name_reserved', 'ask_engineer', 'spec_incomplete', 'column_not_found', 'question_not_human', 'engineer_answers']) {
  assert.ok(system.includes(must), `system prompt mentions ${must}`);
}
assert.equal(system.includes('suggested_capability'), false);
assert.equal(wf.connections['Call Knowledge Retrieval'].main[0][0].node, 'Attach excel RAG evidence');
assert.equal(wf.connections['Attach excel RAG evidence'].main[0][0].node, 'Excel Extractor AI Agent');
assert.equal(wf.connections['Excel Extractor AI Agent'].main[0][0].node, 'Summarize AI steps');
assert.equal(wf.connections['Result stored?'].main[0][0].node, 'Format excel result');
assert.equal(wf.connections['Result stored?'].main[1][0].node, 'Fetch excel result');
assert.equal(wf.connections['Format excel result'].main[0][0].node, 'Close excel session');
assert.equal(agent.parameters.options.maxIterations, 8);
assert.equal(wf.settings.executionTimeout, 1800);
assert.ok(sourceCode('Summarize AI steps').includes('ask_engineer'));
assert.equal(sourceCode('Summarize AI steps').includes('вызвал'), false, 'no "called N tools" template shown to engineers');
const queryTable = wf.nodes.find((n) => n.name === 'query_table');
assert.ok(String(queryTable.parameters.toolDescription).includes('массив') || String(queryTable.parameters.toolDescription).includes('Массив'));
const activityProgress = wf.nodes.find((n) => n.name === 'Activity — Excel Extractor progress');
assert.equal(activityProgress.parameters.options.timeout, 2000);
assert.equal(activityProgress.parameters.options.response.response.neverError, true);
assert.equal(activityProgress.parameters.authentication, undefined);
const close = wf.nodes.find((n) => n.name === 'Close excel session');
assert.ok(String(close.parameters.url).includes('/close'));
assert.equal(close.parameters.authentication, 'genericCredentialType');
assert.ok(toolNodes.length >= 8);
for (const t of toolNodes) {
  assert.equal(t.typeVersion, 4.4, t.name);
  assert.equal(t.parameters.descriptionType, 'manual', t.name);
  assert.equal(t.parameters.authentication, 'genericCredentialType', t.name);
  assert.equal(t.parameters.genericAuthType, 'httpHeaderAuth', t.name);
  const body = String(t.parameters.jsonBody);
  assert.ok(body.includes("session_id: $('Open excel session').first().json.session_id"), t.name);
  assert.equal(body.includes('$json.session_id'), false, t.name);
  assert.deepEqual(wf.connections[t.name], { ai_tool: [[{ node: 'Excel Extractor AI Agent', type: 'ai_tool', index: 0 }]] }, t.name);
}

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
assert.match(String(agent.parameters.options.systemMessage || ''), /excel_protocol/);

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
    return { first: () => items[0], all: () => items };
  };
  const fn = new AsyncFunction('$json', '$', '$input', sourceCode(name));
  const result = await fn(json, lookup, { first: () => ({ json }), all: () => [{ json }] });
  assert.ok(Array.isArray(result) && result[0]?.json);
  return result[0].json;
}

(async () => {
  const inventory = {
    files: ['rates.xlsx'],
    sheets: [{ name: 'Wells', rows: 3, columns: 2, file: 'rates.xlsx' }],
    table_count: 1,
    tables: [{ table_id: 'tbl_1', file: 'rates.xlsx', sheet: 'Wells', range: 'A1:B3', columns: ['Скважина', 'Дебит'], sample: [{ Скважина: '1601', Дебит: 120 }] }],
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

  // Summarize AI steps: a stored result (extract_* / ask_engineer) → fetch it; otherwise a prose question.
  const opened = { task_id: 'TASK-1', session_id: 'sess-1' };
  const summarized = await run(
    'Summarize AI steps',
    {
      output: 'Извлёк даты ввода для 14 скважин.',
      intermediateSteps: [
        { action: { tool: 'describe_table' } },
        { action: { tool: 'extract_commissioning' } },
      ],
    },
    { 'Open excel session': opened },
  );
  assert.equal(summarized.skip_fetch, false);
  assert.equal(summarized.has_result, true);
  assert.equal(summarized.status_message, 'Извлёк даты ввода для 14 скважин.');
  const noResult = await run(
    'Summarize AI steps',
    { output: 'Не нашёл таблицу с датами.', intermediateSteps: [{ action: { tool: 'detect_tables' } }, { action: { tool: 'describe_table' } }] },
    { 'Open excel session': opened },
  );
  assert.equal(noResult.skip_fetch, true);
  assert.equal(noResult.status, 'needs_input');
  assert.equal(noResult.task_id, 'TASK-1');
  assert.match(noResult.requests[0].question, /Уточните, на каком листе/);
  assert.equal(noResult.requests[0].accepts.free_text, true);
  assert.equal(/[a-z]+_[a-z_]+/.test(noResult.requests[0].question), false, 'engineer question has no snake_case tokens');
  assert.equal(noResult.issues[0].type, 'no_extract');
  const askedHuman = await run(
    'Summarize AI steps',
    { output: 'Нужно уточнение инженера.', intermediateSteps: [{ action: { tool: 'ask_engineer' } }] },
    { 'Open excel session': opened },
  );
  assert.equal(askedHuman.skip_fetch, false, 'ask_engineer stored needs_input in the session — fetch it');

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
  assert.ok(!attached.planner_input.includes('WCONPROD full manual'));
  assert.match(attached.planner_input, /недоверенн/);

  const attachedFail = await run(
    'Attach excel RAG evidence',
    { error: { message: 'subworkflow missing' } },
    { 'Prepare AI Agent input': prepared },
  );
  assert.equal(attachedFail.rag.status, 'unavailable');
  assert.equal(attachedFail.rag.cards.length, 0);
  assert.match(attachedFail.planner_input, /Не спрашивай HITL про RAG/);
  assert.ok(attachedFail.session_id === 'sess-1');

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
