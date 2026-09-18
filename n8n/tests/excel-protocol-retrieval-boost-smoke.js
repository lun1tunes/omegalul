'use strict';
/**
 * Proves excel_protocol delta cards improve retrieval surface vs historical baseline.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const workspace = process.env.WORKSPACE_ROOT || path.resolve(__dirname, '..', '..');
const guidePath = path.join(workspace, 'n8n/rag/excel-agent-operating-guide.documents.json');
const baselinePath = path.join(workspace, 'n8n/tests/fixtures/excel-protocol-searchable-baseline.json');
const ingestion = JSON.parse(fs.readFileSync(path.join(workspace, 'n8n/workflows/core/tnavigator-schedule-knowledge-ingestion.workflow.json'), 'utf8'));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const src = (wf, name) => {
  const n = wf.nodes.find((x) => x.name === name);
  assert(n && n.type === 'n8n-nodes-base.code', `missing ${name}`);
  return n.parameters.jsCode;
};
async function ingest(json) {
  const fn = new AsyncFunction('$json', '$input', src(ingestion, 'Normalize approved SCHEDULE knowledge'));
  const items = [{ json }];
  const r = await fn(json, { all: () => items, first: () => items[0] });
  return r[0].json;
}

const REQUIRED_SECTIONS = ['Назначение.', 'Когда применять.', 'Канон протокола.', 'Валидация'];
const EVAL_QUERIES = [
  {
    id: 'open-workbook-find-table',
    target: 'excel-agent-discovery-and-tables',
    query: 'открыть новый workbook и найти таблицу',
  },
  {
    id: 'wide-table-unpivot',
    target: 'excel-agent-discovery-and-tables',
    query: 'широкая таблица unpivot колонки-даты',
  },
  {
    id: 'inventory-open-session',
    target: 'excel-agent-discovery-and-tables',
    query: 'инвентарь open_session inspect.tables',
  },
  {
    id: 'expected-output-datasets',
    target: 'excel-agent-query-and-result-protocol',
    query: 'expected_output.datasets задаёт имя и поля',
  },
  {
    id: 'extract-table-dataset',
    target: 'excel-agent-query-and-result-protocol',
    query: 'extract_table фиксирует набор данных',
  },
  {
    id: 'commissioning-vs-params',
    target: 'excel-agent-query-and-result-protocol',
    query: 'facts extract_commissioning new_wells extract_well_parameters',
  },
  {
    id: 'ask-engineer-sheet',
    target: 'excel-agent-clarification-and-continuation',
    query: 'спросить инженера какой лист и колонка',
  },
  {
    id: 'options-as-engineer-sees',
    target: 'excel-agent-clarification-and-continuation',
    query: 'варианты как их видит инженер',
  },
  {
    id: 'no-session-id-in-tool',
    target: 'excel-agent-trust-boundary',
    query: 'не передавать session_id в вызов Excel tool',
  },
  {
    id: 'copy-opaque-ids',
    target: 'excel-agent-trust-boundary',
    query: 'скопировать tbl_ res_ art_ из ответа инструмента',
  },
];

function tokenize(text) {
  return [...new Set(String(text).toLowerCase().match(/[a-zа-яё0-9_./-]{3,}/gi) || [])];
}
function coverage(query, corpus) {
  const tokens = tokenize(query);
  assert(tokens.length >= 3, `query too short for coverage: ${query}`);
  const hay = String(corpus).toLowerCase();
  const hits = tokens.filter((t) => hay.includes(t)).length;
  return hits / tokens.length;
}
function searchableFromBlock(b) {
  const examples = Array.isArray(b.examples) ? b.examples : [];
  const parts = [
    b.title || '',
    b.text || '',
    (b.keywords || []).join(' '),
    (b.topics || []).join(' '),
    (b.task_patterns || []).join(' '),
    ...examples.flatMap((e) => [e.title || '', e.task || '', e.schedule_text || '', e.explanation || '']),
  ];
  return parts.filter(Boolean).join('\n\n');
}

(async () => {
  const guide = JSON.parse(fs.readFileSync(guidePath, 'utf8'));
  const baseline = JSON.parse(fs.readFileSync(baselinePath, 'utf8'));
  const live = {};
  for (const doc of guide.documents || []) {
    const b = doc.schedule_knowledge_block || doc;
    if (b.target_base !== 'excel_protocol') continue;
    live[b.knowledge_id] = b;
  }
  assert.equal(Object.keys(live).length, 4);
  assert.equal(Object.keys(baseline).length, 4);

  let structureOk = 0;
  for (const [kid, b] of Object.entries(live)) {
    const expectedRevision = {
      'excel-agent-trust-boundary': '5',
      'excel-agent-discovery-and-tables': '7',
      'excel-agent-query-and-result-protocol': '6',
      'excel-agent-clarification-and-continuation': '5',
    }[kid];
    assert.equal(String(b.revision), expectedRevision, kid);
    assert.ok(/[А-Яа-яA-Z]/.test(String(b.title)), kid);
    assert.ok(!/^excel agent /i.test(String(b.title)), `slug title still present: ${kid}`);
    assert.ok((b.task_patterns || []).length >= 4, kid);
    assert.ok((b.examples || []).length >= 2, kid);
    for (const section of REQUIRED_SECTIONS) {
      assert.ok(String(b.text).includes(section), `${kid} missing ${section}`);
    }
    const normalized = await ingest({ schedule_knowledge_block: { ...b, contract: 'schedule_knowledge_block', contract_version: '1.0' } });
    assert.equal(normalized.status, 'approved_for_ingestion', kid);
    assert.ok((normalized.metadata.task_patterns || []).length >= 4, kid);
    const searchable = normalized.text || '';
    for (const pattern of b.task_patterns.slice(0, 3)) {
      assert.ok(searchable.toLowerCase().includes(String(pattern).toLowerCase()), `${kid} pattern missing from searchable`);
    }
    for (const ex of b.examples) {
      assert.ok(searchable.includes(ex.task) || searchable.includes(ex.title), `${kid} example missing from searchable`);
    }
    structureOk += 1;
  }

  const rows = [];
  for (const q of EVAL_QUERIES) {
    const card = live[q.target];
    assert.ok(card, q.target);
    const oldCorpus = baseline[q.target].searchable;
    const newCorpus = searchableFromBlock(card);
    const oldScore = coverage(q.query, oldCorpus);
    const newScore = coverage(q.query, newCorpus);
    const delta = newScore - oldScore;
    rows.push({ ...q, oldScore, newScore, delta });
    assert.ok(
      newScore > oldScore + 1e-9,
      `${q.id}: no coverage boost (old=${oldScore.toFixed(3)} new=${newScore.toFixed(3)}) — rewrite failed for ${q.target}`,
    );
  }

  const avgOld = rows.reduce((s, r) => s + r.oldScore, 0) / rows.length;
  const avgNew = rows.reduce((s, r) => s + r.newScore, 0) / rows.length;
  const avgDelta = avgNew - avgOld;
  assert.ok(
    avgDelta >= 0.12,
    `average boost too small: Δ=${avgDelta.toFixed(3)} (old=${avgOld.toFixed(3)} new=${avgNew.toFixed(3)}); rewrite did not deliver a real retrieval surface gain`,
  );
  assert.ok(avgNew >= 0.72, `new mean coverage too low: ${avgNew.toFixed(3)}`);

  let rankingWins = 0;
  for (const q of EVAL_QUERIES) {
    const scores = Object.fromEntries(
      Object.entries(live).map(([kid, b]) => [kid, coverage(q.query, searchableFromBlock(b))]),
    );
    const targetScore = scores[q.target];
    const bestOther = Math.max(...Object.entries(scores).filter(([kid]) => kid !== q.target).map(([, s]) => s));
    if (targetScore >= bestOther) rankingWins += 1;
  }
  assert.ok(
    rankingWins >= Math.ceil(EVAL_QUERIES.length * 0.75),
    `target-card ranking wins too low: ${rankingWins}/${EVAL_QUERIES.length}`,
  );

  const scenarios = structureOk + rows.length;
  console.log(
    `Excel protocol retrieval boost smoke: ${scenarios} scenarios passed (${structureOk} cards + ${rows.length} query boosts, avg Δ=${avgDelta.toFixed(3)} ${avgOld.toFixed(3)}→${avgNew.toFixed(3)}, rankingWins=${rankingWins}/${EVAL_QUERIES.length})`,
  );
})().catch((e) => {
  console.error(e.stack || e);
  process.exit(1);
});
