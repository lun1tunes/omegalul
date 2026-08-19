'use strict';

// Executes the exported n8n Code-node sources.  The catalogue below is a
// synthetic test fixture, not vendor documentation and not a production 22.2
// field catalogue.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || '/workspace';
const workflows = path.join(workspace, 'n8n', 'workflows', 'core');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function source(file, nodeName) {
  const workflow = JSON.parse(fs.readFileSync(path.join(workflows, file), 'utf8'));
  const node = workflow.nodes.find((candidate) => candidate.name === nodeName);
  assert(node && node.type === 'n8n-nodes-base.code', `${file}: ${nodeName} is missing`);
  return node.parameters.jsCode;
}

async function execute(file, nodeName, json) {
  const fn = new AsyncFunction('$json', source(file, nodeName));
  const result = await fn(json);
  assert(Array.isArray(result) && result.length === 1 && result[0].json);
  return result[0].json;
}

const sourceHash = `sha256:${'a'.repeat(64)}`;
const catalogueHash = `sha256:${'b'.repeat(64)}`;
const citation = {
  document_id: 'synthetic-test-catalogue',
  document_revision: '22.2',
  source_hash: sourceHash,
  page: 'fixture',
  heading: 'TEST ONLY',
};

function catalogue(overrides = {}) {
  return {
    contract: 'schedule_schema_catalogue',
    contract_version: '1.0',
    catalogue_ref: 'catalogue://test/tnavigator/22.2',
    catalogue_hash: catalogueHash,
    source_hash: sourceHash,
    simulator_profile: {
      vendor: 'Rock Flow Dynamics', simulator: 'tNavigator', version: '22.2',
    },
    approved: true,
    approved_by: 'test-engineer',
    approval_gate_id: 'test-gate',
    schemas: [
      {
        schema_id: 'fixture:DATES:v1',
        schema_revision: 'fixture-1',
        keyword: 'DATES',
        variant: 'default',
        citation,
        fields: [
          { name: 'DATE', position: 1, type: 'date', format: 'DD MON YYYY', required: true, quote: 'none' },
        ],
        semantics: { period: 'ANY', clock: { sets_from_field: 'DATE' } },
        layout: { newline: 'LF', indent: '  ', delimiter: 'SPACE', record_terminator: 'SLASH', block_terminator: 'NONE' },
      },
      {
        schema_id: 'fixture:WCONPROD:v1',
        schema_revision: 'fixture-1',
        keyword: 'WCONPROD',
        variant: 'default',
        citation,
        fields: [
          { name: 'WELL', position: 1, type: 'string', required: true, quote: 'single' },
          { name: 'STATUS', position: 2, type: 'enum', enum: ['OPEN', 'SHUT'], required: true, case: 'upper' },
          { name: 'CONTROL', position: 3, type: 'enum', enum: ['ORAT', 'BHP'], required: true, case: 'upper' },
          { name: 'ORAT', position: 4, type: 'number', required: true },
          { name: 'BHP', position: 5, type: 'number', required: false, default_allowed: true },
        ],
        semantics: { period: 'ANY', clock: { uses_current: true } },
        layout: { newline: 'LF', indent: '  ', delimiter: 'SPACE', record_terminator: 'SLASH', block_terminator: 'SLASH_LINE' },
      },
    ],
    ...overrides,
  };
}

function createEvents() {
  return [
    {
      event_id: 'date-1', operation: 'ADD', keyword: 'DATES', variant: 'default',
      fields: { DATE: '2025-01-01' }, provenance: [{ source_ref: 'task://cutover' }],
    },
    {
      event_id: 'control-1', operation: 'ADD', keyword: 'WCONPROD', variant: 'default',
      fields: { WELL: 'WELL-1', STATUS: 'open', CONTROL: 'ORAT', ORAT: '1000.5', BHP: { state: 'default' } },
      provenance: [{ source_ref: 'excel://rates#row=2' }],
    },
  ];
}

function codes(result) {
  return new Set((result.findings || []).map((finding) => finding.code));
}

