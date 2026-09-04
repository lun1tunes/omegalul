'use strict';

/**
 * Control Plane Proxy: schema+clear wipe gate, and list_cases Format
 * must keep every Postgres row (n8n independently batching assigns
 * pairedItem 0..N even when Normalize emitted one item).
 */
const assert = require('node:assert/strict');
const { readWorkflow } = require('./_workflow');

const workflow = readWorkflow('mas-control-plane-proxy.workflow.json');
const blob = JSON.stringify(workflow);
assert.equal(blob.includes('$env'), false);
assert.equal(blob.includes('$vars'), false);

function source(name) {
  const node = workflow.nodes.find((candidate) => candidate.name === name);
  assert.ok(node && node.type === 'n8n-nodes-base.code', `missing Code node: ${name}`);
  return node.parameters.jsCode;
}

function normalize(itemJson, mode = 'webhook') {
  const fn = new Function('$json', '$execution', source('Normalize control-plane request'));
  return fn(itemJson, { mode });
}

function format({ prepared, incoming }) {
  const lookup = (name) => {
    assert.equal(name, 'Normalize control-plane request');
    return { all: () => prepared.map((json) => ({ json })) };
  };
  const input = { all: () => incoming };
  const fn = new Function('$json', '$', '$input', source('Format control-plane response'));
  return fn(incoming[0] ? incoming[0].json : {}, lookup, input);
}

const flags = workflow.nodes.find((n) => n.name === 'Operator flags');
const clearFlag = flags.parameters.assignments.assignments.find((a) => a.name === 'clear');
assert.equal(clearFlag.value, false);

const schemaOnly = normalize({ body: { operation: 'schema' }, clear: true }, 'webhook');
assert.equal(schemaOnly.length, 1);
assert.equal(schemaOnly[0].json.wiped, false);
assert.equal(schemaOnly[0].json.query.includes('TRUNCATE TABLE cases'), false);
assert.equal(schemaOnly[0].json.query.startsWith('CREATE TABLE IF NOT EXISTS cases'), true);

const schemaClear = normalize({ body: { operation: 'schema', clear: true }, clear: false }, 'webhook');
assert.equal(schemaClear[0].json.wiped, true);
assert.equal(schemaClear[0].json.query.includes('TRUNCATE TABLE cases'), true);
assert.equal(schemaClear[0].json.query.includes('TRUNCATE TABLE agent_registry'), false);
assert.equal(schemaClear[0].json.query.includes('CREATE TABLE IF NOT EXISTS agent_registry'), true);

const manualFlag = normalize({ body: { operation: 'schema' }, clear: true }, 'manual');
assert.equal(manualFlag[0].json.wiped, true);

const listWithClear = normalize(
  { body: { operation: 'list_cases', clear: true, limit: 10 }, clear: true },
  'manual',
);
assert.equal(listWithClear[0].json.wiped, false);
assert.equal(listWithClear[0].json.query.includes('TRUNCATE'), false);

const wipeOp = normalize({ body: { operation: 'wipe' } }, 'webhook');
assert.equal(wipeOp[0].json.wiped, true);
assert.equal(wipeOp[0].json.query.includes('TRUNCATE TABLE cases'), true);

const prepared = [
  {
    operation: 'list_cases',
    query: 'SELECT 1',
    params: [200],
    wiped: false,
  },
];
const incoming = [
  { json: { case_id: 'CASE-a', status: 'done' }, pairedItem: 0 },
  { json: { case_id: 'CASE-b', status: 'running' }, pairedItem: 1 },
  { json: { case_id: 'CASE-c', status: 'new' }, pairedItem: 2 },
  { json: {}, pairedItem: 0 },
];
const listed = format({ prepared, incoming });
assert.equal(listed.length, 1);
assert.equal(listed[0].json.ok, true);
assert.equal(listed[0].json.operation, 'list_cases');
assert.deepEqual(
  listed[0].json.result.map((row) => row.case_id),
  ['CASE-a', 'CASE-b', 'CASE-c'],
);

console.log('mas-control-plane-proxy-smoke: ok');
