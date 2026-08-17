'use strict';
/**
 * End-to-end smoke: handoff duration/brief → activity service presentation fields.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawn } = require('node:child_process');
const http = require('node:http');

const workspace = process.env.WORKSPACE_ROOT || '/workspace';
const orchestrator = JSON.parse(
  fs.readFileSync(path.join(workspace, 'n8n/workflows/core/universal-engineering-orchestrator.workflow.json'), 'utf8'),
);
const traceWf = JSON.parse(
  fs.readFileSync(path.join(workspace, 'n8n/workflows/core/mas-trace-event-writer.workflow.json'), 'utf8'),
);
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function source(name) {
  const node = orchestrator.nodes.find((n) => n.name === name);
  assert(node, name);
  return node.parameters.jsCode;
}

async function run(name, json) {
  const fn = new AsyncFunction('$json', '$', source(name));
  const out = await fn(json, () => ({ first: () => ({ json: {} }) }));
  return out[0].json;
}

function request(port, method, urlPath, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const payload = body == null ? null : Buffer.from(JSON.stringify(body));
    const req = http.request(
      {
        host: '127.0.0.1',
        port,
        path: urlPath,
        method,
        headers: {
          ...(payload ? { 'Content-Type': 'application/json', 'Content-Length': payload.length } : {}),
          ...headers,
        },
      },
      (res) => {
        const chunks = [];
        res.on('data', (c) => chunks.push(c));
        res.on('end', () => {
          const text = Buffer.concat(chunks).toString('utf8');
          let json = null;
          try {
            json = JSON.parse(text);
          } catch {
            json = text;
          }
          resolve({ status: res.statusCode, json, text });
        });
      },
    );
    req.on('error', reject);
    if (payload) req.write(payload);
    req.end();
  });
}

(async () => {
  let passed = 0;

  const activityConnection = traceWf.nodes.find((n) => n.name === 'Activity connection');
  assert.ok(activityConnection && activityConnection.type === 'n8n-nodes-base.set');
  const urlField = (activityConnection.parameters.assignments.assignments || []).find(
    (a) => a.name === 'activity_base_url',
  );
  assert.ok(urlField && String(urlField.value).includes('8200'));
  assert.equal(JSON.stringify(traceWf).includes('X-Activity-Key'), false);
  assert.equal(JSON.stringify(traceWf).includes('ACTIVITY_KEY'), false);
  passed += 1;

  // 1) Duration is computed between DELEGATED and specialist return.
  const first = await run('Resolve allowlisted specialist', {
    task_id: 'eng_ui_1',
    version: 1,
    packet_json: JSON.stringify({
      specialist_id: 'excel_extraction_specialist',
      attempt: 1,
      objective: 'Extract ORAT',
    }),
    runtime_json: '{}',
  });
  assert.equal(JSON.parse(first.runtime_json).handoff_events[0].status, 'DELEGATED');
  assert.ok(JSON.parse(first.runtime_json).specialist_timer.specialist_id);

  await new Promise((r) => setTimeout(r, 25));
  const second = await run('Route successful specialist handoff', {
    task_id: 'eng_ui_1',
    specialist_id: 'excel_extraction_specialist',
    request_json: JSON.stringify({ task_type: 'schedule_build' }),
    specialist_packet: { inputs: { schedule_request: { build_mode: 'CREATE' } } },
    specialist_result: {
      status: 'succeeded',
      summary: 'extracted',
      compact_data: {
        source_snapshot_hash: 'fnv1a32:abcd1234',
        correlation_id: 'corr-ui',
        preview_records: [{ ORAT: 1 }],
        conflicts: [],
      },
      artifact_refs: [],
      evidence: [],
    },
    runtime_json: first.runtime_json,
  });
  const events = JSON.parse(second.runtime_json).handoff_events;
  const ready = events.find((e) => e.status === 'EXCEL_EVIDENCE_READY');
  assert.ok(ready);
  assert.ok(Number(ready.duration_ms) >= 20, `expected duration, got ${ready.duration_ms}`);
  assert.ok(ready.brief || ready.summary);
  assert.ok(ready.at);
  passed += 1;

  // 2) Activity service presentation contract over HTTP.
  const port = 18200 + Math.floor(Math.random() * 200);
  let stderrBuf = '';
  let stdoutBuf = '';
  const activityPyCandidates = [
    process.env.MAS_ACTIVITY_PYTHON,
    path.join(workspace, 'mas-activity-service', '.venv', 'bin', 'python'),
    '/tmp/mas-act-venv/bin/python',
    'python3',
  ].filter(Boolean);
  let activityPython = 'python3';
  for (const candidate of activityPyCandidates) {
    try {
      const probe = require('node:child_process').spawnSync(candidate, ['-c', 'import uvicorn'], { encoding: 'utf8' });
      if (probe.status === 0) { activityPython = candidate; break; }
    } catch {}
  }
  const child = spawn(
    activityPython,
    ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(port)],
    {
      cwd: path.join(workspace, 'mas-activity-service'),
      env: { ...process.env, PYTHONPATH: path.join(workspace, 'mas-activity-service') },
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
  child.stderr.on('data', (chunk) => {
    stderrBuf += chunk.toString();
  });
  child.stdout.on('data', (chunk) => {
    stdoutBuf += chunk.toString();
  });
  try {
    let readyHttp = false;
    for (let i = 0; i < 80; i += 1) {
      if (child.exitCode != null) break;
      try {
        const health = await request(port, 'GET', '/health');
        if (health.status === 200) {
          readyHttp = true;
          break;
        }
      } catch {
        await new Promise((r) => setTimeout(r, 100));
      }
    }
    assert.ok(
      readyHttp,
      `activity service failed to start on ${port}\nexit=${child.exitCode}\nstderr=${stderrBuf}\nstdout=${stdoutBuf}`,
    );

    const sync = await request(
      port,
      'POST',
      '/v1/sync',
      {
        task_id: 'eng_ui_1',
        events: events.map((e) => ({
          event_type: 'handoff',
          at: e.at,
          status: e.status,
          summary: e.summary,
          brief: e.brief,
          duration_ms: e.duration_ms,
          handoff: {
            from_specialist: e.from_specialist,
            to_specialist: e.to_specialist,
            from_role: e.from_role,
            to_role: e.to_role,
            details: e.details,
          },
        })),
      },
    );
    assert.equal(sync.status, 200);
    assert.ok(sync.json.count >= 2);

    const feed = await request(port, 'GET', '/v1/tasks/eng_ui_1');
    assert.equal(feed.status, 200);
    assert.equal(feed.json.contract_version, '1.1');
    const excelTurn = feed.json.activity.find((t) => t.status === 'EXCEL_EVIDENCE_READY');
    assert.ok(excelTurn.brief.length > 20);
    assert.ok(/UTC|Тюмень|\+0?5|GMT/i.test(excelTurn.at_abs), excelTurn.at_abs);
    assert.ok(excelTurn.duration_label);
    assert.ok(['ok', 'info', 'wait', 'block'].includes(excelTurn.outcome));

    const ui = await request(port, 'GET', '/');
    assert.equal(ui.status, 200);
    assert.ok(String(ui.text).includes('MAS'));
    const js = await request(port, 'GET', '/static/app.js');
    assert.ok(String(js.text).includes('duration_label'));
    assert.ok(String(js.text).includes('at_abs'));
    assert.ok(String(js.text).includes('brief'));
    passed += 1;
  } finally {
    child.kill('SIGTERM');
  }

  console.log(`MAS activity presentation smoke: ${passed} scenarios passed`);
})().catch((err) => {
  console.error(err.stack || err);
  process.exit(1);
});
