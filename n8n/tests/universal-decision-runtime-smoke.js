'use strict';

// Executes the exact exported Code-node JavaScript for Universal Planner,
// Excel adapter, independent Verifier and batched trace preparation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || '/workspace';
const read = (name) => JSON.parse(fs.readFileSync(
  path.join(workspace, 'n8n', 'workflows', 'core', name),
  'utf8',
));
const orchestrator = read('universal-engineering-orchestrator.workflow.json');
const excel = read('excel-engineering-specialist-adapter.workflow.json');
const trace = read('mas-trace-event-writer.workflow.json');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function source(workflow, name) {
  const node = workflow.nodes.find((candidate) => candidate.name === name);
  assert(node && node.type === 'n8n-nodes-base.code', `missing Code node: ${name}`);
  return node.parameters.jsCode;
}

async function execute(workflow, name, json, nodes = {}, items = [{ json }]) {
  const lookup = (nodeName) => {
    if (!Object.prototype.hasOwnProperty.call(nodes, nodeName)) {
      throw new Error(`node not executed: ${nodeName}`);
    }
    return { first: () => ({ json: nodes[nodeName] }) };
  };
  const input = {
    first: () => items[0],
    all: () => items,
  };
  const fn = new AsyncFunction('$json', '$', '$input', source(workflow, name));
  const result = await fn(json, lookup, input);
  assert(Array.isArray(result) && result.length >= 1 && result.every((item) => item.json));
  return result;
}

function decisionRecord(objective = 'Perform bounded engineering work') {
  return {
    contract: 'decision_record',
    contract_version: '1.0',
    objective,
    considered_inputs: [],
    proposed_actions: [{ action: 'delegate' }],
    selected_action: { action: 'delegate', reason_codes: ['OBSERVED_REQUEST'] },
    rejected_actions: [],
    assumptions: [],
    evidence_refs: [],
    citations: [{ document_id: 'approved-source', revision: '1' }],
    tool_call_ids: [],
    unresolved_questions: [],
    acceptance_check_results: [],
  };
}

function basePlannerRequest(requestOverrides = {}) {
  return {
    task_id: 'eng-1',
    version: 1,
    retry_count: 0,
    risk_class: 'low',
    request_json: JSON.stringify({
      objective: 'Run a governed engineering calculation',
      artifact_refs: [{ ref: 'artifact://input/1', kind: 'input', revision: '1', description: 'input' }],
      ...requestOverrides,
    }),
    history_json: '[]',
    specialist_catalog: [
      { specialist_id: 'engineering_calculation_specialist' },
      { specialist_id: 'schedule_builder_specialist' },
    ],
  };
}

function plannerOutput(overrides = {}) {
  return {
    decision: 'delegate',
    task_type: 'engineering_calculation',
    risk_class: 'low',
    reason: 'Bounded calculation is ready.',
    questions: [],
    plan: { workflow_kind: 'calculation' },
    specialist_packet: {
      contract: 'specialist_packet',
      contract_version: '1.0',
      specialist_id: 'engineering_calculation_specialist',
      objective: 'Calculate a governed quantity',
      inputs: { entity: 'WELL-1', effective_at: '2025-01-01', value: 10 },
      controls: { unit_system: 'METRIC' },
      acceptance_criteria: [{ id: 'c1', required: true, check: 'numeric result', expected: 'finite' }],
      artifact_refs: [],
    },
    decision_record: decisionRecord(),
    ...overrides,
  };
}

function specialistResult(overrides = {}) {
  return {
    contract: 'specialist_result',
    contract_version: '1.0',
    task_id: 'eng-1',
    specialist_id: 'engineering_calculation_specialist',
    attempt: 1,
    status: 'succeeded',
    summary: 'Calculation complete.',
    deliverables: [{ kind: 'calculation' }],
    artifact_refs: [{ ref: 'artifact://result/1', kind: 'result', revision: '1', description: 'result' }],
    compact_data: {},
    assumptions: [],
    warnings: [],
    evidence: [{ source_ref: 'artifact://input/1', revision: '1' }],
    self_check: { performed: true, passed: true, checks: [{ id: 'c1', passed: true }], reproducibility: 'fixture' },
    human_request: null,
    error: null,
    continuation: null,
    ...overrides,
  };
}

