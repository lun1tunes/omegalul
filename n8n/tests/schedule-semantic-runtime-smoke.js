'use strict';

// Synthetic catalogue fixtures exercise the generic semantic runtime.  They
// are deliberately not vendor field layouts and must never be used as 22.2
// production grammar.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || '/workspace';
const workflow = JSON.parse(fs.readFileSync(
  path.join(workspace, 'n8n', 'workflows', 'tnavigator-schedule-builder.workflow.json'),
  'utf8',
));
const node = workflow.nodes.find((candidate) => candidate.name === 'Validate merged SCHEDULE package');
assert(node && node.type === 'n8n-nodes-base.code');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const execute = new AsyncFunction('$json', node.parameters.jsCode);

const sourceHash = `sha256:${'a'.repeat(64)}`;
const catalogueHash = `sha256:${'b'.repeat(64)}`;
const packageHash = `sha256:${'c'.repeat(64)}`;
const boundaryHash = `sha256:${'d'.repeat(64)}`;
const citation = {
  document_id: 'synthetic-semantic-test', document_revision: '22.2',
  source_hash: sourceHash, page: 'fixture', heading: 'TEST ONLY',
};
const field = (name, position, type = 'string') => ({ name, position, type, required: true });

function catalogue(overrides = {}) {
  return {
    contract: 'schedule_schema_catalogue', contract_version: '1.0',
    catalogue_ref: 'catalogue://test/semantic/22.2', catalogue_hash: catalogueHash,
    source_hash: sourceHash, access_scope: 'test',
    simulator_profile: { vendor: 'Rock Flow Dynamics', simulator: 'tNavigator', version: '22.2' },
    approved: true, approved_by: 'test-engineer', approval_gate_id: 'test-gate',
    schemas: [
      {
        schema_id: 'fixture:DATES', schema_revision: '1', keyword: 'DATES', variant: 'default', citation,
        fields: [field('DATE', 1, 'date')],
        semantics: { period: 'ANY', clock: { sets_from_field: 'DATE' } },
      },
      {
        schema_id: 'fixture:GRUPTREE', schema_revision: '1', keyword: 'GRUPTREE', variant: 'default', citation,
        fields: [field('CHILD', 1), field('PARENT', 2)],
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
        fields: [field('WELL', 1), field('GROUP', 2)],
        semantics: {
          period: 'ANY', clock: { uses_current: true },
          references: [{ entity_type: 'group', id_field: 'GROUP', required: true }],
          definitions: [{ entity_type: 'well', id_field: 'WELL', action: 'UPSERT' }],
        },
      },
      {
        schema_id: 'fixture:WCONHIST', schema_revision: '1', keyword: 'WCONHIST', variant: 'default', citation,
        fields: [field('WELL', 1), field('RATE', 2, 'number')],
        semantics: {
          period: 'HISTORY', clock: { uses_current: true },
          references: [{ entity_type: 'well', id_field: 'WELL', required: true }],
          prerequisites: [{ keyword: 'WELSPECS', scope: 'ENTITY', entity_field: 'WELL', prerequisite_entity_field: 'WELL' }],
          state_assignments: [{ namespace: 'well-control', entity_type: 'well', entity_field: 'WELL', key_fields: [], value_fields: ['RATE'] }],
        },
      },
      {
        schema_id: 'fixture:WCONPROD', schema_revision: '1', keyword: 'WCONPROD', variant: 'default', citation,
        fields: [field('WELL', 1), field('CONTROL', 2), field('RATE', 3, 'number')],
        semantics: {
          period: 'FORECAST', clock: { uses_current: true },
          references: [{ entity_type: 'well', id_field: 'WELL', required: true }],
          prerequisites: [{ keyword: 'WELSPECS', scope: 'ENTITY', entity_field: 'WELL', prerequisite_entity_field: 'WELL' }],
          state_assignments: [{ namespace: 'well-control', entity_type: 'well', entity_field: 'WELL', key_fields: ['CONTROL'], value_fields: ['RATE'] }],
          numeric_constraints: [{ field: 'RATE', min: 0 }],
          wildcard_rules: [{ field: 'WELL', entity_type: 'well' }],
        },
      },
      {
        schema_id: 'fixture:WELLTRACK', schema_revision: '1', keyword: 'WELLTRACK', variant: 'default', citation,
        fields: [field('WELL', 1), field('MD_FROM', 2, 'number'), field('MD_TO', 3, 'number')],
        semantics: {
          period: 'ANY', clock: { uses_current: true },
          references: [{ entity_type: 'well', id_field: 'WELL', required: true }],
          interval_rules: [{ namespace: 'well-track-md', entity_type: 'well', entity_field: 'WELL', start_field: 'MD_FROM', end_field: 'MD_TO', scope: 'EFFECTIVE_DATE', allow_touching: true }],
        },
      },
      {
        schema_id: 'fixture:WECON:retire', schema_revision: '1', keyword: 'WECON', variant: 'retire', citation,
        fields: [field('WELL', 1)],
        semantics: { period: 'ANY', clock: { uses_current: true }, lifecycle_effects: [{ entity_type: 'well', id_field: 'WELL', action: 'RETIRE' }] },
      },
      {
        schema_id: 'fixture:WECON:reactivate', schema_revision: '1', keyword: 'WECON', variant: 'reactivate', citation,
        fields: [field('WELL', 1)],
        semantics: { period: 'ANY', clock: { uses_current: true }, lifecycle_effects: [{ entity_type: 'well', id_field: 'WELL', action: 'REACTIVATE' }] },
      },
      {
        schema_id: 'fixture:WTEST', schema_revision: '1', keyword: 'WTEST', variant: 'default', citation,
        fields: [field('WELL', 1), field('INTERVAL', 2, 'number')],
        semantics: {
          period: 'ANY', clock: { uses_current: true },
          references: [{ entity_type: 'well', id_field: 'WELL', required: true }],
          numeric_constraints: [{ field: 'INTERVAL', min: 0, min_exclusive: true }],
          wildcard_rules: [{ field: 'WELL', entity_type: 'well' }],
        },
      },
    ],
    ...overrides,
  };
}

