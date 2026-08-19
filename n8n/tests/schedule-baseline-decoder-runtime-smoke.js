'use strict';

// Synthetic schemas only. These fixtures exercise the portable decoder and
// two-phase semantic replay; they are not tNavigator vendor record layouts.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || '/workspace';
const workflows = path.join(workspace, 'n8n', 'workflows', 'core');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function source(file, name) {
  const workflow = JSON.parse(fs.readFileSync(path.join(workflows, file), 'utf8'));
  const node = workflow.nodes.find((candidate) => candidate.name === name);
  assert(node && node.type === 'n8n-nodes-base.code', `missing Code node: ${name}`);
  return node.parameters.jsCode;
}

const analyzeFn = new AsyncFunction('$json', source(
  'tnavigator-schedule-builder.workflow.json', 'Analyze lossless baseline inventory',
));
const decodeFn = new AsyncFunction('$json', source(
  'tnavigator-schedule-builder.workflow.json', 'Decode typed baseline records',
));
const validateFn = new AsyncFunction('$json', source(
  'tnavigator-schedule-builder.workflow.json', 'Validate merged SCHEDULE package',
));

const sourceHash = `sha256:${'a'.repeat(64)}`;
const catalogueHash = `sha256:${'b'.repeat(64)}`;
const citation = {
  document_id: 'synthetic-decoder-test', document_revision: '22.2',
  source_hash: sourceHash, page: 'fixture', heading: 'TEST ONLY',
};
const str = (name, position) => ({ name, position, type: 'string', required: true, quote: 'single' });
const raw = (name, position, type = 'number') => ({ name, position, type, required: true });

function catalogue(overrides = {}) {
  return {
    contract: 'schedule_schema_catalogue', contract_version: '1.0',
    catalogue_ref: 'catalogue://test/decoder/22.2', catalogue_hash: catalogueHash,
    source_hash: sourceHash, simulator_profile: { vendor: 'Rock Flow Dynamics', simulator: 'tNavigator', version: '22.2' },
    approved: true, approved_by: 'test-engineer', approval_gate_id: 'test-gate',
    non_record_keywords: ['SCHEDULE'],
    schemas: [
      {
        schema_id: 'fixture:DATES', schema_revision: '1', keyword: 'DATES', variant: 'default', citation,
        parser: { token_width: 3 },
        fields: [{ name: 'DATE', position: 1, type: 'date', format: 'DD MON YYYY', required: true, parse_token_width: 3 }],
        semantics: { period: 'ANY', clock: { sets_from_field: 'DATE' } },
      },
      {
        schema_id: 'fixture:GRUPTREE', schema_revision: '1', keyword: 'GRUPTREE', variant: 'default', citation,
        fields: [str('CHILD', 1), str('PARENT', 2)],
        semantics: {
          period: 'ANY', clock: { uses_current: true },
          definitions: [
            { entity_type: 'group', id_field: 'CHILD', action: 'UPSERT' },
            { entity_type: 'group', id_field: 'PARENT', action: 'UPSERT' },
          ],
          hierarchy_edges: [{ graph: 'groups', child_entity_type: 'group', parent_entity_type: 'group', child_field: 'CHILD', parent_field: 'PARENT' }],
        },
      },
      {
        schema_id: 'fixture:WELSPECS', schema_revision: '1', keyword: 'WELSPECS', variant: 'default', citation,
        fields: [str('WELL', 1), str('GROUP', 2)],
        semantics: {
          period: 'ANY', clock: { uses_current: true },
          references: [{ entity_type: 'group', id_field: 'GROUP', required: true }],
          definitions: [{ entity_type: 'well', id_field: 'WELL', action: 'UPSERT' }],
        },
      },
      {
        schema_id: 'fixture:WCONPROD:ORAT', schema_revision: '1', keyword: 'WCONPROD', variant: 'orat', citation,
        parser: { match: { CONTROL: 'ORAT' } },
        fields: [str('WELL', 1), { ...raw('CONTROL', 2, 'enum'), enum: ['ORAT', 'BHP'] }, raw('RATE', 3), { ...raw('BHP', 4), default_allowed: true }],
        semantics: {
          period: 'FORECAST', clock: { uses_current: true },
          references: [{ entity_type: 'well', id_field: 'WELL', required: true }],
          prerequisites: [{ keyword: 'WELSPECS', scope: 'ENTITY', entity_field: 'WELL', prerequisite_entity_field: 'WELL' }],
          state_assignments: [{ namespace: 'well-control', entity_type: 'well', entity_field: 'WELL', key_fields: ['CONTROL'], value_fields: ['RATE', 'BHP'] }],
        },
      },
      {
        schema_id: 'fixture:WCONPROD:BHP', schema_revision: '1', keyword: 'WCONPROD', variant: 'bhp', citation,
        parser: { match: { CONTROL: 'BHP' } },
        fields: [str('WELL', 1), { ...raw('CONTROL', 2, 'enum'), enum: ['ORAT', 'BHP'] }, raw('RATE', 3), { ...raw('BHP', 4), default_allowed: true }],
        semantics: {
          period: 'FORECAST', clock: { uses_current: true },
          references: [{ entity_type: 'well', id_field: 'WELL', required: true }],
          prerequisites: [{ keyword: 'WELSPECS', scope: 'ENTITY', entity_field: 'WELL', prerequisite_entity_field: 'WELL' }],
          state_assignments: [{ namespace: 'well-control', entity_type: 'well', entity_field: 'WELL', key_fields: ['CONTROL'], value_fields: ['RATE', 'BHP'] }],
        },
      },
    ],
    ...overrides,
  };
}

