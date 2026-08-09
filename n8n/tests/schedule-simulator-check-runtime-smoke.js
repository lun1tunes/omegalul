'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || '/workspace';
const workflow = JSON.parse(fs.readFileSync(path.join(
  workspace, 'n8n', 'workflows', 'tnavigator-schedule-simulator-check-adapter.workflow.json',
), 'utf8'));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function source(name) {
  const node = workflow.nodes.find((candidate) => candidate.name === name);
  assert(node && node.type === 'n8n-nodes-base.code', `missing Code node: ${name}`);
  return node.parameters.jsCode;
}

async function run(name, json, nodes = {}) {
  const lookup = (nodeName) => ({ first: () => ({ json: nodes[nodeName] || {} }) });
  const fn = new AsyncFunction('$json', '$', source(name));
  const rows = await fn(json, lookup);
  assert(Array.isArray(rows) && rows.length === 1 && rows[0].json);
  return rows[0].json;
}

const sha = (char) => `sha256:${char.repeat(64)}`;
function submit(overrides = {}) {
  return {
    contract: 'simulator_check_request', contract_version: '1.0', action: 'SUBMIT',
    task_id: 'task-1', trace_id: 'trace-1', request_id: 'request-1', idempotency_key: 'task-1:check:1',
    simulator_profile: { vendor: 'Rock Flow Dynamics', simulator: 'tNavigator', version: '22.2' },
    artifact: { ref: 'artifact://schedule/task-1', manifest_hash: sha('a'), kind: 'schedule-package', immutable: true },
    wait_for_terminal_seconds: 30, ...overrides,
  };
}
const codes = (result) => new Set((result.findings || []).map((finding) => finding.code));

