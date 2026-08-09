'use strict';

// Runs the exported hybrid retrieval Code nodes with deterministic fixtures.
// PostgreSQL and embeddings are deliberately mocked here; a separate DB smoke
// validates the SQL against the pgvector image.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || '/workspace';
const workflow = JSON.parse(fs.readFileSync(
  path.join(workspace, 'n8n', 'workflows', 'tnavigator-schedule-hybrid-retrieval.workflow.json'),
  'utf8',
));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function source(name) {
  const node = workflow.nodes.find((candidate) => candidate.name === name);
  assert(node && node.type === 'n8n-nodes-base.code', `missing Code node: ${name}`);
  return node.parameters.jsCode;
}

async function run(name, { json = {}, items = [], nodes = {} } = {}) {
  const input = { all: () => items.map((item) => ({ json: item })) };
  const lookup = (nodeName) => ({
    first: () => ({ json: nodes[nodeName] || {} }),
  });
  const fn = new AsyncFunction('$json', '$input', '$', source(name));
  const result = await fn(json, input, lookup);
  assert(Array.isArray(result) && result.length === 1 && result[0].json);
  return result[0].json;
}

function query(exact = ['WCONPROD']) {
  return {
    contract: 'schedule_retrieval_query',
    contract_version: '1.0',
    query: `${exact.join(' ')} control`,
    top_k: 10,
    exact_keyword_terms: exact,
    filters: {
      vendor: 'Rock Flow Dynamics',
      simulator: 'tNavigator',
      simulator_version: '22.2',
      authority_level: 'vendor_manual',
      approval_status: 'approved',
      access_scope: 'petroleum-engineering',
      keyword_families: exact,
    },
  };
}

function metadata(overrides = {}) {
  return {
    document_id: 'tnav-22.2',
    document_revision: '22.2',
    source_hash: `sha256:${'a'.repeat(64)}`,
    page: '123',
    heading: 'SCHEDULE / WCONPROD',
    keyword_families: '["WCONPROD","DATES"]',
    vendor: 'Rock Flow Dynamics',
    simulator: 'tNavigator',
    simulator_version: '22.2',
    authority_level: 'vendor_manual',
    approval_status: 'approved',
    access_scope: 'petroleum-engineering',
    ...overrides,
  };
}

function schemaCatalogue(overrides = {}) {
  const hash = `sha256:${'a'.repeat(64)}`;
  return {
    contract: 'schedule_schema_catalogue',
    contract_version: '1.0',
    catalogue_ref: 'catalogue://tnavigator/22.2/approved',
    catalogue_hash: `sha256:${'b'.repeat(64)}`,
    source_hash: hash,
    access_scope: 'petroleum-engineering',
    simulator_profile: { vendor: 'Rock Flow Dynamics', simulator: 'tNavigator', version: '22.2' },
    approved: true,
    approved_by: 'responsible-engineer',
    approval_gate_id: 'gate-1',
    schemas: [{
      schema_id: 'fixture:WCONPROD', schema_revision: '1', keyword: 'WCONPROD', variant: 'default',
      citation: { document_id: 'tnav-22.2', document_revision: '22.2', source_hash: hash, page: '123' },
      fields: [{ name: 'WELL', position: 1, type: 'string', required: true }],
      semantics: { period: 'ANY' },
    }],
    ...overrides,
  };
}

