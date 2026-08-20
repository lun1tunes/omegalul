'use strict';

// Executes the exact exported Code-node JavaScript for CAS — Persist Task State.
const assert = require('node:assert/strict');
const { readWorkflow } = require('./_workflow');
const workflow = readWorkflow('cas-persist-task.workflow.json');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function source(name) {
  const node = workflow.nodes.find((candidate) => candidate.name === name);
  assert(node && node.type === 'n8n-nodes-base.code', `missing Code node: ${name}`);
  return node.parameters.jsCode;
}

async function execute(name, json, nodes = {}, items = [{ json }]) {
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
  const fn = new AsyncFunction('$json', '$', '$input', source(name));
  const result = await fn(json, lookup, input);
  assert(Array.isArray(result) && result.length === 1 && result[0].json);
  return result[0].json;
}

function attempted(overrides = {}) {
  return {
    task_id: 'eng-1',
    version: 2,
    previous_version: 1,
    status: 'delegated',
    risk_class: 'high',
    request_json: '{}',
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
    should_delegate: true,
    ...overrides,
  };
}

function request(operation, stateOverrides = {}) {
  return { cas_operation: operation, attempted: attempted(stateOverrides) };
}

function tableRow(overrides = {}) {
  const row = attempted(overrides);
  delete row.previous_version;
  delete row.should_delegate;
  return row;
}

(async () => {
  const insert = await execute('Validate CAS persist request', request('insert', {
    version: 1,
    previous_version: undefined,
  }));
  assert.equal(insert.cas_request_valid, true);
  assert.equal(insert.cas_operation, 'insert');
  assert.equal(insert.cas_route, 0);
  assert.equal(insert.task_id, 'eng-1');
  assert.deepEqual(insert.cas_findings, []);

  const update = await execute('Validate CAS persist request', request('update'));
  assert.equal(update.cas_request_valid, true);
  assert.equal(update.cas_operation, 'update');
  assert.equal(update.cas_route, 1);
  assert.equal(update.previous_version, 1);

  const badOp = await execute('Validate CAS persist request', request('upsert'));
  assert.equal(badOp.cas_request_valid, false);
  assert(badOp.cas_findings.some((item) => item.code === 'CAS_OPERATION_INVALID'));

  const missingPrev = await execute('Validate CAS persist request', request('update', {
    previous_version: undefined,
  }));
  assert.equal(missingPrev.cas_request_valid, false);
  assert(missingPrev.cas_findings.some((item) => item.code === 'CAS_PREVIOUS_VERSION_REQUIRED'));

  const missingTask = await execute('Validate CAS persist request', request('insert', {
    task_id: '   ',
    version: 1,
    previous_version: undefined,
  }));
  assert.equal(missingTask.cas_request_valid, false);
  assert(missingTask.cas_findings.some((item) => item.code === 'CAS_TASK_ID_REQUIRED'));

  const missingCols = await execute('Validate CAS persist request', request('insert', {
    version: 1,
    previous_version: undefined,
    status: undefined,
    gate_json: undefined,
  }));
  assert.equal(missingCols.cas_request_valid, false);
  const missing = missingCols.cas_findings.find((item) => item.code === 'CAS_STATE_COLUMNS_MISSING');
  assert(missing);
  assert(missing.fields.includes('status'));
  assert(missing.fields.includes('gate_json'));
  assert(!missing.fields.includes('history_json'));

  const prepared = await execute('Validate CAS persist request', request('update'));
  const confirmNodes = { 'Validate CAS persist request': prepared };

  const zeroRows = await execute('Confirm CAS persist', {}, confirmNodes, []);
  assert.equal(zeroRows.cas_succeeded, false);
  assert.equal(zeroRows.status, 'conflict');
  assert.match(zeroRows.message, /Reload task status/);
  assert.equal(zeroRows.last_error.code, 'CAS_CONFLICT');
  assert.equal(zeroRows.last_error.matched_rows, 0);
  assert.equal(zeroRows.should_delegate, true);

  const echoed = await execute('Confirm CAS persist', prepared, confirmNodes, [{ json: prepared }]);
  assert.equal(echoed.cas_succeeded, false);
  assert.equal(echoed.last_error.code, 'CAS_CONFLICT');
  assert.equal(echoed.last_error.matched_rows, 0);

  const persisted = tableRow({ status: 'delegated' });
  const success = await execute('Confirm CAS persist', persisted, confirmNodes, [{ json: persisted }]);
  assert.equal(success.cas_succeeded, true);
  assert.equal(success.cas_operation, 'update');
  assert.equal(success.task_id, 'eng-1');
  assert.equal(success.version, 2);
  assert.equal(success.status, 'delegated');
  assert.equal(success.should_delegate, true);
  assert.equal(success.cas_attempted, undefined);
  assert.equal(success.cas_request_valid, undefined);

  const mixed = await execute(
    'Confirm CAS persist',
    persisted,
    confirmNodes,
    [{ json: prepared }, { json: persisted }],
  );
  assert.equal(mixed.cas_succeeded, true);
  assert.equal(mixed.status, 'delegated');
  assert.equal(mixed.should_delegate, true);

  const twoRows = await execute(
    'Confirm CAS persist',
    persisted,
    confirmNodes,
    [{ json: persisted }, { json: { ...persisted, risk_class: 'low' } }],
  );
  assert.equal(twoRows.cas_succeeded, false);
  assert.equal(twoRows.last_error.code, 'CAS_CONFLICT');
  assert.equal(twoRows.last_error.matched_rows, 2);

  const invalid = await execute('Build invalid CAS persist result', badOp);
  assert.equal(invalid.cas_succeeded, false);
  assert.equal(invalid.status, 'conflict');
  assert.equal(invalid.last_error.code, 'INVALID_CAS_REQUEST');
  assert(invalid.last_error.findings.some((item) => item.code === 'CAS_OPERATION_INVALID'));

  const confirmInvalid = await execute('Confirm CAS persist', {}, {
    'Validate CAS persist request': badOp,
  }, [{ json: persisted }]);
  assert.equal(confirmInvalid.cas_succeeded, false);
  assert.equal(confirmInvalid.last_error.code, 'CAS_CONFLICT');

  console.log('CAS persist runtime smoke: 13 scenarios passed');
})().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