async function main() {
  const normalized = await run('Normalize simulator check request', { simulator_check_request: submit() });
  assert.equal(normalized.status, 'accepted');
  assert.equal(normalized.action, 'SUBMIT');
  assert.equal(normalized.artifact.manifest_hash, sha('a'));

  const statusRequest = await run('Normalize simulator check request', { simulator_check_request: {
    ...submit(), action: 'STATUS', artifact: undefined, job_id: 'job:123', expected_job_version: 2,
  } });
  assert.equal(statusRequest.status, 'accepted');
  assert.equal(statusRequest.job_id, 'job:123');

  const invalidProfile = await run('Normalize simulator check request', { simulator_check_request: submit({ simulator_profile: { vendor: 'RFD', simulator: 'tNavigator', version: '26.2' } }) });
  assert(codes(invalidProfile).has('SIMULATOR_PROFILE_NOT_APPROVED'));
  const mutableArtifact = await run('Normalize simulator check request', { simulator_check_request: submit({ artifact: { ref: 'x', manifest_hash: sha('a'), kind: 'schedule-package', immutable: false } }) });
  assert(codes(mutableArtifact).has('IMMUTABLE_SCHEDULE_ARTIFACT_REQUIRED'));
  const excessiveWait = await run('Normalize simulator check request', { simulator_check_request: submit({ wait_for_terminal_seconds: 121 }) });
  assert(codes(excessiveWait).has('SIMULATOR_WAIT_LIMIT_INVALID'));

  const configured = { ...normalized, service_url: 'https://runner.corp.example/tnav', check_profile_id: 'TNAV_22_2_SCHEDULE_CHECK', allow_insecure_localhost: false, max_wait_seconds: 60 };
  const prepared = await run('Prepare simulator service request', configured);
  assert.equal(prepared.status, 'request_ready');
  assert.equal(prepared.http_request.method, 'POST');
  assert.equal(prepared.http_request.url, 'https://runner.corp.example/tnav/v1/simulator-checks');
  assert.equal(prepared.http_request.body.check_profile_id, 'TNAV_22_2_SCHEDULE_CHECK');
  assert.equal(prepared.http_request.body.artifact.ref, 'artifact://schedule/task-1');
  assert(!JSON.stringify(prepared.http_request).includes('command'));
  assert(!JSON.stringify(prepared.http_request).includes('host_path'));

  const statusPrepared = await run('Prepare simulator service request', { ...statusRequest, service_url: 'https://runner.corp.example', check_profile_id: 'TNAV_22_2_SCHEDULE_CHECK', max_wait_seconds: 60 });
  assert.equal(statusPrepared.http_request.method, 'GET');
  assert(statusPrepared.http_request.url.endsWith('/v1/simulator-checks/job%3A123'));
  assert.equal(statusPrepared.http_request.send_body, false);

  const placeholder = await run('Prepare simulator service request', { ...normalized, service_url: 'REPLACE_IN_UI', check_profile_id: 'REPLACE_IN_UI' });
  assert(codes(placeholder).has('SIMULATOR_SERVICE_CONFIGURATION_REQUIRED'));
  const insecure = await run('Prepare simulator service request', { ...normalized, service_url: 'http://runner.internal:8080', check_profile_id: 'profile' });
  assert(codes(insecure).has('SIMULATOR_SERVICE_URL_NOT_APPROVED'));
  const localhost = await run('Prepare simulator service request', { ...normalized, service_url: 'http://127.0.0.1:8080', check_profile_id: 'profile', allow_insecure_localhost: true });
  assert.equal(localhost.status, 'request_ready');

  const responseBase = {
    contract: 'simulator_check_service_result', contract_version: '1.0', request_id: 'request-1',
    job_id: 'job-1', job_version: 1, check_profile_id: 'TNAV_22_2_SCHEDULE_CHECK',
    simulator_profile: { vendor: 'Rock Flow Dynamics', simulator: 'tNavigator', version: '22.2' },
    artifact_manifest_hash: sha('a'), diagnostics: { error_count: 0, warning_count: 0, summary: 'check completed' },
    findings: [], result_artifact_refs: [],
  };
  const passed = await run('Normalize simulator service response', { ...responseBase, state: 'passed', result_hash: sha('b'), finished_at: '2026-08-09T10:00:00Z' }, { 'Prepare simulator service request': prepared });
  assert.equal(passed.status, 'passed');
  assert.equal(passed.release_gate_passed, true);
  assert.equal(passed.terminal, true);

  const queued = await run('Normalize simulator service response', { ...responseBase, state: 'queued', poll_after_seconds: 15 }, { 'Prepare simulator service request': prepared });
  assert.equal(queued.status, 'queued');
  assert.equal(queued.release_gate_passed, false);
  assert.equal(queued.poll_after_seconds, 15);

  const failed = await run('Normalize simulator service response', { ...responseBase, state: 'failed', diagnostics: { error_count: 2, warning_count: 1 }, findings: [{ code: 'TNAV_PARSE_ERROR', severity: 'error', message: 'bounded finding' }] }, { 'Prepare simulator service request': prepared });
  assert.equal(failed.status, 'failed');
  assert.equal(failed.terminal, true);
  assert.equal(failed.release_gate_passed, false);

  const falsePass = await run('Normalize simulator service response', { ...responseBase, state: 'passed', result_hash: '', diagnostics: { error_count: 1, warning_count: 0 } }, { 'Prepare simulator service request': prepared });
  assert(codes(falsePass).has('SIMULATOR_PASS_EVIDENCE_INVALID'));
  assert.equal(falsePass.release_gate_passed, false);

  const mismatch = await run('Normalize simulator service response', { ...responseBase, state: 'passed', result_hash: sha('b'), artifact_manifest_hash: sha('f') }, { 'Prepare simulator service request': prepared });
  assert(codes(mismatch).has('SIMULATOR_RESULT_ARTIFACT_MISMATCH'));
  const malformed = await run('Normalize simulator service response', { state: 'passed', job_id: '../../../etc/passwd' }, { 'Prepare simulator service request': prepared });
  assert(codes(malformed).has('SIMULATOR_SERVICE_CONTRACT_INVALID'));
  assert(codes(malformed).has('SIMULATOR_SERVICE_JOB_ID_INVALID'));

  console.log('SCHEDULE simulator check adapter smoke: 14 scenarios passed');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
