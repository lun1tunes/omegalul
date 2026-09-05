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
assert.equal(model.parameters.options.timeout, 300000);
const tools = wf.nodes.filter((n) => n.type === '@n8n/n8n-nodes-langchain.toolHttpRequest').map((n) => n.name);
for (const name of [
  'inspect_schedule',
  'search_keywords',
  'get_keyword',
  'apply_commissioning',
  'apply_group_rebind',
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
assert.equal(wf.connections['Prepare AI Agent input'].main[0][0].node, 'Call Knowledge Retrieval');
assert.equal(wf.connections['Call Knowledge Retrieval'].main[0][0].node, 'Attach schedule RAG evidence');
assert.equal(wf.connections['Attach schedule RAG evidence'].main[0][0].node, 'Schedule Builder AI Agent');
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
const call = orch.nodes.find((n) => n.name === 'Call Schedule Builder');
assert.equal(call.type, 'n8n-nodes-base.executeWorkflow');
assert.equal(call.typeVersion, 1.3);
assert.equal(call.onError, 'continueRegularOutput');
assert.equal(call.parameters.workflowId.value, 'REPLACE_SCHEDULE_BUILDER_AGENT_IN_UI');
assert.equal(call.parameters.workflowId.cachedResultName, 'Agent — Schedule Builder');
assert.equal(call.parameters.options.waitForSubWorkflow, true);
assert.deepEqual(Object.keys(call.parameters.workflowInputs.value), ['agent_task']);
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
      suggested_capability: 'operations',
    },
    {
      'Normalize schedule task': { agent_task: { objective: 'Поставь ORAT 80 на скважины, не трогай факт' } },
    },
  );
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

  console.log('schedule-builder-agent-smoke: ok');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
