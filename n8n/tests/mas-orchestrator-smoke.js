'use strict';
/**
 * Thin Orchestrator — MAS: one step, no RAG/SCHEDULE, errorWorkflow bound.
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
  assert.equal(text.includes('Hybrid Retrieval'), false);
  assert.equal(text.includes('Call SCHEDULE Builder'), false);
  assert.equal(text.includes('engineering-orchestrator'), false);
  for (const need of [
    'Normalize step request',
    'Load case',
    'Load agent registry',
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
  const continueNode = wf.nodes.find((n) => n.name === 'POST continue run');
  assert.ok(String(continueNode.parameters.jsonBody).includes("action: 'step'"));
  assert.ok(String(continueNode.parameters.jsonBody).includes('orchestrator-self'));
  assert.equal(continueNode.parameters.url, "={{ $json.continue_url }}");
  assert.equal(continueNode.parameters.authentication, 'genericCredentialType');
  assert.equal(continueNode.parameters.genericAuthType, 'httpHeaderAuth');
  assert.ok(continueNode.credentials && continueNode.credentials.httpHeaderAuth);
  assert.equal(continueNode.parameters.options.timeout, 3000);
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
  assert.equal(failedMerge.state.data.schedule.summary.includes('SCHEDULE не собран'), true);
  assert.ok(!('facts' in (failedMerge.state.data || {})));

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
