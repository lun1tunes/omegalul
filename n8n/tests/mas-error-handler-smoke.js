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

  console.log(`mas-error-handler-smoke: ok (${scenarios.length} taxonomy + CASE_ID_REQUIRED + preserve inputs)`);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