const baseText = [
  'SCHEDULE',
  'DATES',
  '  1 JAN 2024 /',
  'GRUPTREE',
  "  'FIELD' 'ROOT' /",
  'WELSPECS',
  "  'WELL''A' 'FIELD' /",
  'INCLUDE',
  "  'controls.inc' /",
  '',
].join('\n');
const includedText = [
  'DATES',
  '  1 JAN 2025 /',
  'WCONPROD',
  "  'WELL''A' ORAT 100 * / -- explicit default marker",
  "  'WELL''A' ORAT 2*100 / -- repeat expansion",
  '',
].join('\n');

async function analyze(text = baseText, includeFiles = [{ path: 'controls.inc', text: includedText }]) {
  const rows = await analyzeFn({ baseline_request: { baseline_schedule_text: text, include_files: includeFiles } });
  assert.equal(rows.length, 1);
  return rows[0].json;
}

async function decode(analysis, cat = catalogue(), overrides = {}) {
  const request = {
    baseline_analysis: analysis, schema_catalogue: cat,
    change_effective_from: '2025-01-01', model_start_date: '2020-01-01',
    initial_semantic_snapshot: {}, ...overrides,
  };
  const rows = await decodeFn({ baseline_decode_request: request });
  assert.equal(rows.length, 1);
  return rows[0].json;
}

async function replayPrefix(decoded, cat = catalogue(), overrides = {}) {
  const request = {
    validation_phase: 'BASELINE_PREFIX', mode: 'CREATE',
    ir_events: decoded.prefix_ir_events, baseline_decode_result: decoded,
    baseline_package_hash: decoded.baseline_package_hash,
    change_effective_from: decoded.change_effective_from,
    model_start_date: decoded.model_start_date,
    simulator_profile: { vendor: 'Rock Flow Dynamics', simulator: 'tNavigator', version: '22.2' },
    schema_catalogue: cat, schema_catalogue_ref: cat.catalogue_ref,
    schema_catalogue_approved: true, approved_keyword_schemas: cat.schemas,
    initial_semantic_snapshot: {}, render_result: { status: 'baseline_decoded', catalogue_hash: cat.catalogue_hash },
    temporal_policy: { forecast_start: '2025-01-01' }, ...overrides,
  };
  const rows = await validateFn({ schedule_validation_request: request });
  assert.equal(rows.length, 1);
  return rows[0].json;
}

