'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const workspace = process.env.WORKSPACE_ROOT || '/workspace';
const workflow = JSON.parse(fs.readFileSync(path.join(workspace, 'n8n/workflows/core/excel-extraction-agent.workflow.json'), 'utf8'));
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
const src = (name) => {
  const node = workflow.nodes.find((item) => item.name === name);
  assert(node && node.type === 'n8n-nodes-base.code', `missing ${name}`);
  return node.parameters.jsCode;
};
async function run(name, { json = {}, nodes = {} } = {}) {
  const fn = new AsyncFunction('$json', '$input', '$', src(name));
  const result = await fn(
    json,
    { first: () => ({ json }) },
    (nodeName) => ({ first: () => ({ json: nodes[nodeName] || {} }) }),
  );
  assert(result?.[0]?.json);
  return result[0].json;
}

(async () => {
  const prepared = {
    session_id: 'sess_test',
    entrypoint: 'http',
    agent_input: JSON.stringify({ instruction: 'base' }),
    request: { prompt: 'Give me the oil price.' },
  };
  const columns = await run('Assess deterministic clarification need', {
    json: {
      result: {
        selected: { table_id: 'tbl_1', sheet: 'Monthly Prices', range: 'A5:E8', columns: ['Column A', 'Crude oil, Brent', 'Crude oil, WTI'] },
        candidates: [{ table_id: 'tbl_1' }],
        sheet_candidates: [],
        column_candidates: ['Crude oil, Brent', 'Crude oil, WTI', 'Crude oil, average'],
        suggested_select: [],
        ambiguous: true,
        reason: 'ambiguous_columns',
      },
    },
    nodes: { 'Prepare AI Agent input': prepared },
  });
  assert.equal(columns.preflight_needs_clarification, true);
  assert.match(columns.preflight_clarification_args.questions[0].question, /Crude oil, Brent/);
  assert.equal(columns.preflight_clarification_args.questions[0].id, 'column_selection');

  const unique = await run('Assess deterministic clarification need', {
    json: {
      result: {
        selected: { table_id: 'tbl_1', sheet: 'Monthly Prices', range: 'A5:E8', columns: ['Natural gas, Europe'] },
        candidates: [{ table_id: 'tbl_1' }],
        sheet_candidates: [],
        column_candidates: [],
        suggested_select: ['Natural gas, Europe'],
        ambiguous: false,
        reason: 'unique_table',
      },
    },
    nodes: { 'Prepare AI Agent input': prepared },
  });
  assert.equal(unique.preflight_needs_clarification, false);
  assert.equal(unique.selected_table_authoritative, false);

  const locked = await run('Assess deterministic clarification need', {
    json: {
      result: {
        selected: { table_id: 'tbl_1', sheet: 'Monthly Prices', range: 'A5:E8', columns: ['Natural gas, Europe'] },
        candidates: [{ table_id: 'tbl_1' }],
        sheet_candidates: [],
        column_candidates: [],
        suggested_select: ['Natural gas, Europe'],
        ambiguous: false,
        reason: 'unique_table',
      },
    },
    nodes: { 'Prepare AI Agent input': { ...prepared, request: { prompt: 'Give me the oil price.', table_selector: 'Monthly Prices' } } },
  });
  assert.equal(locked.selected_table_authoritative, true);

  const repair = await run('Prepare deterministic query repair', {
    json: { session_id: 'sess_test', final_args: { status: 'error' } },
    nodes: {
      'Get deterministic session state': {
        session_id: 'sess_test',
        tables: { tbl_1: { table_id: 'tbl_1' } },
        result_sets: {},
        tool_history: [],
        table_match: {
          selected_table_id: 'tbl_1',
          ambiguous: false,
          candidate_ids: ['tbl_1'],
          suggested_select: ['Column A', 'Natural gas, Europe'],
          suggested_limit: 12,
          suggested_tail: true,
          suggested_filters: [],
        },
      },
    },
  });
  assert.equal(repair.needs_query_repair, true);
  assert.equal(repair.repair_args.table_id, 'tbl_1');
  assert.deepEqual(repair.repair_args.select, ['Column A', 'Natural gas, Europe']);
  assert.equal(repair.repair_args.limit, 12);
  assert.equal(repair.repair_args.tail, true);

  const noRepair = await run('Prepare deterministic query repair', {
    json: { session_id: 'sess_test', final_args: { status: 'error' } },
    nodes: {
      'Get deterministic session state': {
        session_id: 'sess_test',
        tables: { tbl_1: { table_id: 'tbl_1' } },
        result_sets: {},
        tool_history: [],
        table_match: { selected_table_id: 'tbl_1', ambiguous: true, candidate_ids: ['tbl_1'], suggested_select: [] },
      },
    },
  });
  assert.equal(noRepair.needs_query_repair, false);

  const candidateOnly = await run('Prepare deterministic query repair', {
    json: { session_id: 'sess_test', final_args: { status: 'error' } },
    nodes: {
      'Get deterministic session state': {
        session_id: 'sess_test',
        tables: { tbl_1: { table_id: 'tbl_1' }, tbl_2: { table_id: 'tbl_2' } },
        result_sets: {},
        tool_history: [],
        table_match: { selected_table_id: '', ambiguous: false, candidate_ids: ['tbl_2'], suggested_select: ['Date'] },
      },
    },
  });
  assert.equal(candidateOnly.needs_query_repair, true);
  assert.equal(candidateOnly.repair_args.table_id, 'tbl_2');
  const extras = await run('Prepare deterministic query repair', {
    json: { session_id: 'sess_test', final_args: { status: 'partial' } },
    nodes: {
      'Get deterministic session state': {
        session_id: 'sess_test',
        tables: { tbl_1: { table_id: 'tbl_1' } },
        result_sets: { res_1: { result_id: 'res_1', columns: ['Column A', 'Natural gas, Europe', 'Banana, Europe'] } },
        tool_history: [{ tool: 'query_table', ok: true }],
        table_match: {
          selected_table_id: 'tbl_1',
          ambiguous: false,
          candidate_ids: ['tbl_1'],
          suggested_select: ['Column A', 'Natural gas, Europe'],
          suggested_limit: 12,
          suggested_tail: true,
        },
      },
    },
  });
  assert.equal(extras.needs_query_repair, false);
  assert.equal(extras.repair_args, null);
  console.log('excel-extractor-runtime-smoke: 7 scenarios passed');
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
