'use strict';

// schedule_datetime: parse DATES forms ↔ valid Date ↔ emit DATES keyword form.
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
vm.runInNewContext(
  `${coreJs}
this.parseScheduleDate=parseScheduleDate;
this.formatDatesTnav=formatDatesTnav;
this.parseScheduleTimeline=parseScheduleTimeline;
this.emitScheduleFromTimeline=emitScheduleFromTimeline;
this.editCommissioningDatesOnTimeline=editCommissioningDatesOnTimeline;
`,
  sandbox,
);

// Parse forms
for (const [raw, iso, tnav] of [
  ['1 JAN 2020', '2020-01-01', '1 JAN 2020'],
  ['23 FEB 2020', '2020-02-23', '23 FEB 2020'],
  ['2020-02-23', '2020-02-23', '23 FEB 2020'],
  ['43884', '2020-02-23', '23 FEB 2020'], // Excel serial
]) {
  const d = sandbox.parseScheduleDate(raw);
  assert.ok(d, `parse ${raw}`);
  assert.equal(d.contract, 'schedule_datetime');
  assert.equal(Object.prototype.toString.call(d.value), '[object Date]');
  assert.equal(d.iso, iso);
  assert.equal(d.tnav, tnav);
  assert.equal(sandbox.formatDatesTnav(d), tnav);
  assert.equal(d.value.toISOString().slice(0, 10), iso);
}

assert.equal(sandbox.parseScheduleDate('31 FEB 2020'), null);
assert.equal(sandbox.parseScheduleDate('not-a-date'), null);

// Timeline step carries schedule_datetime; emit rebuilds DATES body from it
const text = [
  'DATES',
  '  1 JAN 2020 /',
  '/',
  'WCONPROD',
  '  1601 OPEN GRAT 1* 1* 200000 1* 1* 90 1* 30 /',
  '/',
  'DATES',
  '  1 FEB 2020 /',
  '/',
].join('\n');
const model = sandbox.parseScheduleTimeline(text, 't.inc');
assert.equal(model.contract_version, '1.1');
assert.ok(model.steps[1].date);
assert.equal(Object.prototype.toString.call(model.steps[1].date.value), '[object Date]');
assert.equal(model.steps[1].date.tnav, '1 JAN 2020');

const edited = sandbox.editCommissioningDatesOnTimeline(model, [{ well: '1601', date: '23 FEB 2020' }]);
assert.equal(edited.status, 'applied');
const out = sandbox.emitScheduleFromTimeline(edited.model);
assert.match(out, /DATES\n\s*23 FEB 2020 \//);
assert.match(out, /1601 OPEN GRAT/);

// Round-trip: emit → parse preserves calendar day
const again = sandbox.parseScheduleTimeline(out, 'out.inc');
const hit = again.steps.find((s) => s.date && s.date.iso === '2020-02-23');
assert.ok(hit);
assert.equal(hit.date.tnav, '23 FEB 2020');
assert.equal(Object.prototype.toString.call(hit.date.value), '[object Date]');

// JSON boundary: Date may stringify — parseScheduleDate revives a real Date
const serialized = JSON.parse(JSON.stringify(hit.date));
const revived = sandbox.parseScheduleDate(serialized);
assert.ok(revived);
assert.equal(Object.prototype.toString.call(revived.value), '[object Date]');
assert.equal(revived.iso, '2020-02-23');
assert.equal(revived.tnav, '23 FEB 2020');

console.log('SCHEDULE datetime parse/emit smoke: passed');
