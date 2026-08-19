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
    specialist_catalog: [
      { specialist_id: 'engineering_calculation_specialist' },
      { specialist_id: 'schedule_builder_specialist' },
      { specialist_id: 'excel_extraction_specialist' },
    ],
  };
}

function excelPacket(overrides = {}) {
  return {
    contract: 'specialist_packet',
    contract_version: '1.0',
    specialist_id: 'excel_extraction_specialist',
    objective: 'Extract governed well rates from the workbook',
    inputs: { requested_fields: ['WELL', 'ORAT'] },
    controls: { bounded_request: true },
    acceptance_criteria: [{ id: 'c1', required: true, check: 'tabular rows', expected: 'finite' }],
    artifact_refs: [],
    ...overrides,
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
  const scheduleGatePacket = JSON.parse(scheduleGate.packet_json);
  assert.equal(scheduleGate.status, 'delegated');
  assert.equal(scheduleGatePacket.specialist_id, 'schedule_builder_specialist');
  assert.equal(JSON.stringify(scheduleGatePacket).includes('2019-06-30'), false);
  assert.equal(JSON.stringify(scheduleGatePacket).includes('shift_commissioning'), false);
  assert.equal(JSON.parse(scheduleGate.plan_json).decision_record.selected_action.reason_codes.includes(
    'ENTITY_TEMPORAL_SCOPE_INCOMPLETE',
  ), false);

  const createScratch = (await execute(
    orchestrator,
    'Validate and apply plan',
    {
      output: plannerOutput({
        decision: 'delegate',
        task_type: 'schedule_build',
        plan: { workflow_kind: 'schedule' },
        specialist_packet: {
          ...plannerOutput().specialist_packet,
          specialist_id: 'schedule_builder_specialist',
          objective: 'Create a forecast SCHEDULE from scratch',
          inputs: { schedule_request: { build_mode: 'CREATE' } },
        },
      }),
    },
    { 'Prepare planner input': basePlannerRequest({ task_type: 'schedule_build', build_mode: 'CREATE' }) },
  ))[0].json;
  const createScratchPacket = JSON.parse(createScratch.packet_json);
  assert.equal(createScratch.status, 'delegated');
  assert.equal(createScratchPacket.inputs.schedule_request.build_mode, 'CREATE');
  assert.equal(JSON.stringify(createScratchPacket).includes('2019-06-30'), false);
  assert.equal(JSON.stringify(createScratchPacket).includes('2071-01-01'), false);
  assert.equal(JSON.stringify(createScratchPacket).includes('shift_commissioning'), false);
  assert.equal(Boolean(createScratchPacket.inputs.schedule_request.simulator_profile), false);
  assert.equal(JSON.stringify(createScratchPacket).includes('Rock Flow Dynamics'), false);

  const excelQuestions = [
    { id: 'units', text: 'Какая unit system / METRIC?' },
    { id: 'scope', text: 'Confirm access_scope petroleum-engineering' },
    { id: 'wb', text: 'Which workbook .xlsx sheet has well rates / ORAT?' },
  ];
  const excelDelegated = (await execute(
    orchestrator,
    'Validate and apply plan',
    {
      output: plannerOutput({
        decision: 'delegate',
        task_type: 'excel_extraction',
        plan: { workflow_kind: 'schedule' },
        questions: excelQuestions,
        specialist_packet: excelPacket(),
      }),
    },
    { 'Prepare planner input': basePlannerRequest({ task_type: 'schedule_build' }) },
  ))[0].json;
  const excelPlan = JSON.parse(excelDelegated.plan_json);
  const excelPacketOut = JSON.parse(excelDelegated.packet_json);
  assert.equal(excelDelegated.status, 'delegated');
  assert.equal(excelDelegated.specialist_id, 'excel_extraction_specialist');
  assert.equal(excelPlan.score.raw_counts.questions, 0);
  assert.equal(excelPlan.score.raw_counts.planner_questions, 3);
  assert.equal(excelPlan.decision_record.unresolved_questions.length, 0);
  assert.equal(excelPlan.decision_record.selected_action.reason_codes.includes('PLANNER_UNRESOLVED_QUESTIONS'), false);
  assert.equal(excelPlan.decision_record.selected_action.reason_codes.includes('ENTITY_TEMPORAL_SCOPE_INCOMPLETE'), false);
  assert.equal(excelPacketOut.controls.access_scope, undefined);
  assert.equal(excelPacketOut.controls.simulator, undefined);
  assert.equal(excelPacketOut.controls.simulator_version, undefined);
  assert.equal(excelPacketOut.controls.policy_version, undefined);
  assert.equal(excelPacketOut.controls.bounded_request, true);

  const excelFlip = (await execute(
    orchestrator,
    'Validate and apply plan',
    {
      output: plannerOutput({
        decision: 'needs_input',
        reason: 'Need units and workbook.',
        questions: [
          { id: 'units', text: 'Specify unit system METRIC or FIELD' },
          { id: 'wb', text: 'Upload the .xlsx workbook' },
        ],
        specialist_packet: excelPacket(),
      }),
    },
    { 'Prepare planner input': basePlannerRequest() },
  ))[0].json;
  assert.equal(excelFlip.status, 'delegated');
  assert.equal(JSON.parse(excelFlip.packet_json).specialist_id, 'excel_extraction_specialist');
  assert.equal(JSON.parse(excelFlip.plan_json).planner_decision, 'delegate');
  assert.equal(JSON.parse(excelFlip.gate_json).questions, undefined);

  const builderBlocked = (await execute(
    orchestrator,
    'Validate and apply plan',
    {
      output: plannerOutput({
        decision: 'delegate',
        task_type: 'schedule_build',
        plan: { workflow_kind: 'schedule' },
        questions: [
          { id: 'keyword_scope', text: 'Какой requested_keyword_scope нужен для Builder?' },
        ],
        specialist_packet: {
          ...plannerOutput().specialist_packet,
          specialist_id: 'schedule_builder_specialist',
          objective: 'Create WCONPROD controls',
          inputs: {
            entity: 'WELL-1',
            effective_at: '2025-01-01',
            schedule_request: { requested_keyword_scope: ['WCONPROD'] },
          },
        },
      }),
    },
    { 'Prepare planner input': basePlannerRequest({ task_type: 'schedule_build' }) },
  ))[0].json;
  const builderBlockedPlan = JSON.parse(builderBlocked.plan_json);
  assert.equal(builderBlocked.status, 'delegated');
  assert.equal(JSON.parse(builderBlocked.packet_json).specialist_id, 'schedule_builder_specialist');
  assert.equal(builderBlockedPlan.decision_record.selected_action.reason_codes.includes(
    'PLANNER_UNRESOLVED_QUESTIONS',
  ), false);
  assert.equal(JSON.parse(builderBlocked.gate_json).questions, undefined);

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
  assert.equal(excelResult.compact_data.result_kind, 'tabular_extract');
  assert.equal(excelResult.compact_data.consumer, 'none');

  const noProvenance = JSON.parse(JSON.stringify(native));
  noProvenance.data.provenance = [];
  const genericNoProvenance = (await execute(excel, 'Adapt native Excel result', noProvenance, excelNodes))[0].json.specialist_result;
  assert.equal(genericNoProvenance.status, 'succeeded');
  assert.equal(genericNoProvenance.compact_data.gate_decisions[0].reason_codes.includes('EXCEL_PROVENANCE_REQUIRED'), false);

  const scheduleExcelNodes = { 'Prepare native Excel invocation': { specialist_packet: { ...packet, inputs: { ...packet.inputs, consumer: 'schedule_builder', capability_id: 'commissioning_date_retarget' } } } };
  const blockedExcel = (await execute(excel, 'Adapt native Excel result', noProvenance, scheduleExcelNodes))[0].json.specialist_result;
  assert.equal(blockedExcel.status, 'needs_input');
  assert(blockedExcel.compact_data.gate_decisions[0].reason_codes.includes('EXCEL_PROVENANCE_REQUIRED'));

  const priceNative = JSON.parse(JSON.stringify(native));
  priceNative.data.columns = ['Date', 'Price'];
  priceNative.data.records = [{ Date: '2024-01-01', Price: 80 }];
  priceNative.data.row_count = 1;
  priceNative.data.returned_count = 1;
  priceNative.field_mapping = { Date: 'Date', Price: 'Price' };
  const genericPriceNodes = { 'Prepare native Excel invocation': { specialist_packet: { ...packet, inputs: { requested_fields: ['Date', 'Price'] } } } };
  const genericPrice = (await execute(excel, 'Adapt native Excel result', priceNative, genericPriceNodes))[0].json.specialist_result;
  assert.equal(genericPrice.status, 'succeeded');
  assert.equal(genericPrice.compact_data.result_kind, 'tabular_extract');
  assert.equal(genericPrice.compact_data.gate_decisions[0].reason_codes.includes('EXCEL_ENTITY_IDENTITY_MISSING'), false);

  const identityNodes = { 'Prepare native Excel invocation': { specialist_packet: { ...packet, inputs: { requested_fields: ['Date', 'Price'], consumer: 'schedule_builder', capability_id: 'commissioning_date_retarget' } } } };
  const missingIdentity = (await execute(excel, 'Adapt native Excel result', priceNative, identityNodes))[0].json.specialist_result;
  assert.equal(missingIdentity.status, 'needs_input');
  assert(missingIdentity.compact_data.gate_decisions[0].reason_codes.includes('EXCEL_ENTITY_IDENTITY_MISSING'));

  const emptyNative = JSON.parse(JSON.stringify(native));
  emptyNative.data.records = [];
  emptyNative.data.row_count = 0;
  emptyNative.data.returned_count = 0;
  emptyNative.data.columns = ['Date', 'Price'];
  const emptyNodes = { 'Prepare native Excel invocation': { specialist_packet: { ...packet, inputs: { requested_fields: ['Date', 'Price'], empty_result_policy: 'expected_empty' } } } };
  const expectedEmpty = (await execute(excel, 'Adapt native Excel result', emptyNative, emptyNodes))[0].json.specialist_result;
  assert.equal(expectedEmpty.status, 'succeeded');
  assert.equal(expectedEmpty.compact_data.gate_decisions[0].reason_codes.includes('EXCEL_NO_FACT_ROWS'), false);

  const caseNative = JSON.parse(JSON.stringify(native));
  caseNative.data.columns = ['well', 'orat'];
  caseNative.data.records = [{ well: 'WELL-1', orat: 100 }];
  caseNative.field_mapping = { well: 'well', orat: 'orat' };
  const caseNodes = { 'Prepare native Excel invocation': { specialist_packet: { ...packet, inputs: { required_columns: ['WELL', 'ORAT'] } } } };
  const caseOk = (await execute(excel, 'Adapt native Excel result', caseNative, caseNodes))[0].json.specialist_result;
  assert.equal(caseOk.status, 'succeeded');
  assert.equal(caseOk.compact_data.gate_decisions[0].reason_codes.includes('EXCEL_REQUIRED_COLUMNS_INCOMPLETE'), false);

  const excelOnlyAfterFacts = (await execute(
    orchestrator,
    'Validate and apply plan',
    {
      output: plannerOutput({
        decision: 'delegate',
        task_type: 'excel_extraction',
        specialist_packet: plannerOutput().specialist_packet,
      }),
    },
    {
      'Prepare planner input': {
        ...basePlannerRequest({ objective: 'Extract oil prices from the workbook', task_type: 'excel_extraction' }),
        result_json: JSON.stringify({
          contract: 'specialist_result',
          contract_version: '1.0',
          task_id: 'eng-1',
          specialist_id: 'excel_extraction_specialist',
          attempt: 1,
          status: 'succeeded',
          summary: 'Extracted prices.',
          compact_data: {
            source_snapshot_hash: 'fnv1a32:abcd1234',
            correlation_id: 'corr-price',
            facts: [{ values: { Date: '2024-01-01', Price: 80 } }],
            columns: ['Date', 'Price'],
            row_count: 1,
          },
        }),
      },
    },
  ))[0].json;
  assert.equal(excelOnlyAfterFacts.status, 'delegated');
  assert.equal(JSON.parse(excelOnlyAfterFacts.packet_json).specialist_id, 'engineering_calculation_specialist');
  assert.equal(JSON.stringify(excelOnlyAfterFacts.packet_json).includes('entity_identity'), false);

  const excelThenSchedule = (await execute(
    orchestrator,
    'Validate and apply plan',
    {
      output: plannerOutput({
        decision: 'delegate',
        task_type: 'schedule_build',
        specialist_packet: plannerOutput().specialist_packet,
      }),
    },
    {
      'Prepare planner input': {
        ...basePlannerRequest({
          objective: 'Shift commissioning dates from the workbook',
          task_type: 'schedule_build',
          baseline_schedule_text: 'DATES\n  1 JAN 2025 /\n/\n',
        }),
        result_json: JSON.stringify({
          contract: 'specialist_result',
          contract_version: '1.0',
          task_id: 'eng-1',
          specialist_id: 'excel_extraction_specialist',
          attempt: 1,
          status: 'succeeded',
          summary: 'Extracted wells.',
          compact_data: {
            source_snapshot_hash: 'fnv1a32:abcd1234',
            correlation_id: 'corr-wells',
            facts: [{ well: '304R', values: { Скважина: '304R', 'Дата ввода': '2019-07-01' } }],
            columns: ['Скважина', 'Дата ввода'],
            row_count: 1,
          },
        }),
      },
    },
  ))[0].json;
  assert.equal(excelThenSchedule.status, 'delegated');
  assert.equal(JSON.parse(excelThenSchedule.packet_json).specialist_id, 'schedule_builder_specialist');
  assert.equal(JSON.parse(excelThenSchedule.packet_json).inputs.schedule_request.source_facts_packet.facts.length, 1);

  const verifierBase = {
    ...base,
    max_retries: 2,
    specialist_packet: plannerOutput().specialist_packet,
    specialist_result: specialistResult(),
    result_json: JSON.stringify(specialistResult()),
    gate_json: '{}',
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
    runtime_json: '{}',
  }))[0].json;
  assert.equal(routedSchedule.post_specialist_route, 'verify');
  assert.equal(JSON.parse(routedSchedule.result_json).compact_data.generated_schedule, scheduleText);
  const verifierHandoff = (await execute(orchestrator, 'Prepare independent verification', routedSchedule))[0].json;
  assert.match(verifierHandoff.verifier_input, /schedule\.inc/);
  assert.match(verifierHandoff.verifier_input, /release_ready/);
  assert.doesNotMatch(verifierHandoff.verifier_input, /1 JAN 2025/);

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
    gate_json: JSON.stringify({ gate_id: 'gate-release', kind: 'result_approval' }),
    result_json: JSON.stringify(scheduleResult),
    verification_json: JSON.stringify({ verdict: 'pass' }),
    runtime_json: '{}',
  };
  const released = (await execute(orchestrator, 'Apply action and version guard', JSON.parse(JSON.stringify(releaseBase))))[0].json;
  assert.equal(released.status, 'completed');
  assert.equal(JSON.parse(released.result_json).release.filename, 'schedule.inc');
  assert.equal(JSON.parse(released.result_json).release.schedule_text, scheduleText);

  const builderApproved = (await execute(orchestrator, 'Apply action and version guard', {
    ...releaseBase,
    gate_json: JSON.stringify({ gate_id: 'gate-release', kind: 'needs_approval' }),
    verification_json: '{}',
    result_json: JSON.stringify({
      ...scheduleResult,
      compact_data: {
        ...scheduleResult.compact_data,
        schedule_verifier_result: { verdict: 'pass', can_release: true },
      },
    }),
  }))[0].json;
  assert.equal(builderApproved.status, 'completed');
  assert.equal(builderApproved.should_plan, false);
  assert.equal(JSON.parse(builderApproved.result_json).release.schedule_text, scheduleText);

  const deliverableOnly = (await execute(orchestrator, 'Apply action and version guard', {
    ...releaseBase,
    verification_json: '{}',
    result_json: JSON.stringify({
      ...scheduleResult,
      compact_data: {
        release_ready: true,
        merge_result: {
          status: 'merged',
          output_package: {
            contract: 'schedule_package',
            contract_version: '1.0',
            root_path: 'schedule.inc',
          },
        },
        schedule_verifier_result: { verdict: 'pass', can_release: true },
      },
    }),
  }))[0].json;
  assert.equal(deliverableOnly.status, 'completed');
  assert.equal(JSON.parse(deliverableOnly.result_json).release.schedule_text, scheduleText);

  const correctionApprove = (await execute(orchestrator, 'Apply action and version guard', {
    ...releaseBase,
    gate_json: JSON.stringify({ gate_id: 'gate-release', kind: 'needs_approval' }),
    result_json: JSON.stringify({
      ...scheduleResult,
      compact_data: { ...scheduleResult.compact_data, release_ready: false },
    }),
  }))[0].json;
  assert.equal(correctionApprove.status, 'planning');
  assert.equal(correctionApprove.should_plan, true);

  const releaseBlocked = (await execute(orchestrator, 'Apply action and version guard', {
    ...releaseBase,
    result_json: JSON.stringify({
      ...scheduleResult,
      deliverables: [{ kind: 'schedule_inc_text', filename: 'schedule.inc', schedule_text: '' }],
      compact_data: { ...scheduleResult.compact_data, generated_schedule: '', merge_result: { ...scheduleResult.compact_data.merge_result, generated_schedule: '' } },
    }),
  }))[0].json;
  assert.equal(releaseBlocked.status, 'conflict');
  assert.match(releaseBlocked.message, /inline \.INC/);

  const replyOnApproval = (await execute(orchestrator, 'Apply action and version guard', {
    ...releaseBase,
    action: 'reply',
    human_response: { text: 'INCLUDE во вложении, продолжайте.' },
    gate_json: JSON.stringify({ gate_id: 'gate-release', kind: 'needs_approval' }),
    packet_json: '{}',
    result_json: JSON.stringify({
      ...scheduleResult,
      compact_data: { ...scheduleResult.compact_data, release_ready: false },
    }),
  }))[0].json;
  assert.equal(replyOnApproval.status, 'planning');
  assert.equal(replyOnApproval.should_plan, true);

  const replyOnResultApproval = (await execute(orchestrator, 'Apply action and version guard', {
    ...releaseBase,
    action: 'reply',
    human_response: { text: 'Не выпускайте: верните ORAT из Excel.' },
    gate_json: JSON.stringify({ gate_id: 'gate-release', kind: 'result_approval' }),
    packet_json: '{}',
  }))[0].json;
  assert.equal(replyOnResultApproval.status, 'planning');
  assert.equal(replyOnResultApproval.should_plan, true);
  assert.equal(replyOnResultApproval.status === 'completed', false);
  const replyRuntime = JSON.parse(replyOnResultApproval.runtime_json);
  assert.equal(replyRuntime.last_hitl_gate.kind, 'result_approval');
  assert.equal(replyRuntime.last_hitl_gate.release_ready, true);

  const replyStartBinaries = (await execute(orchestrator, 'Apply action and version guard', {
    ...releaseBase,
    action: 'reply',
    human_response: { text: 'принято' },
    hitl_new_attachments: false,
    binary: { schedule_files: { fileName: 'root.inc' } },
    gate_json: JSON.stringify({ gate_id: 'gate-release', kind: 'result_approval' }),
  }))[0].json;
  assert.equal(replyStartBinaries.should_plan, true);
  assert.equal(JSON.parse(replyStartBinaries.runtime_json).hitl_attachments_pending, false);

  const replyFormFalse = (await execute(orchestrator, 'Apply action and version guard', {
    ...releaseBase,
    action: 'reply',
    human_response: { text: 'принято' },
    hitl_new_attachments: 'False',
    binary: { file: { fileName: 'dates.xlsx' }, schedule_files: { fileName: 'root.inc' } },
    gate_json: JSON.stringify({ gate_id: 'gate-release', kind: 'result_approval' }),
  }))[0].json;
  assert.equal(JSON.parse(replyFormFalse.runtime_json).hitl_attachments_pending, false);

  const replyNewHitlFile = (await execute(orchestrator, 'Apply action and version guard', {
    ...releaseBase,
    action: 'reply',
    human_response: { text: 'прикладываю недостающий INCLUDE' },
    hitl_new_attachments: true,
    binary: { schedule_files: { fileName: 'WELLS.INC' } },
    gate_json: JSON.stringify({ gate_id: 'gate-release', kind: 'needs_input' }),
  }))[0].json;
  assert.equal(JSON.parse(replyNewHitlFile.runtime_json).hitl_attachments_pending, true);

  const plannerIn = (await execute(orchestrator, 'Prepare planner input', {
    task_id: 'eng-1',
    retry_count: 0,
    request_json: JSON.stringify({
      objective: 'Revise forecast schedule',
      hitl_reply_text: 'принято, но проверь DATES',
    }),
    runtime_json: JSON.stringify({
      last_hitl_gate: { kind: 'result_approval', gate_id: 'gate-release', release_ready: true },
      human_response: { text: 'принято, но проверь DATES' },
    }),
    result_json: JSON.stringify(scheduleResult),
    verification_json: JSON.stringify({ verdict: 'pass' }),
    plan_json: '{}',
  }))[0].json;
  const plannerPayload = JSON.parse(plannerIn.planner_input);
  assert.equal(plannerPayload.active_human_gate.kind, 'result_approval');
  assert.equal(plannerPayload.active_human_gate.release_ready, true);
  assert.match(plannerPayload.latest_human_reply.text, /DATES/);
  assert.match(plannerPayload.instruction, /human_intent/);
  assert.doesNotMatch(plannerIn.planner_input, /1 JAN 2025/);
  assert.equal(plannerPayload.previous_specialist_result.compact_data.release_ready, true);
  assert.equal(plannerPayload.previous_specialist_result.deliverables[0].schedule_text, undefined);

  const releaseReadySchedule = specialistResult({
    specialist_id: 'schedule_builder_specialist',
    status: 'needs_approval',
    artifact_refs: [{ ref: 'artifact://result/1', kind: 'result', revision: '1', description: 'result' }],
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
  const normalizedRelease = (await execute(
    orchestrator,
    'Normalize specialist result',
    { specialist_result: releaseReadySchedule },
    {
      'Resolve allowlisted specialist': {
        task_id: 'eng-1',
        specialist_id: 'schedule_builder_specialist',
        retry_count: 0,
        specialist_packet: { attempt: 1 },
      },
    },
  ))[0].json;
  assert.equal(normalizedRelease.specialist_requires_verification, true);
  assert.equal(normalizedRelease.specialist_direct_gate, false);

  const schedulePacket = {
    contract: 'specialist_packet',
    contract_version: '1.0',
    specialist_id: 'schedule_builder_specialist',
    objective: 'Revise forecast schedule',
    inputs: { schedule_request: { requested_keyword_scope: ['WCONPROD', 'DATES'] } },
    controls: { unit_system: 'METRIC' },
    acceptance_criteria: [{ id: 'c1', required: true, check: 'ORAT preserved', expected: 'finite' }],
    artifact_refs: [{ ref: 'artifact://input/1', kind: 'input', revision: '1', description: 'input' }],
  };
  const releasePlanBase = {
    ...basePlannerRequest({
      objective: 'Revise forecast schedule',
      task_type: 'schedule_build',
      hitl_reply_text: 'принято',
      artifact_refs: [{ ref: 'artifact://input/1', kind: 'input', revision: '1', description: 'input' }],
    }),
    result_json: JSON.stringify(scheduleResult),
    verification_json: JSON.stringify({ verdict: 'pass' }),
    packet_json: JSON.stringify(schedulePacket),
    runtime_json: JSON.stringify({
      last_hitl_gate: { kind: 'result_approval', gate_id: 'gate-release', release_ready: true },
      human_response: { text: 'принято' },
    }),
  };
  const acceptRelease = (await execute(
    orchestrator,
    'Validate and apply plan',
    {
      output: plannerOutput({
        human_intent: 'accept_release',
        decision: 'needs_approval',
        task_type: 'schedule_build',
        specialist_packet: schedulePacket,
      }),
    },
    { 'Prepare planner input': releasePlanBase },
  ))[0].json;
  assert.equal(acceptRelease.status, 'completed');
  assert.equal(JSON.parse(acceptRelease.result_json).release.schedule_text, scheduleText);

  const acceptBlockedByNewFile = (await execute(
    orchestrator,
    'Validate and apply plan',
    {
      output: plannerOutput({
        human_intent: 'accept_release',
        decision: 'needs_approval',
        task_type: 'schedule_build',
        specialist_packet: schedulePacket,
      }),
    },
    {
      'Prepare planner input': {
        ...releasePlanBase,
        runtime_json: JSON.stringify({
          last_hitl_gate: { kind: 'result_approval', gate_id: 'gate-release', release_ready: true },
          human_response: { text: 'принято' },
          hitl_attachments_pending: true,
        }),
      },
    },
  ))[0].json;
  assert.notEqual(acceptBlockedByNewFile.status, 'completed');

  const includeAccept = (await execute(
    orchestrator,
    'Validate and apply plan',
    {
      output: plannerOutput({
        human_intent: 'accept_release',
        decision: 'needs_input',
        task_type: 'schedule_build',
        specialist_packet: schedulePacket,
      }),
    },
    {
      'Prepare planner input': {
        ...basePlannerRequest({
          objective: 'Revise forecast schedule',
          task_type: 'schedule_build',
          hitl_reply_text: 'принято',
        }),
        result_json: JSON.stringify({
          specialist_id: 'schedule_builder_specialist',
          compact_data: { release_ready: false, failed_stage: 'baseline_analysis' },
        }),
        packet_json: JSON.stringify(schedulePacket),
        runtime_json: JSON.stringify({
          last_hitl_gate: { kind: 'needs_input', gate_id: 'gate-include', release_ready: false },
          human_response: { text: 'принято' },
        }),
      },
    },
  ))[0].json;
  assert.notEqual(includeAccept.status, 'completed');

  const reviseDraft = (await execute(
    orchestrator,
    'Validate and apply plan',
    {
      output: plannerOutput({
        human_intent: 'revise',
        decision: 'delegate',
        task_type: 'schedule_build',
        reason: 'Engineer asked to rework ORAT.',
        specialist_packet: schedulePacket,
      }),
    },
    {
      'Prepare planner input': {
        ...releasePlanBase,
        request_json: JSON.stringify({
          objective: 'Revise forecast schedule',
          task_type: 'schedule_build',
          hitl_reply_text: 'неправильно. доработай ORAT',
          artifact_refs: [{ ref: 'artifact://input/1', kind: 'input', revision: '1', description: 'input' }],
        }),
        runtime_json: JSON.stringify({
          last_hitl_gate: { kind: 'result_approval', gate_id: 'gate-release', release_ready: true },
          human_response: { text: 'неправильно. доработай ORAT' },
        }),
      },
    },
  ))[0].json;
  assert.equal(reviseDraft.status, 'delegated');
  assert.notEqual(reviseDraft.status, 'completed');
  const revisePacket = JSON.parse(reviseDraft.packet_json);
  assert.match(String(revisePacket.inputs.human_instruction || ''), /ORAT/);
  assert.match(String(revisePacket.inputs.schedule_request.human_instruction || ''), /ORAT/);

  const traceState = {
    task_id: 'eng-1',
    trace_id: 'trace-1',
    status: 'completed',
    requested_by: 'engineer',
    plan_json: applied.plan_json,
    result_json: JSON.stringify(excelResult),
    verification_json: verified.verification_json,
    gate_json: '{}',
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

  console.log('Universal decision runtime smoke: 41 scenarios passed');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
