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
    'Call agent (n8n)',
    'Call agent (HTTP)',
    'Agent not bound',
    'Merge agent result',
    'Continue loop?',
    'Probe ping?',
    'Needs create?',
    'Apply request extras',
    'Status only?',
    'Resume persist?',
    'Needs interpret?',
    'Interpret free-text answer',
    'Answer interpretation Structured Output',
    'Apply interpreted answer',
    'Resume to decision?',
    'Not a resume?',
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
  assert.ok(modelEdges.some((e) => e.node === 'Verify completion' && e.type === 'ai_languageModel'));
  assert.ok(modelEdges.some((e) => e.node === 'Interpret free-text answer' && e.type === 'ai_languageModel'));
  assert.ok(modelEdges.some((e) => e.node === 'Answer interpretation Structured Output' && e.type === 'ai_languageModel'));
  const parserEdges = wf.connections['Decision Structured Output'].ai_outputParser[0];
  assert.equal(parserEdges[0].node, 'Decision LLM');
  const interpretParser = wf.connections['Answer interpretation Structured Output'].ai_outputParser[0];
  assert.equal(interpretParser[0].node, 'Interpret free-text answer');
  const interpretLlm = wf.nodes.find((n) => n.name === 'Interpret free-text answer');
  assert.equal(interpretLlm.type, '@n8n/n8n-nodes-langchain.chainLlm');
  assert.equal(interpretLlm.typeVersion, 1.9);
  assert.equal(interpretLlm.parameters.hasOutputParser, true);

  const runtime = wf.nodes.find((n) => n.name === 'Runtime endpoints');
  assert.ok(runtime);
  assert.equal(runtime.type, 'n8n-nodes-base.executeWorkflow');
  assert.equal(runtime.typeVersion, 1.3);
  assert.equal(runtime.parameters.workflowId.value, 'REPLACE_MAS_RUNTIME_CONFIG_IN_UI');
  assert.equal(runtime.parameters.workflowId.cachedResultName, 'MAS — Runtime Config');
  assert.equal(runtime.parameters.options.waitForSubWorkflow, true);
  assert.equal(text.includes('excel_tools_api_key'), false);
  assert.equal(text.includes('mas-host-bridge'), false);
  {
    // Phase 2 — universal agent call: no per-agent executeWorkflow nodes, no REPLACE_*_AGENT_IN_UI
    // placeholders. The target workflow id is an expression resolved from agent_registry.invoke
    // (or Runtime Config agent_workflow_ids) in "Prepare agent call".
    const callWf = wf.nodes.find((n) => n.name === 'Call agent (n8n)');
    assert.equal(callWf.type, 'n8n-nodes-base.executeWorkflow');
    assert.equal(callWf.typeVersion, 1.3);
    assert.equal(callWf.parameters.workflowId.mode, 'id');
    assert.equal(callWf.parameters.workflowId.value, '={{ $json.invoke_workflow_id }}');
    assert.equal('cachedResultName' in callWf.parameters.workflowId, false, 'nothing to bind in the UI');
    assert.equal(callWf.parameters.options.waitForSubWorkflow, true);
    assert.equal(callWf.onError, 'continueRegularOutput');
    assert.deepEqual(Object.keys(callWf.parameters.workflowInputs.value), ['agent_task']);
    assert.equal(callWf.parameters.workflowInputs.value.agent_task, '={{ $json.agent_task }}');
    const callHttp = wf.nodes.find((n) => n.name === 'Call agent (HTTP)');
    assert.equal(callHttp.type, 'n8n-nodes-base.httpRequest');
    assert.equal(callHttp.parameters.url, '={{ $json.invoke_url }}');
    assert.equal(callHttp.parameters.jsonBody, '={{ $json.agent_task }}');
    assert.equal(callHttp.onError, 'continueRegularOutput');
    const unbound = wf.nodes.find((n) => n.name === 'Agent not bound');
    assert.equal(unbound.type, 'n8n-nodes-base.code');
    assert.equal(text.includes('REPLACE_EXCEL_EXTRACTION_AGENT_IN_UI'), false);
    assert.equal(text.includes('REPLACE_SCHEDULE_BUILDER_AGENT_IN_UI'), false);
    assert.equal(text.includes('calculation_agent_url'), false);
    const router = wf.nodes.find((n) => n.name === 'Action router');
    assert.equal(router.parameters.numberOutputs, 4);
    assert.match(router.parameters.output, /workflow:0,http:1,unbound:2,none:3/);
    const outs = wf.connections['Action router'].main.map((edges) => edges[0].node);
    assert.deepEqual(outs, ['Call agent (n8n)', 'Call agent (HTTP)', 'Agent not bound', 'No agent this step']);
    for (const src of ['Call agent (n8n)', 'Call agent (HTTP)', 'Agent not bound']) {
      assert.equal(wf.connections[src].main[0][0].node, 'Merge agent result', src);
    }
    // Orchestrator knows no agent by name anywhere in its code or prompts (Phase 2 invariant).
    const orchestratorText = wf.nodes
      .filter((n) => n.type !== 'n8n-nodes-base.stickyNote')
      .map((n) => JSON.stringify(n.parameters))
      .join('\n');
    for (const name of ['excel_extractor', 'schedule_builder', 'calculation_agent', 'Excel Extractor', 'Schedule Builder', 'slimExcel']) {
      assert.equal(orchestratorText.includes(name), false, `orchestrator must not know agent "${name}"`);
    }
    const registrySql = wf.nodes.find((n) => n.name === 'Load agent registry').parameters.query;
    assert.match(registrySql, /FROM agent_registry/);
    assert.match(registrySql, /WHERE enabled/);
    for (const col of ['invoke', 'input_schema', 'output_schema', 'hitl_policy', 'enabled', 'version']) {
      assert.ok(registrySql.includes(col), `registry SELECT must carry ${col}`);
    }
  }
  const helperNames = [
    'Apply request extras',
    'Prepare decision context',
    'Parse decision',
    'Merge agent result',
    'Apply interpreted answer',
  ];
  const helperChunks = helperNames.map((name) => {
    const js = source(name);
    const begin = js.indexOf('/* mas_state_utils begin */');
    const end = js.indexOf('/* mas_state_utils end */');
    assert.ok(begin >= 0 && end > begin, `state utils markers missing in ${name}`);
    assert.ok(js.includes('function readUnlistedWellsPolicy'), name);
    assert.ok(js.includes('function normalizeHitlAnswer'), name);
    assert.ok(js.includes('function applyInterpretedDecision'), name);
    assert.ok(js.includes('function resolveInvoke'), name);
    assert.ok(js.includes('function sanitizeAgents'), name);
    assert.ok(js.includes('function buildRetrievalQuery'), name);
    // Phase 2: regex-derived routing tags are gone (plan §2 debt).
    for (const gone of ['inferTaskPatterns', 'inferRetrievalQuery', 'inferRoutingTopics', 'inferRoutingKeywordFamilies', 'parseKeepRemove', 'slimExcel']) {
      assert.equal(js.includes(gone), false, `${name}: ${gone}`);
    }
    return js.slice(begin, end);
  });
  assert.equal(new Set(helperChunks).size, 1, 'state helpers must be identical across Code nodes');
  assert.equal(helperChunks[0].includes("s.includes('keep')"), false);
  assert.equal(helperChunks[0].includes("s.includes('remove')"), false);
  assert.equal(helperChunks[0].includes('parseKeepRemove'), false);
  {
    // Activity option button answer: {choice, text, label} — no regex on the human label.
    const helpers = new Function(`${helperChunks[0]}; return { normalizeHitlAnswer, readUnlistedWellsPolicy, applyInterpretedDecision, buildClarifyQuestion, looksMachineText };`)();
    const clicked = { choice: 'remove', text: 'Убрать из прогноза', label: 'Убрать из прогноза' };
    assert.equal(helpers.normalizeHitlAnswer('unlisted_wells_policy', clicked, '').unlisted_wells_policy, 'remove');
    assert.equal(helpers.readUnlistedWellsPolicy({ unlisted_wells_policy: clicked }), 'remove');
    assert.equal(helpers.readUnlistedWellsPolicy({ 'Q-parent-group': clicked }), null, 'choice is gated by the question id');
    assert.equal(helpers.readUnlistedWellsPolicy({ unlisted_wells_policy: { choice: 'keep', text: 'Оставить как в baseline' } }), 'keep');
    const q = {
      question_id: 'unlisted_wells_policy',
      question: 'В Excel нет скважин. Оставить или убрать?',
      options: [{ value: 'keep', label: 'Оставить' }, { value: 'remove', label: 'Убрать' }],
    };
    const accepted = helpers.applyInterpretedDecision('unlisted_wells_policy', q, { text: 'убери лишние' }, { decision: 'remove', confidence: 0.9, paraphrase: 'Убрать лишние скважины' });
    assert.equal(accepted.accepted, true);
    assert.equal(accepted.answer.choice, 'remove');
    assert.equal(accepted.answer.unlisted_wells_policy, 'remove');
    const rejected = helpers.applyInterpretedDecision('unlisted_wells_policy', q, { text: 'наверное' }, { decision: 'remove', confidence: 0.4, paraphrase: 'неясно' });
    assert.equal(rejected.accepted, false);
    const clarify = helpers.buildClarifyQuestion(q);
    assert.match(clarify.question, /Не удалось однозначно понять ответ/);
    assert.equal(helpers.looksMachineText(clarify.question), false);
  }
  {
    // Two workbooks in one case: the first keeps the `excel` slot, the second survives as an attachment
    // (role excel) — the Excel Extractor reads both; nest(flatten(x)) is lossless.
    const helpers = new Function(`${helperChunks[0]}; return { flattenArtifacts, nestArtifacts };`)();
    const flat = {
      excel: { artifact_id: 'excel', filename: 'dates.xlsx', role: 'excel', bytes: 1 },
      excel_1: { artifact_id: 'excel_1', filename: 'params.xlsx', role: 'excel', bytes: 2 },
      schedule_source: { artifact_id: 'schedule_source', filename: 'baseline.inc', role: 'schedule_source', bytes: 3 },
    };
    const nested = helpers.nestArtifacts(flat);
    assert.equal(nested.excel.artifact_id, 'excel');
    assert.deepEqual(nested.attachments.map((a) => a.artifact_id), ['excel_1']);
    assert.deepEqual(Object.keys(helpers.flattenArtifacts(nested)).sort(), ['excel', 'excel_1', 'schedule_source']);
    const reversed = helpers.nestArtifacts({ excel_1: flat.excel_1, excel: flat.excel });
    assert.equal(reversed.excel.artifact_id, 'excel', 'order of arrival does not decide who owns the slot');
    assert.deepEqual(reversed.attachments.map((a) => a.artifact_id), ['excel_1']);
  }
  {
    // Phase 1.5 — artifacts are cards {kind, producer}: engineer uploads are inputs, whatever an agent
    // returns under `artifacts` is that agent's deliverable. Cards round-trip through nest/flatten, the
    // `diff` text is a card too (not a loose string), and `deliverables()` never names a slot.
    const helpers = new Function(`${helperChunks[0]}; return { flattenArtifacts, nestArtifacts, mergeIncomingArtifacts, artifactCards, deliverables };`)();
    const uploads = {
      excel: { artifact_id: 'excel', filename: 'dates.xlsx', role: 'excel', bytes: 1, kind: 'input', producer: 'user' },
      schedule_source: { artifact_id: 'schedule_source', filename: 'baseline.inc', role: 'schedule_source', bytes: 3, kind: 'input', producer: 'user' },
    };
    const merged = helpers.mergeIncomingArtifacts(uploads, { schedule_out: 'DATES\n 1 JAN 2026 /\n/\n', diff: '--- a\n+++ b\n' }, 'schedule_builder');
    assert.equal(merged.schedule.out.producer, 'schedule_builder');
    assert.equal(merged.schedule.out.kind, 'deliverable');
    assert.equal(merged.schedule.diff.producer, 'schedule_builder');
    assert.equal(merged.schedule.diff.role, 'diff');
    const roundTrip = helpers.nestArtifacts(helpers.flattenArtifacts(merged));
    assert.deepEqual(roundTrip, merged, 'cards survive nest(flatten(x))');
    const cards = helpers.artifactCards(merged);
    assert.deepEqual(cards.map((c) => [c.artifact_id, c.kind, c.producer]), [
      ['excel', 'input', 'user'],
      ['schedule_source', 'input', 'user'],
      ['schedule_out', 'deliverable', 'schedule_builder'],
      ['diff', 'deliverable', 'schedule_builder'],
    ]);
    // Second workbook must rank above INCLUDE stubs (combat 3 buries excel_1 after ~20 includes).
    const crowded = helpers.artifactCards({
      excel: { artifact_id: 'excel', filename: 'dates.xlsx', role: 'excel', kind: 'input', producer: 'user' },
      schedule_source: { artifact_id: 'schedule_source', filename: 'MONITORING_FDP.INC', role: 'schedule_source', kind: 'input', producer: 'user' },
      schedule_source_1: { artifact_id: 'schedule_source_1', filename: 'GRUPTREE.GRDECL', role: 'schedule_include', kind: 'input', producer: 'user' },
      excel_1: { artifact_id: 'excel_1', filename: 'params.xlsx', role: 'excel', kind: 'input', producer: 'user' },
    });
    assert.deepEqual(crowded.filter((c) => c.kind === 'input').map((c) => c.filename), [
      'dates.xlsx', 'params.xlsx', 'MONITORING_FDP.INC', 'GRUPTREE.GRDECL',
    ]);
    assert.ok(cards.every((c) => !('text' in c)), 'cards carry no inline text');
    assert.deepEqual(helpers.deliverables(merged).map((d) => d.filename), ['schedule_result.inc', 'schedule_changes.diff']);
    // Legacy state (agent artifacts merged before producer existed): still deliverables, producer unknown.
    const legacy = helpers.deliverables({ schedule_out: 'x'.repeat(30), schedule: { diff: 'd' } });
    assert.deepEqual(legacy.map((d) => [d.artifact_id, d.kind, d.producer]).sort(), [['diff', 'deliverable', ''], ['schedule_out', 'deliverable', '']]);
    // An empty diff (nothing changed) is not an artifact.
    assert.deepEqual(helpers.deliverables(helpers.mergeIncomingArtifacts({}, { schedule_out: 'x'.repeat(30), diff: '' }, 'schedule_builder')).map((d) => d.artifact_id), ['schedule_out']);
    // Phase 4.3: a card returned by POST /cases/{id}/artifacts (binary deliverable of an agent) merges as-is.
    const uploadedCard = { artifact_id: 'report', role: 'attachment', kind: 'deliverable', producer: 'demo_agent', filename: 'сводка.xlsx', mime_type: 'application/vnd.ms-excel', bytes: 1200, summary: 'Сводка по 3 скважинам' };
    const withUpload = helpers.mergeIncomingArtifacts(uploads, { report: uploadedCard }, 'demo_agent');
    const reportCard = helpers.deliverables(withUpload).find((d) => d.artifact_id === 'report');
    assert.deepEqual([reportCard.kind, reportCard.producer, reportCard.filename, reportCard.bytes], ['deliverable', 'demo_agent', 'сводка.xlsx', 1200]);
    assert.deepEqual(Object.keys(helpers.flattenArtifacts(withUpload)).sort(), ['excel', 'report', 'schedule_source']);
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
  assert.equal(text.includes('excel_extractor_url'), false, 'no HTTP /agent/run URL — specialist is executeWorkflow');
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
  {
    // CASE-6a9ee76b: ambiguous free text → Apply interpreted answer persisted the re-ask itself; the ack must
    // not read Apply request extras (needs_interpret, no events) and POST a second, empty hitl.request.
    const reask = {
      case_id: 'CASE-reask', did_resume: true, next_status: 'waiting_user', activity_base_url: 'http://mas-activity:8200',
      persist_events: [['CASE-reask', '', 'hitl.request', 'orchestrator', '', 'waiting_user', 'Не удалось однозначно понять ответ.', '', '{}']],
    };
    const extras = { case_id: 'CASE-reask', did_resume: true, needs_interpret: true, next_status: 'waiting_user', activity_base_url: 'http://mas-activity:8200' };
    const acked = await run('Prepare Activity ack', extras, {
      'Apply request extras': extras,
      'Apply interpreted answer': reask,
    });
    assert.equal(acked.activity_sync, false, 'interpret re-ask is already persisted — no ack event');
    const onlyExtras = await run('Prepare Activity ack', extras, { 'Apply request extras': extras });
    assert.equal(onlyExtras.activity_sync, true, 'without persisted events the ack still syncs (status path)');
  }

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
  // CASE-6a9ee318: Activity already wrote `case.created` (initial_event); the orchestrator's own copy
  // was silently dropped before 1.3 and duplicated the row once Expand events learned to read Code-node
  // outputs. `Prepare start case` emits no events; `Expand start events` never reads it.
  assert.deepEqual(createdCase.persist_events, []);
  assert.equal(source('Expand start events').includes("$('Prepare start case')"), false);
  assert.ok(source('Expand resume events').includes("$('Apply interpreted answer')"));
  assert.ok(source('Expand resume events').includes("$('Apply request extras')"));

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
          data: { facts: fatFacts },
          // Phase 2: agent results live in state.agents[<agent_id>] — no excel/calc/schedule buckets.
          agents: {
            excel_extractor: {
              status: 'completed', summary: 'Извлечено 40 скважин из Excel', task_id: 'TASK-OLD', step: 1,
              data: { facts: fatFacts, normalized_rows: [{ preview: fatFacts, row_count: 40 }], blob: 'z'.repeat(13000) },
            },
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
      'Load agent registry': [
        {
          agent_id: 'excel_extractor', title: 'Excel', when_to_use: 'читает Excel', input_required: ['excel'], output_provides: ['facts'],
          invoke: { kind: 'n8n_workflow', workflow_id: 'wf-excel' }, input_schema: {}, output_schema: { data: { facts: 'даты' } },
          hitl_policy: 'agent_asks', enabled: true, version: '2',
        },
        {
          agent_id: 'schedule_builder', title: 'Schedule', when_to_use: 'правит SCHEDULE', input_required: ['schedule_source'], output_provides: ['schedule_out'],
          invoke: { kind: 'n8n_workflow', workflow_id: 'wf-schedule' }, input_schema: { data: { facts: 'даты' } }, output_schema: {},
          hitl_policy: 'agent_asks', enabled: true, version: '2',
        },
      ],
      'Runtime endpoints': {
        activity_base_url: 'http://mas-activity:8200',
        excel_extractor_url: 'http://excel-tools:8000/agent/run',
      },
    },
  );
  assert.equal(prepared.compact.files.excel, 1);
  assert.equal(prepared.compact.files.schedule_source, 1);
  assert.equal(prepared.compact.files.grdecl, 1);
  assert.equal(prepared.compact.files.includes, 0);
  assert.deepEqual(prepared.compact.inputs.map((i) => i.filename), ['dates.xlsx', 'base.inc', 'GRUPTREE.GRDECL']);
  assert.equal(prepared.compact.inputs_total, 3);
  for (const legacy of ['has_excel', 'has_schedule_source', 'excel_filename', 'excel_facts', 'wells_in_excel', 'schedule_source_filename']) {
    assert.equal(legacy in prepared.compact, false, `compact must not carry domain field ${legacy}`);
  }
  // Agent slot is generic: status/summary/data_keys; the oversized `blob` key is dropped by the byte budget.
  assert.equal(prepared.compact.agents.excel_extractor.status, 'completed');
  assert.deepEqual(prepared.compact.agents.excel_extractor.data_keys, ['facts', 'normalized_rows']);
  assert.deepEqual(prepared.state.agents.excel_extractor.data.omitted_keys, ['blob']);
  assert.equal('facts' in prepared.state.agents.excel_extractor.data, true, 'small keys survive the budget');
  assert.equal(prepared.compact.current_task.agent_id, 'excel_extractor');
  assert.deepEqual(prepared.state.current_task.data_keys, ['excel_extractor'], 'current_task references agents that produced data');
  assert.equal(prepared.compact.current_task.context, undefined);
  assert.ok(!prepared.planner_input.includes('pad'));
  assert.ok(!prepared.planner_input.includes('zzzz'));
  assert.ok(!('facts' in (prepared.state.data || {})));
  assert.deepEqual(prepared.state.hitl.answers['Q-1'], { text: 'ok' });
  assert.equal(prepared.activity_base_url, 'http://mas-activity:8200');
  // Registry rows reach the LLM without `invoke` (deployment detail) but with the planning columns.
  assert.match(prepared.planner_input, /excel_extractor/);
  assert.match(prepared.planner_input, /"hitl_policy": "agent_asks"/);
  assert.match(prepared.planner_input, /"input_schema"/);
  assert.equal(prepared.planner_input.includes('wf-excel'), false, 'workflow ids are not planning facts');
  assert.equal(prepared.planner_input.includes('"enabled"'), false);
  // Journal line carries data keys the agent produced (what the next agent can consume).
  assert.equal(prepared.registry.length, 2);
  assert.equal(prepared.schedule_retrieval_request.filters.target_base, 'orchestrator_routing');
  assert.deepEqual(prepared.schedule_retrieval_request.filters.knowledge_types, ['routing_card']);
  assert.equal(prepared.schedule_retrieval_request.filters.access_scope, 'petroleum-engineering');
  // Phase 2: no regex-derived tags in the retrieval request — selectors + plain-text query only.
  for (const gone of ['keyword_families', 'topics', 'task_patterns']) {
    assert.equal(gone in prepared.schedule_retrieval_request.filters, false, gone);
  }
  assert.match(prepared.schedule_retrieval_request.query, /даты ввода/);
  assert.match(prepared.schedule_retrieval_request.query, /Извлечено 40 скважин из Excel/);
  assert.ok(prepared.schedule_retrieval_request.query.length <= 800);
  // No rule-based "next step" hint: the Decision LLM must reason from state + registry + RAG.
  assert.ok(!prepared.planner_input.includes('Подсказка следующего шага'));
  assert.ok(!/Дальше schedule_builder|Сначала excel_extractor|сразу schedule_builder|уже есть — finish/.test(prepared.planner_input));
  assert.equal(prepared.schedule_retrieval_request.top_k, 12);
  assert.deepEqual(prepared.retrieval_selector, {
    target_base: 'orchestrator_routing',
    knowledge_types: ['routing_card'],
  });

  // An empty registry is a deployment error, not something to reason around.
  await assert.rejects(
    run('Prepare decision context', { case_id: 'CASE-1' }, {
      'Load case': { state: { goal: 'x', artifacts: {} }, status: 'running' },
      'Load agent registry': [],
      'Runtime endpoints': {},
    }),
    /agent_registry has no enabled agents/,
  );

  // Legacy state from before Phase 2 (data.excel bucket) still loads; the bucket is slimmed like an agent slot.
  const legacyPrepared = await run(
    'Prepare decision context',
    { case_id: 'CASE-1' },
    {
      'Load case': {
        state: {
          goal: 'перепривяжи скважины в группу G1',
          artifacts: { schedule_source: { filename: 'base.inc', artifact_id: 'schedule_source' } },
          data: { excel: { facts: fatFacts, blob: 'z'.repeat(13000) } },
        },
        status: 'running',
      },
      'Load agent registry': { agent_id: 'schedule_builder', title: 'Schedule', invoke: { kind: 'n8n_workflow', workflow_id: 'wf-s' }, enabled: true },
      'Runtime endpoints': { activity_base_url: 'http://mas-activity:8200' },
    },
  );
  assert.deepEqual(Object.keys(legacyPrepared.state.data.excel).sort(), ['blob', 'facts', 'omitted_keys'].filter((k) => k !== 'blob'));
  assert.deepEqual(legacyPrepared.compact.agents, {});
  assert.match(legacyPrepared.schedule_retrieval_request.query, /перепривяжи скважины/);

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
  assert.match(attachedFail.planner_input, /Не спрашивай инженера про базу знаний/);
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

  const unlistedQuestion = {
    question_id: 'unlisted_wells_policy',
    question: 'В Excel нет скважин: 201. Оставить или убрать?',
    options: [
      { value: 'keep', label: 'Оставить как в baseline' },
      { value: 'remove', label: 'Убрать из прогноза' },
    ],
  };
  const unlistedState = {
    status: 'waiting_user',
    state: {
      goal: 'g',
      hitl: { pending: true, questions: [unlistedQuestion], answers: {} },
    },
  };

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
        source: 'human',
        human_response: 'январь',
        gate_id: 'Q-1',
      },
    },
  );
  assert.equal(hitlApplied.did_resume, true);
  assert.equal(hitlApplied.needs_interpret, false);
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
        source: 'human',
        human_response: JSON.stringify({ text: 'январь' }),
        gate_id: 'Q-1',
      },
    },
  );
  assert.equal(hitlJson.state.hitl.answers['Q-1'].text, 'январь');

  const normalizedObject = await run(
    'Normalize step request',
    {
      action: 'resume',
      case_id: 'CASE-1',
      source: 'human',
      gate_id: 'unlisted_wells_policy',
      human_response: { text: 'убери', choice: 'remove' },
    },
  );
  assert.equal(typeof normalizedObject.human_response, 'string');
  assert.equal(JSON.parse(normalizedObject.human_response).choice, 'remove');
  assert.equal(normalizedObject.source, 'human');
  assert.equal(normalizedObject.is_resume, true);

  const unlistedButton = await run(
    'Apply request extras',
    unlistedState,
    {
      'Normalize step request': {
        case_id: 'CASE-1',
        is_resume: true,
        action: 'resume',
        source: 'human',
        human_response: JSON.stringify({ choice: 'remove', text: 'Убрать из прогноза', label: 'Убрать из прогноза' }),
        gate_id: 'unlisted_wells_policy',
      },
    },
  );
  assert.equal(unlistedButton.did_resume, true);
  assert.equal(unlistedButton.needs_interpret, false);
  assert.equal(unlistedButton.state.hitl.answers.unlisted_wells_policy.unlisted_wells_policy, 'remove');
  assert.equal(unlistedButton.next_status, 'running');
  assert.equal(unlistedButton.persist_events[0][2], 'hitl.answered');

  const unlistedHitl = await run(
    'Apply request extras',
    unlistedState,
    {
      'Normalize step request': {
        case_id: 'CASE-1',
        is_resume: true,
        action: 'resume',
        source: 'human',
        human_response: 'убери лишние скважины',
        gate_id: 'unlisted_wells_policy',
      },
    },
  );
  assert.equal(unlistedHitl.did_resume, true);
  assert.equal(unlistedHitl.needs_interpret, true);
  assert.equal(unlistedHitl.state.hitl.pending, true);
  assert.equal(unlistedHitl.state.hitl.answers.unlisted_wells_policy, undefined);
  assert.ok(String(unlistedHitl.interpret_input).includes('убери лишние скважины'));

  const unlistedKeep = await run(
    'Apply request extras',
    unlistedState,
    {
      'Normalize step request': {
        case_id: 'CASE-1',
        is_resume: true,
        action: 'resume',
        source: 'human',
        human_response: 'оставь лишние скважины',
        gate_id: 'unlisted_wells_policy',
      },
    },
  );
  assert.equal(unlistedKeep.needs_interpret, true);
  assert.equal(unlistedKeep.state.hitl.answers.unlisted_wells_policy, undefined);

  const unlistedKeepWord = await run(
    'Apply request extras',
    unlistedState,
    {
      'Normalize step request': {
        case_id: 'CASE-1',
        is_resume: true,
        action: 'resume',
        source: 'human',
        human_response: 'keep extra wells',
        gate_id: 'unlisted_wells_policy',
      },
    },
  );
  assert.equal(unlistedKeepWord.needs_interpret, true);
  assert.equal(unlistedKeepWord.state.hitl.pending, true);

  const unlistedUpkeep = await run(
    'Apply request extras',
    unlistedState,
    {
      'Normalize step request': {
        case_id: 'CASE-1',
        is_resume: true,
        action: 'resume',
        source: 'human',
        human_response: 'upkeep of extra wells',
        gate_id: 'unlisted_wells_policy',
      },
    },
  );
  assert.equal(unlistedUpkeep.needs_interpret, true, 'substring keep in upkeep must not skip interpret');
  assert.equal(unlistedUpkeep.state.hitl.answers.unlisted_wells_policy, undefined);

  const interpretedRemove = await run(
    'Apply interpreted answer',
    {},
    {
      'Apply request extras': {
        case_id: 'CASE-1',
        did_resume: true,
        needs_interpret: true,
        interpret_qid: 'unlisted_wells_policy',
        interpret_raw: { text: 'убери лишние скважины' },
        interpret_question: unlistedQuestion,
        state: unlistedState.state,
      },
      'Interpret free-text answer': {
        output: { decision: 'remove', confidence: 0.9, paraphrase: 'Убрать скважины, которых нет в Excel' },
      },
    },
  );
  assert.equal(interpretedRemove.next_status, 'running');
  assert.equal(interpretedRemove.state.hitl.pending, false);
  assert.equal(interpretedRemove.state.hitl.answers.unlisted_wells_policy.choice, 'remove');
  assert.equal(interpretedRemove.state.hitl.answers.unlisted_wells_policy.unlisted_wells_policy, 'remove');
  assert.equal(interpretedRemove.persist_events[0][2], 'hitl.answered');

  const interpretedUnsure = await run(
    'Apply interpreted answer',
    {},
    {
      'Apply request extras': {
        case_id: 'CASE-1',
        did_resume: true,
        needs_interpret: true,
        interpret_qid: 'unlisted_wells_policy',
        interpret_raw: { text: 'наверное так' },
        interpret_question: unlistedQuestion,
        state: unlistedState.state,
      },
      'Interpret free-text answer': {
        output: { decision: 'remove', confidence: 0.4, paraphrase: 'неясно' },
      },
    },
  );
  assert.equal(interpretedUnsure.next_status, 'waiting_user');
  assert.equal(interpretedUnsure.state.hitl.pending, true);
  assert.match(String(interpretedUnsure.state.hitl.questions[0].question), /Не удалось однозначно понять ответ/);
  assert.equal(interpretedUnsure.persist_events[0][2], 'hitl.request');
  // Developer log: the re-ask carries the execution reference like every other orchestrator event.
  assert.equal(JSON.parse(interpretedUnsure.persist_events[0][8]).execution_id, 'exec-1');

  const agentResume = await run(
    'Apply request extras',
    { status: 'running', state: { goal: 'g', step_count: 2 } },
    {
      'Normalize step request': {
        case_id: 'CASE-1',
        is_resume: true,
        action: 'resume',
        source: 'agent',
        task_id: 'TASK-long',
        agent_id: 'calculation_agent',
        human_response: JSON.stringify({ status: 'completed' }),
      },
    },
  );
  assert.equal(agentResume.did_resume, true);
  assert.equal(agentResume.needs_interpret, false);
  assert.equal(agentResume.next_status, 'running');
  assert.equal(agentResume.persist_events[0][2], 'orchestrator.resume');
  assert.equal(agentResume.persist_events[0][3], 'agent');
  // Phase 4.5: a payload shaped like agent_result is merged exactly like a synchronous result (slot + journal + agent.result).
  assert.equal(agentResume.state.agents.calculation_agent.status, 'completed');
  assert.equal(agentResume.state.agents.calculation_agent.task_id, 'TASK-long');
  assert.equal(agentResume.state.ledger.history.some((e) => e.kind === 'agent' && e.agent_id === 'calculation_agent' && e.status === 'completed'), true);
  assert.equal(agentResume.persist_events[1][2], 'agent.result');

  // A system wake-up without an agent_result stays a plain journal note.
  const systemResume = await run(
    'Apply request extras',
    { status: 'running', state: { goal: 'g', step_count: 2 } },
    { 'Normalize step request': { case_id: 'CASE-1', is_resume: true, action: 'resume', source: 'system', task_id: '', agent_id: '', human_response: 'перезапуск по таймеру' } },
  );
  assert.equal(systemResume.next_status, 'running');
  assert.equal(systemResume.state.ledger.history.some((e) => e.kind === 'external' && e.source === 'system'), true);
  assert.equal(systemResume.persist_events.length, 1);

  // Long agent round-trip: in_progress → case waits for the agent (no step spent, loop stops) → the agent's
  // final result arrives via resume source=agent with the task id taken from current_task.
  {
    const parsed = { case_id: 'CASE-long', agent_id: 'long_agent', agent_task: { task_id: 'TASK-9' }, registry: [{ agent_id: 'long_agent', title: 'Долгий расчёт' }], state: { goal: 'g', step_count: 3, artifacts: {}, agents: {} }, max_steps: 12 };
    const inProgress = await run('Merge agent result', { body: { task_id: 'TASK-9', status: 'in_progress', message: 'Расчёт запущен, ориентировочно две минуты.', watch: { kind: 'poll', ref: 'job-1', poll_hint: '20s' } } }, { 'Prepare agent call': parsed, 'Parse decision': parsed });
    assert.equal(inProgress.next_status, 'waiting_agent');
    assert.equal(inProgress.should_continue, false);
    assert.deepEqual(inProgress.state.current_task, { task_id: 'TASK-9', agent_id: 'long_agent' });
    assert.equal(inProgress.state.agents.long_agent.status, 'in_progress');
    assert.equal(inProgress.events[0].kind, 'agent.progress');
    assert.equal(inProgress.events[0].status, 'waiting_agent');
    assert.deepEqual(inProgress.events[0].payload.watch, { kind: 'poll', ref: 'job-1', poll_hint: '20s' });
    const finished = await run(
      'Apply request extras',
      { status: 'waiting_agent', state: inProgress.state },
      { 'Normalize step request': { case_id: 'CASE-long', is_resume: true, action: 'resume', source: 'agent', task_id: '', agent_id: 'long_agent', human_response: JSON.stringify({ task_id: 'TASK-9', status: 'completed', message: 'Расчёт готов: глубина 2540 м.', data: { top_perforation_md: 2540 } }) } },
    );
    assert.equal(finished.next_status, 'running');
    assert.equal(finished.state.agents.long_agent.status, 'completed');
    assert.equal(finished.state.agents.long_agent.task_id, 'TASK-9');
    assert.deepEqual(finished.state.agents.long_agent.data, { top_perforation_md: 2540 });
    assert.equal(finished.state.current_task, null);
    assert.equal(finished.persist_events.map((e) => e[2]).join(','), 'orchestrator.resume,agent.result');
  }

  const restoredAfterInterpret = await run(
    'Restore after resume',
    { next_status: 'waiting_user' },
    {
      'Apply request extras': { did_resume: true, next_status: 'running', state: { hitl: { pending: true } } },
      'Apply interpreted answer': interpretedUnsure,
    },
  );
  assert.equal(restoredAfterInterpret.next_status, 'waiting_user');
  assert.match(String(restoredAfterInterpret.state.hitl.questions[0].question), /Не удалось однозначно понять ответ/);

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
          // The LLM likes to "help" with its own data. CASE-6a9ec6b3-74e34e: it put per-well `facts` with
          // invented dates into action.task, and Schedule Builder preferred them to data.excel.facts.
          task: { objective: 'Извлечь даты', excel_artifact: 'a.xlsx', facts: [{ well: '295R', date: '2019-10-01' }] },
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
  assert.equal(parsed.agent_task.objective, 'Извлечь даты');
  assert.equal(parsed.agent_task.inputs.activity_base_url, 'http://activity:8200');
  assert.equal(parsed.agent_task.inputs.artifacts, undefined);
  assert.deepEqual(parsed.agent_task.inputs.artifact_ids, ['excel']);
  assert.equal(parsed.agent_task.inputs.context, undefined);
  // inputs are orchestrator-owned references only; LLM-authored payload never reaches the agent as data.
  assert.equal(parsed.agent_task.inputs.facts, undefined);
  assert.equal(parsed.agent_task.inputs.excel_artifact, undefined);
  assert.deepEqual(Object.keys(parsed.agent_task.inputs).sort(), ['activity_base_url', 'artifact_ids', 'data_refs', 'schedule_root']);
  assert.equal(parsed.state.current_task.task_id, 'TASK-1');
  assert.equal(parsed.state.current_task.context, undefined);
  assert.equal(parsed.state.version, 1);
  assert.equal(parsed.next_status, 'running');
  assert.deepEqual(parsed.events.map((e) => e.kind), ['orchestrator.decision', 'agent.handoff']);
  assert.equal(parsed.events.some((e) => e.kind === 'orchestrator.status'), false);
  // Developer log («Лог»): the raw LLM decision and the exact agent_task ride on the events together
  // with the n8n execution reference (this harness mocks $execution.id = exec-1; $workflow is absent → '').
  const [decisionEv, handoffEv] = parsed.events;
  assert.equal(decisionEv.payload.decision.action.type, 'call_agent');
  assert.equal(decisionEv.payload.decision.action.handoff_message, 'Достань даты');
  assert.deepEqual(decisionEv.payload.decision.action.task.facts, [{ well: '295R', date: '2019-10-01' }], 'what the LLM invented is visible in the log, never in inputs');
  assert.deepEqual({ e: decisionEv.payload.execution_id, w: decisionEv.payload.workflow_id }, { e: 'exec-1', w: '' });
  assert.equal(handoffEv.payload.agent_task.task_id, 'TASK-1');
  assert.deepEqual(handoffEv.payload.agent_task.inputs, parsed.agent_task.inputs);
  assert.equal(handoffEv.payload.execution_id, 'exec-1');
  assert.equal(parsed.state.current_task.agent_task, undefined, 'state does not grow: the copy lives in the event only');

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
            answers: { unlisted_wells_policy: { choice: 'remove', text: 'Убрать из прогноза', label: 'Убрать из прогноза' } },
          },
        },
        activity_base_url: 'http://activity:8200',
      },
    },
  );
  assert.equal(afterHitl.agent_task.inputs.unlisted_wells_policy, 'remove');
  assert.equal(afterHitl.agent_task.context.hitl.answers.unlisted_wells_policy.choice, 'remove');
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
  assert.equal(keepWordCompact.compact.unlisted_wells_policy, null);

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
  // Phase 2: the result lands in state.agents[<agent_id>] verbatim (the orchestrator does not know what
  // `facts` means — only the byte budget applies); no data.excel bucket is created.
  assert.deepEqual(merged.state.agents.excel_extractor.data.facts, [{ well: 'A', date: '2020-01-01', values: { pad: 'secret' } }]);
  assert.equal(merged.state.agents.excel_extractor.status, 'completed');
  assert.equal(merged.state.agents.excel_extractor.summary, 'ok');
  assert.equal(merged.state.agents.excel_extractor.task_id, 'TASK-1');
  assert.deepEqual(merged.state.agents.excel_extractor.data_keys, ['facts']);
  assert.equal('excel' in merged.state.data, false);
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
  assert.equal(failedMerge.state.agents.schedule_builder.status, 'failed');
  assert.equal(failedMerge.state.agents.schedule_builder.summary.includes('SCHEDULE не собран'), true);
  assert.equal('schedule' in failedMerge.state.data, false);
  assert.ok(!('facts' in (failedMerge.state.data || {})));
  {
    // A failed retry never erases data a previous completed run of the same agent produced.
    const keepData = await run(
      'Merge agent result',
      { status: 'failed', message: 'упал', data: {}, artifacts: {} },
      {
        'Parse decision': parsed,
        'Prepare agent call': { ...parsed, agent_id: 'excel_extractor', state: merged.state, activity_base_url: 'http://activity:8200' },
      },
    );
    assert.equal(keepData.state.agents.excel_extractor.status, 'failed');
    assert.deepEqual(keepData.state.agents.excel_extractor.data_keys, ['facts']);
  }
  {
    // "Agent not bound" feeds Merge a failed agent_result with a human-readable reason (no ids in the text).
    const unboundResult = await run('Agent not bound', {
      agent_id: 'ghost_agent',
      invoke_reason: 'not_in_registry',
      invoke_message: 'Выбранный агент не найден в реестре агентов — вызвать его нельзя.',
    });
    assert.equal(unboundResult.status, 'failed');
    assert.deepEqual(unboundResult.issues, [{ code: 'agent_unbound', reason: 'not_in_registry' }]);
    const unboundMerge = await run('Merge agent result', unboundResult, {
      'Parse decision': parsed,
      'Prepare agent call': { ...parsed, agent_id: 'ghost_agent', activity_base_url: 'http://activity:8200' },
    });
    assert.equal(unboundMerge.events[0].kind, 'agent.failed');
    assert.match(unboundMerge.events[0].status_message, /не найден в реестре/);
    assert.equal(unboundMerge.should_continue, true, 'the loop re-decides with the failure in the journal');
  }

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
    // Step 0 of the journal = the engineer's inputs, by filename (CASE-6a9ec5ef-905bb0: without it the completion
    // check invented an uncoverable part «получить старый прогнозный schedule» and escalated to review).
    assert.match(prepared.planner_input, /- шаг 0: инженер приложил 1 файл\(ов\): baseline\.inc/);
    assert.ok(!prepared.planner_input.includes('пока ничего не сделано'));
    const journalSection = prepared.planner_input.slice(0, prepared.planner_input.indexOf('\nТекущее состояние:'));
    assert.ok(!journalSection.includes('schedule_source'), 'journal names files, not artifact roles');
    assert.deepEqual(prepared.compact.journal, { history: [], stall_count: 0, completed_agents: [] });
    // No inputs at all → the journal says so explicitly.
    const bare = await prepare({ ...baseState, artifacts: {} });
    assert.match(bare.planner_input, /пока ничего не сделано/);
    let parsed = await decide(prepared, delegate);
    assert.equal(parsed.action_type, 'call_agent');
    assert.equal(parsed.should_call_agent, true);
    assert.equal(parsed.decision.progress.goal_satisfied, false);
    // First delegation with a wrong goal_satisfied flag (combat_case2 regression): the agent has never
    // run, so the action is the intent — call it, do not finish on the flag.
    const slip = await decide(prepared, { ...delegate, progress: { ...delegate.progress, goal_satisfied: true } });
    assert.equal(slip.action_type, 'call_agent');
    assert.equal(slip.should_call_agent, true);
    assert.equal(slip.next_status, 'running');
    assert.equal(slip.events.find((e) => e.kind === 'orchestrator.decision').payload.guard, 'goal_flag_ignored');

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
    // Phase 1.5: merged artifacts are that agent's deliverables; the agent.result event lists them.
    const resultEvent = merged.events.find((e) => e.kind === 'agent.result');
    assert.deepEqual(resultEvent.payload.deliverables.map((d) => [d.artifact_id, d.producer, d.kind]), [
      ['schedule_out', 'schedule_builder', 'deliverable'],
      ['diff', 'schedule_builder', 'deliverable'],
    ]);
    assert.equal(merged.state.artifacts.schedule.out.producer, 'schedule_builder');

    // Step 2: the LLM now sees the journal in planner_input …
    prepared = await prepare(merged.state);
    assert.match(prepared.planner_input, /шаг 2: schedule_builder → completed: Перепривязал 2 скважины/);
    assert.deepEqual(prepared.compact.journal.completed_agents, ['schedule_builder']);
    // … and the deliverables as cards (no `has_schedule_out` slot flag anywhere in the context).
    assert.deepEqual(prepared.compact.deliverables.map((d) => d.producer), ['schedule_builder', 'schedule_builder']);
    assert.equal('has_schedule_out' in prepared.compact, false);
    assert.ok(!prepared.planner_input.includes('has_schedule_out'));

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
    // evidence mentions `schedule_out` (an id) → the engineer sees the journal summary instead.
    assert.match(contradiction.state.data.result.summary_for_human, /Schedule Builder: Перепривязал 2 скважины/);
    assert.equal(/schedule_out/.test(contradiction.state.data.result.summary_for_human), false);

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
    assert.equal(finished.payload.execution_id, 'exec-1', 'developer log links the finish to its n8n execution');
    // Phase 1.5: the finish carries the case result as deliverable cards by producer, not "the schedule".
    assert.deepEqual(finished.payload.deliverables.map((d) => [d.producer, d.artifact_id, d.filename]), [
      ['schedule_builder', 'schedule_out', 'schedule_result.inc'],
      ['schedule_builder', 'diff', 'schedule_changes.diff'],
    ]);
    // finish without summary_for_human → summary composed from the journal, never "вызвал агента".
    const finishBare = await decide(prepared, { progress: { goal_satisfied: true, evidence: 'ok' }, status_message: 'Готово', action: { type: 'finish' } });
    assert.match(finishBare.events.find((e) => e.kind === 'case.finished').status_message, /Schedule Builder: Перепривязал 2 скважины/);
    // The LLM leaked ids into the engineer-facing summary → the journal summary is shown instead.
    const finishLeaky = await decide(prepared, {
      progress: { goal_satisfied: true, evidence: 'ok' },
      status_message: 'Готово',
      action: { type: 'finish', result: { summary_for_human: 'schedule_builder перепривязал скважины, подготовлены schedule_out и diff.' } },
    });
    const leakyMsg = finishLeaky.events.find((e) => e.kind === 'case.finished').status_message;
    assert.match(leakyMsg, /Schedule Builder: Перепривязал 2 скважины/);
    assert.equal(/schedule_out|schedule_builder/.test(leakyMsg), false, leakyMsg);

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
    const askedEv = asked.events.find((e) => e.kind === 'hitl.request');
    assert.equal(askedEv.payload.question_id, 'unlisted_wells_policy');
    assert.equal(askedEv.payload.execution_id, 'exec-1', 'agent question in the developer log links to the merge execution');
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

    // ---- Verified completion (combat_case0/1 regression: LLM finished after Excel only) ----
    // Structure: finish takes the detour Decision LLM → Finish proposed? → completion check → Parse decision.
    assert.equal(wf.connections['Decision LLM'].main[0][0].node, 'Finish proposed?');
    assert.equal(wf.connections['Finish proposed?'].main[0][0].node, 'Prepare completion check');
    assert.equal(wf.connections['Finish proposed?'].main[1][0].node, 'Parse decision');
    assert.equal(wf.connections['Prepare completion check'].main[0][0].node, 'Verify completion');
    assert.equal(wf.connections['Verify completion'].main[0][0].node, 'Parse decision');
    assert.equal(wf.connections['Verification Structured Output'].ai_outputParser[0][0].node, 'Verify completion');
    assert.ok(wf.connections['Decision Chat Model — configure in UI'].ai_languageModel[0].some((c) => c.node === 'Verify completion'));
    const verify = wf.nodes.find((n) => n.name === 'Verify completion');
    assert.equal(verify.type, '@n8n/n8n-nodes-langchain.chainLlm');
    const verifySystem = verify.parameters.messages.messageValues[0].message;
    assert.match(verifySystem, /извлечь данные ≠ построить/);
    assert.equal(/excel_extractor|schedule_builder|WCONPROD|Excel/.test(verifySystem), false, 'completion check knows no domain');
    assert.equal(wf.connections['No agent this step'].main[0][0].node, 'Continue loop?', 'a continue step re-enters the loop');
    // Runtime: Excel done, LLM proposes finish, the check says the deliverable is not covered.
    const excelOnly = {
      ...baseState,
      step_count: 2,
      artifacts: { ...baseState.artifacts, excel: { artifact_id: 'excel', filename: 'dates.xlsx' } },
      ledger: { history: [{ kind: 'agent', step: 1, status: 'completed', agent_id: 'excel_extractor', task_id: 'TASK-1', summary: 'Извлечено фактов: 8', artifacts_added: [] }], stall_count: 0, reviews: 0, last_human_step: 0 },
    };
    const preparedExcel = await prepare(excelOnly);
    const falseFinish = {
      progress: { goal_satisfied: true, evidence: 'даты извлечены' },
      status_message: 'Готово',
      action: { type: 'finish', result: { summary_for_human: 'Даты извлечены, прогнозный schedule обновлён.' } },
    };
    const checkInput = await run('Prepare completion check', { output: falseFinish }, { 'Prepare decision context': preparedExcel, 'Decision LLM': { output: falseFinish } });
    assert.match(checkInput.verify_input, /^Цель:/);
    assert.match(checkInput.verify_input, /excel_extractor → completed: Извлечено фактов: 8/);
    assert.match(checkInput.verify_input, /Предложенный итог оркестратора:\nДаты извлечены, прогнозный schedule обновлён\./);
    assert.equal(checkInput.verify_input.includes('Текущее состояние:'), false, 'the check sees goal + journal + proposal, not the raw state dump');
    const rejected = {
      goal_parts: [
        { part: 'новые даты ввода извлечены из Excel', covered: true, evidence: 'шаг 1' },
        { part: 'обновлённый прогнозный schedule-файл', covered: false, evidence: 'в журнале нет' },
      ],
      unsupported_claims: ['прогнозный schedule обновлён'],
      all_covered: false,
      verdict_for_human: 'Даты из Excel извлечены, но обновлённый schedule ещё не собран',
    };
    const decideVerified = (prepared, output, verification) =>
      run('Parse decision', { output: verification }, { 'Prepare decision context': prepared, 'Decision LLM': { output }, 'Verify completion': { output: verification } });
    const firstReject = await decideVerified(preparedExcel, falseFinish, rejected);
    assert.equal(firstReject.action_type, 'continue', 'first rejection → one more orchestrator step, no HITL');
    assert.equal(firstReject.next_status, 'running');
    assert.equal(firstReject.should_call_agent, false);
    assert.equal(firstReject.events.find((e) => e.kind === 'orchestrator.decision').payload.guard, 'completion_unverified');
    assert.equal(firstReject.events.some((e) => e.kind === 'case.finished'), false);
    const contMsg = firstReject.events.find((e) => e.kind === 'orchestrator.status').status_message;
    assert.match(contMsg, /обновлённый schedule ещё не собран/);
    assert.match(contMsg, /Продолжаю работу/);
    assert.equal(/[a-z]+_[a-z_]+|[{}[\]]/.test(contMsg), false, contMsg);
    assert.equal(firstReject.state.ledger.verify_rejections, 1);
    const vEntry = firstReject.state.ledger.history.at(-1);
    assert.equal(vEntry.kind, 'verification');
    assert.deepEqual(vEntry.uncovered, ['обновлённый прогнозный schedule-файл']);
    // "No agent this step" turns the continue decision into a loop trigger.
    const noAgent = await run('No agent this step', { ...firstReject, orchestrator_step_url: 'http://n8n/webhook/mas-orchestrator-step' });
    assert.equal(noAgent.should_continue, true);
    assert.equal(noAgent.continue_url, 'http://n8n/webhook/mas-orchestrator-step');
    const noAgentFinish = await run('No agent this step', { action_type: 'finish', next_status: 'done' });
    assert.equal(noAgentFinish.should_continue, false);
    // Next step: the Decision LLM sees the gap in the journal and must close it.
    const preparedAfterReject = await prepare(firstReject.state);
    assert.match(preparedAfterReject.planner_input, /проверка завершения отклонила finish — не покрыто: обновлённый прогнозный schedule-файл/);
    assert.equal(preparedAfterReject.compact.journal.history.at(-1).kind, 'verification');
    const closesGap = await decideVerified(preparedAfterReject, { ...delegate, progress: { goal_satisfied: false, evidence: 'schedule не собран' } }, null);
    assert.equal(closesGap.action_type, 'call_agent');
    assert.equal(closesGap.agent_task.agent_id, 'schedule_builder');
    // Stubborn finish rejected a second time → the engineer decides (prose, accept / continue).
    const secondReject = await decideVerified(preparedAfterReject, falseFinish, rejected);
    assert.equal(secondReject.action_type, 'ask_user');
    assert.equal(secondReject.next_status, 'waiting_user');
    assert.equal(secondReject.events.find((e) => e.kind === 'orchestrator.decision').payload.guard, 'completion_review');
    const reviewQ = secondReject.state.hitl.questions[0];
    assert.match(reviewQ.question_id, /^result_review_/);
    assert.match(reviewQ.question, /проверка нашла пробел/);
    assert.match(reviewQ.question, /Не хватает: обновлённый прогнозный schedule-файл/);
    assert.match(reviewQ.question, /Сделано: Excel Extractor: Извлечено фактов: 8/);
    assert.deepEqual(reviewQ.options.map((o) => o.value), ['accept', 'rework']);
    assert.equal(/[a-z]+_[a-z_]+|[{}[\]]/.test(reviewQ.question), false, reviewQ.question);
    // A verified finish passes through and is marked as such.
    const okFinish = await decideVerified(preparedExcel, falseFinish, { ...rejected, goal_parts: rejected.goal_parts.map((p) => ({ ...p, covered: true })), all_covered: true, unsupported_claims: [] });
    assert.equal(okFinish.action_type, 'finish');
    assert.equal(okFinish.events.find((e) => e.kind === 'case.finished').payload.completion_verified, true);
    assert.equal(okFinish.events.find((e) => e.kind === 'case.finished').status_message, 'Даты извлечены, прогнозный schedule обновлён.');
    // golden_case_1 regression: the model contradicted itself (every part covered, all_covered:false).
    // Per-part judgements win → verified finish, no continue step, no review.
    const selfContradicting = await decideVerified(preparedExcel, falseFinish, { ...rejected, goal_parts: rejected.goal_parts.map((p) => ({ ...p, covered: true })), all_covered: false, unsupported_claims: [] });
    assert.equal(selfContradicting.action_type, 'finish');
    assert.equal(selfContradicting.events.find((e) => e.kind === 'case.finished').payload.completion_verified, true);
    assert.equal(selfContradicting.state.ledger.verify_rejections || 0, 0);
    // Partial replies: either field alone is usable; an uncovered part must reject even without the flag
    // (discarding the reply would let the finish through unchecked).
    const partsOnly = await decideVerified(preparedExcel, falseFinish, { goal_parts: rejected.goal_parts });
    assert.equal(partsOnly.action_type, 'continue', 'goal_parts without all_covered still rejects');
    const flagOnly = await decideVerified(preparedExcel, falseFinish, { all_covered: true });
    assert.equal(flagOnly.action_type, 'finish');
    assert.equal(flagOnly.events.find((e) => e.kind === 'case.finished').payload.completion_verified, true);
    // Nothing usable (empty parts, no boolean flag) → no verification, never a rejection with no gaps.
    for (const empty of [{ goal_parts: [] }, { goal_parts: [{ part: '', covered: false }] }, { all_covered: 'yes' }, {}]) {
      const noVerdict = await decideVerified(preparedExcel, falseFinish, empty);
      assert.equal(noVerdict.action_type, 'finish', JSON.stringify(empty));
      assert.equal('completion_verified' in noVerdict.events.find((e) => e.kind === 'case.finished').payload, false, JSON.stringify(empty));
      assert.equal(noVerdict.state.ledger.verify_rejections || 0, 0);
    }
    // All parts covered but the proposed summary claims more than the journal → the journal summary is shown.
    const overclaim = await decideVerified(preparedExcel, falseFinish, { ...rejected, goal_parts: rejected.goal_parts.map((p) => ({ ...p, covered: true })), all_covered: true, unsupported_claims: ['прогнозный schedule обновлён'] });
    assert.equal(overclaim.action_type, 'finish');
    assert.equal(overclaim.events.find((e) => e.kind === 'case.finished').status_message, 'Excel Extractor: Извлечено фактов: 8');
    // The human accepted in a review → finish even if the check would object.
    const acceptedState = { ...excelOnly, ledger: { ...excelOnly.ledger, history: [...excelOnly.ledger.history, { kind: 'human', step: 3, question_id: 'result_review_3', question: 'Принять?', answer: 'Принять результат как есть', review_accept: true }] } };
    const acceptedFinish = await decideVerified(await prepare(acceptedState), falseFinish, rejected);
    assert.equal(acceptedFinish.action_type, 'finish');
  }

  // ---------------------------------------------------------------------------------------------
  // O13 — the plan is an object the orchestrator keeps (regression for CASE-6a9fa129-5ae077: the model wrote
  // `plan_update: [{step:1, agent:"…", action:"call_agent", reason:"…"}]` — no id — and the plan was lost).
  // ---------------------------------------------------------------------------------------------
  {
    const registry = [
      { agent_id: 'excel_extractor', title: 'Excel Extractor' },
      { agent_id: 'schedule_builder', title: 'Schedule Builder' },
    ];
    const req = { case_id: 'CASE-PLAN', action: 'step', is_status: false, is_resume: false };
    const baseState = {
      case_id: 'CASE-PLAN',
      goal: 'Сдвинуть даты ввода по Excel и собрать новый schedule',
      status: 'running',
      step_count: 0,
      version: 1,
      artifacts: { excel: { artifact_id: 'excel', filename: 'dates.xlsx' }, schedule: { source: { artifact_id: 'schedule_source', filename: 'baseline.inc' } } },
      data: {},
      hitl: { pending: false, questions: [], answers: {} },
      plan: [],
    };
    const prepare = (state) => run('Prepare decision context', {}, {
      'Apply request extras': { ...req, state, next_status: 'running' },
      'Load case': { case_id: 'CASE-PLAN', state: JSON.stringify(state), status: 'running' },
      'Load agent registry': registry,
      'Runtime endpoints': { activity_base_url: 'http://mas-activity:8200', max_steps: '12' },
    });
    const decide = (prepared, output, verification) => (verification === undefined
      ? run('Parse decision', { output }, { 'Prepare decision context': prepared })
      : run('Parse decision', { output: verification }, { 'Prepare decision context': prepared, 'Decision LLM': { output }, 'Verify completion': { output: verification } }));
    const merge = (parsed, body) => run('Merge agent result', { body }, { 'Prepare agent call': parsed, 'Parse decision': parsed });
    const decisionPayload = (parsed) => parsed.events.find((e) => e.kind === 'orchestrator.decision').payload;
    const callExcel = {
      progress: { goal_satisfied: false, evidence: 'ничего не сделано', missing: 'даты и schedule' },
      status_message: 'Сначала извлеку даты из Excel.',
      action: { type: 'call_agent', agent_id: 'excel_extractor', handoff_message: 'Извлеки даты ввода из книги.' },
    };

    // A fresh case: the prompt asks for a plan and explains the vocabulary.
    let prepared = await prepare(baseState);
    assert.match(prepared.planner_input, /План задачи \(твоя декомпозиция; статусы pending, active, done, blocked, dropped\):\n- план ещё не составлен: составь его в plan_update/);
    const system = wf.nodes.find((n) => n.name === 'Decision LLM').parameters.messages.messageValues[0].message;
    assert.match(system, /plan_update/);
    assert.match(system, /Пункт без id отбрасывается/);
    assert.match(system, /finish возможен, только когда в плане нет пунктов pending, active или blocked/);
    const schema = JSON.parse(wf.nodes.find((n) => n.name === 'Decision Structured Output').parameters.inputSchema);
    assert.deepEqual(schema.properties.plan_update.items.required, ['id']);
    assert.deepEqual(schema.properties.plan_update.items.properties.status.enum, ['pending', 'active', 'done', 'blocked', 'dropped']);

    // The live shape from CASE-6a9fa129-5ae077 (no id) → rejected and counted, state.plan stays empty.
    const prose = await decide(prepared, { ...callExcel, plan_update: [
      { step: 1, agent: 'Excel Extractor', action: 'call_agent', reason: 'Нужны даты' },
      { step: 2, agent: 'Schedule Builder', action: 'call_agent', reason: 'Собрать schedule' },
    ] });
    assert.equal(prose.action_type, 'call_agent');
    assert.deepEqual(prose.state.plan, []);
    assert.deepEqual(decisionPayload(prose).plan, { total: 0, open: 0, accepted: 0, rejected: 2 });

    // A proper plan: ids, titles, agent by title or by id, dependencies; call_agent marks the item active.
    let parsed = await decide(prepared, { ...callExcel, plan_update: [
      { id: 'p1', title: 'Даты ввода скважин из Excel', agent_id: 'Excel Extractor' },
      { id: 'p2', title: 'Новый schedule на основе baseline', agent_id: 'schedule_builder', depends_on: ['p1'] },
      { id: 'p3', title: 'Согласование с инженером', agent_id: 'ghost_agent', status: 'nonsense' },
    ] });
    assert.equal(parsed.action_type, 'call_agent');
    assert.deepEqual(parsed.state.plan, [
      { id: 'p1', title: 'Даты ввода скважин из Excel', status: 'active', agent_id: 'excel_extractor' },
      { id: 'p2', title: 'Новый schedule на основе baseline', status: 'pending', agent_id: 'schedule_builder', depends_on: ['p1'] },
      { id: 'p3', title: 'Согласование с инженером', status: 'pending' },
    ]);
    assert.deepEqual(decisionPayload(parsed).plan, { total: 3, open: 3, accepted: 3, rejected: 0 });
    assert.deepEqual(parsed.agent_task.inputs.artifact_ids.includes('excel'), true);

    // The agent completes → its active item is done, nothing else moves.
    let merged = await merge(parsed, { status: 'completed', agent_id: 'excel_extractor', message: 'Извлечено дат: 4', data: { facts: [1, 2, 3, 4] } });
    assert.deepEqual(merged.state.plan.map((p) => [p.id, p.status]), [['p1', 'done'], ['p2', 'pending'], ['p3', 'pending']]);

    // The plan is what the Decision LLM and the completion check read next to the journal; titles feed retrieval.
    prepared = await prepare(merged.state);
    assert.match(prepared.planner_input, /План задачи[^\n]*\n- p1 \[done\] Даты ввода скважин из Excel — agent_id: excel_extractor\n- p2 \[pending\] Новый schedule на основе baseline — agent_id: schedule_builder \(после: p1\)\n- p3 \[pending\] Согласование с инженером\n/);
    assert.ok(prepared.planner_input.indexOf('План задачи') < prepared.planner_input.indexOf('\nТекущее состояние:'), 'plan is inside the slice Prepare verify forwards');
    assert.deepEqual(prepared.compact.plan[1], { id: 'p2', title: 'Новый schedule на основе baseline', status: 'pending', agent_id: 'schedule_builder' });
    assert.match(prepared.schedule_retrieval_request.query, /План: Даты ввода скважин из Excel; Новый schedule на основе baseline; Согласование с инженером/);

    // An update touches only the fields it names: status without title keeps the title; a title without status keeps the status.
    const touched = await decide(prepared, {
      ...callExcel,
      action: { type: 'call_agent', agent_id: 'schedule_builder', handoff_message: 'Собери schedule по датам.' },
      plan_update: [{ id: 'p3', status: 'dropped', note: 'инженер не просил' }, { id: 'p2', title: 'Новый schedule по датам из Excel' }],
    });
    assert.deepEqual(touched.state.plan.map((p) => [p.id, p.status, p.title]), [
      ['p1', 'done', 'Даты ввода скважин из Excel'],
      ['p2', 'active', 'Новый schedule по датам из Excel'],
      ['p3', 'dropped', 'Согласование с инженером'],
    ]);
    assert.equal(touched.state.plan[2].note, 'инженер не просил');

    // The plan survives a resume (state round-trips through Apply request extras / sanitizeState).
    const resumed = await run('Apply request extras', { status: 'waiting_user', state: { ...merged.state, hitl: { pending: true, questions: [{ question_id: 'Q-1', question: '?' }], answers: {} } } }, {
      'Normalize step request': { case_id: 'CASE-PLAN', is_resume: true, action: 'resume', source: 'human', human_response: 'да', gate_id: 'Q-1' },
    });
    assert.deepEqual(resumed.state.plan, merged.state.plan);

    // finish while p2 is still pending → treated like a rejected completion check (one continue step), even
    // when the verifier itself says all covered; the open item is named in the journal and in the status.
    const finish = { progress: { goal_satisfied: true, evidence: 'даты извлечены' }, status_message: 'Готово.', action: { type: 'finish', result: { summary_for_human: 'Даты извлечены, schedule обновлён.' } } };
    const allCovered = { goal_parts: [{ part: 'даты извлечены', covered: true, evidence: 'шаг 1' }], all_covered: true, verdict_for_human: 'Всё сделано' };
    const early = await decide(prepared, { ...finish, plan_update: [{ id: 'p3', status: 'dropped' }] }, allCovered);
    assert.equal(early.action_type, 'continue');
    assert.equal(decisionPayload(early).guard, 'completion_unverified');
    assert.deepEqual(early.state.ledger.history.at(-1).uncovered, ['Новый schedule на основе baseline']);
    const earlyMsg = early.events.find((e) => e.kind === 'orchestrator.status').status_message;
    assert.match(earlyMsg, /Не хватает: Новый schedule на основе baseline/);
    assert.equal(earlyMsg.includes('Всё сделано'), false, 'a positive verdict is not quoted when the plan is what blocks');
    assert.equal(/[a-z]+_[a-z_]+|[{}[\]]/.test(earlyMsg), false, earlyMsg);
    assert.deepEqual(decisionPayload(early).plan, { total: 3, open: 1, accepted: 1, rejected: 0 });
    // Next step the model sees the gap twice: in the journal and in the plan block.
    const afterEarly = await prepare(early.state);
    assert.match(afterEarly.planner_input, /отклонила finish — не покрыто: Новый schedule на основе baseline/);
    assert.match(afterEarly.planner_input, /- p2 \[pending\] Новый schedule на основе baseline/);

    // The builder runs and completes → p2 done → a verified finish passes, the plan rides on case.finished.
    const callBuilder = await decide(afterEarly, { ...callExcel, action: { type: 'call_agent', agent_id: 'schedule_builder', handoff_message: 'Собери schedule.' } });
    assert.equal(callBuilder.state.plan.find((p) => p.id === 'p2').status, 'active');
    merged = await merge(callBuilder, { status: 'completed', agent_id: 'schedule_builder', message: 'Сдвинул даты 4 скважин', artifacts: { schedule_out: 'DATES\n/\n', diff: 'd' } });
    assert.deepEqual(merged.state.plan.map((p) => [p.id, p.status]), [['p1', 'done'], ['p2', 'done'], ['p3', 'dropped']]);
    const done = await decide(await prepare(merged.state), finish, allCovered);
    assert.equal(done.action_type, 'finish');
    assert.equal(done.events.find((e) => e.kind === 'case.finished').payload.completion_verified, true);
    assert.deepEqual(done.events.find((e) => e.kind === 'case.finished').payload.plan.map((p) => p.status), ['done', 'done', 'dropped']);

    // A plan item the model closes in the same reply as finish does not block it.
    const stale = { ...merged.state, plan: merged.state.plan.map((p) => (p.id === 'p2' ? { ...p, status: 'active' } : p)) };
    const closedInline = await decide(await prepare(stale), { ...finish, plan_update: [{ id: 'p2', status: 'done' }] }, allCovered);
    assert.equal(closedInline.action_type, 'finish');
    // … while a second finish over the same open item (verify_rejections is already 1) goes to the engineer.
    const stillOpen = await decide(await prepare(stale), finish, allCovered);
    assert.equal(stillOpen.action_type, 'ask_user');
    assert.equal(decisionPayload(stillOpen).guard, 'completion_review');
    assert.match(stillOpen.state.hitl.questions[0].question, /Не хватает: Новый schedule на основе baseline/);
    assert.equal(/[a-z]+_[a-z_]+|[{}[\]]/.test(stillOpen.state.hitl.questions[0].question), false, stillOpen.state.hitl.questions[0].question);
  }

  // ---------------------------------------------------------------------------------------------
  // Phase 2 — an agent added with `upsert_agent` is callable without regenerating the orchestrator:
  // "Prepare agent call" resolves the route from the registry row (+ Runtime Config overrides) only.
  // ---------------------------------------------------------------------------------------------
  {
    const registry = [
      { agent_id: 'echo_agent', title: 'Эхо', when_to_use: 'тест', input_required: [], output_provides: ['echo'], invoke: { kind: 'n8n_workflow', workflow_id: 'WF-ECHO-1', workflow_name: 'Agent — Echo' }, enabled: true },
      { agent_id: 'http_agent', title: 'HTTP', invoke: JSON.stringify({ kind: 'http', url: '{math_url}/agent/run' }), enabled: 'true' },
      { agent_id: 'off_agent', title: 'Выключен', invoke: { kind: 'n8n_workflow', workflow_id: 'WF-OFF' }, enabled: false },
      { agent_id: 'naked_agent', title: 'Без привязки', invoke: {}, enabled: true },
      { agent_id: 'lost_http', title: 'Без адреса', invoke: { kind: 'http', url: '{missing_url}/run' }, enabled: true },
    ];
    const call = (agent_id, endpoints = {}) => run(
      'Prepare agent call',
      { should_call_agent: true, agent_id, registry, agent_task: { agent_id } },
      { 'Runtime endpoints': { math_url: 'http://math:8100/', ...endpoints } },
    );
    const echo = await call('echo_agent');
    assert.equal(echo.route, 'workflow');
    assert.equal(echo.invoke_workflow_id, 'WF-ECHO-1');
    assert.equal(echo.invoke_workflow_name, 'Agent — Echo');
    assert.equal(echo.invoke_message, '');
    // Field: UI import gave the workflow a new id → Runtime Config agent_workflow_ids wins over the registry.
    const rebound = await call('echo_agent', { agent_workflow_ids: JSON.stringify({ echo_agent: 'LIVE-ID-42' }) });
    assert.equal(rebound.route, 'workflow');
    assert.equal(rebound.invoke_workflow_id, 'LIVE-ID-42');
    // HTTP agent: JSONB may arrive as a string; placeholders come from Runtime Config; trailing slash trimmed.
    const http = await call('http_agent');
    assert.equal(http.route, 'http');
    assert.equal(http.invoke_url, 'http://math:8100/agent/run');
    // Anything that cannot be reached is routed to "Agent not bound" with a human-readable reason.
    for (const [id, reason, text] of [
      ['off_agent', 'disabled', /«Выключен» отключён/],
      ['naked_agent', 'no_invoke', /не описано, как его вызывать/],
      ['lost_http', 'no_url', /адрес его сервиса не задан/],
      ['ghost', 'not_in_registry', /Выбранный агент не найден в реестре/],
    ]) {
      const r = await call(id);
      assert.equal(r.route, 'unbound', id);
      assert.equal(r.invoke_reason, reason, id);
      assert.match(r.invoke_message, text);
      assert.equal(/[a-z]+_[a-z_]+|[{}[\]]|=/.test(r.invoke_message), false, r.invoke_message);
    }
    const none = await run('Prepare agent call', { should_call_agent: false, agent_id: 'echo_agent', registry }, {});
    assert.equal(none.route, 'none');
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
