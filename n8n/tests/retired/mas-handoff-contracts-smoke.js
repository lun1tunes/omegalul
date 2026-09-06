'use strict';
/**
 * Factor-gated smoke for the four practical handoff improvements.
 * Each scenario fails closed if the corresponding value factor is missing.
 */
const assert = require('node:assert/strict');
const { readWorkflow } = require('../_workflow');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || '/workspace';
const read = (rel) => JSON.parse(fs.readFileSync(path.join(workspace, rel), 'utf8'));
const orchestrator = readWorkflow('universal-engineering-orchestrator.workflow.json');
const traceWf = readWorkflow('mas-trace-event-writer.workflow.json');
const registry = read('n8n/contracts/specialist_registry.v1.json');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function source(workflow, name) {
  const node = workflow.nodes.find((n) => n.name === name);
  assert(node && node.type === 'n8n-nodes-base.code', `missing ${name}`);
  return node.parameters.jsCode;
}

async function run(name, json, nodes = {}) {
  const fn = new AsyncFunction(
    '$json',
    '$',
    source(orchestrator, name),
  );
  const result = await fn(json, (nodeName) => {
    if (!Object.prototype.hasOwnProperty.call(nodes, nodeName)) throw new Error(`missing node ${nodeName}`);
    return { first: () => ({ json: nodes[nodeName] }) };
  });
  assert(Array.isArray(result) && result[0]?.json);
  return result[0].json;
}

async function normalizeTrace(events) {
  const fn = new AsyncFunction('$json', source(traceWf, 'Normalize MAS trace event'));
  const rows = await fn({ mas_trace_events: events });
  return rows.map((r) => r.json.trace_event);
}

