'use strict';

// Human-facing HITL copy: findings stay machine codes; Activity/gate text is Russian.
const assert = require('node:assert/strict');
const { readWorkflow } = require('./_workflow');
const builder = readWorkflow('tnavigator-schedule-builder.workflow.json');
const orch = readWorkflow('universal-engineering-orchestrator.workflow.json');
const excel = readWorkflow('excel-extraction-agent.workflow.json');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function source(wf, name) {
  const node = wf.nodes.find((n) => n.name === name);
  assert(node && node.type === 'n8n-nodes-base.code', `missing ${name}`);
  return node.parameters.jsCode;
}

async function execute(wf, name, json, nodes = {}) {
  const lookup = (nodeName) => {
    if (!Object.prototype.hasOwnProperty.call(nodes, nodeName)) {
      throw new Error(`node not executed: ${nodeName}`);
    }
    return { first: () => ({ json: nodes[nodeName] }) };
  };
  const fn = new AsyncFunction('$json', '$', source(wf, name));
  const result = await fn(json, lookup);
  assert(Array.isArray(result) && result[0] && result[0].json);
  return result[0].json;
}

function hasCyrillic(s) {
  return /[А-Яа-яЁё]/.test(String(s || ''));
}

function askText(q) {
  return String(q?.text || q?.question || q?.message || '');
}

