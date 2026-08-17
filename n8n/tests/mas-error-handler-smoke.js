'use strict';
/**
 * Error — MAS Case Handler: taxonomy classify, fail-closed without case_id, ack shape.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || path.resolve(__dirname, '../..');
const read = (rel) => JSON.parse(fs.readFileSync(path.join(workspace, rel), 'utf8'));
const wf = read('n8n/workflows/core/mas-error-handler.workflow.json');
const orch = read('n8n/workflows/core/universal-engineering-orchestrator.workflow.json');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function source(workflow, name) {
  const node = workflow.nodes.find((n) => n.name === name);
  assert(node && node.type === 'n8n-nodes-base.code', `missing ${name}`);
  return node.parameters.jsCode;
}

async function run(name, json) {
  const fn = new AsyncFunction('$json', '$', source(wf, name));
  const result = await fn(json, () => ({ first: () => ({ json: {} }) }));
  assert(Array.isArray(result) && result[0]?.json);
  return result[0].json;
}

(async () => {
  assert.equal(wf.name, 'Error — MAS Case Handler');
  for (const need of [
    'Classify MAS error event',
    'Build CAS error patch',
    'Call CAS persist — error case',
    'Call Writer — MAS Trace (error)',
    'POST error handoff to MAS Activity',
    'Format MAS error ack',
  ]) {
    assert.ok(wf.nodes.some((n) => n.name === need), `missing node ${need}`);
  }
  for (const need of [
    'Call Error — MAS Case Handler (specialist)',
    'Call Error — MAS Case Handler (verification)',
    'Prepare MAS error event (specialist)',
  ]) {
    assert.ok(orch.nodes.some((n) => n.name === need), `orch missing ${need}`);
  }
  assert.ok(
    JSON.stringify(orch.nodes).includes('REPLACE_ERROR_HANDLER_IN_UI'),
    'orch must bind Error Handler placeholder',
  );

  const scenarios = [
    ['llm_error', 'LLM_CALL_FAILED', 'retryable_error', true],
    ['invalid_json', 'INVALID_STRUCTURED_OUTPUT', 'retryable_error', true],
    ['calc_timeout', 'CALC_SERVICE_TIMEOUT', 'retryable_error', true],
    ['missing_data', 'MISSING_MANDATORY_DATA', 'awaiting_human', false],
    ['validator_reject', 'VALIDATOR_REJECTED', 'awaiting_human', false],
    ['rag_error', 'RAG_UNAVAILABLE', 'awaiting_human', false],
    ['document_access', 'DOCUMENT_ACCESS_DENIED', 'awaiting_human', false],
    ['approval_error', 'APPROVAL_GATE_FAILED', 'awaiting_human', false],
  ];

  for (const [scenario, code, casStatus, restartable] of scenarios) {
    const out = await run('Classify MAS error event', {
      mas_error_event: {
        contract: 'mas_error_event',
        contract_version: '1.0',
        task_id: 'eng_error_smoke_1',
        scenario,
        safe_message: `Test ${scenario}`,
      },
    });
    assert.equal(out.has_task_id, true, scenario);
    assert.equal(out.error_code, code, scenario);
    assert.equal(out.cas_status, casStatus, scenario);
    assert.equal(out.restartable, restartable, scenario);
    assert.ok(String(out.safe_message).includes('eng_error_smoke_1'), `case_id in message for ${scenario}`);
  }

  const anon = await run('Classify MAS error event', {
    mas_error_event: { scenario: 'llm_error', safe_message: 'что-то пошло не так' },
  });
  assert.equal(anon.accepted, false);
  assert.equal(anon.code, 'CASE_ID_REQUIRED');
  assert.equal(anon.activity_notified, false);

  const built = await run('Build CAS error patch', {
    has_task_id: true,
    task_id: 'eng_error_smoke_1',
    taxonomy_scenario: 'llm_error',
    error_code: 'LLM_CALL_FAILED',
    cas_status: 'retryable_error',
    restartable: true,
    safe_message: 'Ошибка LLM. Case eng_error_smoke_1.',
    last_error: { code: 'LLM_CALL_FAILED' },
    cas_snapshot: {
      task_id: 'eng_error_smoke_1',
      version: 2,
      status: 'planning',
      risk_class: 'high',
      request_json: '{"objective":"keep-me"}',
      runtime_json: '{}',
      plan_json: '{}',
      packet_json: '{}',
      result_json: '{}',
      verification_json: '{}',
      gate_json: '{}',
      retry_count: 0,
      max_retries: 2,
      created_at: '2026-01-01T00:00:00.000Z',
      updated_at: '2026-01-01T00:00:00.000Z',
    },
  });
  assert.equal(built.should_persist_cas, true);
  assert.equal(built.attempted.request_json.includes('keep-me'), true);
  assert.equal(built.attempted.status, 'retryable_error');
  assert.equal(built.human_gate.restartable, true);

  
  const failedSnap = await run('Classify MAS error event', {
    mas_error_event: {
      task_id: 'eng_error_smoke_1',
      scenario: 'llm_error',
      cas_snapshot: { status: 'failed', version: 3 },
    },
  });
  assert.equal(failedSnap.cas_status, 'failed');
  assert.equal(failedSnap.restartable, false);

  // awaiting_human error path must not paint as CASE_ERROR in the Activity feed.
  const hitlTrace = await run('Prepare structured error trace', {
    has_task_id: true,
    task_id: 'eng_error_smoke_1',
    safe_message: 'Нужно решение по retry budget (case_id=eng_error_smoke_1)',
    error_code: 'VALIDATOR_REJECTED',
    cas_status: 'awaiting_human',
    taxonomy_scenario: 'validator_reject',
    human_gate: { kind: 'needs_decision' },
    findings: [],
    stage: 'hitl',
    passthrough: {},
  });
  assert.equal(hitlTrace.activity_event.status, 'VALIDATOR_REJECTED');
  assert.notEqual(hitlTrace.activity_event.status, 'CASE_ERROR');

  // Orchestrator: normal needs_approval must not invoke Error Handler / must not steal user_message.
  const orchPrepare = source(orch, 'Prepare MAS error event (specialist)');
  const prepareFn = new AsyncFunction('$json', '$', orchPrepare);
  const approveSkip = await prepareFn(
    {
      task_id: 'eng_approve_smoke',
      status: 'awaiting_human',
      version: 4,
      risk_class: 'high',
      retry_count: 0,
      max_retries: 2,
      created_at: '2026-01-01T00:00:00.000Z',
      updated_at: '2026-01-01T00:00:00.000Z',
      request_json: '{}',
      runtime_json: JSON.stringify({ last_error: { code: 'LLM_CALL_FAILED' } }),
      plan_json: '{}',
      packet_json: '{}',
      result_json: JSON.stringify({
        status: 'needs_approval',
        user_message: 'Черновик прогнозного schedule файла готов. Нужно ваше утверждение перед выпуском.',
        summary: 'Черновик прогнозного schedule файла прошёл проверки и готов к утверждению.',
      }),
      verification_json: '{}',
      gate_json: JSON.stringify({ kind: 'needs_approval', gate_id: 'gate_approve' }),
    },
    () => ({ first: () => ({ json: {} }) }),
  );
  assert.equal(approveSkip[0].json.invoke_error_handler, false);
  assert.equal(approveSkip[0].json.mas_error_event, null);

  const leftoverHitl = await prepareFn(
    {
      task_id: 'eng_hitl_leftover',
      status: 'awaiting_human',
      version: 5,
      risk_class: 'high',
      retry_count: 2,
      max_retries: 2,
      created_at: '2026-01-01T00:00:00.000Z',
      updated_at: '2026-01-01T00:00:00.000Z',
      request_json: '{}',
      runtime_json: JSON.stringify({ last_error: { code: 'LLM_CALL_FAILED', safe_message: 'old' } }),
      plan_json: '{}',
      packet_json: '{}',
      result_json: JSON.stringify({ status: 'succeeded', user_message: 'Черновик готов.' }),
      verification_json: '{}',
      gate_json: JSON.stringify({ kind: 'result_approval' }),
    },
    () => ({ first: () => ({ json: {} }) }),
  );
  assert.equal(leftoverHitl[0].json.invoke_error_handler, false);

  const gateFn = new AsyncFunction('$json', '$', source(orch, 'Build specialist gate or error'));
  const hitlGate = await gateFn(
    {
      task_id: 'eng_hitl_gate',
      version: 3,
      retry_count: 0,
      max_retries: 2,
      runtime_json: '{}',
      specialist_id: 'schedule_builder_specialist',
      specialist_result: {
        specialist_id: 'schedule_builder_specialist',
        status: 'needs_approval',
        user_message: 'Черновик прогнозного schedule файла готов. Нужно ваше утверждение перед выпуском.',
        summary: 'Черновик прогнозного schedule файла прошёл проверки и готов к утверждению.',
        human_request: { kind: 'needs_approval', questions: [{ id: 'release_approval', question: 'Утвердите выпуск.' }] },
        error: null,
      },
    },
    () => ({ first: () => ({ json: {} }) }),
  );
  const hitlOut = hitlGate[0].json;
  assert.equal(hitlOut.status, 'awaiting_human');
  const runtime = JSON.parse(hitlOut.runtime_json);
  const hops = runtime.handoff_events || [];
  assert.ok(hops.some((e) => e.from_role === 'Orchestrator' && e.to_role === 'User' && /утвержден/i.test(e.summary)));
  assert.ok(hops.some((e) => e.to_role === 'Orchestrator'));
  assert.equal(runtime.last_error, null);
  assert.equal(JSON.parse(hitlOut.gate_json).kind, 'needs_approval');

  const releaseGate = await gateFn(
    {
      task_id: 'eng_release_gate',
      version: 3,
      retry_count: 0,
      max_retries: 2,
      runtime_json: '{}',
      specialist_id: 'schedule_builder_specialist',
      specialist_result: {
        specialist_id: 'schedule_builder_specialist',
        status: 'needs_approval',
        user_message: 'Черновик прогнозного schedule файла готов. Нужно ваше утверждение перед выпуском.',
        summary: 'Черновик прогнозного schedule файла прошёл проверки и готов к утверждению.',
        compact_data: { release_ready: true },
        human_request: { kind: 'needs_approval', questions: [{ id: 'release_approval', question: 'Утвердите выпуск.' }] },
        error: null,
      },
    },
    () => ({ first: () => ({ json: {} }) }),
  );
  assert.equal(JSON.parse(releaseGate[0].json.gate_json).kind, 'result_approval');

  const casConflict = await run('Classify MAS error event', {
    mas_error_event: {
      task_id: 'eng_conflict_smoke',
      safe_message: 'Stale expected_version. Reload task state and resubmit against the current version.',
      cas_snapshot: { status: 'conflict', version: 4 },
    },
  });
  assert.equal(casConflict.taxonomy_scenario, 'approval_error');
  assert.equal(casConflict.error_code, 'APPROVAL_GATE_FAILED');
  assert.notEqual(casConflict.taxonomy_scenario, 'llm_error');

  const casConflictSnapOnly = await run('Classify MAS error event', {
    mas_error_event: {
      task_id: 'eng_conflict_snap',
      cas_snapshot: { status: 'conflict', message: 'Concurrent or non-unique state update detected.' },
    },
  });
  assert.equal(casConflictSnapOnly.taxonomy_scenario, 'approval_error');

  const casConflictMessageOnly = await run('Classify MAS error event', {
    mas_error_event: {
      task_id: 'eng_conflict_msg',
      safe_message: 'Stale expected_version. Reload task state and resubmit against the current version.',
    },
  });
  assert.equal(casConflictMessageOnly.taxonomy_scenario, 'approval_error');
  assert.notEqual(casConflictMessageOnly.taxonomy_scenario, 'llm_error');

  const realErr = await prepareFn(
    {
      task_id: 'eng_err_smoke',
      status: 'retryable_error',
      version: 2,
      risk_class: 'high',
      retry_count: 1,
      max_retries: 2,
      created_at: '2026-01-01T00:00:00.000Z',
      updated_at: '2026-01-01T00:00:00.000Z',
      request_json: '{}',
      runtime_json: JSON.stringify({
        last_error: { code: 'LLM_CALL_FAILED', safe_message: 'LLM timeout' },
      }),
      plan_json: '{}',
      packet_json: '{}',
      result_json: JSON.stringify({
        status: 'retryable_error',
        user_message: 'Черновик прогнозного schedule файла готов. Нужно ваше утверждение перед выпуском.',
        summary: 'should-not-leak',
      }),
      verification_json: '{}',
      gate_json: '{}',
    },
    () => ({ first: () => ({ json: {} }) }),
  );
  assert.equal(realErr[0].json.invoke_error_handler, true);
  assert.match(String(realErr[0].json.mas_error_event.safe_message), /LLM timeout/);
  assert.doesNotMatch(
    String(realErr[0].json.mas_error_event.safe_message),
    /утверждение|Черновик/,
  );

  console.log(
    `mas-error-handler-smoke: ok (${scenarios.length} taxonomy + CASE_ID_REQUIRED + preserve inputs + hitl-not-CASE_ERROR + approve-skip)`,
  );
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
