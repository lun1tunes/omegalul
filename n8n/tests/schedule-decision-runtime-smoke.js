'use strict';

// Executes the exact exported n8n Code-node JavaScript for the observable
// decision/readiness contracts. No model-authored percentage is trusted here.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || '/workspace';
const readWorkflow = (name) => JSON.parse(fs.readFileSync(
  path.join(workspace, 'n8n', 'workflows', name),
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

  console.log('SCHEDULE decision runtime smoke: 9 scenarios passed');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
