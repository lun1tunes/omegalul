'use strict';

// ECLIPSE/tNav: keyword tables with records must close with a bare '/' line.
// Guards the timeline emit bug where '/' lived only in body and was dropped.
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const workspace = process.env.WORKSPACE_ROOT || path.resolve(__dirname, '..', '..');
const templates = path.join(workspace, 'n8n', 'templates');
const combat = path.join(workspace, 'simulation-model-example', 'combat-dates-revise');

const timelineJs = execFileSync(
  'python3',
  ['-c', 'from schedule_timeline_runtime import timeline_core_js; print(timeline_core_js())'],
  { cwd: templates, encoding: 'utf8' },
);

const rendererJs = execFileSync(
  'python3',
  ['-c', 'from schedule_schema_runtime import build_schema_renderer_js; print(build_schema_renderer_js(["WCONPROD","DATES"]))'],
  { cwd: templates, encoding: 'utf8' },
);

function isKeywordHeader(trimmed) {
  if (!trimmed || trimmed === '/' || trimmed.startsWith('--')) return false;
  const m = trimmed.toUpperCase().match(/^([A-Z][A-Z0-9_]*)\b/);
  if (!m) return false;
  // Well names like N001 contain digits — not SCHEDULE keywords.
  if (/[0-9]/.test(m[1])) return false;
  return true;
}

function assertBareBlockSlash(scheduleText, label) {
  const lines = String(scheduleText || '').split(/\n/);
  let checked = 0;
  const missing = [];
  for (let i = 0; i < lines.length; i++) {
    const t = lines[i].trim();
    if (!isKeywordHeader(t)) continue;
    const kw = t.split(/\s+/)[0].toUpperCase();
    if (kw === 'DATES' || kw === 'INCLUDE') continue;
    let sawRecord = false;
    let bare = false;
    for (let j = i + 1; j < lines.length; j++) {
      const u = lines[j].trim();
      if (!u || u.startsWith('--')) continue;
      if (isKeywordHeader(u)) break;
      if (u === '/') {
        bare = true;
        break;
      }
      sawRecord = true;
    }
    if (sawRecord) {
      checked += 1;
      if (!bare) missing.push(`${kw}@${i + 1}`);
    }
  }
  assert.equal(missing.length, 0, `${label}: missing bare '/' after ${missing.slice(0, 8).join(', ')}`);
  assert.ok(checked > 0, `${label}: expected at least one keyword table with records`);
  return checked;
}

const sandbox = {};
vm.runInNewContext(
  `${timelineJs}\nthis.parseScheduleTimeline=parseScheduleTimeline;\nthis.emitScheduleFromTimeline=emitScheduleFromTimeline;\nthis.runCommissioningRevise=runCommissioningRevise;`,
  sandbox,
);

// 1) Synthetic round-trip
const synthetic = [
  'DATES',
  '  1 JAN 2025 /',
  '/',
  'WCONHIST',
  '1043 OPEN LRAT 18.9 0 0 /',
  '1054 OPEN LRAT 16.38 1.765 0 3* 57 /',
  '/',
  'WCONPROD',
  "  'W1' OPEN ORAT 10 * /",
  '/',
].join('\n');
const emitted = sandbox.emitScheduleFromTimeline(sandbox.parseScheduleTimeline(synthetic, 't.inc'));
assertBareBlockSlash(emitted, 'synthetic-emit');
assert.match(emitted, /WCONHIST\n1043 OPEN LRAT 18\.9 0 0 \/\n1054 OPEN LRAT 16\.38 1\.765 0 3\* 57 \/\n\/\n\nWCONPROD/);
assert.match(emitted, /WCONPROD\n  'W1' OPEN ORAT 10 \* \/\n\//);

// 2) Combat baseline commissioning revise
const baseline = fs.readFileSync(path.join(combat, 'baseline.inc'), 'utf8');
const wells = JSON.parse(fs.readFileSync(path.join(combat, 'date_shift_map.json'), 'utf8')).wells;
const facts = wells.map((w) => ({ well: w.well, date: w.new_date }));
const revise = sandbox.runCommissioningRevise(baseline, facts, 'schedule.inc', {
  instruction_blob: 'REVISE',
});
assert.equal(revise.status, 'applied');
const combatChecked = assertBareBlockSlash(revise.generated_schedule, 'combat-revise');

// 3) CREATE renderer default: missing layout.block_terminator → slash_line (not none)
assert.match(
  rendererJs,
  /block_terminator:clean\(l\.block_terminator\)\.toUpperCase\(\)==='NONE'\?'none':'slash_line'/,
);

console.log(
  `SCHEDULE block-terminator smoke: passed (synthetic + combat revise tables=${combatChecked})`,
);
