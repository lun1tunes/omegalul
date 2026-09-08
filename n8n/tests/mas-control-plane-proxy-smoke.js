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
const pin = (workflow.pinData || {})['MAS control-plane webhook'];
assert.ok(Array.isArray(pin) && pin[0] && pin[0].json && pin[0].json.body);
assert.equal(pin[0].json.body.operation, 'schema');
assert.equal(String(pin[0].json.webhookUrl || '').includes('webhook-test'), true);

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
assert.equal(schemaClear[0].json.query.includes('$$'), false);
assert.equal(schemaClear[0].json.query.indexOf('CREATE TABLE IF NOT EXISTS cases') < schemaClear[0].json.query.indexOf('TRUNCATE TABLE cases'), true);

const manualFlag = normalize({ body: { operation: 'schema' }, clear: true }, 'manual');
assert.equal(manualFlag[0].json.wiped, true);

const webhookTestFlag = normalize(
  {
    body: { operation: 'schema' },
    clear: true,
    webhookUrl: 'http://localhost:5678/webhook-test/mas-control-plane',
  },
  'webhook',
);
assert.equal(webhookTestFlag[0].json.wiped, true);

const checkboxOnly = normalize({ clear: true }, 'manual');
assert.equal(checkboxOnly[0].json.operation, 'schema');
assert.equal(checkboxOnly[0].json.wiped, true);

const listWithClear = normalize(
  { body: { operation: 'list_cases', clear: true, limit: 10 }, clear: true },
  'manual',
);
assert.equal(listWithClear[0].json.wiped, false);
assert.equal(listWithClear[0].json.query.includes('TRUNCATE'), false);

const wipeOp = normalize({ body: { operation: 'wipe' } }, 'webhook');
assert.equal(wipeOp[0].json.wiped, true);
assert.equal(wipeOp[0].json.query.includes('TRUNCATE TABLE cases'), true);

// Phase 2: the registry is executable. `schema` upgrades a live table in place and fills the new
// columns for the seeded agents without overwriting engineer edits (fill, not update).
const REGISTRY_COLUMNS = ['agent_id', 'title', 'when_to_use', 'input_required', 'output_provides', 'invoke', 'input_schema', 'output_schema', 'hitl_policy', 'enabled', 'version'];
for (const col of ['invoke', 'input_schema', 'output_schema', 'hitl_policy', 'enabled', 'version']) {
  assert.ok(schemaOnly[0].json.query.includes(`ALTER TABLE agent_registry ADD COLUMN IF NOT EXISTS ${col} `), `schema upgrades ${col}`);
}
assert.ok(schemaOnly[0].json.query.includes("CASE WHEN agent_registry.invoke = '{}'::jsonb THEN EXCLUDED.invoke ELSE agent_registry.invoke END"));
assert.equal(schemaOnly[0].json.query.includes('when_to_use = EXCLUDED.when_to_use'), false, 'schema must not overwrite engineer-edited descriptions');
assert.ok(schemaOnly[0].json.query.includes('"kind":"n8n_workflow"') && schemaOnly[0].json.query.includes('"kind":"http"'), 'seed carries invoke for every agent');
assert.ok(schemaOnly[0].json.query.includes('{math_url}/agent/run'), 'HTTP agent URL is a Runtime Config placeholder, not a hardcoded host');

const listAgents = normalize({ body: { operation: 'list_agents' } }, 'webhook');
assert.equal(listAgents[0].json.query, `SELECT ${REGISTRY_COLUMNS.join(', ')} FROM agent_registry ORDER BY agent_id`);

const upsert = normalize(
  {
    body: {
      operation: 'upsert_agent',
      row: {
        agent_id: 'echo_agent',
        title: 'Эхо',
        when_to_use: 'Тестовый агент',
        input_required: [],
        output_provides: ['echo'],
        invoke: { kind: 'n8n_workflow', workflow_id: 'wf-echo' },
        enabled: false,
        version: '3',
      },
    },
  },
  'webhook',
);
assert.ok(upsert[0].json.query.startsWith(`INSERT INTO agent_registry(${REGISTRY_COLUMNS.join(',')}) VALUES(`));
assert.ok(upsert[0].json.query.includes('$6::jsonb') && upsert[0].json.query.includes('$10::boolean'));
assert.deepEqual(upsert[0].json.params, [
  'echo_agent', 'Эхо', 'Тестовый агент', '[]', '["echo"]', '{"kind":"n8n_workflow","workflow_id":"wf-echo"}', '{}', '{}', 'agent_asks', 'false', '3',
]);
const upsertDefaults = normalize({ body: { operation: 'upsert_agent', row: { agent_id: 'x' } } }, 'webhook');
assert.deepEqual(upsertDefaults[0].json.params.slice(5), ['{}', '{}', '{}', 'agent_asks', 'true', '1']);
assert.throws(() => normalize({ body: { operation: 'upsert_agent', row: {} } }, 'webhook'), /agent_id/);

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