async function validateCandidate(events, snapshot, cat = catalogue()) {
  const text = "DATES\n  1 JAN 2025 /\nWCONPROD\n  'WELL''A' ORAT 200 100 /\n";
  const request = {
    validation_phase: 'CANDIDATE', mode: 'REVISE', schedule_text: text,
    output_package: { package_hash: `sha256:${'c'.repeat(64)}`, files: [{ file_ref: 'schedule.inc', text }] },
    render_result: { status: 'rendered', catalogue_hash: cat.catalogue_hash, rendered_records: events.map((e) => ({ event_id: e.event_id })) },
    ir_events: events, simulator_profile: { vendor: 'Rock Flow Dynamics', simulator: 'tNavigator', version: '22.2' },
    schema_catalogue: cat, schema_catalogue_ref: cat.catalogue_ref, schema_catalogue_approved: true,
    approved_keyword_schemas: cat.schemas, semantic_baseline_snapshot: snapshot,
    baseline_package_hash: snapshot.package_hash, temporal_policy: { forecast_start: '2025-01-01' },
  };
  const rows = await validateFn({ schedule_validation_request: request });
  return rows[0].json;
}

const codes = (result) => new Set((result.findings || []).map((finding) => finding.code));

async function main() {
  const analysis = await analyze();
  assert.equal(analysis.status, 'analyzed');
  assert.deepEqual(analysis.reachable_files, ['controls.inc', 'schedule.inc']);

  const good = await decode(analysis);
  assert.equal(good.status, 'decoded');
  assert.deepEqual(good.execution_files, ['schedule.inc', 'controls.inc']);
  assert.deepEqual(good.decoded_records.map((r) => r.keyword), ['DATES', 'GRUPTREE', 'WELSPECS', 'DATES', 'WCONPROD', 'WCONPROD']);
  assert.equal(good.decoded_records[2].fields.WELL, "WELL'A");
  assert.equal(good.decoded_records[4].fields.RATE, 100);
  assert.deepEqual(good.decoded_records[4].fields.BHP, { state: 'default' });
  assert.equal(good.decoded_records[4].variant, 'orat');
  assert.equal(good.decoded_records[5].fields.RATE, 100);
  assert.equal(good.decoded_records[5].fields.BHP, 100);
  assert.equal(good.prefix_records.length, 3);
  assert.equal(good.suffix_records[0].effective_at, '2025-01-01');

  const replay1 = await replayPrefix(good);
  const replay2 = await replayPrefix(good);
  assert.equal(replay1.status, 'valid');
  assert.equal(replay1.validation_phase, 'BASELINE_PREFIX');
  assert.equal(replay1.semantic_state_snapshot.snapshot_kind, 'PRE_CHANGE_BOUNDARY');
  assert.equal(replay1.semantic_state_snapshot.change_effective_from, '2025-01-01');
  assert.equal(replay1.semantic_state_snapshot.boundary_hash, replay2.semantic_state_snapshot.boundary_hash);
  assert(replay1.semantic_state_snapshot.entities.some((e) => e.entity_type === 'well' && e.entity_id === "WELL'A"));

  const candidateEvents = [
    { event_id: 'candidate-date', operation: 'ADD', keyword: 'DATES', variant: 'default', fields: { DATE: '2025-01-01' } },
    { event_id: 'candidate-control', operation: 'ADD', keyword: 'WCONPROD', variant: 'orat', fields: { WELL: "WELL'A", CONTROL: 'ORAT', RATE: 200, BHP: 100 } },
  ];
  const candidate = await validateCandidate(candidateEvents, replay1.semantic_state_snapshot);
  assert.equal(candidate.status, 'valid');

  const staleCatalogue = catalogue({ catalogue_hash: `sha256:${'e'.repeat(64)}` });
  const stale = await validateCandidate(candidateEvents, replay1.semantic_state_snapshot, staleCatalogue);
  assert(codes(stale).has('SEMANTIC_SNAPSHOT_CATALOGUE_MISMATCH'));

  const tamperedSnapshot = {
    ...replay1.semantic_state_snapshot,
    entities: [...replay1.semantic_state_snapshot.entities, { entity_type: 'well', entity_id: 'INJECTED' }],
  };
  const tampered = await validateCandidate(candidateEvents, tamperedSnapshot);
  assert(codes(tampered).has('SEMANTIC_SNAPSHOT_HASH_MISMATCH'));

  const beforeBoundaryEvents = [
    { ...candidateEvents[0], fields: { DATE: '2024-06-01' } },
    { ...candidateEvents[1] },
  ];
  const beforeBoundary = await validateCandidate(beforeBoundaryEvents, replay1.semantic_state_snapshot);
  assert(codes(beforeBoundary).has('SEMANTIC_EVENT_BEFORE_CHANGE_BOUNDARY'));

  const ambiguousSchemas = catalogue();
  ambiguousSchemas.schemas = [
    ...ambiguousSchemas.schemas.filter((schema) => schema.keyword !== 'WCONPROD'),
    { ...ambiguousSchemas.schemas.find((schema) => schema.keyword === 'WCONPROD'), variant: 'twin-a', schema_id: 'expert:WCONPROD:twin-a', parser: {} },
    { ...ambiguousSchemas.schemas.find((schema) => schema.keyword === 'WCONPROD'), variant: 'twin-b', schema_id: 'expert:WCONPROD:twin-b', parser: {} },
  ];
  const ambiguous = await decode(analysis, ambiguousSchemas);
  assert(codes(ambiguous).has('BASELINE_RECORD_VARIANT_AMBIGUOUS'));

  const preferred = catalogue();
  preferred.schemas = preferred.schemas.map((schema) => (
    schema.keyword === 'WCONPROD'
      ? { ...schema, parser: { ...(schema.parser || {}), allow_trailing_omission: true, token_width: 20 } }
      : schema
  ));
  // Add a competing WCONPROD variant with a mismatched CONTROL enum so scoring keeps the original.
  preferred.schemas.push({
    ...preferred.schemas.find((schema) => schema.keyword === 'WCONPROD'),
    variant: 'wrat',
    schema_id: 'expert:WCONPROD:wrat',
    fields: preferred.schemas.find((schema) => schema.keyword === 'WCONPROD').fields.map((field) => (
      field.name === 'CONTROL' ? { ...field, enum: ['WRAT'] } : field
    )),
  });
  const disambiguated = await decode(analysis, preferred);
  assert.equal(disambiguated.status, 'decoded');
  assert(!codes(disambiguated).has('BASELINE_RECORD_VARIANT_AMBIGUOUS'));

  const malformedAnalysis = await analyze(baseText.replace("'FIELD' 'ROOT' /", "'FIELD' 'ROOT' EXTRA /"));
  const malformed = await decode(malformedAnalysis);
  assert(codes(malformed).has('BASELINE_RECORD_SCHEMA_MISMATCH'));

  const missingSemantics = catalogue();
  delete missingSemantics.schemas.find((schema) => schema.keyword === 'WELSPECS').semantics;
  const noSemantics = await decode(analysis, missingSemantics);
  assert(codes(noSemantics).has('BASELINE_SCHEMA_INVALID'));

  const duplicateIncludeText = baseText + "INCLUDE\n  'controls.inc' /\n";
  const duplicateIncludeAnalysis = await analyze(duplicateIncludeText);
  const duplicateInclude = await decode(duplicateIncludeAnalysis);
  assert(codes(duplicateInclude).has('INCLUDE_MULTIPLE_EXPANSION'));

  const unknownAnalysis = await analyze(baseText.replace('WELSPECS', 'UNKNOWNKW'));
  const unknown = await decode(unknownAnalysis);
  assert(codes(unknown).has('OPAQUE_BASELINE_SEMANTICS_UNAVAILABLE'));
  assert.equal(unknown.status, 'decoded');
  assert.equal(unknown.findings.find((f) => f.code === 'OPAQUE_BASELINE_SEMANTICS_UNAVAILABLE').severity, 'warning');
  assert.equal(unknown.hard_blockers.includes('OPAQUE_BASELINE_SEMANTICS_UNAVAILABLE'), false);
  const targetedOpaque = await decode(unknownAnalysis, catalogue(), { requested_keyword_scope: ['UNKNOWNKW'] });
  assert.equal(targetedOpaque.status, 'needs_input');
  assert.equal(targetedOpaque.findings.find((f) => f.code === 'OPAQUE_BASELINE_SEMANTICS_UNAVAILABLE').severity, 'error');

  const missingBodyAnalysis = await analyze(baseText, []);
  assert.equal(missingBodyAnalysis.status, 'analyzed');
  const missingBodyFinding = (missingBodyAnalysis.findings || []).find((f) => f.code === 'INCLUDE_NOT_FOUND');
  assert.equal(missingBodyFinding.severity, 'warning');
  const missingBody = await decode(missingBodyAnalysis);
  assert.equal(missingBody.status, 'decoded');
  assert.equal(missingBody.findings.find((f) => f.code === 'INCLUDE_NOT_FOUND').severity, 'warning');
  assert.equal(missingBody.hard_blockers.includes('INCLUDE_NOT_FOUND'), false);
  assert.deepEqual(missingBody.decoded_records.map((r) => r.keyword), ['DATES', 'GRUPTREE', 'WELSPECS']);

  const noDates = await decode(analysis, catalogue(), { change_effective_from: '', model_start_date: '' });
  assert.equal(noDates.status, 'decoded');
  assert(!codes(noDates).has('CHANGE_EFFECTIVE_FROM_REQUIRED'));
  assert(!codes(noDates).has('MODEL_START_DATE_REQUIRED'));
  assert.equal(noDates.change_effective_from, null);
  assert.equal(noDates.model_start_date, null);
  const noDatesReplay = await replayPrefix(noDates);
  assert.equal(noDatesReplay.status, 'valid', JSON.stringify(noDatesReplay.hard_blockers || noDatesReplay.findings));

  const preDatesText = [
    'SCHEDULE',
    'GRUPTREE',
    "  'FIELD' 'ROOT' /",
    'DATES',
    '  1 JAN 2024 /',
    'WELSPECS',
    "  'WELL''A' 'FIELD' /",
    'WCONPROD',
    "  'WELL''A' ORAT 100 * /",
    '',
  ].join('\n');
  const preDatesAnalysis = await analyze(preDatesText, []);
  assert.equal(preDatesAnalysis.status, 'analyzed');
  const preDates = await decode(preDatesAnalysis, catalogue(), { change_effective_from: '', model_start_date: '' });
  assert.equal(preDates.status, 'decoded', JSON.stringify(preDates.hard_blockers || preDates.findings));
  assert(!codes(preDates).has('BASELINE_EFFECTIVE_DATE_REQUIRED'));
  const grup = preDates.decoded_records.find((r) => r.keyword === 'GRUPTREE');
  assert.equal(grup.effective_at, null);
  assert.equal(grup.clock_state, 'pre_first_dates');
  const wcon = preDates.decoded_records.find((r) => r.keyword === 'WCONPROD');
  assert.equal(wcon.effective_at, '2024-01-01');
  assert.equal(wcon.clock_state, 'dated');
  const preReplay = await replayPrefix(preDates);
  assert.equal(preReplay.status, 'valid', JSON.stringify(preReplay.hard_blockers || preReplay.findings));

  const lf = await analyze(baseText, [{ path: 'controls.inc', text: includedText }]);
  const crlf = await analyze(baseText.replace(/\n/g, '\r\n'), [{ path: 'controls.inc', text: includedText.replace(/\n/g, '\r\n') }]);
  assert.equal(lf.package.files[0].manifest.line_endings, 'LF');
  assert.equal(crlf.package.files[0].manifest.line_endings, 'CRLF');
  const lfDecoded = await decode(lf);
  const crlfDecoded = await decode(crlf);
  assert.deepEqual(lfDecoded.decoded_records.map((r) => [r.keyword, r.fields]), crlfDecoded.decoded_records.map((r) => [r.keyword, r.fields]));
  assert(lf.package.files[0].nodes.some((n) => n.raw.includes('\n')));
  assert(crlf.package.files[0].nodes.some((n) => n.raw.includes('\r\n')));

  console.log('SCHEDULE baseline decoder and two-phase replay smoke: 19 scenarios passed');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
