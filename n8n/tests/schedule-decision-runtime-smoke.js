'use strict';

// Executes the exact exported n8n Code-node JavaScript for the observable
// decision/readiness contracts. No model-authored percentage is trusted here.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || '/workspace';
const readWorkflow = (name) => JSON.parse(fs.readFileSync(
  path.join(workspace, 'n8n', 'workflows', 'core', name),
  'utf8',
));
const plannerWorkflow = readWorkflow('tnavigator-schedule-builder.workflow.json');
const builderWorkflow = readWorkflow('tnavigator-schedule-builder.workflow.json');
const traceWorkflow = readWorkflow('mas-trace-event-writer.workflow.json');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function source(workflow, name) {
  const node = workflow.nodes.find((candidate) => candidate.name === name);
  assert(node && node.type === 'n8n-nodes-base.code', `missing Code node: ${name}`);
  return node.parameters.jsCode;
}

async function run(workflow, name, json, nodes = {}) {
  const lookup = (nodeName) => {
    if (!Object.prototype.hasOwnProperty.call(nodes, nodeName)) {
      throw new Error(`node not executed: ${nodeName}`);
    }
    return { first: () => ({ json: nodes[nodeName] }) };
  };
  const fn = new AsyncFunction('$json', '$', source(workflow, name));
  const result = await fn(json, lookup);
  assert(Array.isArray(result) && result.length === 1 && result[0].json);
  return result[0].json;
}

function decisionRecord(objective = 'Build the requested SCHEDULE controls') {
  return {
    contract: 'decision_record',
    contract_version: '1.0',
    objective,
    considered_inputs: [],
    proposed_actions: [],
    selected_action: { action: 'proposed', reason_codes: ['OBSERVED_SCOPE'] },
    rejected_actions: [],
    assumptions: [],
    evidence_refs: [],
    citations: [],
    tool_call_ids: [],
    unresolved_questions: [],
    acceptance_check_results: [],
  };
}

function citation(keywords = ['DATES', 'WCONPROD']) {
  return {
    document_id: 'tnav-22.2',
    document_revision: '22.2',
    source_hash: `sha256:${'a'.repeat(64)}`,
    page: '123',
    heading: 'SCHEDULE',
    keyword_families: keywords,
  };
}

function plan(overrides = {}) {
  return {
    status: 'proposed',
    build_mode: 'CREATE',
    keyword_scope: ['DATES', 'WCONPROD'],
    stages: [{
      stage_id: 'controls',
      capability: 'schedule_controls',
      keywords: ['DATES', 'WCONPROD'],
      required_evidence: [{ id: 'rates', required: true, status: 'supported', source_ref: 'fact:rates' }],
      dependencies: [],
      entity_scope: ['WELL-1'],
      temporal_scope: ['2025-01-01'],
      acceptance_checks: [{ check: 'source_map_complete' }],
    }],
    questions: [],
    preservation_policy: 'not_applicable',
    rationale: 'Requested controls are covered by a bounded stage.',
    decision_record: decisionRecord(),
    ...overrides,
  };
}

function plannerRequest(evidence = [{
  kind: 'schedule_rag_evidence',
  value: { citations: [citation()], results: [citation()] },
}]) {
  return {
    planner_request: {
      task: { objective: 'Build the requested SCHEDULE controls' },
      build_mode: 'CREATE',
      requested_keyword_scope: ['DATES', 'WCONPROD'],
      evidence,
    },
  };
}

function builderWork(overrides = {}) {
  return {
    status: 'succeeded',
    summary: 'Drafted one governed control.',
    build_mode: 'CREATE',
    generated_schedule: 'WCONPROD\n WELL-1 OPEN ORAT 100 /\n',
    changes: [{ operation: 'ADD', keyword: 'WCONPROD', rendered_text: 'WCONPROD' }],
    requirements_matrix: [{ keyword: 'WCONPROD', required: true, status: 'supported', source_ref: 'fact:rate' }],
    source_map: [{ keyword: 'WCONPROD', source_ref: 'fact:rate' }],
    completeness_report: { complete: true },
    preservation_report: { policy: 'not_applicable' },
    evidence_gap: [],
    deliverables: [],
    artifact_refs: [],
    assumptions: [],
    warnings: [],
    evidence: [{ kind: 'fact', ref: 'fact:rate' }],
    self_check: { performed: true, passed: true, checks: [], reproducibility: 'fixture' },
    human_request: null,
    error: null,
    continuation: null,
    decision_record: decisionRecord(),
    ...overrides,
  };
}

