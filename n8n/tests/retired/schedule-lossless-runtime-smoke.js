'use strict';

// Executes the exact Code-node sources exported for n8n 2.30.8.  This is a
// runtime contract smoke, not a reimplementation of the parser or merger.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const { workflowFile } = require('../_workflow');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

function codeFrom(name, nodeName) {
  const workflow = JSON.parse(fs.readFileSync(workflowFile(name), 'utf8'));
  const node = workflow.nodes.find((candidate) => candidate.name === nodeName);
  assert(node, `${name}: node ${nodeName} is missing`);
  assert.equal(node.type, 'n8n-nodes-base.code');
  return node.parameters.jsCode;
}

async function runCode(source, json) {
  const fn = new AsyncFunction('$json', source);
  const output = await fn(json);
  assert(Array.isArray(output) && output.length === 1 && output[0].json);
  return output[0].json;
}

function file(result, ref) {
  const found = result.package?.files?.find((candidate) => candidate.file_ref === ref)
    || result.output_package?.files?.find((candidate) => candidate.file_ref === ref);
  assert(found, `file ${ref} is missing`);
  return found;
}

function findingCodes(result) {
  return new Set((result.findings || []).map((finding) => finding.code));
}

async function main() {
  const analyzeCode = codeFrom(
    'tnavigator-schedule-builder.workflow.json',
    'Analyze lossless baseline inventory',
  );
  const mergeCode = codeFrom(
    'tnavigator-schedule-builder.workflow.json',
    'Merge SCHEDULE draft deterministically',
  );

  const rootText = [
    '-- root comment',
    'SCHEDULE',
    '',
    'WELSPECS',
    " 'W-1' 'FIELD' 10 20 1* 'OIL' /",
    '',
    'MYSTERY_KW',
    ' 1 * /',
    '',
    'INCLUDE',
    " 'includes/wells.inc' /",
    '',
  ].join('\r\n');
  const childText = [
    '-- child comment',
    'WCONPROD',
    " 'W-1' 'OPEN' 'ORAT' 1000 4* /",
    '/',
    '',
  ].join('\n');

  const analysis = await runCode(analyzeCode, {
    baseline_request: {
      root_path: 'model/schedule.inc',
      baseline_schedule_text: rootText,
      include_files: [{ path: 'model/includes/wells.inc', text: childText }],
      encoding: 'utf-8-text',
    },
  });
  assert.equal(analysis.status, 'analyzed');
  assert.equal(analysis.package.files.length, 2);
  assert.equal(file(analysis, 'model/schedule.inc').manifest.line_endings, 'CRLF');
  assert.equal(file(analysis, 'model/includes/wells.inc').manifest.line_endings, 'LF');
  assert(analysis.opaque_keywords.includes('MYSTERY_KW'));
  assert.equal(analysis.keyword_inventory.WELSPECS, 1);
  assert.equal(analysis.keyword_inventory.WCONPROD, 1);
  assert.equal(analysis.include_graph['model/schedule.inc'][0].target_file_ref, 'model/includes/wells.inc');
  assert.deepEqual(analysis.reachable_files, ['model/includes/wells.inc', 'model/schedule.inc']);
  for (const current of analysis.package.files) {
    assert.equal(current.nodes.map((node) => node.raw).join(''), current.text);
  }

  const hashVector = await runCode(analyzeCode, {
    baseline_request: { root_path: 'hash.inc', baseline_schedule_text: 'abc' },
  });
  assert.equal(
    file(hashVector, 'hash.inc').manifest.sha256,
    'sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad',
  );

  const zeroChange = await runCode(mergeCode, {
    merge_request: { mode: 'REVISE', baseline_analysis: analysis, changes: [] },
  });
  assert.equal(zeroChange.status, 'merged');
  assert.equal(zeroChange.output_package.package_hash, analysis.package.package_hash);
  assert.equal(zeroChange.preservation_report.zero_change_byte_identical, true);
  assert.equal(file(zeroChange, 'model/schedule.inc').text, rootText);
  assert.equal(file(zeroChange, 'model/includes/wells.inc').text, childText);

  const target = file(analysis, 'model/includes/wells.inc').nodes.find(
    (node) => node.keyword === 'WCONPROD',
  );
  assert(target);
  const replacement = [
    'WCONPROD',
    " 'W-1' 'OPEN' 'ORAT' 1250 4* /",
    '/',
    '',
  ].join('\n');
  const modified = await runCode(mergeCode, {
    merge_request: {
      mode: 'REVISE',
      baseline_analysis: analysis,
      changes: [{
        operation: 'MODIFY',
        keyword: 'WCONPROD',
        target_node_id: target.node_id,
        expected_raw_hash: target.raw_hash,
        rendered_text: replacement,
      }],
    },
  });
  assert.equal(modified.status, 'merged');
  assert.equal(modified.applied_changes.length, 1);
  assert.equal(file(modified, 'model/schedule.inc').text, rootText);
  assert.equal(
    file(modified, 'model/includes/wells.inc').text,
    childText.slice(0, target.start_char) + replacement + childText.slice(target.end_char),
  );
  assert.equal(modified.preservation_report.modified_count, 1);

  const stale = await runCode(mergeCode, {
    merge_request: {
      mode: 'REVISE',
      baseline_analysis: analysis,
      changes: [{
        operation: 'MODIFY',
        keyword: 'WCONPROD',
        target_node_id: target.node_id,
        expected_raw_hash: 'sha256:stale',
        rendered_text: replacement,
      }],
    },
  });
  assert.equal(stale.status, 'needs_input');
  assert(findingCodes(stale).has('TARGET_HASH_REQUIRED_OR_STALE'));
  assert.equal(stale.applied_changes.length, 0);
  assert.equal(stale.rejected_changes.length, 1);
  assert.equal(file(stale, 'model/includes/wells.inc').text, childText);
  assert.equal(stale.output_package.package_hash, analysis.package.package_hash);

  const removeWithoutApproval = await runCode(mergeCode, {
    merge_request: {
      mode: 'REVISE',
      baseline_analysis: analysis,
      changes: [{
        operation: 'REMOVE',
        keyword: 'WCONPROD',
        target_node_id: target.node_id,
        expected_raw_hash: target.raw_hash,
      }],
    },
  });
  assert.equal(removeWithoutApproval.status, 'needs_input');
  assert(findingCodes(removeWithoutApproval).has('REMOVE_REQUIRES_ACCOUNTABLE_APPROVAL'));
  assert.equal(file(removeWithoutApproval, 'model/includes/wells.inc').text, childText);

  const missingInclude = await runCode(analyzeCode, {
    baseline_request: {
      root_path: 'schedule.inc',
      baseline_schedule_text: "INCLUDE\n 'missing.inc' /\n",
    },
  });
  assert.equal(missingInclude.status, 'analyzed');
  assert(findingCodes(missingInclude).has('INCLUDE_NOT_FOUND'));
  const missingFinding = (missingInclude.findings || []).find((f) => f.code === 'INCLUDE_NOT_FOUND');
  assert.equal(missingFinding.severity, 'warning');
  assert((missingInclude.package.files[0].nodes || []).some((n) => n.keyword === 'INCLUDE'));

  const unsafeInclude = await runCode(analyzeCode, {
    baseline_request: {
      root_path: 'schedule.inc',
      baseline_schedule_text: "INCLUDE\n '../outside.inc' /\n",
    },
  });
  assert.equal(unsafeInclude.status, 'needs_input');
  assert(findingCodes(unsafeInclude).has('INCLUDE_PATH_UNSAFE'));
  assert(!findingCodes(unsafeInclude).has('INCLUDE_NOT_FOUND'));

  const parentRelativeInclude = await runCode(analyzeCode, {
    baseline_request: {
      root_path: 'schedule.inc',
      baseline_schedule_text: "INCLUDE\n '../outside.inc' /\n",
      include_files: [
        { path: '../outside.inc', text: "DATES\n  1 JAN 2025 /\n" },
      ],
    },
  });
  assert.equal(parentRelativeInclude.status, 'needs_input');
  assert(findingCodes(parentRelativeInclude).has('INCLUDE_PATH_UNSAFE'));
  assert(!findingCodes(parentRelativeInclude).has('INCLUDE_NOT_FOUND'));

  const absoluteInclude = await runCode(analyzeCode, {
    baseline_request: {
      root_path: 'schedule.inc',
      baseline_schedule_text: "INCLUDE\n '/models/shared/controls.inc' /\n",
      include_files: [
        { path: '/models/shared/controls.inc', text: "DATES\n  1 FEB 2025 /\n" },
      ],
    },
  });
  assert.equal(absoluteInclude.status, 'needs_input');
  assert(findingCodes(absoluteInclude).has('INCLUDE_PATH_UNSAFE'));

  const urlInclude = await runCode(analyzeCode, {
    baseline_request: {
      root_path: 'schedule.inc',
      baseline_schedule_text: "INCLUDE\n 'file:///models/shared/controls.inc' /\n",
      include_files: [
        { path: 'file:///models/shared/controls.inc', text: "DATES\n  1 MAR 2025 /\n" },
      ],
    },
  });
  assert.equal(urlInclude.status, 'needs_input');
  assert(findingCodes(urlInclude).has('INCLUDE_PATH_UNSAFE'));

  const collapsedSafeInclude = await runCode(analyzeCode, {
    baseline_request: {
      root_path: 'model/schedule.inc',
      baseline_schedule_text: "INCLUDE\n 'includes/../includes/wells.inc' /\n",
      include_files: [
        { path: 'model/includes/wells.inc', text: "DATES\n  1 APR 2025 /\n" },
      ],
    },
  });
  assert.equal(collapsedSafeInclude.status, 'analyzed');
  assert(!findingCodes(collapsedSafeInclude).has('INCLUDE_PATH_UNSAFE'));
  assert(!findingCodes(collapsedSafeInclude).has('INCLUDE_NOT_FOUND'));

  const malformedRoot = await runCode(analyzeCode, {
    baseline_request: {
      root_path: 'bad\0path.inc',
      baseline_schedule_text: "DATES\n  1 MAY 2025 /\n",
    },
  });
  assert.equal(malformedRoot.status, 'needs_input');
  assert(findingCodes(malformedRoot).has('ROOT_PATH_INVALID'));
  assert(!findingCodes(malformedRoot).has('ROOT_PATH_UNSAFE'));

  const cycle = await runCode(analyzeCode, {
    baseline_request: {
      root_path: 'schedule.inc',
      baseline_schedule_text: "INCLUDE\n 'a.inc' /\n",
      include_files: [
        { path: 'a.inc', text: "INCLUDE\n 'schedule.inc' /\n" },
      ],
    },
  });
  assert.equal(cycle.status, 'needs_input');
  assert(findingCodes(cycle).has('INCLUDE_CYCLE'));

  console.log('SCHEDULE lossless runtime smoke: 14 scenarios passed');
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
