'use strict';
/**
 * Thin Orchestrator — MAS: one step, retrieval selector orchestrator_routing, errorWorkflow bound.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || path.resolve(__dirname, '../..');
const read = (rel) => JSON.parse(fs.readFileSync(path.join(workspace, rel), 'utf8'));
const wf = read('n8n/workflows/core/mas-orchestrator.workflow.json');
const err = read('n8n/workflows/core/mas-error-traces.workflow.json');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function source(name) {
  const node = wf.nodes.find((n) => n.name === name);
  assert.ok(node && node.type === 'n8n-nodes-base.code', `missing code ${name}`);
  return node.parameters.jsCode;
}

function toItem(payload) {
  if (payload && typeof payload === 'object' && !Array.isArray(payload) && payload.json !== undefined && payload.binary !== undefined) {
    return payload;
  }
  return { json: payload };
}

async function run(name, json, nodes = {}, binary = {}) {
  const resolved = {
    'Authenticated MAS webhook': { json, binary },
    'Normalize step request': { json, binary },
    'Runtime endpoints': { json: {} },
    ...nodes,
  };
  const lookup = (nodeName) => {
    if (!Object.prototype.hasOwnProperty.call(resolved, nodeName)) {
      throw new Error(`node not executed: ${nodeName}`);
    }
    const payload = resolved[nodeName];
    const items = Array.isArray(payload) ? payload.map(toItem) : [toItem(payload)];
    return { first: () => items[0], all: () => items };
  };
  const fn = new AsyncFunction('$json', '$', '$input', '$execution', source(name));
  const result = await fn(json, lookup, { first: () => ({ json, binary }), all: () => [{ json, binary }] }, { id: 'exec-1' });
  assert.ok(Array.isArray(result) && result[0]?.json);
  return result[0].json;
}

(async () => {
  assert.equal(wf.name, 'Orchestrator — MAS');
  assert.equal(wf.id, 'e9bbdb6e-3b7c-5dc0-851a-30bd9f2eb0d6');
  assert.equal(wf.settings.errorWorkflow, err.id);
  const webhook = wf.nodes.find((n) => n.type === 'n8n-nodes-base.webhook');
  assert.equal(webhook.parameters.path, 'mas-orchestrator-step');
  const text = JSON.stringify(wf);
  assert.equal(text.includes('Call routing Hybrid Retrieval'), false);
  assert.equal(text.includes('Call SCHEDULE Builder'), false);
  assert.equal(text.includes('engineering-orchestrator'), false);
  for (const need of [
    'Normalize step request',
    'Load case',
    'Load agent registry',
    'Prepare decision context',
    'Call Knowledge Retrieval',
    'Attach orchestrator RAG evidence',
    'Decision LLM',
    'Parse decision',
    'Action router',
    'Call Excel Extractor',
    'Call Calculation Agent',
    'Call Schedule Builder',
    'Merge agent result',
    'Continue loop?',
    'Probe ping?',
    'Needs create?',
    'Apply request extras',
    'Status only?',
    'Resume persist?',
    'POST continue run',
    'Prepare Activity ack',
    'Activity sync?',
    'POST step ack to MAS Activity',
  ]) {
    assert.ok(wf.nodes.some((n) => n.name === need), `missing ${need}`);
  }
  const exec = wf.nodes.filter((n) => n.type !== 'n8n-nodes-base.stickyNote');
  const xs = exec.map((n) => n.position[0]);
  const ys = exec.map((n) => n.position[1]);
  const width = Math.max(...xs) - Math.min(...xs);
  const uniqueY = new Set(ys).size;
  assert.ok(width < 2200, `orchestrator too wide: ${width}`);
  assert.ok(uniqueY >= 8, `orchestrator still a line: uniqueY=${uniqueY}`);
  assert.ok(wf.nodes.some((n) => n.name === 'lane intake'));
  const decision = wf.nodes.find((n) => n.name === 'Decision LLM');
  assert.equal(decision.type, '@n8n/n8n-nodes-langchain.chainLlm');
  assert.equal(decision.typeVersion, 1.9);
  assert.equal(decision.parameters.hasOutputParser, true);
  assert.equal(text.includes('"Decision Agent"'), false);

  const model = wf.nodes.find((n) => n.name === 'Decision Chat Model — configure in UI');
  assert.ok(model);
  assert.equal(model.parameters.model.value, 'qwen3.6-plus');
  assert.equal(model.parameters.options.temperature, 0);
  assert.equal(model.parameters.options.maxTokens, 1024);
  const modelEdges = wf.connections['Decision Chat Model — configure in UI'].ai_languageModel[0];
  assert.equal(modelEdges[0].type, 'ai_languageModel');
  assert.ok(modelEdges.some((e) => e.node === 'Decision LLM' && e.type === 'ai_languageModel'));
  assert.ok(modelEdges.some((e) => e.node === 'Decision Structured Output' && e.type === 'ai_languageModel'));
  const parserEdges = wf.connections['Decision Structured Output'].ai_outputParser[0];
  assert.equal(parserEdges[0].node, 'Decision LLM');

  const runtime = wf.nodes.find((n) => n.name === 'Runtime endpoints');
  assert.ok(runtime);
  assert.equal(runtime.type, 'n8n-nodes-base.executeWorkflow');
  assert.equal(runtime.typeVersion, 1.3);
  assert.equal(runtime.parameters.workflowId.value, 'REPLACE_MAS_RUNTIME_CONFIG_IN_UI');
  assert.equal(runtime.parameters.workflowId.cachedResultName, 'MAS — Runtime Config');
  assert.equal(runtime.parameters.options.waitForSubWorkflow, true);
  assert.equal(text.includes('excel_tools_api_key'), false);
  assert.equal(text.includes('mas-host-bridge'), false);
  const callSchedule = wf.nodes.find((n) => n.name === 'Call Schedule Builder');
  assert.equal(callSchedule.type, 'n8n-nodes-base.executeWorkflow');
  assert.equal(callSchedule.typeVersion, 1.3);
  assert.equal(callSchedule.parameters.workflowId.value, 'REPLACE_SCHEDULE_BUILDER_AGENT_IN_UI');
  assert.equal(callSchedule.parameters.workflowId.cachedResultName, 'Agent — Schedule Builder');
  assert.equal(callSchedule.parameters.options.waitForSubWorkflow, true);
  assert.equal(callSchedule.onError, 'continueRegularOutput');
  const helperNames = [
    'Apply request extras',
    'Prepare decision context',
    'Parse decision',
    'Merge agent result',
  ];
  const helperChunks = helperNames.map((name) => {
    const js = source(name);
    const begin = js.indexOf('/* mas_state_utils begin */');
    const end = js.indexOf('/* mas_state_utils end */');
    assert.ok(begin >= 0 && end > begin, `state utils markers missing in ${name}`);
    assert.ok(js.includes('function readUnlistedWellsPolicy'), name);
    assert.match(js, /if\(nested\) return nested;\s*continue;/, name);
    assert.ok(js.includes('function normalizeHitlAnswer'), name);
    assert.ok(js.includes('function inferTaskPatterns'), name);
    assert.ok(js.includes('function inferRetrievalQuery'), name);
    return js.slice(begin, end);
  });
  assert.equal(new Set(helperChunks).size, 1, 'state helpers must be identical across Code nodes');
  assert.equal(helperChunks[0].includes("s.includes('keep')"), false);
  assert.equal(helperChunks[0].includes("s.includes('remove')"), false);
  assert.match(helperChunks[0], /\\bkeep\\b/);
  assert.match(helperChunks[0], /\\bremove\\b/);
  {
    // Activity option button answer: {choice, text, label} — no regex on the human label.
    const helpers = new Function(`${helperChunks[0]}; return { normalizeHitlAnswer, readUnlistedWellsPolicy };`)();
    const clicked = { choice: 'remove', text: 'Убрать из прогноза', label: 'Убрать из прогноза' };
    assert.equal(helpers.normalizeHitlAnswer('unlisted_wells_policy', clicked, '').unlisted_wells_policy, 'remove');
    assert.equal(helpers.readUnlistedWellsPolicy({ unlisted_wells_policy: clicked }), 'remove');
    assert.equal(helpers.readUnlistedWellsPolicy({ 'Q-parent-group': clicked }), null, 'choice is gated by the question id');
    assert.equal(helpers.readUnlistedWellsPolicy({ unlisted_wells_policy: { choice: 'keep', text: 'Оставить как в baseline' } }), 'keep');
  }
  const continueNode = wf.nodes.find((n) => n.name === 'POST continue run');
  assert.ok(String(continueNode.parameters.jsonBody).includes("action: 'step'"));
  assert.ok(String(continueNode.parameters.jsonBody).includes('orchestrator-self'));
  assert.equal(continueNode.parameters.url, "={{ $json.continue_url }}");
  assert.equal(continueNode.parameters.authentication, 'genericCredentialType');
  assert.equal(continueNode.parameters.genericAuthType, 'httpHeaderAuth');
  assert.ok(continueNode.credentials && continueNode.credentials.httpHeaderAuth);
  assert.equal(continueNode.parameters.options.timeout, 8000);
  assert.equal(source('Merge agent result').includes('/cases/'), false);
  assert.ok(source('Merge agent result').includes('mas-orchestrator-step'));
  const ackHttp = wf.nodes.find((n) => n.name === 'POST step ack to MAS Activity');
  assert.equal(ackHttp.parameters.options.timeout, 2000);
  const callExcel = wf.nodes.find((n) => n.name === 'Call Excel Extractor');
  assert.equal(callExcel.type, 'n8n-nodes-base.executeWorkflow');
  assert.equal(callExcel.typeVersion, 1.3);
  assert.equal(callExcel.parameters.workflowId.value, 'REPLACE_EXCEL_EXTRACTION_AGENT_IN_UI');
  assert.equal(callExcel.parameters.workflowId.cachedResultName, 'Agent — Excel Extractor');
  assert.equal(callExcel.parameters.options.waitForSubWorkflow, true);
  assert.equal(callExcel.onError, 'continueRegularOutput');
  assert.deepEqual(Object.keys(callExcel.parameters.workflowInputs.value), ['agent_task']);
  assert.equal(callExcel.parameters.workflowInputs.value.agent_task, '={{ $json.agent_task }}');
  assert.equal(text.includes('excel_extractor_url'), false, 'no HTTP /agent/run URL — specialist is executeWorkflow');
  assert.equal(callSchedule.parameters.workflowInputs.value.agent_task, '={{ $json.agent_task }}');
  const callRag = wf.nodes.find((n) => n.name === 'Call Knowledge Retrieval');
  assert.equal(callRag.type, 'n8n-nodes-base.executeWorkflow');
  assert.equal(callRag.typeVersion, 1.3);
  assert.equal(callRag.parameters.workflowId.value, 'REPLACE_SCHEDULE_RAG_RETRIEVAL_IN_UI');
  assert.equal(callRag.parameters.workflowId.cachedResultName, 'MAS — Knowledge Retrieval');
  assert.equal(callRag.parameters.options.waitForSubWorkflow, true);
  assert.equal(callRag.onError, 'continueRegularOutput');
  assert.equal(
    callRag.parameters.workflowInputs.value.schedule_retrieval_request,
    '={{ $json.schedule_retrieval_request }}',
  );
  assert.equal(wf.connections['Prepare decision context'].main[0][0].node, 'Call Knowledge Retrieval');
  assert.equal(wf.connections['Call Knowledge Retrieval'].main[0][0].node, 'Attach orchestrator RAG evidence');
  assert.equal(wf.connections['Attach orchestrator RAG evidence'].main[0][0].node, 'Decision LLM');
  const system = decision.parameters.messages.messageValues[0].message;
  assert.match(system, /orchestrator_routing/);
  assert.equal(system.includes('не ходи в RAG'), false);
  assert.match(source('Prepare decision context'), /orchestrator_routing/);
  assert.equal(source('Prepare decision context').includes('schedule_mvp'), false);
  assert.match(source('Attach orchestrator RAG evidence'), /Prepare decision context/);
  assert.match(source('Attach orchestrator RAG evidence'), /orchestrator_routing/);
  assert.equal(text.includes('formBinaryData'), false);
  assert.equal(text.includes('.first().binary'), false);
  assert.equal(text.includes('multipart-form-data'), false);

  const probeIf = wf.connections['Probe ping?'];
  assert.equal(probeIf.main[0][0].node, 'Prepare Activity ack');
  assert.equal(probeIf.main[1][0].node, 'Needs create?');
  assert.equal(wf.connections['Normalize step request'].main[0][0].node, 'Probe ping?');
  assert.equal(wf.connections['Status only?'].main[0][0].node, 'Prepare Activity ack');
  assert.equal(wf.connections['Prepare Activity ack'].main[0][0].node, 'Activity sync?');
  const ack = wf.nodes.find((n) => n.name === 'Prepare Activity ack');
  assert.match(ack.parameters.jsCode, /persist_events/);
  assert.match(ack.parameters.jsCode, /!persisted/);
  assert.equal(ack.parameters.jsCode.includes("payload.message||kind"), false);

  const probed = await run('Normalize step request', {
    action: 'probe',
    requested_by: 'activity-diagnostics',
  });
  assert.equal(probed.is_probe, true);
  assert.equal(probed.case_id, 'CASE-readiness-probe');
  assert.equal(probed.should_continue, false);

  const probedWrapped = await run('Normalize step request', {
    body: { action: 'status', case_id: 'CASE-readiness-probe' },
  });
  assert.equal(probedWrapped.is_probe, true);
  assert.equal(probedWrapped.case_id, 'CASE-readiness-probe');

  await assert.rejects(
    () => run('Normalize step request', { action: 'step' }),
    /case_id is required/,
  );

  const stepped = await run('Normalize step request', { case_id: 'CASE-1', action: 'step' });
  assert.equal(stepped.is_probe, false);
  assert.equal(stepped.is_status, false);
  assert.equal(stepped.case_id, 'CASE-1');

  const statusCase = await run('Normalize step request', { action: 'status', case_id: 'CASE-1' });
  assert.equal(statusCase.is_probe, false);
  assert.equal(statusCase.is_status, true);

  const started = await run('Normalize step request', { action: 'start', task_description: 'Сдвинуть даты' });
  assert.equal(started.needs_create, true);
  assert.equal(started.goal, 'Сдвинуть даты');
  assert.match(started.case_id, /^CASE-/);

  const created = await run('Normalize step request', {
    action: 'create',
    case_id: 'CASE-from-activity',
    task_description: 'Новая задача из Activity',
    activity_base_url: 'http://mas-activity:8200',
  });
  assert.equal(created.needs_create, true);
  assert.equal(created.case_id, 'CASE-from-activity');
  assert.equal(created.goal, 'Новая задача из Activity');
  assert.equal(created.activity_base_url, 'http://mas-activity:8200');

  const createdCase = await run(
    'Prepare start case',
    {
      case_id: 'CASE-from-activity',
      goal: 'Новая задача из Activity',
      task_name: 'Демо',
      requested_by: 'tester',
    },
  );
  assert.deepEqual(createdCase.state.artifacts, {});
  assert.equal(createdCase.state.version, 1);
  assert.match(JSON.stringify(createdCase.persist_events[0]), /Activity/);

  const fatFacts = Array.from({ length: 40 }, (_, i) => ({
    well: `W${i}`,
    date: '2020-01-01',
    values: { pad: 'x'.repeat(80) },
  }));
  const prepared = await run(
    'Prepare decision context',
    { case_id: 'CASE-1' },
    {
      'Load case': {
        state: {
          goal: 'даты ввода',
          artifacts: {
            excel: { filename: 'dates.xlsx', artifact_id: 'excel' },
            schedule_source: { filename: 'base.inc', artifact_id: 'schedule_source' },
            schedule_source_1: { filename: 'GRUPTREE.GRDECL', artifact_id: 'schedule_source_1' },
          },
          data: {
            facts: fatFacts,
            excel: { facts: fatFacts, normalized_rows: [{ preview: fatFacts, row_count: 40 }] },
          },
          current_task: {
            task_id: 'TASK-OLD',
            agent_id: 'excel_extractor',
            context: { data: { facts: fatFacts } },
          },
          hitl: { pending: false, answers: { 'Q-1': JSON.stringify({ text: 'ok' }) } },
        },
        status: 'running',
      },
      'Load agent registry': { agent_id: 'excel_extractor', title: 'Excel' },
      'Runtime endpoints': {
        activity_base_url: 'http://mas-activity:8200',
        excel_extractor_url: 'http://excel-tools:8000/agent/run',
      },
    },
  );
  assert.equal(prepared.compact.has_excel, true);
  assert.equal(prepared.compact.has_schedule_source, true);
  assert.equal(prepared.compact.files.grdecl, 1);
  assert.equal(prepared.compact.files.includes, 0);
  assert.equal(prepared.compact.excel_filename, 'dates.xlsx');
  assert.equal(prepared.compact.excel_facts, 40);
  assert.equal(prepared.compact.wells_in_excel.length, 20);
  assert.equal(prepared.compact.current_task.agent_id, 'excel_extractor');
  assert.equal(prepared.compact.current_task.context, undefined);
  assert.ok(!prepared.planner_input.includes('pad'));
  assert.ok(!('facts' in (prepared.state.data || {})));
  assert.deepEqual(prepared.state.hitl.answers['Q-1'], { text: 'ok' });
  assert.equal(prepared.activity_base_url, 'http://mas-activity:8200');
  assert.match(prepared.planner_input, /excel_extractor/);
  assert.equal(prepared.schedule_retrieval_request.filters.target_base, 'orchestrator_routing');
  assert.deepEqual(prepared.schedule_retrieval_request.filters.knowledge_types, ['routing_card']);
  assert.equal(prepared.schedule_retrieval_request.filters.access_scope, 'petroleum-engineering');
  const families = prepared.schedule_retrieval_request.filters.keyword_families;
  assert.ok(families.includes('XLSX'));
  assert.ok(families.includes('EXCEL_EXTRACTOR'));
  assert.ok(families.includes('INC'));
  assert.ok(families.includes('SCHEDULE_BUILDER'));
  assert.ok(families.includes('COMMISSIONING'));
  assert.equal(families.includes('GROUP_CONTROL'), false);
  assert.equal(families.includes('WCONPROD'), false);
  assert.equal(families.includes('DATES'), false);
  assert.ok(!prepared.schedule_retrieval_request.query.includes('WCONPROD'));
  assert.ok(!prepared.schedule_retrieval_request.query.includes('dates.xlsx'));
  assert.ok(!prepared.schedule_retrieval_request.query.includes('base.inc'));
  assert.ok(!prepared.schedule_retrieval_request.query.includes('excel workbook'));
  assert.ok(!prepared.schedule_retrieval_request.query.includes('schedule_builder —'));
  assert.match(prepared.schedule_retrieval_request.query, /даты ввода/);
  assert.match(prepared.schedule_retrieval_request.query, /Извлечено 40 скважин из Excel/);
  assert.match(prepared.schedule_retrieval_request.query, /Нужно обновить baseline SCHEDULE/);
  // No rule-based "next step" hint: the Decision LLM must reason from state + registry + RAG.
  assert.ok(!prepared.planner_input.includes('Подсказка следующего шага'));
  assert.ok(!/Дальше schedule_builder|Сначала excel_extractor|сразу schedule_builder|уже есть — finish/.test(prepared.planner_input));
  assert.equal(prepared.schedule_retrieval_request.top_k, 12);
  const topics = prepared.schedule_retrieval_request.filters.topics;
  assert.ok(topics.includes('Excel'));
  assert.ok(topics.includes('SCHEDULE'));
  assert.ok(topics.includes('handoff'));
  assert.equal(topics.includes('маршрутизация'), false);
  assert.deepEqual(prepared.retrieval_selector, {
    target_base: 'orchestrator_routing',
    knowledge_types: ['routing_card'],
  });
  const patterns = prepared.schedule_retrieval_request.filters.task_patterns;
  assert.ok(patterns.includes('даты ввода'));
  assert.ok(patterns.includes('новые даты ввода скважин'));
  assert.ok(patterns.includes('сдвиг дат'));
  assert.equal(patterns.includes('перепривязка групп'), false);

  const groupPrepared = await run(
    'Prepare decision context',
    { case_id: 'CASE-1' },
    {
      'Load case': {
        state: {
          goal: 'перепривяжи скважины в группу G1',
          artifacts: { schedule_source: { filename: 'base.inc', artifact_id: 'schedule_source' } },
          data: {},
        },
        status: 'running',
      },
      'Load agent registry': { agent_id: 'schedule_builder', title: 'Schedule' },
      'Runtime endpoints': { activity_base_url: 'http://mas-activity:8200' },
    },
  );
  const groupFam = groupPrepared.schedule_retrieval_request.filters.keyword_families;
  assert.ok(groupFam.includes('GROUP_CONTROL'));
  assert.ok(groupFam.includes('INC'));
  assert.equal(groupFam.includes('COMMISSIONING'), false);
  assert.equal(groupFam.includes('XLSX'), false);
  assert.ok(groupPrepared.schedule_retrieval_request.filters.task_patterns.includes('перепривязка групп'));
  assert.ok(!groupPrepared.schedule_retrieval_request.query.includes('base.inc'));
  assert.ok(groupPrepared.schedule_retrieval_request.filters.topics.includes('SCHEDULE'));

  const attached = await run(
    'Attach orchestrator RAG evidence',
    {
      contract: 'schedule_retrieval_result',
      status: 'succeeded',
      evidence_ready: true,
      results: [
        {
          knowledge_id: 'route-excel-extractor',
          knowledge_type: 'routing_card',
          target_base: 'orchestrator_routing',
          title: 'Excel',
          body: { text: 'Делегируйте excel_extractor когда есть xlsx и в artifacts есть Excel файл.' },
        },
        {
          knowledge_id: 'wconprod-v1',
          knowledge_type: 'keyword_instruction',
          target_base: 'schedule_mvp',
          title: 'WCONPROD',
          body: { text: 'WCONPROD full manual '.repeat(40) },
        },
      ],
      findings: [],
    },
    { 'Prepare decision context': prepared },
  );
  assert.equal(attached.rag.target_base, 'orchestrator_routing');
  assert.equal(attached.rag.status, 'ready');
  assert.equal(attached.rag.cards.length, 1);
  assert.equal(attached.rag.cards[0].knowledge_id, 'route-excel-extractor');
  assert.ok(!attached.planner_input.includes('WCONPROD full manual'));
  assert.match(attached.planner_input, /excel_extractor/);
  assert.equal(attached.case_id, 'CASE-1');
  assert.ok(attached.state);

  const attachedFail = await run(
    'Attach orchestrator RAG evidence',
    { error: { message: 'subworkflow missing' } },
    { 'Prepare decision context': prepared },
  );
  assert.equal(attachedFail.rag.status, 'unavailable');
  assert.equal(attachedFail.rag.cards.length, 0);
  assert.match(attachedFail.planner_input, /Не спрашивай HITL про RAG/);
  assert.ok(attachedFail.planner_input.includes(prepared.planner_input.slice(0, 40)));

  const attachedEmpty = await run(
    'Attach orchestrator RAG evidence',
    {
      contract: 'schedule_retrieval_result',
      status: 'succeeded',
      results: [],
      findings: [{ code: 'NO_AUTHORIZED_EVIDENCE' }],
    },
    { 'Prepare decision context': prepared },
  );
  assert.equal(attachedEmpty.rag.status, 'empty');
  assert.deepEqual(attachedEmpty.rag.findings, ['NO_AUTHORIZED_EVIDENCE']);

  const longCard = 'R'.repeat(900);
  const attachedTrim = await run(
    'Attach orchestrator RAG evidence',
    {
      contract: 'schedule_retrieval_result',
      status: 'succeeded',
      results: Array.from({ length: 8 }, (_, i) => ({
        knowledge_id: `route-card-${i}`,
        knowledge_type: 'routing_card',
        target_base: 'orchestrator_routing',
        title: `Card ${i}`,
        body: { text: longCard },
      })),
    },
    { 'Prepare decision context': prepared },
  );
  assert.equal(attachedTrim.rag.cards.length, 6);
  assert.equal(attachedTrim.rag.cards[0].text.length, 700);

  const attachedShort = await run(
    'Attach orchestrator RAG evidence',
    {
      contract: 'schedule_retrieval_result',
      status: 'succeeded',
      results: [
        {
          knowledge_id: 'tiny',
          knowledge_type: 'routing_card',
          target_base: 'orchestrator_routing',
          title: 'Tiny',
          body: { text: 'коротко' },
        },
        {
          knowledge_id: 'zero-score',
          knowledge_type: 'routing_card',
          target_base: 'orchestrator_routing',
          title: 'Zero',
          rrf_score: 0,
          body: { text: 'Достаточно длинный текст карточки маршрутизации чтобы пройти порог длины.' },
        },
        {
          knowledge_id: 'route-excel-extractor',
          knowledge_type: 'routing_card',
          target_base: 'orchestrator_routing',
          title: 'Excel',
          body: { text: 'Делегируйте excel_extractor когда есть xlsx и в artifacts есть Excel файл.' },
        },
      ],
    },
    { 'Prepare decision context': prepared },
  );
  assert.equal(attachedShort.rag.status, 'ready');
  assert.equal(attachedShort.rag.cards.length, 1);
  assert.equal(attachedShort.rag.cards[0].knowledge_id, 'route-excel-extractor');

  const attachedFloor = await run(
    'Attach orchestrator RAG evidence',
    {
      contract: 'schedule_retrieval_result',
      status: 'succeeded',
      results: [
        {
          knowledge_id: 'weak',
          knowledge_type: 'routing_card',
          target_base: 'orchestrator_routing',
          title: 'Weak',
          rrf_score: 0.01,
          body: { text: 'Достаточно длинный текст карточки маршрутизации чтобы пройти порог длины.' },
        },
        {
          knowledge_id: 'strong',
          knowledge_type: 'routing_card',
          target_base: 'orchestrator_routing',
          title: 'Strong',
          rrf_score: 0.09,
          body: { text: 'Делегируйте excel_extractor когда есть xlsx и в artifacts есть Excel файл.' },
        },
      ],
    },
    { 'Prepare decision context': prepared },
  );
  assert.equal(attachedFloor.rag.cards.length, 1);
  assert.equal(attachedFloor.rag.cards[0].knowledge_id, 'strong');

  const resumed = await run('Normalize step request', {
    action: 'resume',
    case_id: 'CASE-1',
    human_response: '2024',
    gate_id: 'Q-1',
  });
  assert.equal(resumed.is_resume, true);
  assert.equal(resumed.human_response, '2024');

  const statusSnap = await run(
    'Apply request extras',
    {
      status: 'waiting_user',
      state: {
        goal: 'g',
        step_count: 3,
        hitl: { pending: true, questions: [{ question_id: 'Q-5', question: 'Какие даты?' }] },
      },
    },
    { 'Normalize step request': { case_id: 'CASE-1', is_status: true, action: 'status' } },
  );
  assert.equal(statusSnap.action_type, 'status');
  assert.equal(statusSnap.human_gate.gate_id, 'Q-5');
  assert.equal(statusSnap.human_gate.expected_version, 3);
  assert.equal(statusSnap.should_continue, false);

  const hitlApplied = await run(
    'Apply request extras',
    {
      status: 'waiting_user',
      state: {
        goal: 'g',
        hitl: { pending: true, questions: [{ question_id: 'Q-1', question: '?' }], answers: {} },
      },
    },
    {
      'Normalize step request': {
        case_id: 'CASE-1',
        is_resume: true,
        action: 'resume',
        human_response: 'январь',
        gate_id: 'Q-1',
      },
    },
  );
  assert.equal(hitlApplied.did_resume, true);
  assert.equal(hitlApplied.state.hitl.pending, false);
  assert.equal(hitlApplied.state.hitl.answers['Q-1'], 'январь');
  assert.equal(hitlApplied.state.version, 1);

  const hitlJson = await run(
    'Apply request extras',
    {
      status: 'waiting_user',
      state: {
        goal: 'g',
        hitl: { pending: true, questions: [{ question_id: 'Q-1', question: '?' }], answers: {} },
      },
    },
    {
      'Normalize step request': {
        case_id: 'CASE-1',
        is_resume: true,
        action: 'resume',
        human_response: JSON.stringify({ text: 'январь' }),
        gate_id: 'Q-1',
      },
    },
  );
  assert.equal(hitlJson.state.hitl.answers['Q-1'].text, 'январь');

  const unlistedHitl = await run(
    'Apply request extras',
    {
      status: 'waiting_user',
      state: {
        goal: 'g',
        hitl: {
          pending: true,
          questions: [{ question_id: 'unlisted_wells_policy', question: 'Скважины не из Excel?' }],
          answers: {},
        },
      },
    },
    {
      'Normalize step request': {
        case_id: 'CASE-1',
        is_resume: true,
        action: 'resume',
        human_response: 'убери лишние скважины',
        gate_id: 'unlisted_wells_policy',
      },
    },
  );
  assert.equal(unlistedHitl.did_resume, true);
  assert.equal(unlistedHitl.state.hitl.answers.unlisted_wells_policy.unlisted_wells_policy, 'remove');
  assert.equal(unlistedHitl.state.hitl.answers.unlisted_wells_policy.raw, 'убери лишние скважины');

  const unlistedKeep = await run(
    'Apply request extras',
    {
      status: 'waiting_user',
      state: {
        goal: 'g',
        hitl: {
          pending: true,
          questions: [{ question_id: 'unlisted_wells_policy', question: 'Скважины не из Excel?' }],
          answers: {},
        },
      },
    },
    {
      'Normalize step request': {
        case_id: 'CASE-1',
        is_resume: true,
        action: 'resume',
        human_response: 'оставь лишние скважины',
        gate_id: 'unlisted_wells_policy',
      },
    },
  );
  assert.equal(unlistedKeep.state.hitl.answers.unlisted_wells_policy.unlisted_wells_policy, 'keep');
  assert.equal(unlistedKeep.state.hitl.answers.unlisted_wells_policy.raw, 'оставь лишние скважины');

  const unlistedKeepWord = await run(
    'Apply request extras',
    {
      status: 'waiting_user',
      state: {
        goal: 'g',
        hitl: {
          pending: true,
          questions: [{ question_id: 'unlisted_wells_policy', question: 'Скважины не из Excel?' }],
          answers: {},
        },
      },
    },
    {
      'Normalize step request': {
        case_id: 'CASE-1',
        is_resume: true,
        action: 'resume',
        human_response: 'keep extra wells',
        gate_id: 'unlisted_wells_policy',
      },
    },
  );
  assert.equal(unlistedKeepWord.state.hitl.answers.unlisted_wells_policy.unlisted_wells_policy, 'keep');

  const unlistedUpkeep = await run(
    'Apply request extras',
    {
      status: 'waiting_user',
      state: {
        goal: 'g',
        hitl: {
          pending: true,
          questions: [{ question_id: 'unlisted_wells_policy', question: 'Скважины не из Excel?' }],
          answers: {},
        },
      },
    },
    {
      'Normalize step request': {
        case_id: 'CASE-1',
        is_resume: true,
        action: 'resume',
        human_response: 'upkeep of extra wells',
        gate_id: 'unlisted_wells_policy',
      },
    },
  );
  assert.equal(unlistedUpkeep.state.hitl.answers.unlisted_wells_policy.unlisted_wells_policy, undefined);
  assert.equal(unlistedUpkeep.state.hitl.answers.unlisted_wells_policy, 'upkeep of extra wells');

  const mismatch = await run(
    'Apply request extras',
    {
      status: 'waiting_user',
      state: {
        goal: 'g',
        version: 4,
        hitl: { pending: true, questions: [{ question_id: 'Q-1', question: '?' }], answers: {} },
      },
    },
    {
      'Normalize step request': {
        case_id: 'CASE-1',
        is_resume: true,
        action: 'resume',
        human_response: 'x',
        gate_id: 'Q-1',
        expected_version: 2,
      },
    },
  );
  assert.equal(mismatch.action_type, 'version_mismatch');
  assert.equal(mismatch.did_resume, false);
  assert.equal(mismatch.should_continue, false);
  assert.match(String(mismatch.message), /expected 2/);

  const parsed = await run(
    'Parse decision',
    {
      output: {
        status_message: 'Сбор данных по Excel',
        action: {
          type: 'call_agent',
          agent_id: 'excel_extractor',
          task_id: 'TASK-1',
          handoff_message: 'Достань даты',
          task: { excel_artifact: 'a.xlsx' },
        },
      },
    },
    {
      'Prepare decision context': {
        case_id: 'CASE-1',
        state: { goal: 'x', artifacts: { excel: 'a.xlsx' }, data: {}, plan: [], step_count: 0 },
        activity_base_url: 'http://activity:8200',
      },
    },
  );
  assert.equal(parsed.action_type, 'call_agent');
  assert.equal(parsed.should_call_agent, true);
  assert.equal(parsed.agent_task.agent_id, 'excel_extractor');
  assert.equal(parsed.agent_task.inputs.activity_base_url, 'http://activity:8200');
  assert.equal(parsed.agent_task.inputs.artifacts, undefined);
  assert.deepEqual(parsed.agent_task.inputs.artifact_ids, ['excel']);
  assert.equal(parsed.agent_task.inputs.context, undefined);
  assert.equal(parsed.state.current_task.task_id, 'TASK-1');
  assert.equal(parsed.state.current_task.context, undefined);
  assert.equal(parsed.state.version, 1);
  assert.equal(parsed.next_status, 'running');
  assert.deepEqual(parsed.events.map((e) => e.kind), ['orchestrator.decision', 'agent.handoff']);
  assert.equal(parsed.events.some((e) => e.kind === 'orchestrator.status'), false);

  const afterHitl = await run(
    'Parse decision',
    {
      output: {
        status_message: 'Передаю schedule_builder.',
        action: {
          type: 'call_agent',
          agent_id: 'schedule_builder',
          task_id: 'TASK-2',
          handoff_message: 'Примени даты и политику unlisted.',
        },
      },
    },
    {
      'Prepare decision context': {
        case_id: 'CASE-1',
        state: {
          goal: 'сдвинь даты',
          artifacts: { excel: 'a.xlsx', schedule_source: 'b.inc' },
          data: { excel: { facts: [{ well: '1601', date: '2020-01-01' }] } },
          plan: [],
          step_count: 1,
          hitl: {
            pending: false,
            questions: [{ question_id: 'unlisted_wells_policy', question: '?' }],
            answers: { unlisted_wells_policy: 'unlisted_wells_policy=remove' },
          },
        },
        activity_base_url: 'http://activity:8200',
      },
    },
  );
  assert.equal(afterHitl.agent_task.inputs.unlisted_wells_policy, 'remove');
  assert.equal(afterHitl.agent_task.context.hitl.answers.unlisted_wells_policy, 'unlisted_wells_policy=remove');
  assert.deepEqual(afterHitl.agent_task.context.hitl.answer_ids, ['unlisted_wells_policy']);

  const upkeepCompact = await run(
    'Prepare decision context',
    { case_id: 'CASE-1' },
    {
      'Load case': {
        state: {
          goal: 'сдвинь даты',
          artifacts: { excel: 'a.xlsx', schedule_source: 'b.inc' },
          data: {},
          hitl: { pending: false, answers: { unlisted_wells_policy: 'upkeep of extra wells' } },
        },
        status: 'running',
      },
      'Load agent registry': { agent_id: 'schedule_builder', title: 'Schedule' },
      'Runtime endpoints': { activity_base_url: 'http://mas-activity:8200' },
    },
  );
  assert.equal(upkeepCompact.compact.unlisted_wells_policy, null);

  const keepWordCompact = await run(
    'Prepare decision context',
    { case_id: 'CASE-1' },
    {
      'Load case': {
        state: {
          goal: 'сдвинь даты',
          artifacts: { excel: 'a.xlsx', schedule_source: 'b.inc' },
          data: {},
          hitl: { pending: false, answers: { unlisted_wells_policy: 'keep extra wells' } },
        },
        status: 'running',
      },
      'Load agent registry': { agent_id: 'schedule_builder', title: 'Schedule' },
      'Runtime endpoints': { activity_base_url: 'http://mas-activity:8200' },
    },
  );
  assert.equal(keepWordCompact.compact.unlisted_wells_policy, 'keep');

  const finished = await run(
    'Parse decision',
    {
      output: {
        status_message: 'schedule_out уже есть — завершаю.',
        action: { type: 'finish', result: { ok: true } },
      },
    },
    {
      'Prepare decision context': {
        case_id: 'CASE-1',
        state: { goal: 'x', artifacts: { schedule_out: 'INC' }, data: {}, plan: [], step_count: 2 },
        activity_base_url: 'http://activity:8200',
      },
    },
  );
  assert.equal(finished.action_type, 'finish');
  assert.deepEqual(finished.events.map((e) => e.kind), ['case.finished']);

  const merged = await run(
    'Merge agent result',
    {
      status: 'completed',
      message: 'ok',
      data: { facts: [{ well: 'A', date: '2020-01-01', values: { pad: 'secret' } }] },
      artifacts: {},
    },
    {
      'Parse decision': parsed,
      'Prepare agent call': { ...parsed, agent_id: 'excel_extractor', activity_base_url: 'http://activity:8200' },
    },
  );
  assert.equal(merged.next_status, 'running');
  assert.equal(merged.should_continue, true);
  assert.deepEqual(merged.state.data.excel.facts, [{ well: 'A', date: '2020-01-01' }]);
  assert.ok(!JSON.stringify(merged.state.data).includes('secret'));
  assert.ok(!('facts' in (merged.state.data || {})));
  assert.equal(merged.state.version, 2);
  assert.equal(merged.continue_url, 'http://127.0.0.1:5678/webhook/mas-orchestrator-step');
  assert.deepEqual(merged.events.map((e) => e.kind), ['agent.result']);
  assert.equal(merged.persist_events[0][2], 'agent.result');

  const failedMerge = await run(
    'Merge agent result',
    { status: 'failed', message: 'Агент не вызвал apply/build — SCHEDULE не собран', data: {}, artifacts: {} },
    {
      'Parse decision': parsed,
      'Prepare agent call': { ...parsed, agent_id: 'schedule_builder', activity_base_url: 'http://activity:8200' },
    },
  );
  assert.equal(failedMerge.next_status, 'running');
  assert.equal(failedMerge.should_continue, true);
  assert.deepEqual(failedMerge.events.map((e) => e.kind), ['agent.failed']);
  assert.equal(failedMerge.state.last_error.agent_id, 'schedule_builder');
  assert.equal(failedMerge.state.last_error.count, 1);
  assert.equal(failedMerge.state.error_count, 1);
  assert.equal(failedMerge.state.data.schedule.summary.includes('SCHEDULE не собран'), true);
  assert.ok(!('facts' in (failedMerge.state.data || {})));

  const thirdFail = await run(
    'Merge agent result',
    { status: 'failed', message: 'сервис недоступен', data: {}, artifacts: {} },
    {
      'Parse decision': parsed,
      'Prepare agent call': {
        ...parsed,
        agent_id: 'schedule_builder',
        activity_base_url: 'http://activity:8200',
        state: {
          ...(parsed.state || {}),
          last_error: { message: 'down', agent_id: 'schedule_builder', count: 2 },
          error_count: 2,
        },
      },
    },
  );
  assert.equal(thirdFail.next_status, 'failed');
  assert.equal(thirdFail.should_continue, false);
  assert.equal(thirdFail.state.error_count, 3);
  assert.deepEqual(thirdFail.events.map((e) => e.kind), ['agent.failed', 'case.failed']);

  const execFail = await run(
    'Merge agent result',
    { error: { message: 'Subworkflow failed' } },
    {
      'Parse decision': parsed,
      'Prepare agent call': { ...parsed, agent_id: 'excel_extractor', activity_base_url: 'http://activity:8200' },
    },
  );
  assert.equal(execFail.events[0].kind, 'agent.failed');
  assert.equal(execFail.state.last_error.message, 'Subworkflow failed');
  assert.equal(execFail.should_continue, true);

  const expanded = await run(
    'Expand agent events',
    merged,
    {
      'Parse decision': parsed,
      'Merge agent result': merged,
    },
  );
  assert.equal(expanded.p3, 'agent.result');

  // ---------------------------------------------------------------------------------------------
  // Completion contract (regression for CASE-6a9da4e2: schedule_builder completed 23× in a loop).
  // Journal (progress ledger) → LLM sees what was done; deterministic guards stop silent repeats.
  // ---------------------------------------------------------------------------------------------
  {
    const registry = [
      { agent_id: 'schedule_builder', title: 'Schedule Builder' },
      { agent_id: 'excel_extractor', title: 'Excel Extractor' },
    ];
    const req = { case_id: 'CASE-LOOP', action: 'step', is_status: false, is_resume: false };
    const baseState = {
      case_id: 'CASE-LOOP',
      goal: 'Скважины 1601 и 1602 в группу DKS, GRAT 200 тыс.',
      status: 'running',
      step_count: 1,
      version: 2,
      artifacts: { schedule: { source: { artifact_id: 'schedule_source', filename: 'baseline.inc' } } },
      data: {},
      hitl: { pending: false, questions: [], answers: {} },
      plan: [],
    };
    const prepare = (state, extra = {}) => run('Prepare decision context', {}, {
      'Apply request extras': { ...req, state, next_status: 'running' },
      'Load case': { case_id: 'CASE-LOOP', state: JSON.stringify(state), status: 'running' },
      'Load agent registry': registry,
      'Runtime endpoints': { activity_base_url: 'http://mas-activity:8200', max_steps: '12', ...extra },
    });
    const decide = (prepared, output) => run('Parse decision', { output }, { 'Prepare decision context': prepared });
    const merge = (parsed, body) => run('Merge agent result', { body }, { 'Prepare agent call': parsed, 'Parse decision': parsed });
    const delegate = {
      progress: { goal_satisfied: false, evidence: 'ничего не сделано', missing: 'перепривязка' },
      status_message: 'Передаю задачу билдеру SCHEDULE.',
      action: { type: 'call_agent', agent_id: 'schedule_builder', handoff_message: 'Перепривяжи 1601 и 1602 в DKS.' },
    };

    // Step 1: fresh case → delegation is legitimate.
    let prepared = await prepare(baseState);
    assert.match(prepared.planner_input, /Журнал задачи/);
    assert.match(prepared.planner_input, /пока ничего не сделано/);
    assert.deepEqual(prepared.compact.journal, { history: [], stall_count: 0, completed_agents: [] });
    let parsed = await decide(prepared, delegate);
    assert.equal(parsed.action_type, 'call_agent');
    assert.equal(parsed.should_call_agent, true);
    assert.equal(parsed.decision.progress.goal_satisfied, false);

    // Agent completes: the journal records what it did and which artifacts appeared.
    let merged = await merge(parsed, {
      status: 'completed',
      agent_id: 'schedule_builder',
      message: 'Перепривязал 2 скважины в DKS (GCONPROD GRAT 200000)',
      data: { group_rebind: {}, edits: [] },
      artifacts: { schedule_out: 'DATES\n 1 JAN 2026 /\n/\n' + 'x'.repeat(300), diff: 'd' },
    });
    assert.equal(merged.next_status, 'running');
    assert.equal(merged.should_continue, true);
    const journal = merged.state.ledger.history;
    assert.equal(journal.length, 1);
    assert.equal(journal[0].kind, 'agent');
    assert.equal(journal[0].agent_id, 'schedule_builder');
    assert.equal(journal[0].status, 'completed');
    assert.match(journal[0].summary, /Перепривязал 2 скважины/);
    assert.ok(journal[0].artifacts_added.includes('schedule_out'));

    // Step 2: the LLM now sees the journal in planner_input …
    prepared = await prepare(merged.state);
    assert.match(prepared.planner_input, /шаг 2: schedule_builder → completed: Перепривязал 2 скважины/);
    assert.deepEqual(prepared.compact.journal.completed_agents, ['schedule_builder']);

    // … (a) if it still re-delegates the same task with no new inputs → result review with the human, not a silent rerun.
    let repeat = await decide(prepared, delegate);
    assert.equal(repeat.action_type, 'ask_user');
    assert.equal(repeat.should_call_agent, false);
    assert.equal(repeat.next_status, 'waiting_user');
    assert.equal(repeat.decision.action.type, 'call_agent', 'raw LLM decision is kept for audit');
    const gate = repeat.state.hitl.questions[0];
    assert.match(gate.question_id, /^result_review_/);
    assert.equal(gate.kind, 'result_approval');
    assert.match(gate.question, /Schedule Builder уже выполнил эту задачу/);
    assert.match(gate.question, /Перепривязал 2 скважины/);
    assert.deepEqual(gate.options.map((o) => o.value), ['accept', 'rework']);
    assert.ok(!/[a-z_]+=[a-z]+|expected_format/.test(gate.question), 'human prose, no machine syntax');
    assert.equal(repeat.events.find((e) => e.kind === 'orchestrator.decision').payload.guard, 'repeat_review');

    // … (b) if it re-delegates with an explicit rework_reason → allowed once, reason travels to the agent.
    let rework = await decide(prepared, {
      ...delegate,
      action: { ...delegate.action, rework_reason: 'GCONPROD стоит до даты ввода 1602' },
    });
    assert.equal(rework.action_type, 'call_agent');
    assert.equal(rework.agent_task.inputs.rework_reason, 'GCONPROD стоит до даты ввода 1602');
    assert.equal(rework.state.ledger.stall_count, 1);
    const reworked = await merge(rework, { status: 'completed', agent_id: 'schedule_builder', message: 'Сдвинул GCONPROD на дату ввода', data: {}, artifacts: {} });
    assert.equal(reworked.state.ledger.history.at(-1).rework_reason, 'GCONPROD стоит до даты ввода 1602');
    // Second rework without new human input is not allowed → review.
    const prepared3 = await prepare(reworked.state);
    const second = await decide(prepared3, { ...delegate, action: { ...delegate.action, rework_reason: 'ещё раз' } });
    assert.equal(second.action_type, 'ask_user');
    assert.equal(second.events.find((e) => e.kind === 'orchestrator.decision').payload.guard, 'stall_review');

    // … (c) if the LLM says goal_satisfied but habitually delegates → finish with an honest summary.
    const contradiction = await decide(prepared, {
      ...delegate,
      progress: { goal_satisfied: true, evidence: 'Schedule Builder перепривязал скважины, schedule_out получен' },
    });
    assert.equal(contradiction.action_type, 'finish');
    assert.equal(contradiction.next_status, 'done');
    assert.match(contradiction.state.data.result.summary_for_human, /перепривязал скважины/);

    // … (d) the proper path: finish with summary_for_human; journal summary is attached for the feed.
    const finish = await decide(prepared, {
      progress: { goal_satisfied: true, evidence: 'schedule_out получен' },
      status_message: 'Готово.',
      action: { type: 'finish', result: { summary_for_human: 'Скважины 1601 и 1602 переведены в группу DKS с GRAT 200000; новый SCHEDULE готов.' } },
    });
    assert.equal(finish.next_status, 'done');
    const finished = finish.events.find((e) => e.kind === 'case.finished');
    assert.equal(finished.status_message, 'Скважины 1601 и 1602 переведены в группу DKS с GRAT 200000; новый SCHEDULE готов.');
    assert.match(finished.payload.done_by_agents, /Schedule Builder: Перепривязал 2 скважины/);
    // finish without summary_for_human → summary composed from the journal, never "вызвал агента".
    const finishBare = await decide(prepared, { progress: { goal_satisfied: true, evidence: 'ok' }, status_message: 'Готово', action: { type: 'finish' } });
    assert.match(finishBare.events.find((e) => e.kind === 'case.finished').status_message, /Schedule Builder: Перепривязал 2 скважины/);

    // Human accepts the review → next step finishes regardless of the LLM's habit.
    const resumeReq = { ...req, is_resume: true, gate_id: gate.question_id, human_response: JSON.stringify({ choice: 'accept', text: 'Принять результат', label: 'Принять результат' }) };
    const resumed = await run('Apply request extras', { state: JSON.stringify(repeat.state), status: 'waiting_user' }, { 'Normalize step request': resumeReq });
    assert.equal(resumed.did_resume, true);
    const human = resumed.state.ledger.history.at(-1);
    assert.equal(human.kind, 'human');
    assert.equal(human.review_accept, true);
    assert.equal(human.answer, 'Принять результат');
    assert.equal(resumed.state.ledger.stall_count, 0);
    const preparedAccepted = await prepare(resumed.state);
    assert.match(preparedAccepted.planner_input, /человек ответил \(принял результат\)/);
    const afterAccept = await decide(preparedAccepted, delegate);
    assert.equal(afterAccept.action_type, 'finish');
    assert.equal(afterAccept.events.find((e) => e.kind === 'case.finished').payload.guard, 'human_accepted');

    // Human asks for rework → new input: delegation is allowed again (stall reset).
    const reworkReq = { ...req, is_resume: true, gate_id: gate.question_id, human_response: JSON.stringify({ choice: 'rework', text: 'Групповой контроль должен начинаться с даты ввода 1602, а не 1601' }) };
    const resumedRework = await run('Apply request extras', { state: JSON.stringify(repeat.state), status: 'waiting_user' }, { 'Normalize step request': reworkReq });
    assert.equal(resumedRework.state.ledger.history.at(-1).review_accept, undefined);
    const afterRework = await decide(await prepare(resumedRework.state), delegate);
    assert.equal(afterRework.action_type, 'call_agent');

    // Agent asked (needs_input) → Activity wrote the answer into state.hitl.answers and called action=step
    // (no orchestrator resume). The journal must still pick the answer up, and finishing is not allowed
    // until the asking agent has run with it (regression for combat_case3: LLM "finished" after the
    // unlisted-wells answer and hallucinated that schedule_builder had produced the schedule).
    const asked = await merge(parsed, {
      status: 'needs_input',
      agent_id: 'schedule_builder',
      message: 'В Excel нет скважин: 201, 208. Оставить или убрать?',
      requests: [{ question_id: 'unlisted_wells_policy', question: 'В Excel нет скважин: 201, 208. Оставить или убрать?', options: [{ value: 'keep', label: 'Оставить' }, { value: 'remove', label: 'Убрать' }] }],
      data: { unlisted_wells: ['201', '208'] },
      artifacts: {},
    });
    assert.equal(asked.next_status, 'waiting_user');
    assert.equal(asked.state.ledger.history.at(-1).status, 'needs_input');
    const activityWritten = {
      ...asked.state,
      hitl: { ...asked.state.hitl, pending: false, answers: { unlisted_wells_policy: { choice: 'remove', text: 'Убрать из прогноза', label: 'Убрать из прогноза' } } },
    };
    const preparedAnswered = await prepare(activityWritten);
    const humanEntry = preparedAnswered.compact.journal.history.at(-1);
    assert.equal(humanEntry.kind, 'human');
    assert.equal(humanEntry.answer, 'Убрать из прогноза');
    assert.match(preparedAnswered.planner_input, /человек ответил: «Убрать из прогноза»/);
    const prematureFinish = await decide(preparedAnswered, {
      progress: { goal_satisfied: true, evidence: 'schedule_builder отработал' },
      status_message: 'Готово',
      action: { type: 'finish', result: { summary_for_human: 'Всё сделано' } },
    });
    assert.equal(prematureFinish.action_type, 'call_agent', 'the answer must reach the agent that asked');
    assert.equal(prematureFinish.agent_task.agent_id, 'schedule_builder');
    assert.match(prematureFinish.agent_task.handoff_message, /Инженер ответил на ваш вопрос/);
    assert.match(prematureFinish.agent_task.handoff_message, /Убрать из прогноза/);
    assert.equal(prematureFinish.agent_task.inputs.unlisted_wells_policy, 'remove');
    assert.equal(prematureFinish.events.find((e) => e.kind === 'orchestrator.decision').payload.guard, 'answer_not_applied');
    // The same answer is not journaled twice on later steps.
    const preparedTwice = await prepare(prematureFinish.state);
    assert.equal(preparedTwice.compact.journal.history.filter((e) => e.kind === 'human').length, 1);
    // Once the asking agent completes, finish is allowed.
    const afterAnswer = await merge(prematureFinish, { status: 'completed', agent_id: 'schedule_builder', message: 'Сдвинул 4 скважины, убрал 2', data: {}, artifacts: { schedule_out: 'x'.repeat(400) } });
    const finalFinish = await decide(await prepare(afterAnswer.state), { progress: { goal_satisfied: true, evidence: 'ok' }, status_message: 'Готово', action: { type: 'finish' } });
    assert.equal(finalFinish.action_type, 'finish');
    assert.match(finalFinish.events.find((e) => e.kind === 'case.finished').status_message, /Сдвинул 4 скважины, убрал 2/);

    // Step budget from Runtime Config: at the limit with a result → review, without result → failed.
    const nearLimit = { ...merged.state, step_count: 12 };
    const atLimitParsed = await decide(await prepare(nearLimit), { ...delegate, action: { ...delegate.action, agent_id: 'excel_extractor' } });
    const atLimit = await merge(atLimitParsed, { status: 'completed', agent_id: 'excel_extractor', message: 'Извлёк 0 строк', data: {}, artifacts: {} });
    assert.equal(atLimit.next_status, 'waiting_user');
    assert.equal(atLimit.should_continue, false);
    assert.match(atLimit.state.hitl.questions[0].question, /исчерпал лимит шагов/);
    const noResultState = { ...baseState, step_count: 12 };
    const noResultParsed = await decide(await prepare(noResultState), delegate);
    const noResult = await merge(noResultParsed, { status: 'failed', agent_id: 'schedule_builder', message: 'boom', data: {}, artifacts: {} });
    assert.equal(noResult.next_status, 'failed', 'budget exhausted and nothing completed → honest failure');
    assert.match(noResult.events.find((e) => e.kind === 'case.failed').status_message, /результата нет/);
    const belowLimit = await merge({ ...noResultParsed, state: { ...noResultParsed.state, step_count: 5 } }, { status: 'failed', agent_id: 'schedule_builder', message: 'boom', data: {}, artifacts: {} });
    assert.equal(belowLimit.next_status, 'running', 'below the budget a first failure is retried by the error budget');
    const noResultDone = await merge(noResultParsed, { status: 'completed', agent_id: 'schedule_builder', message: '', data: {}, artifacts: {} });
    assert.equal(noResultDone.next_status, 'waiting_user', 'a completed result (even terse) is reviewed, not thrown away');
  }

  assert.equal(err.name, 'Error — MAS Node Traces');
  assert.equal(err.settings.errorWorkflow || '', '');
  assert.ok(err.nodes.some((n) => n.type === 'n8n-nodes-base.errorTrigger'));
  assert.ok(err.nodes.some((n) => n.name === 'Insert error_traces'));
  assert.ok(JSON.stringify(err).includes('system.node_error'));
  console.log('mas-orchestrator-smoke: ok');
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
