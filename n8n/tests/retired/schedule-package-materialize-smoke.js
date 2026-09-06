'use strict';

// Materialize flat drag-and-drop SCHEDULE uploads into Builder package fields.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const vm = require('node:vm');

const { workspace, workflowFile } = require('../_workflow');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function loadMaterializeFn() {
  const py = spawnSync('python3', ['-c', 'from schedule_package_materialize import MATERIALIZE_CORE_JS; print(MATERIALIZE_CORE_JS)'], {
    cwd: path.join(workspace, 'n8n', 'templates'),
    encoding: 'utf8',
    maxBuffer: 4 * 1024 * 1024,
  });
  assert.equal(py.status, 0, py.stderr || 'failed to load MATERIALIZE_CORE_JS');
  const context = {
    console,
    TextEncoder,
    Buffer: require('node:buffer').Buffer,
  };
  vm.createContext(context);
  vm.runInContext(`${py.stdout}\nthis.__fn = materializeSchedulePackage;`, context);
  return context.__fn;
}

function walkPackage(dir, out = []) {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, ent.name);
    if (ent.isDirectory()) walkPackage(p, out);
    else if (/\.(INC|GRDECL|DATA|SCH|TXT)$/i.test(ent.name)) out.push(p);
  }
  return out;
}

async function analyzeWithBuilder(pkg) {
  const workflow = JSON.parse(fs.readFileSync(workflowFile('tnavigator-schedule-builder.workflow.json'), 'utf8'));
  const node = workflow.nodes.find((n) => n.name === 'Analyze lossless baseline inventory');
  assert(node);
  const fn = new AsyncFunction('$json', node.parameters.jsCode);
  const out = await fn({
    baseline_request: {
      root_path: pkg.root_path,
      baseline_schedule_text: pkg.baseline_schedule_text,
      include_files: pkg.include_files,
      encoding: 'utf-8-text',
    },
  });
  return out[0].json;
}

async function main() {
  const materialize = loadMaterializeFn();
  const packageDir = path.join(workspace, 'simulation-model-example', 'package');
  const files = walkPackage(packageDir);
  assert.ok(files.length >= 14);

  // Flat basename drop (browser multi-file).
  const flatUploads = files.map((p) => ({
    fileName: path.basename(p),
    text: fs.readFileSync(p, 'utf8'),
  }));
  const flat = materialize({
    uploads: flatUploads,
    preferred_root: 'MONITORING_1_2_2_1_4q25_3_NORTH1_6_FDP.INC',
  });
  assert.equal(flat.ok, true, flat.errors?.join('; '));
  assert.equal(flat.package.root_path, 'SCHEDULE/FORECAST/MONITORING_1_2_2_1_4q25_3_NORTH1_6_FDP.INC');
  assert.equal(flat.package.file_count, 14);
  assert.equal(flat.package.include_files.length, 13);

  const analysis = await analyzeWithBuilder(flat.package);
  assert.equal(analysis.status, 'analyzed', JSON.stringify(analysis.findings || []).slice(0, 500));
  assert.equal(analysis.package.files.length, 14);

  // Ambiguous root: two INCLUDE files without preferred_root.
  const amb = materialize({
    uploads: [
      { fileName: 'a.inc', text: "INCLUDE\n'x.inc' /\n/\n" },
      { fileName: 'b.inc', text: "INCLUDE\n'y.inc' /\n/\n" },
      { fileName: 'x.inc', text: '-- x\n' },
      { fileName: 'y.inc', text: '-- y\n' },
    ],
  });
  assert.equal(amb.ok, false);
  assert.match(amb.errors.join(' '), /Ambiguous SCHEDULE root|set schedule_root/i);

  // Duplicate basename fails.
  const dup = materialize({
    uploads: [
      { fileName: 'a.inc', text: "INCLUDE\n'b.inc' /\n/\n" },
      { fileName: 'b.inc', text: '-- one\n' },
      { fileName: 'b.inc', text: '-- two\n' },
    ],
    preferred_root: 'a.inc',
  });
  assert.equal(dup.ok, false);
  assert.match(dup.errors.join(' '), /Duplicate basename/i);

  // Missing INCLUDE body is a warning — package still materializes from root.
  const missing = materialize({
    uploads: [{ fileName: 'root.inc', text: "INCLUDE\n'missing.inc' /\n/\n" }],
  });
  assert.equal(missing.ok, true);
  assert.equal(missing.package.file_count, 1);
  assert.match(missing.warnings.join(' '), /Missing uploaded body/i);

  // INCLUDE in a comment/string is not a directive and must not manufacture
  // the `INCLUDE path invalid` error.
  const prose = materialize({
    uploads: [{
      fileName: 'MVP1_schedule_IN.INC',
      text: "-- do not INCLUDE 'ghost.inc'\nMESSAGE 'INCLUDE \\\"ghost.inc\\\"' /\nDATES\n  1 JAN 2025 /\n",
    }],
  });
  assert.equal(prose.ok, true, prose.errors?.join('; '));
  assert.equal(prose.package.file_count, 1);

  // A root with no INCLUDE directive is a valid single-file package.
  const noInclude = materialize({
    uploads: [{ fileName: 'MVP1_schedule_IN.INC', text: 'DATES\n  1 JAN 2025 /\n' }],
  });
  assert.equal(noInclude.ok, true, noInclude.errors?.join('; '));
  assert.equal(noInclude.package.file_count, 1);

  console.log(JSON.stringify({
    ok: true,
    scenarios: 6,
    flat_root: flat.package.root_path,
    flat_files: flat.package.file_count,
    analyze_status: analysis.status,
  }));
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