async function main() {
  const plannerNodes = { 'Prepare SCHEDULE planner input': plannerRequest() };
  const fullPlan = await run(plannerWorkflow, 'Validate SCHEDULE pipeline plan', { output: plan() }, plannerNodes);
  assert.equal(fullPlan.score.stage_score, 100);
  assert.equal(fullPlan.score.decision, 'continue');
  assert.equal(fullPlan.decision_record.contract, 'decision_record');

  const attention = await run(
    plannerWorkflow,
    'Validate SCHEDULE pipeline plan',
    { output: plan() },
    { 'Prepare SCHEDULE planner input': plannerRequest([]) },
  );
  assert.equal(attention.score.stage_score, 80);
  assert.equal(attention.score.decision, 'attention');

  const missingEvidence = plan({
    stages: [{
      ...plan().stages[0],
      required_evidence: [{ id: 'rates', required: true, status: 'missing' }],
    }],
    questions: [{ id: 'rate', question: 'Provide governed well rates.' }],
  });
  const blockedPlan = await run(
    plannerWorkflow,
    'Validate SCHEDULE pipeline plan',
    { output: missingEvidence },
    plannerNodes,
  );
  assert.equal(blockedPlan.score.decision, 'hitl');
  assert(blockedPlan.hard_blockers.includes('PLAN_MANDATORY_EVIDENCE_GAP'));

  const cyclic = plan({
    stages: [
      { ...plan().stages[0], stage_id: 'a', dependencies: ['b'] },
      { ...plan().stages[0], stage_id: 'b', dependencies: ['a'] },
    ],
  });
  const blockedCycle = await run(plannerWorkflow, 'Validate SCHEDULE pipeline plan', { output: cyclic }, plannerNodes);
  assert.equal(blockedCycle.score.decision, 'hitl');
  assert(blockedCycle.hard_blockers.includes('PLAN_DEPENDENCY_CYCLE'));

  const root = {
    packet: { objective: 'Build WCONPROD' },
    request: {
      objective: 'Build WCONPROD',
      source_facts_packet: { source_snapshot_hash: 'sha256:facts', facts: [{ fact_id: 'rate' }], conflicts: [] },
      rag_evidence: { citations: [citation(['WCONPROD'])], results: [citation(['WCONPROD'])] },
    },
  };
  const builderNodes = {
    'Normalize SCHEDULE pipeline packet': root,
    'Validate SCHEDULE pipeline plan': { contract: 'schedule_plan', build_mode: 'CREATE', keyword_scope: ['WCONPROD'] },
  };
  const built = await run(builderWorkflow, 'Validate SCHEDULE builder stage', { output: builderWork() }, builderNodes);
  assert.equal(built.score.stage_score, 100);
  assert.equal(built.score.decision, 'continue');

  const missingMap = await run(
    builderWorkflow,
    'Validate SCHEDULE builder stage',
    { output: builderWork({ source_map: [] }) },
    builderNodes,
  );
  assert.equal(missingMap.score.decision, 'hitl');
  assert(missingMap.hard_blockers.includes('SOURCE_MAP_INCOMPLETE'));

  const removeWork = builderWork({
    build_mode: 'REVISE',
    changes: [{ operation: 'REMOVE', keyword: 'WCONPROD', target_node_id: 'schedule.inc:42', expected_raw_hash: `sha256:${'d'.repeat(64)}` }],
    preservation_report: { policy: 'preserve_unmentioned' },
  });
  const removeNodes = {
    'Normalize SCHEDULE pipeline packet': {
      ...root,
      request: { ...root.request, explicit_remove_approved: true },
    },
    'Validate SCHEDULE pipeline plan': { contract: 'schedule_plan', build_mode: 'REVISE', keyword_scope: ['WCONPROD'] },
    'Query targeted baseline records': {
      contract: 'baseline_inventory_query_result', contract_version: '1.0', status: 'succeeded',
      records: [{ target_node_id: 'schedule.inc:42', expected_raw_hash: `sha256:${'d'.repeat(64)}` }],
    },
  };
  const unaccountableRemove = await run(
    builderWorkflow,
    'Validate SCHEDULE builder stage',
    { output: removeWork },
    removeNodes,
  );
  assert(unaccountableRemove.hard_blockers.includes('REMOVE_REQUIRES_ACCOUNTABLE_APPROVAL'));

  removeNodes['Normalize SCHEDULE pipeline packet'].request.remove_approval = {
    actor: 'reservoir-engineer',
    reason: 'Approved conceptual shutdown',
    gate_id: 'gate-123',
  };
  const accountableRemove = await run(
    builderWorkflow,
    'Validate SCHEDULE builder stage',
    { output: removeWork },
    removeNodes,
  );
  assert.equal(accountableRemove.hard_blockers.length, 0);
  assert.equal(accountableRemove.score.decision, 'continue');

  const assembleNodes = {
    'Normalize SCHEDULE pipeline packet': { task_id: 'eng-1', attempt: 1 },
    'Validate SCHEDULE builder stage': {
      status: 'succeeded',
      build_mode: 'CREATE',
      score: { stage_score: 100, decision: 'continue' },
      decision_record: decisionRecord(),
      artifact_refs: [],
      requirements_matrix: [],
      source_map: [],
      completeness_report: { complete: true },
      assumptions: [],
      warnings: [],
      evidence: [],
      agent_tool_trace: [],
    },
    'Render typed SCHEDULE IR deterministically': { status: 'rendered', hard_blockers: [], catalogue_hash: 'h' },
    'Apply commissioning timeline revise': {
      status: 'merged',
      generated_schedule: 'DATES\n  1 JAN 2025 /\n/\n',
      output_package: {
        contract: 'schedule_package',
        contract_version: '1.0',
        root_path: 'schedule.inc',
        package_hash: 'abc',
        files: [{ file_ref: 'schedule.inc', text: 'DATES\n  1 JAN 2025 /\n/\n' }],
      },
    },
    'Validate merged SCHEDULE package': {
      status: 'valid',
      score: { stage_score: 100 },
      findings: [],
      hard_blockers: [],
    },
    'Validate SCHEDULE pipeline plan': {
      status: 'proposed',
      score: { decision: 'continue', stage_score: 100 },
      decision_record: decisionRecord(),
    },
  };
  const releaseReady = await run(
    builderWorkflow,
    'Build release-ready specialist result',
    {
      verdict: 'pass',
      can_release: true,
      score: { stage_score: 100 },
      findings: [],
      required_corrections: [],
    },
    assembleNodes,
  );
  assert.equal(releaseReady.specialist_result.status, 'needs_approval');
  assert.equal(releaseReady.specialist_result.compact_data.release_ready, true);
  assert.equal(releaseReady.specialist_result.human_request.kind, 'needs_approval');
  assert.equal(releaseReady.specialist_result.human_request.questions[0].id, 'release_approval');
  assert.equal(typeof releaseReady.specialist_result.human_request.questions[0].question, 'string');

  const notReady = await run(
    builderWorkflow,
    'Build release-ready specialist result',
    {
      verdict: 'reject',
      can_release: false,
      score: { stage_score: 40 },
      findings: [{ code: 'DATES_TERMINATOR', severity: 'error' }],
      required_corrections: ['Закройте блок DATES символом /'],
    },
    assembleNodes,
  );
  assert.equal(notReady.specialist_result.status, 'needs_input');
  assert.equal(notReady.specialist_result.compact_data.release_ready, false);
  assert.equal(notReady.specialist_result.human_request.kind, 'needs_input');
  assert.equal(notReady.specialist_result.human_request.questions[0].question, 'Закройте блок DATES символом /');
  assert.equal(notReady.specialist_result.human_request.questions[0].required, true);

  const emptyCorrections = await run(
    builderWorkflow,
    'Build release-ready specialist result',
    {
      verdict: 'reject',
      can_release: false,
      score: { stage_score: 40 },
      findings: [],
      required_corrections: [],
    },
    assembleNodes,
  );
  assert.equal(emptyCorrections.specialist_result.status, 'needs_input');
  assert.equal(emptyCorrections.specialist_result.human_request.questions[0].id, 'schedule_not_release_ready');

  const trace = await run(traceWorkflow, 'Normalize MAS trace event', {
    mas_trace_event: {
      trace_id: 'trace-1',
      task_id: 'task-1',
      stage: 'builder',
      summary: 'Decision recorded',
      decision_record: {
        ...decisionRecord(),
        considered_inputs: [{ kind: 'source', source_hash: 'sha256:a', raw_prompt: 'do not store', token: 'do not store' }],
        citations: [{ document_id: 'manual', page: '1', content: 'licensed text must not enter trace' }],
      },
    },
  });
  assert.equal(trace.trace_event.decision_record.contract, 'decision_record');
  assert(!trace.trace_row.details_json.includes('do not store'));
  assert(!trace.trace_row.details_json.includes('licensed text'));
  assert(!trace.trace_row.details_json.includes('raw_prompt'));

  const wellDateFacts = [{
    fact_id: 'w1',
    well: '1601',
    value: '2025-01-01',
    values: { Скважина: '1601', 'Дата ввода': '2025-01-01' },
  }];
  const reviseFactsNodes = (requestExtra = {}) => ({
    'Normalize SCHEDULE pipeline packet': {
      packet: { objective: 'Revise schedule' },
      request: {
        objective: 'Revise schedule',
        build_mode: 'REVISE',
        source_facts_packet: { source_snapshot_hash: 'sha256:facts', facts: wellDateFacts, conflicts: [] },
        rag_evidence: { citations: [citation(['WCONPROD'])], results: [citation(['WCONPROD'])] },
        ...requestExtra,
      },
    },
    'Validate SCHEDULE pipeline plan': { contract: 'schedule_plan', build_mode: 'REVISE', keyword_scope: ['WCONPROD'] },
    'Query targeted baseline records': {
      contract: 'baseline_inventory_query_result',
      contract_version: '1.0',
      status: 'succeeded',
      records: [],
    },
  });
  const emptyReviseWork = builderWork({
    build_mode: 'REVISE',
    ir_events: [],
    changes: [],
    source_map: [{ keyword: 'WCONPROD', source_ref: 'fact:w1', entity: '1601' }],
    preservation_report: { policy: 'preserve_unmentioned' },
  });
  const noCap = await run(builderWorkflow, 'Validate SCHEDULE builder stage', { output: emptyReviseWork }, reviseFactsNodes());
  assert.equal(noCap.hard_blockers.includes('COMMISSIONING_CAPABILITY_REQUIRED'), false);
  assert.equal((noCap.error?.findings || []).some((f) => f.code === 'COMMISSIONING_TIMELINE_PATH'), false);

  const withCap = await run(
    builderWorkflow,
    'Validate SCHEDULE builder stage',
    { output: emptyReviseWork },
    reviseFactsNodes({ capability_id: 'commissioning_date_retarget' }),
  );
  assert.ok((withCap.error?.findings || []).some((f) => f.code === 'COMMISSIONING_TIMELINE_PATH'));
  assert.equal(withCap.hard_blockers.includes('COMMISSIONING_CAPABILITY_REQUIRED'), false);
  assert.equal((withCap.source_map || []).some((s) => s.path === 'timeline_commissioning_revise'), true);

  const groupKwNodes = (requestExtra = {}, scope = ['WELSPECS', 'GRUPTREE', 'GCONPROD', 'WECON']) => ({
    'Normalize SCHEDULE pipeline packet': {
      packet: { objective: 'Group rebind' },
      request: {
        objective: 'Group rebind',
        build_mode: 'REVISE',
        source_facts_packet: { facts: [], conflicts: [] },
        rag_evidence: { citations: [citation(scope)], results: [citation(scope)] },
        ...requestExtra,
      },
    },
    'Validate SCHEDULE pipeline plan': { contract: 'schedule_plan', build_mode: 'REVISE', keyword_scope: scope },
    'Query targeted baseline records': {
      contract: 'baseline_inventory_query_result',
      contract_version: '1.0',
      status: 'succeeded',
      records: [],
    },
  });
  const groupIrWork = builderWork({
    build_mode: 'REVISE',
    ir_events: [{ operation: 'ADD', keyword: 'WELSPECS', entity: '1601', fields: { WELL: '1601' } }],
    changes: [],
    source_map: [{ keyword: 'WELSPECS', source_ref: 'plan', entity: '1601' }],
    requirements_matrix: [{ keyword: 'WELSPECS', required: true, status: 'supported', source_ref: 'plan' }],
    preservation_report: { policy: 'preserve_unmentioned' },
  });
  const groupKwOnly = await run(builderWorkflow, 'Validate SCHEDULE builder stage', { output: groupIrWork }, groupKwNodes());
  assert.equal((groupKwOnly.error?.findings || []).some((f) => f.code === 'GROUP_REBIND_TIMELINE_PATH'), false);

  const groupSpec = {
    wells: ['1601', '1602'],
    parent_group: 'DKS',
    parent_of_parent: 'FIELD',
    well_groups: { 1601: 'G1601', 1602: 'G1602' },
    gas_rate: 200000,
    control: 'GRAT',
  };
  const groupExplicit = await run(
    builderWorkflow,
    'Validate SCHEDULE builder stage',
    { output: groupIrWork },
    groupKwNodes({ capability_id: 'group_membership_rebind', group_rebind: groupSpec }),
  );
  assert.ok((groupExplicit.error?.findings || []).some((f) => f.code === 'GROUP_REBIND_TIMELINE_PATH'));
  assert.equal((groupExplicit.source_map || []).some((s) => s.path === 'timeline_group_revise'), true);

  const emptyPlanRevise = plan({
    build_mode: 'REVISE',
    stages: [],
    keyword_scope: ['DATES', 'WCONPROD'],
    preservation_policy: 'preserve_unmentioned',
  });
  const planFactsNoCap = await run(
    plannerWorkflow,
    'Validate SCHEDULE pipeline plan',
    { output: emptyPlanRevise },
    {
      'Prepare SCHEDULE planner input': {
        planner_request: {
          task: { objective: 'Revise dates' },
          build_mode: 'REVISE',
          requested_keyword_scope: ['DATES', 'WCONPROD'],
          source_facts_packet: { facts: wellDateFacts },
          evidence: [{ kind: 'source_facts_packet', value: { facts: wellDateFacts } }],
        },
      },
    },
  );
  assert.ok(planFactsNoCap.hard_blockers.includes('PLAN_STAGES_MISSING'));
  assert.equal((planFactsNoCap.stages || []).some((s) => s.stage_id === 'commissioning_dates'), false);

  const planFactsWithCap = await run(
    plannerWorkflow,
    'Validate SCHEDULE pipeline plan',
    { output: emptyPlanRevise },
    {
      'Prepare SCHEDULE planner input': {
        planner_request: {
          task: { objective: 'Revise dates' },
          build_mode: 'REVISE',
          capability_id: 'commissioning_date_retarget',
          requested_keyword_scope: ['DATES', 'WCONPROD'],
          source_facts_packet: { facts: wellDateFacts },
          evidence: [{ kind: 'source_facts_packet', value: { facts: wellDateFacts } }],
        },
      },
    },
  );
  assert.ok((planFactsWithCap.stages || []).some((s) => s.stage_id === 'commissioning_dates'));
  assert.equal(planFactsWithCap.hard_blockers.includes('PLAN_STAGES_MISSING'), false);

  console.log('SCHEDULE decision runtime smoke: passed');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
