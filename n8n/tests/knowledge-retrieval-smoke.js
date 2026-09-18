'use strict';
/**
 * 8.1 retrieval: no false schema abstain, tag query tokens, russian FTS SQL,
 * semantic type filter. CASE-6aa99f94-439404 (Builder SCHEMA_KEYWORD_SCOPE_REQUIRED).
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const workspace = process.env.WORKSPACE_ROOT || path.resolve(__dirname, '..', '..');
const workflow = JSON.parse(fs.readFileSync(path.join(workspace, 'n8n/workflows/core/tnavigator-schedule-hybrid-retrieval.workflow.json'), 'utf8'));
const agent = JSON.parse(fs.readFileSync(path.join(workspace, 'n8n/workflows/core/schedule-builder-agent.workflow.json'), 'utf8'));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const src = (wf, name) => {
  const n = wf.nodes.find((x) => x.name === name);
  assert(n && n.type === 'n8n-nodes-base.code', `missing ${name}`);
  return n.parameters.jsCode;
};
async function run(name, { json = {}, items = [], nodes = {} } = {}) {
  const fn = new AsyncFunction('$json', '$input', '$', src(workflow, name));
  const result = await fn(json, { all: () => items.map((json) => ({ json })) }, (n) => ({ first: () => ({ json: nodes[n] || {} }) }));
  assert(result?.[0]?.json);
  return result[0].json;
}
async function attachAgent(raw, prepared) {
  const fn = new AsyncFunction('$json', '$input', '$', src(agent, 'Attach schedule RAG evidence'));
  const result = await fn(raw, { all: () => [{ json: raw }] }, (n) => ({ first: () => ({ json: n === 'Prepare AI Agent input' ? prepared : {} }) }));
  return result[0].json;
}
const meta = (over = {}) => ({
  target_base: 'schedule_mvp',
  knowledge_type: 'keyword_instruction',
  knowledge_id: 'dates-schedule-clock-v1',
  revision: '2',
  parent_key: 'schedule_mvp:dates-schedule-clock-v1:2',
  keyword_families: ['DATES'],
  access_scope: 'petroleum-engineering',
  knowledge_status: 'current',
  ingest_key: 'chunk-dates',
  ...over,
});
const datesQuery = {
  contract: 'schedule_retrieval_query',
  contract_version: '1.0',
  query: 'даты ввода скважин',
  top_k: 10,
  exact_keyword_terms: [],
  filters: {
    target_base: 'schedule_mvp',
    access_scope: 'petroleum-engineering',
    knowledge_types: ['keyword_instruction', 'worked_example'],
    keyword_families: [],
    require_coverage: true,
    require_schema: true,
    knowledge_status: 'current',
  },
};
const wefacQuery = {
  ...datesQuery,
  query: 'коэффициент эксплуатации',
  filters: { ...datesQuery.filters },
};
const wconQuery = {
  ...datesQuery,
  query: 'WCONPROD лимит по воде. Нужен прогнозный контроль. Не выдумывать дебиты.',
  exact_keyword_terms: ['WCONPROD'],
  filters: { ...datesQuery.filters, keyword_families: ['WCONPROD'] },
};
(async () => {
  const lexSql = workflow.nodes.find((n) => n.name === 'PostgreSQL lexical + exact candidates').parameters.query;
  assert.ok(lexSql.includes("plainto_tsquery('russian'"));
  assert.ok(lexSql.includes("to_tsquery('simple'"));
  assert.ok(lexSql.includes('left($1,300)') || lexSql.includes('left($1, 300)'));
  assert.ok(lexSql.includes('tnavigator_schedule_knowledge_v2'));
  const tagSql = workflow.nodes.find((n) => n.name === 'PostgreSQL tag candidates').parameters.query;
  assert.ok(tagSql.includes('ILIKE'));
  assert.ok(tagSql.includes('$7::jsonb'));
  assert.ok(tagSql.includes('LIMIT $8'));
  const ingestSql = JSON.parse(fs.readFileSync(path.join(workspace, 'n8n/workflows/core/tnavigator-schedule-knowledge-ingestion.workflow.json'), 'utf8'))
    .nodes.find((n) => n.name === 'Finalize indexes and deduplicate chunks').parameters.query;
  assert.ok(ingestSql.includes("to_tsvector('russian'"));

  const validated = await run('Validate SCHEDULE retrieval request', { json: { schedule_retrieval_request: { query: 'даты ввода скважин', filters: { target_base: 'schedule_mvp' } } } });
  assert.equal(validated.status, 'query_ready');
  assert.deepEqual(validated.filters.keyword_families, []);
  assert.equal(validated.filters.require_schema, true);

  const tagPrep = await run('Prepare tag retrieval', { nodes: { 'Validate SCHEDULE retrieval request': wefacQuery } });
  assert.ok(tagPrep.query_tokens.includes('коэффициент'));
  assert.ok(tagPrep.query_tokens.includes('эксплуатации'));
  const tagParams = tagPrep.sql_parameters;
  assert.equal(tagParams.length, 8);
  assert.ok(JSON.parse(tagParams[6]).includes('коэффициент'));

  const lookup = { 'Validate SCHEDULE retrieval request': datesQuery };
  const lex = await run('Wrap lexical candidates', { nodes: lookup, items: [{ candidate_id: 'd1', page_content: 'DATES даты ввода скважин', metadata: meta(), lexical_rank: 1, lexical_score: 0.2, exact_hit: 0 }] });
  const tag = await run('Wrap tag candidates', { nodes: lookup, items: [{ candidate_id: 'd1', page_content: 'DATES', metadata: meta(), tag_rank: 1 }] });
  const sem = await run('Wrap semantic candidates', {
    nodes: lookup,
    items: [
      { document: { pageContent: 'DATES календарь', metadata: meta() }, score: 0.9 },
      { document: { pageContent: 'routing', metadata: meta({ knowledge_type: 'routing_card', knowledge_id: 'route-x', parent_key: 'orchestrator_routing:route-x:1', target_base: 'orchestrator_routing' }) }, score: 0.99 },
    ],
  });
  assert.equal(sem.candidates.length, 1);
  assert.equal(sem.candidates[0].metadata.knowledge_type, 'keyword_instruction');

  const ranked = await run('Fuse authorized candidates with deterministic RRF', { items: [lex, tag, sem] });
  assert.equal(ranked.status, 'ranked');
  assert.ok(ranked.ranked_parents.length >= 1);

  const hydrated = await run('Hydrate full parent knowledge blocks', {
    nodes: { 'Prepare full parent knowledge lookup': { evidence: ranked } },
    items: [{
      target_base: 'schedule_mvp', knowledge_id: 'dates-schedule-clock-v1', revision: '2', knowledge_type: 'keyword_instruction', status: 'active',
      keywords: ['DATES'], topics: ['календарь'], task_patterns: ['даты ввода'], title: 'DATES',
      body_json: { contract: 'schedule_knowledge_block', text: 'DATES — календарь. Когда применять. Дата ввода скважины.', summary: 'Когда применять. Календарь SCHEDULE и дата ввода скважины. Не путать с ECDATES.' },
      content_hash: 'fnv1a32:dates01', access_scope: 'petroleum-engineering', author: 'expert',
    }],
  });
  assert.equal(hydrated.status, 'succeeded');
  assert.equal(hydrated.results.length, 1);

  const attached = await run('Attach approved schema catalogue', { nodes: { 'Prepare approved schema catalogue lookup': { evidence: hydrated } }, items: [{}] });
  assert.equal(attached.status, 'succeeded');
  assert.equal(attached.results.length, 1);
  assert.equal(attached.schema_catalogue, null);
  assert.ok(!(attached.findings || []).some((f) => f.code === 'SCHEMA_KEYWORD_SCOPE_REQUIRED'));

  const wefacMeta = meta({ knowledge_id: 'wefac-well-efficiency-v1', parent_key: 'schedule_mvp:wefac-well-efficiency-v1:1', keyword_families: ['WEFAC'], ingest_key: 'wefac-1' });
  const wefacTag = await run('Wrap tag candidates', { nodes: { 'Validate SCHEDULE retrieval request': wefacQuery }, items: [{ candidate_id: 'w1', page_content: 'WEFAC коэффициент эксплуатации', metadata: wefacMeta, tag_rank: 1 }] });
  const wefacRanked = await run('Fuse authorized candidates with deterministic RRF', { items: [wefacTag] });
  assert.equal(wefacRanked.status, 'ranked');
  assert.deepEqual(wefacRanked.ranked_parents[0].branches, ['tag']);
  assert.equal(wefacRanked.ranked_parents[0].knowledge_id, 'wefac-well-efficiency-v1');

  const wconLookup = { 'Validate SCHEDULE retrieval request': wconQuery };
  const wconLex = await run('Wrap lexical candidates', { nodes: wconLookup, items: [{ candidate_id: 'wcon', page_content: 'WCONPROD', metadata: meta({ knowledge_id: 'wconprod-forecast-control-v1', parent_key: 'schedule_mvp:wconprod-forecast-control-v1:3', keyword_families: ['WCONPROD'] }), lexical_rank: 1, lexical_score: 0.8, exact_hit: 1 }] });
  assert.equal(wconLex.candidates[0].exact_hit, true);
  const threeSentence = await run('Wrap lexical candidates', {
    nodes: { 'Validate SCHEDULE retrieval request': { ...datesQuery, query: 'Нужно сдвинуть даты ввода. Скважины уже есть в модели. Календарь SCHEDULE должен совпасть с Excel.' } },
    items: [
      { candidate_id: 'a', page_content: 'даты', metadata: meta(), lexical_rank: 1, lexical_score: 0.3, exact_hit: 0 },
      { candidate_id: 'b', page_content: 'ввода', metadata: meta({ knowledge_id: 'welspecs-v1', parent_key: 'schedule_mvp:welspecs-v1:1' }), lexical_rank: 2, lexical_score: 0.2, exact_hit: 0 },
      { candidate_id: 'c', page_content: 'скважин', metadata: meta({ knowledge_id: 'wconhist-v1', parent_key: 'schedule_mvp:wconhist-v1:1' }), lexical_rank: 3, lexical_score: 0.1, exact_hit: 0 },
    ],
  });
  assert.ok(threeSentence.candidates.length >= 3);

  const prepared = { retrieval_selector: { target_base: 'schedule_mvp', knowledge_types: ['keyword_instruction', 'worked_example'] }, planner_input: 'task', schedule_retrieval_request: datesQuery };
  const pad = 'Назначение. '.padEnd(120, 'x');
  const compact = await attachAgent({
    contract: 'schedule_retrieval_result',
    status: 'succeeded',
    filters: datesQuery.filters,
    results: [
      { knowledge_id: 'wconprod-forecast-control-v1', knowledge_type: 'keyword_instruction', title: 'WCONPROD', rrf_score: 0.016, branches: ['semantic'], body: { target_base: 'schedule_mvp', knowledge_type: 'keyword_instruction', summary: 'Когда применять. FORECAST. WELTARG меняет цель без полного WCONPROD. Не путать с WCONHIST.', text: pad + ' полный текст без этого слова в начале' } },
      { knowledge_id: 'other-multi', knowledge_type: 'keyword_instruction', title: 'other', rrf_score: 0.005, branches: ['lexical', 'semantic'], body: { target_base: 'schedule_mvp', knowledge_type: 'keyword_instruction', text: pad + ' other card' } },
    ],
  }, prepared);
  assert.equal(compact.rag.status, 'ready');
  const ids = compact.rag.cards.map((c) => c.knowledge_id);
  assert.ok(ids.includes('wconprod-forecast-control-v1'));
  assert.equal(ids.includes('other-multi'), false);
  const wconCard = compact.rag.cards.find((c) => c.knowledge_id === 'wconprod-forecast-control-v1');
  assert.ok(wconCard.text.includes('WELTARG'));

  const orchQuery = {
    ...datesQuery,
    query: 'новые даты ввода скважин',
    filters: {
      target_base: 'orchestrator_routing',
      access_scope: 'petroleum-engineering',
      knowledge_types: ['routing_card'],
      keyword_families: [],
      topics: [],
      require_coverage: false,
      require_schema: false,
      knowledge_status: 'current',
    },
  };
  const orchSem = await run('Wrap semantic candidates', {
    nodes: { 'Validate SCHEDULE retrieval request': orchQuery },
    items: [
      { document: { pageContent: 'даты ввода Excel', metadata: meta({ target_base: 'orchestrator_routing', knowledge_type: 'routing_card', knowledge_id: 'route-excel-extractor', parent_key: 'orchestrator_routing:route-excel-extractor:9', ingest_key: 'excel-r' }) }, score: 0.8 },
      { document: { pageContent: 'агента нет кластер', metadata: meta({ target_base: 'orchestrator_routing', knowledge_type: 'routing_card', knowledge_id: 'route-cluster-calculation', parent_key: 'orchestrator_routing:route-cluster-calculation:4', ingest_key: 'cluster-r', topics: ['absent_agent'] }) }, score: 0.99 },
    ],
  });
  const orchRanked = await run('Fuse authorized candidates with deterministic RRF', { items: [orchSem] });
  const orchIds = orchRanked.ranked_parents.map((p) => p.knowledge_id);
  assert.ok(orchIds.includes('route-excel-extractor'));
  assert.equal(orchIds.includes('route-cluster-calculation'), false);
  const stubSem = await run('Wrap semantic candidates', {
    nodes: { 'Validate SCHEDULE retrieval request': { ...orchQuery, filters: { ...orchQuery.filters, topics: ['absent_agent'] } } },
    items: [
      { document: { pageContent: 'даты ввода Excel', metadata: meta({ target_base: 'orchestrator_routing', knowledge_type: 'routing_card', knowledge_id: 'route-excel-extractor', parent_key: 'orchestrator_routing:route-excel-extractor:9', ingest_key: 'excel-r' }) }, score: 0.8 },
      { document: { pageContent: 'агента нет кластер', metadata: meta({ target_base: 'orchestrator_routing', knowledge_type: 'routing_card', knowledge_id: 'route-cluster-calculation', parent_key: 'orchestrator_routing:route-cluster-calculation:4', ingest_key: 'cluster-r', topics: ['absent_agent'] }) }, score: 0.99 },
    ],
  });
  const stubRanked = await run('Fuse authorized candidates with deterministic RRF', { items: [stubSem] });
  assert.ok(stubRanked.ranked_parents.some((p) => p.knowledge_id === 'route-cluster-calculation'));

  const exampleMeta = meta({
    knowledge_id: 'worked-commissioning-dates-v1',
    knowledge_type: 'worked_example',
    parent_key: 'schedule_mvp:worked-commissioning-dates-v1:1',
    keyword_families: ['DATES'],
    ingest_key: 'ex-dates',
  });
  const datesWithExample = await run('Wrap lexical candidates', {
    nodes: lookup,
    items: [
      { candidate_id: 'd1', page_content: 'сдвинь даты ввода', metadata: meta(), lexical_rank: 1, lexical_score: 0.25, exact_hit: 0 },
      { candidate_id: 'ex1', page_content: 'пример сдвиг дат apply_commissioning', metadata: exampleMeta, lexical_rank: 2, lexical_score: 0.2, exact_hit: 0 },
    ],
  });
  const datesRanked = await run('Fuse authorized candidates with deterministic RRF', { items: [datesWithExample] });
  const dateIds = datesRanked.ranked_parents.map((p) => p.knowledge_id);
  assert.ok(dateIds.includes('dates-schedule-clock-v1'));
  assert.ok(dateIds.includes('worked-commissioning-dates-v1'));

  console.log('knowledge-retrieval smoke: 8.1/8.2/8.5 scenarios passed');
})().catch((e) => { console.error(e.stack || e); process.exit(1); });