const event = (event_id, keyword, fields, operation = 'ADD') => ({
  event_id, keyword, variant: 'default', operation, fields,
  provenance: [{ source_ref: `fixture://${event_id}` }],
});
const date = (id, value, operation = 'ADD') => event(id, 'DATES', { DATE: value }, operation);
const initial = (extra = {}) => ({
  contract: 'schedule_semantic_snapshot', contract_version: '1.0',
  catalogue_hash: catalogueHash, package_hash: 'base-model', replay_through: null,
  entities: [{ entity_type: 'group', entity_id: 'FIELD', created_at: '2000-01-01' }],
  hierarchy_edges: [], state_assignments: [], keyword_occurrences: [], ...extra,
});
const temporal = { history_end: '2024-12-31', forecast_start: '2025-01-01' };

function scheduleText(events) {
  return events.filter((e) => ['ADD', 'MODIFY'].includes(e.operation)).map((e) => {
    if (e.keyword === 'DATES') {
      const [y, m, d] = e.fields.DATE.split('-');
      const month = ['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'][Number(m) - 1];
      return `DATES\n  ${Number(d)} ${month} ${y} /\n`;
    }
    return `${e.keyword}\n  FIXTURE /\n/\n`;
  }).join('');
}

async function validate(events, overrides = {}) {
  const cat = overrides.schema_catalogue || catalogue();
  const text = scheduleText(events);
  const request = {
    mode: overrides.mode || 'CREATE', schedule_text: text,
    output_package: { contract: 'schedule_package', contract_version: '1.0', root_path: 'schedule.inc', package_hash: packageHash, files: [{ file_ref: 'schedule.inc', text }] },
    render_result: { status: 'rendered', catalogue_hash: cat.catalogue_hash, rendered_records: events.filter((e) => ['ADD', 'MODIFY'].includes(e.operation)).map((e) => ({ event_id: e.event_id, keyword: e.keyword })) },
    ir_events: events, simulator_profile: { vendor: 'Rock Flow Dynamics', simulator: 'tNavigator', version: '22.2' },
    schema_catalogue: cat, schema_catalogue_ref: cat.catalogue_ref, schema_catalogue_approved: true,
    approved_keyword_schemas: cat.schemas, temporal_policy: overrides.temporal_policy || temporal,
    initial_semantic_snapshot: overrides.initial_semantic_snapshot === undefined ? initial() : overrides.initial_semantic_snapshot,
    semantic_baseline_snapshot: overrides.semantic_baseline_snapshot,
    baseline_package_hash: overrides.baseline_package_hash,
  };
  const result = await execute({ schedule_validation_request: request });
  assert(Array.isArray(result) && result.length === 1 && result[0].json);
  return result[0].json;
}