(async () => {
  const finding = {
    code: 'INCLUDE_NOT_FOUND',
    severity: 'error',
    file_ref: 'schedule.inc',
    path: 'timeblocks/JAN2025.inc',
    target_file_ref: 'timeblocks/JAN2025.inc',
  };
  const packet = { task_id: 'eng_hitl_1', attempt: 1, packet_findings: [finding, finding, finding] };
  const gate = await execute(
    builder,
    'Build SCHEDULE pipeline gate result',
    {
      findings: [finding, finding, finding],
      status: 'needs_input',
      summary: 'SCHEDULE pipeline requires additional evidence or a controlled decision.',
    },
    { 'Normalize SCHEDULE pipeline packet': packet },
  );
  const sr = gate.specialist_result;
  assert.equal(sr.status, 'needs_input');
  assert(hasCyrillic(sr.user_message), sr.user_message);
  assert(!/SCHEDULE pipeline requires/i.test(sr.user_message + sr.summary));
  assert.equal(sr.human_request.questions.length, 1, JSON.stringify(sr.human_request.questions));
  const qText = askText(sr.human_request.questions[0]);
  assert(hasCyrillic(qText), qText);
  assert(/JAN2025\.inc/.test(qText), qText);
  assert(/той же дате/.test(qText), qText);
  assert(!/Прикрепите этот \.inc к ответу/.test(qText), qText);
  assert(!/^INCLUDE_NOT_FOUND$/.test(qText));
  assert(!sr.human_request.questions.some((item) => askText(item) === 'INCLUDE_NOT_FOUND'));
  assert((sr.compact_data.findings || []).every((f) => f.code === 'INCLUDE_NOT_FOUND'));
  assert.equal(sr.compact_data.findings[0].code, 'INCLUDE_NOT_FOUND');
  assert.equal(sr.error.findings[0].code, 'INCLUDE_NOT_FOUND');

  const twoPaths = await execute(
    builder,
    'Build SCHEDULE pipeline gate result',
    {
      findings: [
        { ...finding, path: 'a.inc', target_file_ref: 'a.inc' },
        { ...finding, path: 'b.inc', target_file_ref: 'b.inc' },
      ],
      status: 'needs_input',
    },
    { 'Normalize SCHEDULE pipeline packet': packet },
  );
  assert.equal(twoPaths.specialist_result.human_request.questions.length, 2);

  const orchGate = await execute(
    orch,
    'Build specialist gate or error',
    {
      task_id: 'eng_hitl_1',
      version: 2,
      retry_count: 0,
      max_retries: 2,
      specialist_id: 'schedule_builder_specialist',
      runtime_json: '{}',
      specialist_result: sr,
    },
  );
  const pending = JSON.parse(orchGate.gate_json);
  assert(hasCyrillic(pending.reason), pending.reason);
  assert(!/SCHEDULE pipeline requires/i.test(pending.reason));
  assert.equal(pending.questions.length, 1);
  assert(hasCyrillic(askText(pending.questions[0])));
  assert(/JAN2025\.inc/.test(askText(pending.questions[0])));

  const englishOrch = await execute(
    orch,
    'Build specialist gate or error',
    {
      task_id: 'eng_hitl_1',
      version: 2,
      retry_count: 0,
      max_retries: 2,
      specialist_id: 'schedule_builder_specialist',
      runtime_json: '{}',
      specialist_result: {
        status: 'needs_input',
        specialist_id: 'schedule_builder_specialist',
        summary: 'SCHEDULE pipeline requires additional evidence or a controlled decision.',
        compact_data: { findings: [finding, finding, finding] },
        error: { code: 'INCLUDE_NOT_FOUND', findings: [finding] },
        human_request: {
          kind: 'needs_input',
          questions: [
            { id: 'f1', question: 'INCLUDE_NOT_FOUND' },
            { id: 'f2', question: 'INCLUDE_NOT_FOUND' },
            { id: 'f3', question: 'INCLUDE_NOT_FOUND' },
          ],
        },
      },
    },
  );
  const englishPending = JSON.parse(englishOrch.gate_json);
  assert(hasCyrillic(englishPending.reason), englishPending.reason);
  assert.equal(englishPending.questions.length, 1);
  assert(hasCyrillic(askText(englishPending.questions[0])));
  assert(/JAN2025\.inc/.test(askText(englishPending.questions[0])));
  assert(askText(englishPending.questions[0]) !== 'INCLUDE_NOT_FOUND');

  const excelGate = await execute(
    excel,
    'Build Excel adapter input gate',
    {},
    {
      'Prepare native Excel invocation': {
        adapter_gate: 'missing_file',
        specialist_packet: { task_id: 'eng_hitl_1', attempt: 1, objective: 'extract' },
      },
    },
  );
  const excelSr = excelGate.specialist_result;
  assert(hasCyrillic(excelSr.user_message || excelSr.summary), excelSr.summary);
  assert(!/Excel extraction requires/i.test(excelSr.summary + (excelSr.user_message || '')));
  assert.equal(excelSr.human_request.questions.length, 1);
  assert(hasCyrillic(askText(excelSr.human_request.questions[0])));
  assert(!/Upload the \.xlsx/i.test(JSON.stringify(excelSr.human_request)));

  const verifiedHitl = await execute(
    orch,
    'Apply verification policy',
    {
      output: {
        verdict: 'needs_input',
        summary: 'Independent verification requires additional evidence.',
        criteria: [],
        findings: [finding, finding],
        required_corrections: [],
        human_gate_reason: null,
        decision_record: {
          contract: 'decision_record',
          contract_version: '1.0',
          objective: 'Verify',
          selected_action: { action: 'needs_input', reason_codes: ['INCLUDE_NOT_FOUND'] },
        },
      },
    },
    {
      'Prepare independent verification': {
        task_id: 'eng_hitl_1',
        version: 2,
        retry_count: 0,
        max_retries: 2,
        risk_class: 'high',
        runtime_json: '{}',
        specialist_packet: { objective: 'revise', acceptance_criteria: [] },
        specialist_result: {
          specialist_id: 'schedule_builder_specialist',
          status: 'needs_input',
          self_check: { performed: true, passed: true },
          evidence: [],
          artifact_refs: [],
        },
      },
    },
  );
  const verifyPending = JSON.parse(verifiedHitl.gate_json);
  assert.equal(verifiedHitl.status, 'awaiting_human');
  assert(hasCyrillic(verifyPending.reason), verifyPending.reason);
  assert(!/additional evidence/i.test(verifyPending.reason));
  assert.equal(verifyPending.questions.length, 1);
  assert(hasCyrillic(askText(verifyPending.questions[0])));
  assert(/JAN2025\.inc/.test(askText(verifyPending.questions[0])));
  assert(askText(verifyPending.questions[0]) !== 'INCLUDE_NOT_FOUND');

  const stalled = await execute(
    orch,
    'Prepare SCHEDULE evidence retry',
    {
      task_id: 'eng_hitl_1',
      version: 2,
      retry_count: 0,
      specialist_id: 'schedule_builder_specialist',
      specialist_packet: { specialist_id: 'schedule_builder_specialist', artifact_refs: [] },
      specialist_result: {
        status: 'needs_input',
        continuation: {
          protocol: 'schedule-builder-evidence-gap-v1',
          gap_signature: 'sig1',
          source_snapshot_hash: 'none',
          evidence_gap: [{ entity: 'P1', reason: 'missing only' }],
        },
      },
      runtime_json: '{}',
    },
  );
  assert.equal(stalled.schedule_evidence_retry, false);
  assert.equal(stalled.specialist_result.error.code, 'MALFORMED_EVIDENCE_GAP');
  assert(hasCyrillic(stalled.specialist_result.user_message || stalled.specialist_result.summary));
  assert(!/^MALFORMED_EVIDENCE_GAP/.test(askText(stalled.specialist_result.human_request.questions[0])));
  assert(hasCyrillic(askText(stalled.specialist_result.human_request.questions[0])));

  console.log('HITL user copy smoke: passed');
})().catch((err) => {
  console.error(err.stack || err);
  process.exit(1);
});
