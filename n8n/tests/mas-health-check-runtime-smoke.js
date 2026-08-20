'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || '/workspace';
const workflow = JSON.parse(fs.readFileSync(
  path.join(workspace, 'n8n', 'workflows', 'core', 'mas-deployment-health-check.workflow.json'),
  'utf8',
));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function source(name) {
  const node = workflow.nodes.find((candidate) => candidate.name === name);
  assert(node && node.type === 'n8n-nodes-base.code', `missing Code node: ${name}`);
  return node.parameters.jsCode;
}

async function run(name, json, nodes) {
  const lookup = (nodeName) => {
    if (!Object.prototype.hasOwnProperty.call(nodes, nodeName)) {
      throw new Error(`node not executed: ${nodeName}`);
    }
    const payload = nodes[nodeName];
    const items = Array.isArray(payload) ? payload : [{ json: payload }];
    return {
      first: () => items[0],
      all: () => items,
    };
  };
  const fn = new AsyncFunction('$json', '$', '$input', source(name));
  const result = await fn(json, lookup, { first: () => ({ json }), all: () => [{ json }] });
  assert(Array.isArray(result) && result.length === 1 && result[0].json);
  return result[0].json;
}

(async () => {
  assert.equal(workflow.name, 'Form — MAS Deployment Health Check');
  assert.equal(workflow.active, false);
  assert.equal(workflow.meta.targetN8nVersion, '2.30.8');

  const prepared = await run('Prepare health probes', { operator_note: 'suite' }, {});
  assert.equal(prepared.probe_task_id, '__mas_health_probe__');
  assert.equal(prepared.action, 'status');

  const report = await run('Build health report', prepared, {
    'Prepare health probes': prepared,
    'Probe task Data Table': [{ json: {} }],
    'Probe trace Data Table': [{ json: {} }],
    'Call Orchestrator probe': [{ json: { status: 'not_found', message: 'Task not found.' } }],
    'Call Trace Writer probe': [{ json: { contract: 'mas_trace_ack', stored: true, event_id: 'evt_health' } }],
    'Probe excel-tools /health': [{ json: { statusCode: 200, body: { status: 'ok' } } }],
    'Probe n8n-runners /healthz': [{ json: { statusCode: 200, body: { status: 'ok' } } }],
    'Probe mas-activity /health': [{ json: { statusCode: 200, body: { status: 'ok', service: 'mas-activity' } } }],
    'Probe n8n /healthz': [{ json: { statusCode: 200, body: { status: 'ok' } } }],
    'Probe math-service /health': [{ json: { statusCode: 200, body: { status: 'ok', service: 'fastapi-math-service' } } }],
    'Probe schedule-builder /health': [{ json: { statusCode: 200, body: { status: 'ok', service: 'schedule-builder-service' } } }],
  });

  assert.equal(report.overall, 'PASS_WITH_TODO');
  assert.equal(report.fail_count, 0);
  assert(report.pass_count >= 6, `expected >=6 live PASS, got ${report.pass_count}`);
  assert(report.todo_count > 0);
  assert(report.form_response_html.includes('PASS_WITH_TODO'));
  assert(report.checks.some((c) => c.check.includes('Call CAS persist — insert new task')));
  assert(report.checks.some((c) => c.where_to_fix.includes('CAS — Persist Task State')));
  assert(report.checks.some((c) => c.where_to_fix.includes('engineering_orchestrator_tasks_v1')));
  assert(report.checks.some((c) => c.check === 'Live: excel-tools /health' && c.status === 'PASS'));
  assert(report.checks.some((c) => c.check === 'Live: mas-activity /health' && c.status === 'PASS'));
  assert(report.checks.some((c) => c.check === 'Live: math-service /health' && c.status === 'PASS'));
  assert(report.checks.some((c) => c.check === 'Live: schedule-builder /health' && c.status === 'PASS'));
  assert(report.checks.some((c) => c.check.startsWith('Live: task Data Table') && c.status === 'TODO'));
  assert(report.checks.some((c) => c.check.includes('Orchestrator') && c.status === 'TODO'));
  assert(!report.checks.some((c) => c.where_to_fix.includes('Insert durable task state')));
  assert(!report.checks.some((c) => String(c.target || c.where_to_fix).includes('SCHEDULE — Knowledge Retrieval')));

  const failReport = await run('Build health report', prepared, {
    'Prepare health probes': prepared,
    'Probe task Data Table': [{ json: { message: 'Data table REPLACE_IN_UI is not configured' } }],
    'Probe trace Data Table': [],
    'Call Orchestrator probe': [{ error: { message: 'workflow not found' } }],
    'Call Trace Writer probe': [{ json: { status: 'error' } }],
    'Probe excel-tools /health': [{ json: { statusCode: 0, message: 'ECONNREFUSED' } }],
    'Probe n8n-runners /healthz': [{ error: { message: 'ENOTFOUND' } }],
    'Probe mas-activity /health': [{ json: { statusCode: 503 } }],
    'Probe n8n /healthz': [{ json: { statusCode: 500 } }],
    'Probe math-service /health': [{ json: { statusCode: 0, message: 'ECONNREFUSED' } }],
    'Probe schedule-builder /health': [{ json: { statusCode: 0, message: 'ECONNREFUSED' } }],
  });
  assert.equal(failReport.overall, 'FAIL');
  assert(failReport.fail_count >= 6);

  console.log('MAS health-check runtime smoke: 3 scenarios passed');
})().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
