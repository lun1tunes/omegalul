'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || '/workspace';
const workflow = JSON.parse(fs.readFileSync(path.join(
  workspace, 'n8n', 'workflows', 'tnavigator-schedule-builder.workflow.json',
), 'utf8'));
const node = workflow.nodes.find((candidate) => candidate.name === 'Run deterministic SCHEDULE intake');
assert(node && node.type === 'n8n-nodes-base.code');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const execute = async (request) => {
  const fn = new AsyncFunction('$json', node.parameters.jsCode);
  const rows = await fn({ schedule_intake_request: request });
  assert(Array.isArray(rows) && rows.length === 1 && rows[0].json);
  return rows[0].json;
};

const sha = (char) => `sha256:${char.repeat(64)}`;
const citation = (keywords = ['DATES', 'WCONPROD']) => ({
  knowledge_id: 'expert-schedule-instructions', revision: '1', content_hash: 'fnv1a32:12345678',
  author: 'test-engineer', knowledge_type: 'keyword_instruction', keyword_families: keywords,
});
const rag = (keywords = ['DATES', 'WCONPROD']) => ({
  contract: 'schedule_rag_evidence', contract_version: '1.0', filters: { target_base: 'schedule_mvp', access_scope: 'petroleum-engineering' }, citations: [citation(keywords)],
  schema_catalogue: {
    contract: 'schedule_schema_catalogue', contract_version: '1.0', catalogue_hash: sha('d'), source_hash: sha('a'),
    access_scope: 'petroleum-engineering', approved: true, approved_by: 'test-engineer', approval_gate_id: 'gate-test',
    simulator_profile: { vendor: 'Rock Flow Dynamics', simulator: 'tNavigator', version: '22.2' },
  },
});
const request = (overrides = {}) => ({
  contract: 'schedule_build_request', contract_version: '1.0', task_id: 'task-1', orchestrator_task_id: 'task-1',
  trace_id: 'trace-1', expected_version: 2, idempotency_key: 'task-1:schedule-build:2',
  policy_version: 'petroleum-schedule-policy-v1', build_mode: 'CREATE', objective: 'Create forecast controls',
  simulator_profile: { vendor: 'Rock Flow Dynamics', simulator: 'tNavigator', version: '22.2', unit_system: 'METRIC' },
  model_start_date: '2020-01-01', forecast_start: '2025-01-01', forecast_end: '2030-01-01',
  requested_keyword_scope: ['DATES', 'WCONPROD'], requested_capability_scope: ['forecast_controls'],
  required_outputs: ['validated SCHEDULE package'], acceptance_criteria: [{ id: 'valid', expected: 'zero errors' }],
  source_artifact_refs: [], stage_gate_policy: { attention_threshold: 85, hitl_threshold: 70, hard_blockers: [] },
  rag_evidence: rag(), ...overrides,
});
const codes = (result) => new Set((result.findings || []).map((finding) => finding.code));

async function main() {
  const validCreate = await execute(request());
  assert.equal(validCreate.status, 'accepted');
  assert.equal(validCreate.build_mode, 'CREATE');
  assert.equal(validCreate.score.stage_score, 100);
  assert.equal(validCreate.score.decision, 'continue');

  const autoCreate = await execute(request({ build_mode: 'AUTO' }));
  assert.equal(autoCreate.status, 'accepted');
  assert.equal(autoCreate.build_mode, 'CREATE');

  const baselineText = 'DATES\n  1 JAN 2025 /\n/\n';
  const reviseFields = { build_mode: 'REVISE', baseline_schedule_text: baselineText, preservation_policy: 'preserve_unmentioned', requested_change_scope: { must_change: [], must_add: [], must_remove: [], must_preserve: [] }, requested_capability_scope: [], required_outputs: [] };
  const validRevise = await execute(request(reviseFields));
  assert.equal(validRevise.status, 'accepted');
  assert.equal(validRevise.build_mode, 'REVISE');
  assert.equal(validRevise.preservation_policy, 'preserve_unmentioned');
  const autoRevise = await execute(request({ ...reviseFields, build_mode: 'AUTO' }));
  assert.equal(autoRevise.build_mode, 'REVISE');

  const createConflict = await execute(request({ baseline_schedule_text: baselineText }));
  assert.equal(createConflict.status, 'needs_decision');
  assert(codes(createConflict).has('CREATE_BASELINE_CONFLICT_REQUIRES_DECISION'));

  const wrongUnits = await execute(request({ simulator_profile: { ...request().simulator_profile, unit_system: 'FIELD' } }));
  assert(codes(wrongUnits).has('METRIC_UNIT_SYSTEM_REQUIRED'));
  const wrongProfile = await execute(request({ simulator_profile: { ...request().simulator_profile, version: '26.2' } }));
  assert(codes(wrongProfile).has('SIMULATOR_PROFILE_NOT_APPROVED'));

  const noForecast = await execute(request({ forecast_start: '', forecast_end: '' }));
  assert(codes(noForecast).has('FORECAST_START_DATE_REQUIRED'));
  assert(codes(noForecast).has('FORECAST_END_DATE_REQUIRED'));
  const overlap = await execute(request({ history_start: '2020-01-01', history_end: '2026-01-01' }));
  assert(codes(overlap).has('HISTORY_FORECAST_OVERLAP'));
  const historyScope = await execute(request({ requested_keyword_scope: ['DATES', 'WCONHIST'], rag_evidence: rag(['DATES', 'WCONHIST']) }));
  assert(codes(historyScope).has('HISTORY_SCOPE_REQUIRES_INTERVAL'));

  const createScope = await execute(request({ requested_capability_scope: [], required_outputs: [] }));
  assert(codes(createScope).has('CREATE_CAPABILITY_SCOPE_REQUIRED'));
  assert(codes(createScope).has('CREATE_REQUIRED_OUTPUTS_REQUIRED'));
  const reviseNoBaseline = await execute(request({ build_mode: 'REVISE', requested_change_scope: null, preservation_policy: '' }));
  assert(codes(reviseNoBaseline).has('BASELINE_REQUIRED'));
  assert(codes(reviseNoBaseline).has('PRESERVATION_POLICY_REQUIRED'));
  assert(codes(reviseNoBaseline).has('REVISE_CHANGE_SCOPE_REQUIRED'));

  const citationGap = await execute(request({ rag_evidence: rag(['DATES']) }));
  assert(codes(citationGap).has('KEYWORD_INSTRUCTION_SCOPE_INCOMPLETE'));
  const stalePolicy = await execute(request({ policy_version: 'petroleum-schedule-policy-v0' }));
  assert(codes(stalePolicy).has('SCHEDULE_POLICY_VERSION_NOT_APPROVED'));
  const taskMismatch = await execute(request({ orchestrator_task_id: 'task-2' }));
  assert(codes(taskMismatch).has('SCHEDULE_BUILD_TASK_MISMATCH'));
  const inlineWithoutHash = await execute(request({ ...reviseFields, baseline_schedule_package_ref: null, baseline_schedule_text: 'DATES\n  1 JAN 2025 /\n/\n' }));
  assert(!codes(inlineWithoutHash).has('INLINE_BASELINE_SOURCE_HASH_REQUIRED'));
  assert.strictEqual(inlineWithoutHash.baseline_present, true);
  const badGatePolicy = await execute(request({ stage_gate_policy: { attention_threshold: 80, hitl_threshold: 60, hard_blockers: [] } }));
  assert(codes(badGatePolicy).has('STAGE_GATE_POLICY_INVALID'));

  console.log('SCHEDULE governed intake runtime smoke: 17 scenarios passed');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