(async () => {
  let passed = 0;

  // Factor: registry — catalogue IDs == allowlist keys; configured routes only.
  const resolveCode = source(orchestrator, 'Resolve allowlisted specialist');
  const plannerCode = source(orchestrator, 'Prepare planner input');
  for (const s of registry.specialists) {
    assert.ok(plannerCode.includes(s.specialist_id), `catalogue missing ${s.specialist_id}`);
    assert.ok(resolveCode.includes(`${s.specialist_id}:{route:${s.route},configured:${s.configured ? 'true' : 'false'}}`), `allowlist drift ${s.specialist_id}`);
  }
  const unknown = await run('Resolve allowlisted specialist', {
    task_id: 'eng-handoff-1',
    version: 1,
    packet_json: JSON.stringify({ specialist_id: 'made_up_specialist', attempt: 1 }),
    runtime_json: '{}',
  });
  assert.equal(unknown.delegation_allowed, false);
  assert.equal(JSON.parse(unknown.runtime_json).last_error.code, 'SPECIALIST_NOT_ALLOWLISTED');
  passed += 1;

  const delegated = await run('Resolve allowlisted specialist', {
    task_id: 'eng-handoff-1',
    version: 1,
    packet_json: JSON.stringify({
      specialist_id: 'excel_extraction_specialist',
      attempt: 1,
      objective: 'Extract rates',
    }),
    runtime_json: '{}',
  });
  assert.equal(delegated.delegation_allowed, true);
  const delEvents = JSON.parse(delegated.runtime_json).handoff_events;
  assert.equal(delEvents.length, 1);
  assert.equal(delEvents[0].status, 'DELEGATED');
  assert.equal(delEvents[0].to_role, 'Excel Extractor');
  passed += 1;

  // Factor: domain packet — Excel handoff without snapshot/correlation fails closed.
  const badHandoff = await run('Route successful specialist handoff', {
    task_id: 'eng-handoff-1',
    specialist_id: 'excel_extraction_specialist',
    request_json: JSON.stringify({ task_type: 'schedule_build' }),
    specialist_packet: { inputs: { schedule_request: { build_mode: 'CREATE' } } },
    specialist_result: {
      status: 'succeeded',
      summary: 'ok',
      compact_data: { preview_records: [{ ORAT: 100 }] },
      artifact_refs: [],
      evidence: [],
    },
    runtime_json: delegated.runtime_json,
  });
  assert.equal(badHandoff.specialist_result.status, 'retryable_error');
  assert.equal(badHandoff.specialist_result.error.code, 'INVALID_SOURCE_FACTS_PACKET');
  assert.equal(badHandoff.post_specialist_route, 'verify');
  passed += 1;

  await new Promise((r) => setTimeout(r, 20));
  const goodHandoff = await run('Route successful specialist handoff', {
    task_id: 'eng-handoff-1',
    specialist_id: 'excel_extraction_specialist',
    request_json: JSON.stringify({ task_type: 'schedule_build' }),
    specialist_packet: { inputs: { schedule_request: { build_mode: 'CREATE' } } },
    specialist_result: {
      status: 'succeeded',
      summary: 'extracted',
      compact_data: {
        source_snapshot_hash: 'fnv1a32:abcd1234',
        correlation_id: 'corr-1',
        preview_records: [{ WELL: 'P1', ORAT: 12 }],
        conflicts: [],
      },
      artifact_refs: [],
      evidence: [],
    },
    runtime_json: delegated.runtime_json,
  });
  assert.equal(goodHandoff.post_specialist_route, 'replan');
  const ready = JSON.parse(goodHandoff.runtime_json).last_error;
  assert.equal(ready.code, 'EXCEL_EVIDENCE_READY');
  assert.equal(ready.source_facts_packet.contract, 'source_facts_packet');
  assert.equal(ready.source_facts_packet.facts.length, 1);
  const excelReady = JSON.parse(goodHandoff.runtime_json).handoff_events.find((e) => e.status === 'EXCEL_EVIDENCE_READY');
  assert.ok(excelReady);
  assert.ok(Number.isFinite(Number(excelReady.duration_ms)), 'specialist duration_ms required');
  assert.ok(String(excelReady.brief || excelReady.summary).length > 0);
  assert.equal(excelReady.contract_version, '1.1');
  passed += 1;

  const excelOnly = await run('Route successful specialist handoff', {
    task_id: 'eng-handoff-1',
    specialist_id: 'excel_extraction_specialist',
    request_json: JSON.stringify({
      objective: 'Extract WCONPROD-like rates and t-navigator dates from the workbook',
      task_type: 'excel_extraction',
    }),
    specialist_packet: { inputs: { requested_fields: ['Date', 'Price'] } },
    specialist_result: {
      status: 'succeeded',
      summary: 'extracted',
      compact_data: {
        source_snapshot_hash: 'fnv1a32:abcd1234',
        correlation_id: 'corr-price',
        preview_records: [{ Date: '2024-01-01', Price: 80 }],
        conflicts: [],
      },
      artifact_refs: [],
      evidence: [],
    },
    runtime_json: delegated.runtime_json,
  });
  assert.equal(excelOnly.post_specialist_route, 'verify');
  assert.notEqual(JSON.parse(excelOnly.runtime_json).last_error?.code, 'EXCEL_EVIDENCE_READY');
  passed += 1;

  // Factor: typed evidence_gap — malformed gaps do not open Excel loop.
  const malformed = await run('Prepare SCHEDULE evidence retry', {
    task_id: 'eng-handoff-1',
    version: 2,
    retry_count: 0,
    specialist_id: 'schedule_builder_specialist',
    specialist_packet: { specialist_id: 'schedule_builder_specialist', artifact_refs: [] },
    specialist_result: {
      status: 'needs_input',
      continuation: {
        protocol: 'schedule-builder-evidence-gap-v1',
        gap_signature: 'sig1',
        source_snapshot_hash: 'none',
        evidence_gap: [{ entity: 'P1', reason: 'missing only' }],
      },
    },
    runtime_json: '{}',
  });
  assert.equal(malformed.schedule_evidence_retry, false);
  assert.equal(malformed.specialist_result.error.code, 'MALFORMED_EVIDENCE_GAP');
  const malformedAsk = String(
    malformed.specialist_result.human_request?.questions?.[0]?.text
    || malformed.specialist_result.human_request?.questions?.[0]?.question
    || '',
  );
  assert(/[А-Яа-яЁё]/.test(malformedAsk), malformedAsk);
  assert(!/^MALFORMED_EVIDENCE_GAP/.test(malformedAsk));
  passed += 1;

  const typed = await run('Prepare SCHEDULE evidence retry', {
    task_id: 'eng-handoff-1',
    version: 2,
    retry_count: 0,
    specialist_id: 'schedule_builder_specialist',
    specialist_packet: { specialist_id: 'schedule_builder_specialist', artifact_refs: [] },
    specialist_result: {
      status: 'needs_input',
      continuation: {
        protocol: 'schedule-builder-evidence-gap-v1',
        gap_signature: 'sig2',
        source_snapshot_hash: 'fnv1a32:snap',
        evidence_gap: [
          {
            entity: 'P1',
            effective_at: '2025-01-01',
            keyword: 'WCONPROD',
            field: 'BHP',
            reason: 'required for forecast control',
            expected_format: 'bar with provenance',
          },
        ],
      },
    },
    runtime_json: '{}',
  });
  assert.equal(typed.schedule_evidence_retry, true);
  assert.equal(typed.specialist_id, 'excel_extraction_specialist');
  assert.equal(typed.specialist_packet.inputs.schedule_evidence_gap[0].field, 'BHP');
  assert.ok(JSON.parse(typed.runtime_json).handoff_events.some((e) => e.status === 'SCHEDULE_EVIDENCE_GAP'));
  passed += 1;

  // Factor: handoff trace + activity feed for chat UI.
  const formatted = await run('Format orchestrator response', {
    task_id: 'eng-handoff-1',
    status: 'delegated',
    version: 3,
    runtime_json: goodHandoff.runtime_json,
    result_json: '{}',
    verification_json: '{}',
    gate_json: '{}',
  });
  assert.equal(formatted.activity_contract, 'mas_activity_feed/v1.1');
  assert.ok(formatted.activity.length >= 2);
  assert.ok(formatted.activity.every((t) => t.from?.role && t.to?.role && (t.brief || t.text)));
  assert.ok(formatted.activity.some((t) => t.status === 'EXCEL_EVIDENCE_READY' && Number.isFinite(Number(t.duration_ms))));
  assert.equal(formatted.audit.handoff_count, formatted.activity.length);
  passed += 1;

  const traced = await run('Prepare final MAS trace event', {
    task_id: 'eng-handoff-1',
    status: 'delegated',
    requested_by: 'engineer',
    runtime_json: goodHandoff.runtime_json,
    result_json: JSON.stringify({ summary: 'ok', compact_data: {}, evidence: [] }),
    verification_json: '{}',
    gate_json: '{}',
    plan_json: '{}',
  });
  const handoffs = traced.mas_trace_events.filter((e) => e.event_type === 'handoff');
  assert.ok(handoffs.length >= 2, 'expected handoff events in final trace fan-in');
  const normalized = await normalizeTrace(traced.mas_trace_events);
  assert.ok(normalized.some((e) => e.event_type === 'handoff' && e.handoff?.to_specialist));
  assert.ok(!JSON.stringify(normalized).toLowerCase().includes('api_key'));
  passed += 1;

  console.log(`MAS handoff contracts smoke: ${passed} scenarios passed`);
})().catch((err) => {
  console.error(err.stack || err);
  process.exit(1);
});
