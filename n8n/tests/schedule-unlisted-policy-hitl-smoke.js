'use strict';

// Variant A: unlisted_wells_policy remove requires explicit enum; prose alone → needs_input.
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const workspace = process.env.WORKSPACE_ROOT || path.resolve(__dirname, '..', '..');
const templates = path.join(workspace, 'n8n', 'templates');
const combat = path.join(workspace, 'simulation-model-example', 'combat-dates-revise');

const coreJs = execFileSync(
  'python3',
  ['-c', 'from schedule_timeline_runtime import timeline_core_js; print(timeline_core_js())'],
  { cwd: templates, encoding: 'utf8' },
);

const sandbox = {};
vm.runInNewContext(
  `${coreJs}\nthis.runCommissioningRevise=runCommissioningRevise;\nthis.detectUnlistedWellsPolicy=detectUnlistedWellsPolicy;`,
  sandbox,
);

const baseline = fs.readFileSync(path.join(combat, 'baseline.inc'), 'utf8');
const wells = JSON.parse(fs.readFileSync(path.join(combat, 'date_shift_map.json'), 'utf8')).wells;
const allFacts = wells.map((w) => ({ well: w.well, date: w.new_date }));
const halfWells = new Set(['304R', '295R', '1601', '1602']);
const halfFacts = allFacts.filter((f) => halfWells.has(f.well));

const proseRemove =
  'REVISE прогнозный SCHEDULE по Excel с датами ввода. В Excel не все скважины: тех скважин, которые есть в примере schedule но нет в файле с запусками — убрать.';
const proseKeep =
  'REVISE прогнозный SCHEDULE: сдвинуть даты ввода по Excel. Инструкция молчит про скважины, которых нет в Excel — по умолчанию сохранить их запуски.';
const humanCase0 =
  'Во вложении excel файл с новыми датами ввода скважин. Используй эти новые даты для обновления прогнозного schedule файла, который так же во вложении.';

assert.equal(sandbox.detectUnlistedWellsPolicy(proseRemove), 'remove');
assert.equal(sandbox.detectUnlistedWellsPolicy(proseKeep), 'keep');
assert.equal(sandbox.detectUnlistedWellsPolicy(humanCase0), 'keep');

// 1) Prose remove, no explicit enum, half Excel → HITL
const hitl = sandbox.runCommissioningRevise(baseline, halfFacts, 'schedule.inc', {
  instruction_blob: proseRemove,
});
assert.equal(hitl.status, 'needs_input');
assert.equal(hitl.generated_schedule, '');
assert.ok((hitl.findings || []).some((f) => f.code === 'UNLISTED_WELLS_POLICY_REQUIRED'));
assert.ok((hitl.questions || []).some((q) => q.id === 'unlisted_wells_policy'));
assert.ok((hitl.unlisted_wells || []).length >= 1);

// 2) Explicit remove + same facts → applied
const removed = sandbox.runCommissioningRevise(baseline, halfFacts, 'schedule.inc', {
  instruction_blob: proseRemove,
  unlisted_wells_policy: 'remove',
});
assert.equal(removed.status, 'applied');
assert.equal(removed.unlisted_wells_policy, 'remove');
assert.ok((removed.generated_schedule || '').length > 0);
assert.ok((removed.removed || []).length > 0 || (removed.unlisted_wells || []).length > 0);

// 3) Case0-style human RU, full wells → applied keep
const c0 = sandbox.runCommissioningRevise(baseline, allFacts, 'schedule.inc', {
  instruction_blob: humanCase0,
});
assert.equal(c0.status, 'applied');
assert.equal(c0.unlisted_wells_policy, 'keep');
assert.equal((c0.unlisted_wells || []).length, 0);

// 4) Silent / keep prose, half Excel → applied keep, unlisted preserved
const kept = sandbox.runCommissioningRevise(baseline, halfFacts, 'schedule.inc', {
  instruction_blob: proseKeep,
});
assert.equal(kept.status, 'applied');
assert.equal(kept.unlisted_wells_policy, 'keep');
assert.ok((kept.unlisted_wells || []).length >= 1);
assert.ok(
  /201\b/.test(kept.generated_schedule) || /208\b/.test(kept.generated_schedule),
  'unlisted commissioning wells should remain under keep',
);

console.log('SCHEDULE unlisted-wells policy HITL smoke: passed (4 scenarios)');
