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

const byName = Object.fromEntries(workflow.nodes.map((n) => [n.name, n]));

function source(name) {
  const node = byName[name];
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

const LAB = {
  activity_base_url: 'http://mas-activity:8200',
  excel_tools_url: 'http://excel-tools:8000',
  schedule_service_url: 'http://schedule-builder:8090',
  math_url: 'http://math-service:8100',
  orchestrator_step_url: 'http://127.0.0.1:5678/webhook/mas-orchestrator-step',
};
const FIELD = {
  activity_base_url: 'http://10.20.30.40:8200/',
  excel_tools_url: 'http://10.20.30.40:8000',
  schedule_service_url: 'http://10.20.30.40:8090',
  math_url: 'http://10.20.30.40:8100',
  orchestrator_step_url: 'https://n8n.corp.example/webhook/mas-orchestrator-step',
};

const ok = (body) => [{ json: { statusCode: 200, body } }];
const healthy = (prepared, overrides = {}) => ({
  'Prepare health probes': prepared,
  'Probe Activity /health': ok({ status: 'ok', service: 'mas-activity', control_plane_backend: 'n8n_proxy' }),
  'Probe Activity /ready': ok({ ready: true, status: 'ready' }),
  'Probe Excel Tools /health': ok({ status: 'ok' }),
  'Probe Schedule Builder /health': ok({ status: 'ok', service: 'schedule-builder-service' }),
  'Probe Math /health': ok({ status: 'ok', service: 'fastapi-math-service' }),
  'Probe Orchestrator webhook': ok({ contract: 'mas_orchestrator_ack', case_id: 'CASE-readiness-probe', status: 'probe' }),
  'Probe Control Plane Proxy webhook': ok({
    ok: true,
    operation: 'list_agents',
    result: [{ agent_id: 'excel_extractor' }, { agent_id: 'calculation_agent' }, { agent_id: 'schedule_builder' }],
  }),
  ...overrides,
});

(async () => {
  /* ---- static contract: one source of URLs, no lab-only probes ---- */
  assert.equal(workflow.name, 'Form — MAS Deployment Health Check');
  assert.equal(workflow.active, false);
  assert.equal(workflow.meta.targetN8nVersion, '2.30.8');
  assert.equal(byName['Health check form'].type, 'n8n-nodes-base.formTrigger');
  assert.equal(byName['Health check form'].typeVersion, 2.6);
  assert.equal(byName['Show health report'].typeVersion, 2.5);

  const runtime = byName['Runtime endpoints'];
  assert.equal(runtime.type, 'n8n-nodes-base.executeWorkflow');
  assert.equal(runtime.typeVersion, 1.3);
  assert.equal(runtime.parameters.workflowId.value, 'REPLACE_MAS_RUNTIME_CONFIG_IN_UI');
  assert.equal(runtime.parameters.workflowId.cachedResultName, 'MAS — Runtime Config');
  assert.equal(runtime.onError, 'continueRegularOutput');

  assert(!workflow.nodes.some((n) => n.type === 'n8n-nodes-base.dataTable'), 'no Data Table probes');
  const https = workflow.nodes.filter((n) => n.type === 'n8n-nodes-base.httpRequest');
  assert.equal(https.length, 7);
  for (const n of https) {
    assert.match(String(n.parameters.url), /^=\{\{ \$\('Prepare health probes'\)\.first\(\)\.json\.urls\.[a-z_]+ \}\}$/, n.name);
    assert.equal(n.continueOnFail, true, n.name);
    assert.equal(n.parameters.options.response.response.fullResponse, true, n.name);
  }
  const whole = JSON.stringify(workflow.nodes.filter((n) => n.type !== 'n8n-nodes-base.stickyNote'));
  for (const lab of ['excel-tools:8000', 'schedule-builder:8090', 'math-service:8100', 'mas-activity:8200', 'n8n-runners', 'n8n:5678']) {
    assert(!whole.includes(lab), `lab DNS literal leaked into executable node: ${lab}`);
  }
  for (const name of ['Probe Orchestrator webhook', 'Probe Control Plane Proxy webhook']) {
    const n = byName[name];
    assert.equal(n.parameters.method, 'POST');
    assert.equal(n.parameters.genericAuthType, 'httpHeaderAuth');
    assert.equal(n.credentials.httpHeaderAuth.id, 'REPLACE_IN_UI');
  }
  assert.match(String(byName['Probe Orchestrator webhook'].parameters.jsonBody), /bodies\.orchestrator_probe/);
  assert.match(String(byName['Probe Control Plane Proxy webhook'].parameters.jsonBody), /bodies\.control_plane_list_agents/);
  const editNote = byName['edit after import'];
  assert(editNote && editNote.parameters.content.includes('Runtime endpoints'));
  assert(editNote.parameters.content.includes('Header Auth'));

  /* ---- Prepare: URLs derived from Runtime Config ---- */
  const labPrepared = await run('Prepare health probes', { operator_note: 'suite' }, { 'Runtime endpoints': LAB });
  assert.equal(labPrepared.urls.activity_health, 'http://mas-activity:8200/health');
  assert.equal(labPrepared.urls.activity_ready, 'http://mas-activity:8200/ready');
  assert.equal(labPrepared.urls.excel_health, 'http://excel-tools:8000/health');
  assert.equal(labPrepared.urls.orchestrator_webhook, LAB.orchestrator_step_url);
  assert.equal(labPrepared.urls.control_plane_webhook, 'http://127.0.0.1:5678/webhook/mas-control-plane');
  assert.deepEqual(labPrepared.bodies.control_plane_list_agents, { operation: 'list_agents' });
  assert.equal(labPrepared.bodies.orchestrator_probe.action, 'probe');
  assert.equal(labPrepared.runtime_issues.filter((i) => i.kind === 'lab_dns').length, 4);

  const fieldPrepared = await run('Prepare health probes', { operator_note: '' }, { 'Runtime endpoints': FIELD });
  assert.equal(fieldPrepared.urls.activity_health, 'http://10.20.30.40:8200/health', 'trailing slash trimmed');
  assert.equal(fieldPrepared.urls.control_plane_webhook, 'https://n8n.corp.example/webhook/mas-control-plane');
  assert.deepEqual(fieldPrepared.runtime_issues, []);

  const unboundPrepared = await run('Prepare health probes', {}, {
    'Runtime endpoints': [{ json: { error: 'Workflow REPLACE_MAS_RUNTIME_CONFIG_IN_UI not found' } }],
  });
  assert(unboundPrepared.runtime_issues.some((i) => i.kind === 'unbound'));
  assert.equal(unboundPrepared.urls.activity_health, '');

  const badPathPrepared = await run('Prepare health probes', {}, {
    'Runtime endpoints': { ...FIELD, orchestrator_step_url: 'http://10.20.30.40:8200/cases/x/run', excel_tools_url: 'REPLACE_IN_UI' },
  });
  assert(badPathPrepared.runtime_issues.some((i) => i.kind === 'wrong_path'));
  assert(badPathPrepared.runtime_issues.some((i) => i.kind === 'placeholder' && i.key === 'excel_tools_url'));
  assert.equal(badPathPrepared.urls.control_plane_webhook, '', 'no proxy URL when step URL is not the orchestrator webhook');

  /* ---- Report: lab → PASS_WITH_TODO, field → PASS ---- */
  const labReport = await run('Build health report', labPrepared, healthy(labPrepared));
  assert.equal(labReport.overall, 'PASS_WITH_TODO');
  assert.equal(labReport.fail_count, 0);
  assert.equal(labReport.todo_count, 4);
  assert(labReport.pass_count >= 8, `expected >=8 PASS, got ${labReport.pass_count}`);
  assert(labReport.form_response_html.includes('Overall: <strong>PASS_WITH_TODO</strong>'));
  assert(labReport.checks.some((c) => c.check === 'Live: Activity control plane = n8n_proxy' && c.status === 'PASS'));
  assert(labReport.checks.some((c) => c.check.startsWith('Live: Orchestrator webhook') && c.status === 'PASS'));
  assert(labReport.checks.some((c) => c.check.startsWith('Live: Control Plane Proxy webhook') && c.detail.includes('excel_extractor')));
  assert(labReport.checks.some((c) => c.status === 'TODO' && c.check.includes('excel_tools_url') && c.where_to_fix.includes('MAS — Runtime Config')));
  assert(!JSON.stringify(labReport.checks).includes('engineering_orchestrator_tasks_v1'));
  assert(!JSON.stringify(labReport.checks).includes('Data Table'));

  const fieldReport = await run('Build health report', fieldPrepared, healthy(fieldPrepared));
  assert.equal(fieldReport.overall, 'PASS');
  assert.equal(fieldReport.fail_count, 0);
  assert.equal(fieldReport.todo_count, 0);
  assert(fieldReport.form_response_html.includes('Overall: <strong>PASS</strong>'));
  assert(fieldReport.form_response_html.includes('10.20.30.40:8200'));

  /* ---- Report: Runtime Config unbound → FAIL pointing at the binding ---- */
  const unboundReport = await run('Build health report', unboundPrepared, {
    'Prepare health probes': unboundPrepared,
    'Probe Activity /health': [{ json: { message: 'Invalid URL' } }],
    'Probe Activity /ready': [],
    'Probe Excel Tools /health': [{ error: { message: 'Invalid URL' } }],
    'Probe Schedule Builder /health': [],
    'Probe Math /health': [],
    'Probe Orchestrator webhook': [],
    'Probe Control Plane Proxy webhook': [],
  });
  assert.equal(unboundReport.overall, 'FAIL');
  const bind = unboundReport.checks.find((c) => c.check === 'Runtime Config: bound and readable');
  assert.equal(bind.status, 'FAIL');
  assert(bind.where_to_fix.includes('Runtime endpoints'));
  assert(unboundReport.checks.filter((c) => c.check.startsWith('Runtime Config:') && c.status === 'FAIL').length >= 6);

  /* ---- Report: field outage / auth mismatch diagnostics ---- */
  const outage = await run('Build health report', fieldPrepared, healthy(fieldPrepared, {
    'Probe Activity /health': ok({ status: 'ok', control_plane_backend: 'memory' }),
    'Probe Activity /ready': [{ json: { statusCode: 503, body: { detail: { ready: false, missing_config: ['CONTROL_PLANE_PROXY_URL'], webhooks: { orchestrator: { ok: false } } } } } }],
    'Probe Excel Tools /health': [{ json: { statusCode: 0, message: 'connect ECONNREFUSED 10.20.30.40:8000' } }],
    'Probe Math /health': [{ error: { message: 'ETIMEDOUT' } }],
    'Probe Orchestrator webhook': [{ json: { statusCode: 403, body: { message: 'Authorization data is wrong!' } } }],
    'Probe Control Plane Proxy webhook': [{ json: { statusCode: 404, body: { message: 'This webhook is not registered' } } }],
  }));
  assert.equal(outage.overall, 'FAIL');
  const find = (prefix) => outage.checks.find((c) => c.check.startsWith(prefix));
  assert.equal(find('Live: Activity control plane').status, 'FAIL');
  assert.equal(find('Live: Activity /ready').status, 'FAIL');
  assert(find('Live: Activity /ready').detail.includes('CONTROL_PLANE_PROXY_URL'));
  assert(find('Live: Activity /ready').detail.includes('orchestrator'));
  assert.equal(find('Live: Excel Tools').status, 'FAIL');
  assert(find('Live: Excel Tools').detail.includes('ECONNREFUSED'));
  assert(find('Live: Excel Tools').where_to_fix.includes('Firewall'));
  assert.equal(find('Live: Math').status, 'FAIL');
  assert.equal(find('Live: Schedule Builder').status, 'PASS');
  assert.equal(find('Live: Orchestrator webhook').status, 'FAIL');
  assert(find('Live: Orchestrator webhook').detail.includes('Header Auth mismatch'));
  assert.equal(find('Live: Control Plane Proxy').status, 'FAIL');
  assert(find('Live: Control Plane Proxy').detail.includes('not Active'));
  assert(outage.form_response_html.includes(`FAIL — fix these first (${outage.fail_count})`));

  /* ---- Report: proxy alive but registry not seeded ---- */
  const unseeded = await run('Build health report', fieldPrepared, healthy(fieldPrepared, {
    'Probe Control Plane Proxy webhook': ok({ ok: true, operation: 'list_agents', result: [{ agent_id: 'excel_extractor' }] }),
  }));
  assert.equal(unseeded.overall, 'FAIL');
  const reg = unseeded.checks.find((c) => c.check.startsWith('Live: Control Plane Proxy'));
  assert(reg.detail.includes('schedule_builder') && reg.detail.includes('"operation":"schema"'));

  console.log('MAS health-check runtime smoke: 6 scenarios passed');
})().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
