'use strict';

// Vendor-neutral decoded records only. This tests deterministic inventory
// selection and mutation identity propagation, not tNavigator field layouts.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || '/workspace';
const workflow = JSON.parse(fs.readFileSync(path.join(
  workspace, 'n8n', 'workflows', 'tnavigator-schedule-baseline-query.workflow.json',
), 'utf8'));
const node = workflow.nodes.find((candidate) => candidate.name === 'Query decoded baseline inventory');
assert(node && node.type === 'n8n-nodes-base.code');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const execute = new AsyncFunction('$json', node.parameters.jsCode);

const decodedHash = `sha256:${'a'.repeat(64)}`;
const packageHash = `sha256:${'b'.repeat(64)}`;
const catalogueHash = `sha256:${'c'.repeat(64)}`;
const rawHash = (i) => `sha256:${(i.toString(16).padStart(64, '0')).slice(-64)}`;

function record(i, keyword = 'WCONPROD') {
  const well = `WELL-${String(i % 25).padStart(2, '0')}`;
  const month = String((i % 12) + 1).padStart(2, '0');
  const target = `schedule.inc:${Math.floor(i / 2)}`;
  return {
    event_id: `baseline:${i}`, keyword, variant: 'default',
    fields: keyword === 'WCONPROD'
      ? { WELL: well, CONTROL: i % 2 ? 'BHP' : 'ORAT', RATE: i, NOTE: `batch-${i % 4}` }
      : { WELL: well, GROUP: `GROUP-${i % 3}` },
    effective_at: `2025-${month}-01`, source_node_id: target, target_node_id: target,
    expected_raw_hash: rawHash(i), file_ref: i % 2 ? 'controls.inc' : 'schedule.inc',
    record_index: i % 2, execution_sequence: i, record_hash: rawHash(i + 10000),
    schema_id: `fixture:${keyword}`, schema_revision: '1',
    provenance: [{ source_ref: `fixture://${i}`, raw_hash: rawHash(i) }],
  };
}

const records = Array.from({ length: 2505 }, (_, i) => record(i, i % 10 === 0 ? 'WELSPECS' : 'WCONPROD'));
const decoded = {
  contract: 'baseline_decode_result', contract_version: '1.0', status: 'decoded',
  decoded_hash: decodedHash, baseline_package_hash: packageHash, catalogue_hash: catalogueHash,
  decoded_records: records, prefix_records: records.slice(0, 1000), suffix_records: records.slice(1000),
};

async function query(queryBody, overrides = {}) {
  const rows = await execute({ baseline_query_request: {
    baseline_decode_result: decoded, expected_decoded_hash: decodedHash,
    query: queryBody, ...overrides,
  } });
  assert.equal(rows.length, 1);
  return rows[0].json;
}

const codes = (result) => new Set((result.findings || []).map((finding) => finding.code));

async function main() {
  const planning = await query({ purpose: 'PLANNING', phase: 'ALL', summary_only: true, sample_limit: 20 });
  assert.equal(planning.status, 'succeeded');
  assert.equal(planning.total_matches, 2505);
  assert.equal(planning.records.length, 0);
  assert(planning.samples.length <= 10); // at most five samples per keyword
  assert.equal(planning.summary.keyword_counts.WELSPECS, 251);
  assert.equal(planning.summary.keyword_counts.WCONPROD, 2254);

  const targeted = await query({
    purpose: 'BUILD', phase: 'ALL', keywords: ['WCONPROD'], entity_values: ['WELL-07'],
    effective_from: '2025-01-01', effective_to: '2025-12-31', limit: 2000,
    require_complete: true,
  });
  assert.equal(targeted.status, 'succeeded');
  assert(targeted.records.length > 0 && targeted.records.length < 2000);
  assert(targeted.records.every((r) => r.keyword === 'WCONPROD' && r.fields.WELL === 'WELL-07'));
  assert(targeted.records.every((r) => r.target_node_id && r.expected_raw_hash && r.provenance.length));

  const exactFields = await query({
    purpose: 'BUILD', keywords: ['WCONPROD'],
    field_filters: [
      { field: 'CONTROL', operator: 'IN', values: ['ORAT'] },
      { field: 'NOTE', operator: 'CONTAINS', value: 'batch-2' },
      { field: 'RATE', operator: 'EXISTS' },
    ],
    limit: 2000, require_complete: true,
  });
  assert.equal(exactFields.status, 'succeeded');
  assert(exactFields.records.every((r) => r.fields.CONTROL === 'ORAT' && r.fields.NOTE === 'batch-2'));

  const byIdentity = await query({
    purpose: 'BUILD', source_node_ids: [records[42].source_node_id],
    file_refs: [records[42].file_ref], limit: 10, require_complete: true,
  });
  assert.equal(byIdentity.status, 'succeeded');
  assert(byIdentity.records.every((r) => r.source_node_id === records[42].source_node_id && r.file_ref === records[42].file_ref));

  const broadBuild = await query({ purpose: 'BUILD', limit: 2000, require_complete: true });
  assert.equal(broadBuild.status, 'needs_input');
  assert(codes(broadBuild).has('BASELINE_QUERY_REFINEMENT_REQUIRED'));
  assert.equal(broadBuild.records.length, 0);

  const page1 = await query({ purpose: 'DIAGNOSTIC', limit: 100, require_complete: false });
  assert.equal(page1.status, 'partial');
  assert.equal(page1.records.length, 100);
  assert.equal(page1.next_cursor, 100);
  const page2 = await query({ purpose: 'DIAGNOSTIC', cursor: page1.next_cursor, limit: 100, require_complete: false });
  assert.equal(page2.records[0].execution_sequence, 100);
  assert.notEqual(page1.query_hash, page2.query_hash);

  const prefix = await query({ purpose: 'DIAGNOSTIC', phase: 'PREFIX', limit: 1000, require_complete: true });
  assert.equal(prefix.status, 'succeeded');
  assert.equal(prefix.total_source_records, 1000);
  const suffix = await query({ purpose: 'DIAGNOSTIC', phase: 'SUFFIX', limit: 2000, require_complete: true });
  assert.equal(suffix.total_source_records, 1505);

  const stable1 = await query({ purpose: 'PLANNING', keywords: ['WCONPROD'], summary_only: true });
  const stable2 = await query({ purpose: 'PLANNING', keywords: ['WCONPROD'], summary_only: true });
  assert.equal(stable1.query_hash, stable2.query_hash);

  const stale = await query({ purpose: 'PLANNING', summary_only: true }, { expected_decoded_hash: `sha256:${'f'.repeat(64)}` });
  assert(codes(stale).has('BASELINE_QUERY_STALE_DECODED_HASH'));
  const unsupported = await query({ purpose: 'BUILD', keywords: ['UNKNOWN'], limit: 10 });
  assert(codes(unsupported).has('BASELINE_QUERY_KEYWORD_UNSUPPORTED'));
  const invalidDate = await query({ purpose: 'BUILD', effective_from: 'tomorrow', limit: 10 });
  assert(codes(invalidDate).has('BASELINE_QUERY_DATE_INVALID'));
  const invalidFilter = await query({ purpose: 'BUILD', field_filters: [{ field: 'BAD FIELD', operator: 'REGEX', value: 'x' }], limit: 10 });
  assert(codes(invalidFilter).has('BASELINE_QUERY_FIELD_FILTER_INVALID'));
  const invalidLimit = await query({ purpose: 'BUILD', limit: 5000 });
  assert(codes(invalidLimit).has('BASELINE_QUERY_LIMIT_INVALID'));

  console.log('SCHEDULE targeted baseline query smoke: 13 scenarios passed');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