async function main() {
  const rendered = await execute(
    'tnavigator-schedule-builder.workflow.json',
    'Render typed SCHEDULE IR deterministically',
    { schedule_render_request: { mode: 'CREATE', schema_catalogue: catalogue(), ir_events: createEvents() } },
  );
  assert.equal(rendered.status, 'rendered');
  assert.equal(rendered.changes.length, 2);
  assert.equal(rendered.changes[0].rendered_text, 'DATES\n  1 JAN 2025 /\n');
  assert.equal(rendered.changes[1].rendered_text, "WCONPROD\n  'WELL-1' OPEN ORAT 1000.5 * /\n/\n\n");
  assert(rendered.changes.every((change) => /^sha256:[a-f0-9]{64}$/.test(change.render_hash)));

  const noExpertAuthor = await execute(
    'tnavigator-schedule-builder.workflow.json',
    'Render typed SCHEDULE IR deterministically',
    { schedule_render_request: { mode: 'CREATE', schema_catalogue: catalogue({ approved_by: '', author: '' }), ir_events: createEvents() } },
  );
  assert.equal(noExpertAuthor.status, 'needs_input');
  assert(codes(noExpertAuthor).has('SCHEMA_EXPERT_AUTHOR_REQUIRED'));
  assert.equal(noExpertAuthor.changes.length, 0);

  const invalidEnumEvents = createEvents();
  invalidEnumEvents[1].fields.STATUS = 'MAYBE';
  const invalidEnum = await execute(
    'tnavigator-schedule-builder.workflow.json', 'Render typed SCHEDULE IR deterministically',
    { schedule_render_request: { mode: 'CREATE', schema_catalogue: catalogue(), ir_events: invalidEnumEvents } },
  );
  assert(codes(invalidEnum).has('IR_FIELD_VALUE_INVALID'));

  const missingFieldEvents = createEvents();
  delete missingFieldEvents[1].fields.ORAT;
  const missingField = await execute(
    'tnavigator-schedule-builder.workflow.json', 'Render typed SCHEDULE IR deterministically',
    { schedule_render_request: { mode: 'CREATE', schema_catalogue: catalogue(), ir_events: missingFieldEvents } },
  );
  assert(codes(missingField).has('IR_REQUIRED_FIELD_MISSING'));

  const unknownFieldEvents = createEvents();
  unknownFieldEvents[1].fields.UNLICENSED_GUESS = 1;
  const unknownField = await execute(
    'tnavigator-schedule-builder.workflow.json', 'Render typed SCHEDULE IR deterministically',
    { schedule_render_request: { mode: 'CREATE', schema_catalogue: catalogue(), ir_events: unknownFieldEvents } },
  );
  assert(codes(unknownField).has('IR_UNKNOWN_FIELD'));

  const createModify = createEvents();
  createModify[1].operation = 'MODIFY';
  const createModeGate = await execute(
    'tnavigator-schedule-builder.workflow.json', 'Render typed SCHEDULE IR deterministically',
    { schedule_render_request: { mode: 'CREATE', schema_catalogue: catalogue(), ir_events: createModify } },
  );
  assert(codes(createModeGate).has('CREATE_REQUIRES_ADD_ONLY'));

  const createMerge = await execute(
    'tnavigator-schedule-builder.workflow.json', 'Merge SCHEDULE draft deterministically',
    { merge_request: { mode: 'CREATE', changes: rendered.changes } },
  );
  assert.equal(createMerge.status, 'merged');
  const expectedCreate = rendered.changes.map((change) => change.rendered_text).join('');
  assert.equal(createMerge.generated_schedule, expectedCreate);

  const validated = await execute(
    'tnavigator-schedule-builder.workflow.json', 'Validate merged SCHEDULE package',
    { schedule_validation_request: {
      mode: 'CREATE',
      schedule_text: createMerge.generated_schedule,
      output_package: createMerge.output_package,
      render_result: rendered,
      ir_events: createEvents(),
      simulator_profile: { vendor: 'Rock Flow Dynamics', simulator: 'tNavigator', version: '22.2' },
      schema_catalogue: catalogue(),
      schema_catalogue_ref: catalogue().catalogue_ref,
      schema_catalogue_approved: true,
      approved_keyword_schemas: catalogue().schemas,
    } },
  );
  assert.equal(validated.status, 'valid');
  assert.deepEqual(validated.keyword_counts, { DATES: 1, WCONPROD: 1 });

  const incompleteCatalogue = await execute(
    'tnavigator-schedule-builder.workflow.json', 'Validate merged SCHEDULE package',
    { schedule_validation_request: {
      mode: 'CREATE',
      schedule_text: createMerge.generated_schedule,
      output_package: createMerge.output_package,
      render_result: rendered,
      ir_events: createEvents(),
      simulator_profile: { vendor: 'Rock Flow Dynamics', simulator: 'tNavigator', version: '22.2' },
      schema_catalogue: catalogue({ schemas: [catalogue().schemas[0]] }),
      schema_catalogue_ref: catalogue().catalogue_ref,
      schema_catalogue_approved: true,
      approved_keyword_schemas: [catalogue().schemas[0]],
    } },
  );
  assert.equal(incompleteCatalogue.status, 'invalid');
  assert(codes(incompleteCatalogue).has('KEYWORD_SCHEMA_NOT_APPROVED'));

  const baselineText = "-- keep this comment\nWCONPROD\n  'WELL-1' OPEN ORAT 900 * /\n/\n";
  const baseline = await execute(
    'tnavigator-schedule-builder.workflow.json', 'Analyze lossless baseline inventory',
    { baseline_request: { root_path: 'schedule.inc', baseline_schedule_text: baselineText } },
  );
  const target = baseline.package.files[0].nodes.find((node) => node.keyword === 'WCONPROD');
  assert(target);
  const reviseEvent = {
    ...createEvents()[1], event_id: 'control-revise', operation: 'MODIFY',
    target_node_id: target.node_id, expected_raw_hash: target.raw_hash,
  };
  const revisedRender = await execute(
    'tnavigator-schedule-builder.workflow.json', 'Render typed SCHEDULE IR deterministically',
    { schedule_render_request: { mode: 'REVISE', schema_catalogue: catalogue(), ir_events: [reviseEvent] } },
  );
  assert.equal(revisedRender.status, 'rendered');
  const revisedMerge = await execute(
    'tnavigator-schedule-builder.workflow.json', 'Merge SCHEDULE draft deterministically',
    { merge_request: { mode: 'REVISE', baseline_analysis: baseline, changes: revisedRender.changes } },
  );
  assert.equal(revisedMerge.status, 'merged');
  assert(revisedMerge.generated_schedule.startsWith('-- keep this comment\n'));
  assert(revisedMerge.generated_schedule.includes('1000.5'));
  assert(!revisedMerge.generated_schedule.includes(' 900 '));

  const staleTarget = { ...reviseEvent, expected_raw_hash: `sha256:${'f'.repeat(64)}` };
  const staleRender = await execute(
    'tnavigator-schedule-builder.workflow.json', 'Render typed SCHEDULE IR deterministically',
    { schedule_render_request: { mode: 'REVISE', schema_catalogue: catalogue(), ir_events: [staleTarget] } },
  );
  assert.equal(staleRender.status, 'rendered');
  const staleMerge = await execute(
    'tnavigator-schedule-builder.workflow.json', 'Merge SCHEDULE draft deterministically',
    { merge_request: { mode: 'REVISE', baseline_analysis: baseline, changes: staleRender.changes } },
  );
  assert.equal(staleMerge.status, 'needs_input');
  assert(codes(staleMerge).has('TARGET_HASH_REQUIRED_OR_STALE'));

  const noTarget = { ...reviseEvent };
  delete noTarget.target_node_id;
  const noTargetRender = await execute(
    'tnavigator-schedule-builder.workflow.json', 'Render typed SCHEDULE IR deterministically',
    { schedule_render_request: { mode: 'REVISE', schema_catalogue: catalogue(), ir_events: [noTarget] } },
  );
  assert(codes(noTargetRender).has('IR_TARGET_IDENTITY_REQUIRED'));

  // Stage-3 CREATE: within each DATES block, keywords emit in fixed algorithm order (not IR order).
  const orderSchemas = [
    catalogue().schemas[0], // DATES
    {
      schema_id: 'fixture:APPLYSCRIPT:v1', schema_revision: 'fixture-1', keyword: 'APPLYSCRIPT', variant: 'default', citation,
      fields: [
        { name: 'SCRIPT_FILE', position: 1, type: 'string', required: true, quote: 'single' },
        { name: 'FUNCTION_NAME', position: 2, type: 'string', required: true, quote: 'single' },
      ],
      semantics: { period: 'ANY', clock: { uses_current: true } },
      layout: { newline: 'LF', indent: '  ', delimiter: 'SPACE', record_terminator: 'SLASH', block_terminator: 'SLASH_LINE' },
    },
    {
      schema_id: 'fixture:WELSPECS:v1', schema_revision: 'fixture-1', keyword: 'WELSPECS', variant: 'default', citation,
      fields: [
        { name: 'WELL', position: 1, type: 'string', required: true, quote: 'single' },
        { name: 'GROUP', position: 2, type: 'string', required: true, quote: 'single' },
      ],
      semantics: { period: 'ANY', clock: { uses_current: true } },
      layout: { newline: 'LF', indent: '  ', delimiter: 'SPACE', record_terminator: 'SLASH', block_terminator: 'SLASH_LINE' },
    },
    catalogue().schemas[1], // WCONPROD
  ];
  const scrambled = [
    {
      event_id: 'd1', operation: 'ADD', keyword: 'DATES', variant: 'default',
      fields: { DATE: '2025-01-01' }, provenance: [{ source_ref: 'task://order' }],
    },
    {
      event_id: 'a1', operation: 'ADD', keyword: 'APPLYSCRIPT', variant: 'default',
      fields: { SCRIPT_FILE: 'hook.py', FUNCTION_NAME: 'on_step' }, provenance: [{ source_ref: 'task://order' }],
    },
    {
      event_id: 'w1', operation: 'ADD', keyword: 'WCONPROD', variant: 'default',
      fields: { WELL: 'WELL-1', STATUS: 'OPEN', CONTROL: 'ORAT', ORAT: '10', BHP: { state: 'default' } },
      provenance: [{ source_ref: 'task://order' }],
    },
    {
      event_id: 's1', operation: 'ADD', keyword: 'WELSPECS', variant: 'default',
      fields: { WELL: 'WELL-1', GROUP: 'G1' }, provenance: [{ source_ref: 'task://order' }],
    },
    {
      event_id: 'd2', operation: 'ADD', keyword: 'DATES', variant: 'default',
      fields: { DATE: '2025-02-01' }, provenance: [{ source_ref: 'task://order' }],
    },
    {
      event_id: 'w2', operation: 'ADD', keyword: 'WCONPROD', variant: 'default',
      fields: { WELL: 'WELL-2', STATUS: 'OPEN', CONTROL: 'ORAT', ORAT: '20', BHP: { state: 'default' } },
      provenance: [{ source_ref: 'task://order' }],
    },
  ];
  const ordered = await execute(
    'tnavigator-schedule-builder.workflow.json',
    'Render typed SCHEDULE IR deterministically',
    { schedule_render_request: { mode: 'CREATE', schema_catalogue: catalogue({ schemas: orderSchemas }), ir_events: scrambled } },
  );
  assert.equal(ordered.status, 'rendered');
  assert.deepEqual(
    ordered.changes.map((c) => c.keyword),
    ['DATES', 'WELSPECS', 'WCONPROD', 'APPLYSCRIPT', 'DATES', 'WCONPROD'],
  );

  const endactioSchema = {
    schema_id: 'fixture:ENDACTIO:end',
    schema_revision: 'fixture-1',
    keyword: 'ENDACTIO',
    variant: 'end',
    citation,
    parser: { token_width: 0 },
    fields: [],
    semantics: { period: 'ANY', clock: { uses_current: true } },
    layout: { newline: 'LF', indent: '', delimiter: 'SPACE', record_terminator: 'NONE', block_terminator: 'NONE' },
  };
  const endactio = await execute(
    'tnavigator-schedule-builder.workflow.json',
    'Render typed SCHEDULE IR deterministically',
    {
      schedule_render_request: {
        mode: 'CREATE',
        schema_catalogue: catalogue({ schemas: [endactioSchema] }),
        ir_events: [{
          event_id: 'end-1', operation: 'ADD', keyword: 'ENDACTIO', variant: 'end',
          fields: {}, provenance: [{ source_ref: 'task://action-close' }],
        }],
      },
    },
  );
  assert.equal(endactio.status, 'rendered');
  assert.equal(endactio.changes[0].rendered_text, 'ENDACTIO\n');

  console.log('SCHEDULE catalogue renderer runtime smoke: 14 scenarios passed');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