const codes = (result) => new Set((result.findings || []).map((finding) => finding.code));
function validCreateEvents() {
  return [
    date('d-hist', '2024-12-31'),
    event('well', 'WELSPECS', { WELL: 'WELL-1', GROUP: 'FIELD' }),
    event('hist', 'WCONHIST', { WELL: 'WELL-1', RATE: 90 }),
    date('d-forecast', '2025-01-01'),
    event('prod', 'WCONPROD', { WELL: 'WELL-1', CONTROL: 'ORAT', RATE: 100 }),
  ];
}

async function main() {
  const good = await validate(validCreateEvents());
  assert.equal(good.status, 'valid');
  assert.equal(good.score.semantics, 100);
  assert.equal(good.semantic_replay.events, 5);
  assert(good.semantic_state_snapshot.snapshot_hash.startsWith('sha256:'));
  assert(good.semantic_state_snapshot.entities.some((e) => e.entity_type === 'well' && e.entity_id === 'WELL-1'));

  const missingEntity = await validate([date('d', '2025-01-01'), event('p', 'WCONPROD', { WELL: 'UNKNOWN', CONTROL: 'ORAT', RATE: 1 })]);
  assert(codes(missingEntity).has('ENTITY_REFERENCE_MISSING'));

  const missingDependencySnapshot = initial({
    entities: [
      { entity_type: 'group', entity_id: 'FIELD', created_at: '2000-01-01' },
      { entity_type: 'well', entity_id: 'WELL-1', created_at: '2000-01-01' },
    ],
  });
  const missingDependency = await validate(
    [date('d', '2025-01-01'), event('p', 'WCONPROD', { WELL: 'WELL-1', CONTROL: 'ORAT', RATE: 1 })],
    { initial_semantic_snapshot: missingDependencySnapshot },
  );
  assert(codes(missingDependency).has('KEYWORD_PREREQUISITE_MISSING'));

  const cycle = await validate([
    date('d', '2024-01-01'),
    event('ab', 'GRUPTREE', { CHILD: 'A', PARENT: 'B' }),
    event('ba', 'GRUPTREE', { CHILD: 'B', PARENT: 'A' }),
  ]);
  assert(codes(cycle).has('HIERARCHY_CYCLE'));

  const parentConflict = await validate([
    date('d', '2024-01-01'),
    event('ab', 'GRUPTREE', { CHILD: 'A', PARENT: 'B' }),
    event('ac', 'GRUPTREE', { CHILD: 'A', PARENT: 'C' }),
  ]);
  assert(codes(parentConflict).has('HIERARCHY_PARENT_CONFLICT'));

  const lateHistory = await validate([
    date('d', '2025-01-01'), event('well', 'WELSPECS', { WELL: 'WELL-1', GROUP: 'FIELD' }),
    event('h', 'WCONHIST', { WELL: 'WELL-1', RATE: 1 }),
  ]);
  assert(codes(lateHistory).has('HISTORY_EVENT_AFTER_CUTOVER'));

  const earlyForecast = await validate([
    date('d', '2024-12-31'), event('well', 'WELSPECS', { WELL: 'WELL-1', GROUP: 'FIELD' }),
    event('p', 'WCONPROD', { WELL: 'WELL-1', CONTROL: 'ORAT', RATE: 1 }),
  ]);
  assert(codes(earlyForecast).has('FORECAST_EVENT_BEFORE_START'));

  const duplicateEvents = validCreateEvents();
  duplicateEvents.push(event('prod-duplicate', 'WCONPROD', { WELL: 'WELL-1', CONTROL: 'ORAT', RATE: 100 }));
  const duplicate = await validate(duplicateEvents);
  assert(codes(duplicate).has('DUPLICATE_STATE_ASSIGNMENT'));

  const conflictEvents = validCreateEvents();
  conflictEvents.push(event('prod-conflict', 'WCONPROD', { WELL: 'WELL-1', CONTROL: 'ORAT', RATE: 200 }));
  const conflict = await validate(conflictEvents);
  assert(codes(conflict).has('CONFLICTING_STATE_ASSIGNMENT'));

  const numeric = await validate([
    date('d-num', '2025-01-01'), event('well-num', 'WELSPECS', { WELL: 'WELL-1', GROUP: 'FIELD' }),
    event('test-num', 'WTEST', { WELL: 'WELL-1', INTERVAL: 0 }),
  ]);
  assert(codes(numeric).has('NUMERIC_VALUE_BELOW_MIN'));

  const wildcard = await validate([
    date('d-wild', '2025-01-01'), event('well-wild', 'WELSPECS', { WELL: 'WELL-1', GROUP: 'FIELD' }),
    event('test-wild', 'WTEST', { WELL: 'WELL-*', INTERVAL: 1 }),
  ]);
  assert(codes(wildcard).has('WILDCARD_EXPANSION_REQUIRED'));

  const intervalOverlap = await validate([
    date('d-int', '2025-01-01'), event('well-int', 'WELSPECS', { WELL: 'WELL-1', GROUP: 'FIELD' }),
    event('int-1', 'WELLTRACK', { WELL: 'WELL-1', MD_FROM: 100, MD_TO: 200 }),
    event('int-2', 'WELLTRACK', { WELL: 'WELL-1', MD_FROM: 150, MD_TO: 250 }),
  ]);
  assert(codes(intervalOverlap).has('INTERVAL_OVERLAP'));

  const intervalBounds = await validate([
    date('d-bounds', '2025-01-01'), event('well-bounds', 'WELSPECS', { WELL: 'WELL-1', GROUP: 'FIELD' }),
    event('int-bounds', 'WELLTRACK', { WELL: 'WELL-1', MD_FROM: 200, MD_TO: 100 }),
  ]);
  assert(codes(intervalBounds).has('INTERVAL_BOUNDS_INVALID'));

  const retired = await validate([
    date('d-retire', '2025-01-01'), event('well-retire', 'WELSPECS', { WELL: 'WELL-1', GROUP: 'FIELD' }),
    { ...event('retire', 'WECON', { WELL: 'WELL-1' }), variant: 'retire' },
    event('test-retired', 'WTEST', { WELL: 'WELL-1', INTERVAL: 1 }),
  ]);
  assert(codes(retired).has('ENTITY_REFERENCE_MISSING'));

  const reactivated = await validate([
    date('d-retire-1', '2025-01-01'), event('well-reactivate', 'WELSPECS', { WELL: 'WELL-1', GROUP: 'FIELD' }),
    { ...event('retire-2', 'WECON', { WELL: 'WELL-1' }), variant: 'retire' },
    date('d-retire-2', '2025-02-01'),
    { ...event('reactivate', 'WECON', { WELL: 'WELL-1' }), variant: 'reactivate' },
    event('test-reactivated', 'WTEST', { WELL: 'WELL-1', INTERVAL: 1 }),
  ]);
  assert.equal(reactivated.status, 'valid');
  assert(reactivated.semantic_replay.lifecycle_checks >= 2);
  assert(Array.isArray(reactivated.semantic_state_snapshot.interval_assignments));

  const reviseEvent = [date('rd', '2025-01-01', 'MODIFY'), event('rp', 'WCONPROD', { WELL: 'WELL-1', CONTROL: 'ORAT', RATE: 200 }, 'MODIFY')];
  const missingBaseline = await validate(reviseEvent, { mode: 'REVISE', initial_semantic_snapshot: null, semantic_baseline_snapshot: null, baseline_package_hash: packageHash });
  assert(codes(missingBaseline).has('SEMANTIC_BASELINE_SNAPSHOT_REQUIRED'));

  const staleBaseline = initial({ catalogue_hash: `sha256:${'f'.repeat(64)}`, package_hash: packageHash });
  const stale = await validate(reviseEvent, { mode: 'REVISE', semantic_baseline_snapshot: staleBaseline, baseline_package_hash: packageHash });
  assert(codes(stale).has('SEMANTIC_SNAPSHOT_CATALOGUE_MISMATCH'));

  const noBoundary = initial({ package_hash: packageHash });
  const boundaryRequired = await validate(reviseEvent, { mode: 'REVISE', semantic_baseline_snapshot: noBoundary, baseline_package_hash: packageHash });
  assert(codes(boundaryRequired).has('SEMANTIC_PRE_CHANGE_BOUNDARY_REQUIRED'));

  const baseline = initial({
    package_hash: packageHash, change_effective_from: '2025-01-01',
    snapshot_kind: 'PRE_CHANGE_BOUNDARY', replay_through: '2024-12-31', boundary_hash: boundaryHash,
    entities: [
      { entity_type: 'group', entity_id: 'FIELD', created_at: '2000-01-01' },
      { entity_type: 'well', entity_id: 'WELL-1', created_at: '2024-01-01' },
    ],
    keyword_occurrences: [{ event_id: 'baseline-well', keyword: 'WELSPECS', fields: { WELL: 'WELL-1', GROUP: 'FIELD' }, effective_at: '2024-01-01' }],
  });
  const futureBoundary = { ...baseline, replay_through: '2025-01-01' };
  const pastChange = await validate(reviseEvent, { mode: 'REVISE', semantic_baseline_snapshot: futureBoundary, baseline_package_hash: packageHash });
  assert(codes(pastChange).has('SEMANTIC_EVENT_NOT_AFTER_BOUNDARY'));

  const revised = await validate(reviseEvent, { mode: 'REVISE', semantic_baseline_snapshot: baseline, baseline_package_hash: packageHash });
  assert.equal(revised.status, 'valid');

  const noSemanticsCatalogue = catalogue();
  delete noSemanticsCatalogue.schemas[0].semantics;
  const noSemantics = await validate(validCreateEvents(), { schema_catalogue: noSemanticsCatalogue });
  assert(codes(noSemantics).has('SCHEMA_SEMANTICS_REQUIRED'));

  const invalidExtendedCatalogue = catalogue();
  invalidExtendedCatalogue.schemas.find((schema) => schema.keyword === 'WELLTRACK').semantics.interval_rules[0].end_field = 'UNKNOWN_FIELD';
  const invalidExtended = await validate(validCreateEvents(), { schema_catalogue: invalidExtendedCatalogue });
  assert(codes(invalidExtended).has('SEMANTIC_INTERVAL_RULE_INVALID'));

  console.log('SCHEDULE semantic state replay smoke: 22 scenarios passed');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