async function main() {
  const base = basePlannerRequest();
  const applied = (await execute(
    orchestrator,
    'Validate and apply plan',
    { output: plannerOutput() },
    { 'Prepare planner input': base },
  ))[0].json;
  const persistedPlan = JSON.parse(applied.plan_json);
  assert.equal(applied.status, 'delegated');
  assert.equal(persistedPlan.score.stage_score, 100);
  assert.equal(persistedPlan.score.decision, 'continue');
  assert.equal(persistedPlan.decision_record.contract, 'decision_record');

  const attentionModel = plannerOutput({
    decision_record: { ...decisionRecord(), citations: [], evidence_refs: [] },
  });
  const attentionBase = basePlannerRequest({ artifact_refs: [] });
  const attention = (await execute(
    orchestrator,
    'Validate and apply plan',
    { output: attentionModel },
    { 'Prepare planner input': attentionBase },
  ))[0].json;
  assert.equal(JSON.parse(attention.plan_json).score.stage_score, 80);
  assert.equal(JSON.parse(attention.plan_json).score.decision, 'attention');
  assert.equal(attention.status, 'delegated');

  const schedule = plannerOutput({
    task_type: 'schedule_build',
    plan: { workflow_kind: 'schedule' },
    specialist_packet: {
      ...plannerOutput().specialist_packet,
      specialist_id: 'schedule_builder_specialist',
      objective: 'Create WCONPROD controls',
      inputs: { schedule_request: { requested_keyword_scope: ['WCONPROD'] } },
    },
  });
  const scheduleGate = (await execute(
    orchestrator,
    'Validate and apply plan',
    { output: schedule },
    { 'Prepare planner input': basePlannerRequest({ task_type: 'schedule_build' }) },
  ))[0].json;
  const scheduleScore = JSON.parse(scheduleGate.plan_json).score;
  assert.equal(scheduleGate.status, 'awaiting_human');
  assert.equal(scheduleScore.decision, 'hitl');
  assert(JSON.parse(scheduleGate.plan_json).decision_record.selected_action.reason_codes.includes(
    'ENTITY_TEMPORAL_SCOPE_INCOMPLETE',
  ));

  const packet = {
    contract: 'specialist_packet',
    contract_version: '1.0',
    task_id: 'eng-1',
    specialist_id: 'excel_extraction_specialist',
    attempt: 1,
    objective: 'Extract governed well rate',
    inputs: { requested_fields: ['WELL', 'ORAT'] },
    controls: { correlation_id: 'corr-1' },
    acceptance_criteria: [],
    artifact_refs: [],
  };
  const excelNodes = { 'Prepare native Excel invocation': { specialist_packet: packet } };
  const native = {
    status: 'success',
    message: 'Extracted one row.',
    next_action: 'none',
    data: {
      columns: ['WELL', 'ORAT'],
      records: [{ WELL: 'WELL-1', ORAT: 100 }],
      row_count: 1,
      returned_count: 1,
      truncated: false,
      provenance: [{ table_id: 'tbl-1', sheet: 'Rates', range: 'A1:B2' }],
    },
    filters_applied: [],
    field_mapping: { WELL: 'WELL', ORAT: 'ORAT' },
    assumptions: [],
    warnings: [],
    errors: [],
    meta: { session_id: 'session-1', tool_call_ids: ['query-1'] },
  };
  const excelResult = (await execute(excel, 'Adapt native Excel result', native, excelNodes))[0].json.specialist_result;
  assert.equal(excelResult.status, 'succeeded');
  assert.equal(excelResult.compact_data.overall_score, 100);
  assert.equal(excelResult.compact_data.decision_record.contract, 'decision_record');

  const noProvenance = JSON.parse(JSON.stringify(native));
  noProvenance.data.provenance = [];
  const blockedExcel = (await execute(excel, 'Adapt native Excel result', noProvenance, excelNodes))[0].json.specialist_result;
  assert.equal(blockedExcel.status, 'needs_input');
  assert(blockedExcel.compact_data.gate_decisions[0].reason_codes.includes('EXCEL_PROVENANCE_REQUIRED'));

  const verifierBase = {
    ...base,
    max_retries: 2,
    specialist_packet: plannerOutput().specialist_packet,
    specialist_result: specialistResult(),
    result_json: JSON.stringify(specialistResult()),
    pending_human_json: '{}',
    last_error_json: '{}',
  };
  const verifierModel = {
    verdict: 'pass',
    summary: 'All criteria passed.',
    criteria: [{ id: 'c1', passed: true }],
    findings: [],
    required_corrections: [],
    human_gate_reason: null,
    decision_record: decisionRecord('Verify the governed calculation'),
  };
  const verified = (await execute(
    orchestrator,
    'Apply verification policy',
    { output: verifierModel },
    { 'Prepare independent verification': verifierBase },
  ))[0].json;
  const verification = JSON.parse(verified.verification_json);
  assert.equal(verification.score.stage_score, 100);
  assert.equal(verification.verdict, 'pass');

  const weakBase = {
    ...verifierBase,
    specialist_result: specialistResult({ artifact_refs: [], evidence: [] }),
  };
  const weak = (await execute(
    orchestrator,
    'Apply verification policy',
    { output: verifierModel },
    { 'Prepare independent verification': weakBase },
  ))[0].json;
  const weakVerification = JSON.parse(weak.verification_json);
  assert.equal(weakVerification.score.stage_score, 80);
  assert.equal(weakVerification.verdict, 'pass_with_warnings');

  const scheduleText = 'DATES\n  1 JAN 2025 /\n/\n';
  const scheduleResult = specialistResult({
    specialist_id: 'schedule_builder_specialist',
    artifact_refs: [],
    deliverables: [{ kind: 'schedule_inc_text', filename: 'schedule.inc', schedule_text: scheduleText }],
    compact_data: {
      release_ready: true,
      generated_schedule: scheduleText,
      merge_result: {
        status: 'merged',
        generated_schedule: scheduleText,
        output_package: {
          contract: 'schedule_package',
          contract_version: '1.0',
          root_path: 'schedule.inc',
          files: [{ file_ref: 'schedule.inc', text: scheduleText }],
        },
      },
      decision_record: decisionRecord('Build SCHEDULE'),
    },
  });
  const routedSchedule = (await execute(orchestrator, 'Route successful specialist handoff', {
    ...base,
    specialist_id: 'schedule_builder_specialist',
    specialist_result: scheduleResult,
    specialist_packet: { inputs: { schedule_request: { requested_keyword_scope: ['WCONPROD'] } } },
    context_json: '{}',
  }))[0].json;
  assert.equal(routedSchedule.post_specialist_route, 'verify');
  assert.equal(JSON.parse(routedSchedule.result_json).compact_data.generated_schedule, scheduleText);
  const verifierHandoff = (await execute(orchestrator, 'Prepare independent verification', routedSchedule))[0].json;
  assert.match(verifierHandoff.verifier_input, /schedule\.inc/);
  assert.match(verifierHandoff.verifier_input, /WCONPROD|DATES/);

  const releaseBase = {
    task_id: 'eng-1',
    state_found: true,
    stored_status: 'awaiting_human',
    stored_version: 3,
    expected_version: 3,
    version: 3,
    action: 'approve',
    gate_id: 'gate-release',
    requested_by: 'engineer',
    human_response: null,
    pending_human_json: JSON.stringify({ gate_id: 'gate-release', kind: 'result_approval' }),
    result_json: JSON.stringify(scheduleResult),
    verification_json: JSON.stringify({ verdict: 'pass' }),
    context_json: '{}',
    history_json: '[]',
  };
  const released = (await execute(orchestrator, 'Apply action and version guard', JSON.parse(JSON.stringify(releaseBase))))[0].json;
  assert.equal(released.status, 'completed');
  assert.equal(JSON.parse(released.result_json).release.filename, 'schedule.inc');
  assert.equal(JSON.parse(released.result_json).release.schedule_text, scheduleText);
  const releaseBlocked = (await execute(orchestrator, 'Apply action and version guard', {
    ...releaseBase,
    result_json: JSON.stringify({
      ...scheduleResult,
      compact_data: { ...scheduleResult.compact_data, generated_schedule: '', merge_result: { ...scheduleResult.compact_data.merge_result, generated_schedule: '' } },
    }),
  }))[0].json;
  assert.equal(releaseBlocked.status, 'conflict');
  assert.match(releaseBlocked.message, /inline \.INC/);

  const traceState = {
    task_id: 'eng-1',
    trace_id: 'trace-1',
    status: 'completed',
    phase: 'terminal',
    requested_by: 'engineer',
    plan_json: applied.plan_json,
    result_json: JSON.stringify(excelResult),
    verification_json: verified.verification_json,
    pending_human_json: '{}',
    last_error_json: '{}',
  };
  const preparedTrace = (await execute(orchestrator, 'Prepare final MAS trace event', traceState))[0].json;
  assert(preparedTrace.mas_trace_events.length >= 4);
  assert(preparedTrace.mas_trace_events.some((event) => event.stage === 'plan'));
  assert(preparedTrace.mas_trace_events.some((event) => event.stage === 'excel'));
  assert(preparedTrace.mas_trace_events.some((event) => event.stage === 'verify'));
  const excelTrace = preparedTrace.mas_trace_events.find((event) => event.stage === 'excel');
  assert.equal(excelTrace.tool_calls[0].tool_call_id, 'query-1');
  assert.equal(Object.prototype.hasOwnProperty.call(excelTrace.tool_calls[0], 'input_hash'), true);
  assert.equal(JSON.stringify(excelTrace.tool_calls).includes('toolInput'), false);

  const traceRows = await execute(trace, 'Normalize MAS trace event', preparedTrace);
  assert.equal(traceRows.length, preparedTrace.mas_trace_events.length);
  assert(traceRows.every((row) => !row.json.trace_row.details_json.includes('raw_prompt')));

  console.log('Universal decision runtime smoke: 14 scenarios passed');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
