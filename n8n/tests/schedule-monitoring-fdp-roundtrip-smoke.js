'use strict';

// Golden combat smoke: real Petrel forecast SCHEDULE INC.
// 1) Nested package + stubs → INCLUDE ../../ resolves (analyzed, 0 unsafe).
// 2) WEFAC/WELOPEN on allowlist → not opaque on root CST.
// Empty-REVISE regenerate stays byte-identical on the root INC text.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || '/workspace';
const workflows = path.join(workspace, 'n8n', 'workflows', 'core');
const packageRoot = path.join(workspace, 'simulation-model-example', 'package');
const rootRel = 'SCHEDULE/FORECAST/MONITORING_1_2_2_1_4q25_3_NORTH1_6_FDP.INC';
const fixture = path.join(packageRoot, rootRel);
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function codeFrom(workflowFile, nodeName) {
  const workflow = JSON.parse(fs.readFileSync(path.join(workflows, workflowFile), 'utf8'));
  const node = workflow.nodes.find((candidate) => candidate.name === nodeName);
  assert(node, `${workflowFile}: node ${nodeName} is missing`);
  return node.parameters.jsCode;
}

async function runCode(source, json) {
  const fn = new AsyncFunction('$json', source);
  const output = await fn(json);
  assert(Array.isArray(output) && output.length === 1 && output[0].json);
  return output[0].json;
}

function walkIncludes(dir, out = []) {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) walkIncludes(p, out);
    else if (/\.(INC|GRDECL)$/i.test(ent.name)) out.push(p);
  }
  return out;
}

async function main() {
  assert(fs.existsSync(fixture), `missing package fixture ${fixture}`);
  const text = fs.readFileSync(fixture, 'utf8');
  const include_files = walkIncludes(path.join(packageRoot, 'INCLUDE')).map((p) => ({
    path: path.relative(packageRoot, p).split(path.sep).join('/'),
    text: fs.readFileSync(p, 'utf8'),
  }));
  assert.equal(include_files.length, 13, 'expected 13 stub include files');

  const analyzeCode = codeFrom('tnavigator-schedule-builder.workflow.json', 'Analyze lossless baseline inventory');
  const mergeCode = codeFrom('tnavigator-schedule-builder.workflow.json', 'Merge SCHEDULE draft deterministically');

  const analysis = await runCode(analyzeCode, {
    baseline_request: {
      root_path: rootRel,
      baseline_schedule_text: text,
      include_files,
      encoding: 'utf-8-text',
    },
  });

  assert.equal(analysis.status, 'analyzed');
  assert.equal(analysis.package.files.length, 14);
  const includeUnsafe = (analysis.findings || []).filter((f) => f.code === 'INCLUDE_PATH_UNSAFE');
  const includeMissing = (analysis.findings || []).filter((f) => f.code === 'INCLUDE_NOT_FOUND');
  assert.equal(includeUnsafe.length, 0);
  assert.equal(includeMissing.length, 0);

  const root = analysis.package.files.find((f) => f.file_ref === rootRel);
  assert(root, 'root file_ref missing from package');
  const concat = root.nodes.map((n) => n.raw).join('');
  assert.equal(concat, text, 'CST nodes must concatenate to original bytes');
  assert.equal(root.nodes.filter((n) => n.keyword === 'DATES').length, 348);
  assert.equal(root.nodes.filter((n) => n.keyword === 'INCLUDE').length, 13);
  assert.equal(root.nodes.filter((n) => n.keyword === 'WCONPROD').length, 10);
  assert.equal(root.nodes.filter((n) => n.keyword === 'GCONPROD').length, 68);
  assert.ok(root.nodes.some((n) => n.keyword === 'WEFAC'));
  assert.ok(root.nodes.some((n) => n.keyword === 'WELOPEN'));

  const opaque = [...new Set(root.nodes.filter((n) => n.opaque).map((n) => n.keyword))].sort();
  assert.deepEqual(opaque, [], 'WEFAC/WELOPEN must be allowlisted (not opaque)');

  const defaults = root.nodes.reduce((sum, n) => sum + (n.default_marker_count || 0), 0);
  assert.ok(defaults >= 100, `expected many default markers (1*/6*), got ${defaults}`);

  // Date-bound lexical timeline: events inherit last DATES value.
  let at = null;
  const dated = [];
  for (const n of root.nodes) {
    if (n.keyword === 'DATES') {
      const m = n.raw.match(/\b(\d{1,2}\s+[A-Z]{3}\s+\d{4})\b/i);
      at = m ? m[1].toUpperCase() : at;
      continue;
    }
    if (!n.keyword) continue;
    dated.push({ at, keyword: n.keyword, opaque: Boolean(n.opaque) });
  }
  const slots = new Set(dated.map((e) => e.at).filter(Boolean));
  assert.ok(slots.size >= 50, `expected many date slots, got ${slots.size}`);

  const merged = await runCode(mergeCode, {
    merge_request: {
      mode: 'REVISE',
      baseline_analysis: analysis,
      changes: [],
    },
  });
  assert.equal(merged.status, 'merged');
  assert.equal(merged.preservation_report.zero_change_byte_identical, true);
  assert.equal(merged.generated_schedule, text);

  console.log(JSON.stringify({
    ok: true,
    scenarios: 1,
    bytes: text.length,
    nodes: root.nodes.length,
    files: analysis.package.files.length,
    defaults,
    date_slots: slots.size,
    opaque,
    analyze_status: analysis.status,
    package_hash: analysis.package.package_hash,
  }));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
