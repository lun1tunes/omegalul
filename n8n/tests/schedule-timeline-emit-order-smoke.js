'use strict';

// Stage-3 timeline emit: within each DATES step, keyword blocks follow WITHIN_DATE_KEYWORD_ORDER.
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const path = require('node:path');
const vm = require('node:vm');

const workspace = process.env.WORKSPACE_ROOT || path.resolve(__dirname, '..', '..');
const py = path.join(workspace, 'n8n', 'templates', 'schedule_timeline_runtime.py');

const coreJs = execFileSync(
  'python3',
  ['-c', 'from schedule_timeline_runtime import timeline_core_js; print(timeline_core_js())'],
  { cwd: path.dirname(py), encoding: 'utf8' },
);

const sandbox = {};
vm.runInNewContext(`${coreJs}\nthis.parseScheduleTimeline=parseScheduleTimeline;\nthis.emitScheduleFromTimeline=emitScheduleFromTimeline;`, sandbox);

const text = [
  'DATES',
  '  1 JAN 2025 /',
  '/',
  'APPLYSCRIPT',
  "  'hook.py' 'on_step' /",
  '/',
  'WCONPROD',
  "  'W1' OPEN ORAT 10 * /",
  '/',
  'WELSPECS',
  "  'W1' 'G1' 1 1 1* OIL /",
  '/',
  'DATES',
  '  1 FEB 2025 /',
  '/',
  'WCONPROD',
  "  'W2' OPEN ORAT 20 * /",
  '/',
].join('\n');

const model = sandbox.parseScheduleTimeline(text, 'schedule.inc');
const out = sandbox.emitScheduleFromTimeline(model);
const headers = out.split('\n').filter((line) => /^(DATES|WELSPECS|WCONPROD|APPLYSCRIPT)\b/.test(line.trim()));
assert.deepEqual(headers, ['DATES', 'WELSPECS', 'WCONPROD', 'APPLYSCRIPT', 'DATES', 'WCONPROD']);

console.log('SCHEDULE timeline within-date emit order smoke: passed');