async function main() {
  const q = query();
  const lookup = { 'Validate SCHEDULE retrieval request': q };

  const emptyLexical = await run('Wrap lexical candidates', { items: [], nodes: lookup });
  const emptyTag = await run('Wrap tag candidates', { items: [], nodes: lookup });
  const emptySemantic = await run('Wrap semantic candidates', { items: [], nodes: lookup });
  assert.deepEqual(emptyLexical.candidates, []);
  assert.deepEqual(emptyTag.candidates, []);
  assert.deepEqual(emptySemantic.candidates, []);

  const noEvidence = await run('Fuse authorized candidates with deterministic RRF', {
    items: [emptyLexical, emptyTag, emptySemantic],
  });
  assert.equal(noEvidence.status, 'abstain');
  assert.equal(noEvidence.evidence_ready, false);
  assert(noEvidence.findings.some((finding) => finding.code === 'NO_AUTHORIZED_EVIDENCE'));

  const lexical = await run('Wrap lexical candidates', {
    nodes: lookup,
    items: [{
      candidate_id: 'chunk-1',
      page_content: 'WCONPROD approved record description',
      metadata: metadata(),
      lexical_rank: 1,
      lexical_score: 0.9,
      exact_hit: 1,
    }],
  });
  const tag = await run('Wrap tag candidates', {
    nodes: lookup,
    items: [{
      candidate_id: 'chunk-1',
      page_content: 'WCONPROD approved record description',
      metadata: metadata(),
      tag_rank: 1,
    }],
  });
  const semantic = await run('Wrap semantic candidates', {
    nodes: lookup,
    items: [{
      document: {
        pageContent: 'WCONPROD approved record description',
        metadata: metadata({ ingest_key: 'chunk-1' }),
      },
      score: 0.95,
    }],
  });
  const success = await run('Fuse authorized candidates with deterministic RRF', {
    items: [lexical, tag, semantic],
  });
  assert.equal(success.status, 'succeeded');
  assert.equal(success.evidence_ready, true);
  assert.equal(success.results.length, 1);
  assert.deepEqual(success.citations[0].keyword_families, ['WCONPROD', 'DATES']);
  assert.deepEqual(success.citations[0].branches, ['lexical', 'semantic', 'tag']);

  const schemaLookupNode = { 'Prepare approved schema catalogue lookup': { evidence: success } };
  const governed = await run('Attach approved schema catalogue', {
    nodes: schemaLookupNode,
    items: [{ schema_catalogue: schemaCatalogue() }],
  });
  assert.equal(governed.status, 'succeeded');
  assert.equal(governed.evidence_ready, true);
  assert.equal(governed.schema_catalogue.catalogue_hash, `sha256:${'b'.repeat(64)}`);

  const catalogueMissing = await run('Attach approved schema catalogue', {
    nodes: schemaLookupNode,
    items: [],
  });
  assert.equal(catalogueMissing.status, 'abstain');
  assert(catalogueMissing.findings.some((finding) => finding.code === 'APPROVED_SCHEMA_CATALOGUE_NOT_FOUND'));

  const wrongScope = await run('Attach approved schema catalogue', {
    nodes: schemaLookupNode,
    items: [{ schema_catalogue: schemaCatalogue({ access_scope: 'other-team' }) }],
  });
  assert.equal(wrongScope.status, 'abstain');
  assert(wrongScope.findings.some((finding) => finding.code === 'APPROVED_SCHEMA_CATALOGUE_NOT_FOUND'));

  const ambiguous = await run('Attach approved schema catalogue', {
    nodes: schemaLookupNode,
    items: [
      { schema_catalogue: schemaCatalogue() },
      { schema_catalogue: schemaCatalogue({ catalogue_hash: `sha256:${'c'.repeat(64)}` }) },
    ],
  });
  assert.equal(ambiguous.status, 'abstain');
  assert(ambiguous.findings.some((finding) => finding.code === 'SCHEMA_CATALOGUE_AMBIGUOUS'));

  const unauthorized = await run('Fuse authorized candidates with deterministic RRF', {
    items: [{
      branch: 'lexical',
      query: q,
      candidates: [{
        candidate_id: 'forbidden',
        page_content: 'wrong scope',
        metadata: metadata({ access_scope: 'other-team' }),
        rank: 1,
      }],
      branch_findings: [],
    }, emptyTag, emptySemantic],
  });
  assert.equal(unauthorized.status, 'abstain');
  assert.equal(unauthorized.retrieval.candidate_count, 0);

  const branchErrorPacket = await run('Wrap semantic candidates', {
    nodes: lookup,
    items: [{ error: 'embedding endpoint unavailable' }],
  });
  const branchFailure = await run('Fuse authorized candidates with deterministic RRF', {
    items: [lexical, tag, branchErrorPacket],
  });
  assert.equal(branchFailure.status, 'abstain');
  assert(branchFailure.findings.some((finding) => finding.code === 'SEMANTIC_BRANCH_FAILED'));
  assert.deepEqual(branchFailure.results, []);

  const incomplete = await run('Fuse authorized candidates with deterministic RRF', {
    items: [{
      branch: 'lexical',
      query: q,
      candidates: [{
        candidate_id: 'no-location',
        page_content: 'WCONPROD',
        metadata: metadata({ page: '', heading: '' }),
        rank: 1,
        exact_hit: true,
      }],
      branch_findings: [],
    }, emptyTag, emptySemantic],
  });
  assert.equal(incomplete.status, 'abstain');
  assert(incomplete.findings.some((finding) => finding.code === 'CITATION_INCOMPLETE'));

  const uncovered = await run('Fuse authorized candidates with deterministic RRF', {
    items: [{
      branch: 'lexical',
      query: query(['WCONPROD', 'DATES']),
      candidates: [{
        candidate_id: 'only-wconprod',
        page_content: 'WCONPROD',
        metadata: metadata({ keyword_families: ['WCONPROD'] }),
        rank: 1,
      }],
      branch_findings: [],
    }, emptyTag, emptySemantic],
  });
  assert.equal(uncovered.status, 'abstain');
  assert(uncovered.findings.some(
    (finding) => finding.code === 'KEYWORD_COVERAGE_INCOMPLETE'
      && finding.keywords.includes('DATES'),
  ));

  console.log('SCHEDULE RAG runtime smoke: 11 scenarios passed');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
