'use strict';

// Text-only DKS/GCONPROD rebind: infer from task prose, mutate timeline, match golden_case_2 MAS_result.
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const workspace = process.env.WORKSPACE_ROOT || path.resolve(__dirname, '..', '..');
const templates = path.join(workspace, 'n8n', 'templates');
const golden2 = path.join(
  workspace,
  'simulation-model-example',
  'golden-cases',
  'golden_case_2',
);

const coreJs = execFileSync(
  'python3',
  ['-c', 'from schedule_timeline_runtime import timeline_core_js; print(timeline_core_js())'],
  { cwd: templates, encoding: 'utf8' },
);

const sandbox = {};
vm.runInNewContext(
  `${coreJs}
this.parseScheduleTimeline=parseScheduleTimeline;
this.inferGroupRebindSpec=inferGroupRebindSpec;
this.runGroupRebindRevise=runGroupRebindRevise;
this.emitScheduleFromTimeline=emitScheduleFromTimeline;`,
  sandbox,
);

const fixture = [
  'GRUPTREE',
  'NORTH FIELD /',
  '/',
  'WECON',
  '1601\t1*\t40000\t1*\t1*\t0.00035\tWELL\t1*\t1*\tRATE\t/',
  '1602\t1*\t40000\t1*\t1*\t0.00035\tWELL\t1*\t1*\tRATE\t/',
  '/',
  'WPIMULT',
  '1601\t0.4\t/',
  '1602\t0.4\t/',
  '/',
  'DATES',
  '  1 JAN 2020 /',
  '/',
  'WCONPROD',
  '1601 OPEN GRAT 1* 1* 200000 1* 1* 90 1* 30 /',
  '1602 OPEN GRAT 1* 1* 257000 1* 1* 90 1* 46 /',
  '/',
].join('\n');

const task =
  'На основе старого прогнозного schedule - скважины 1601 и 1602 помести в отдельную группу - "DKS", и задай этим скважинам групповой контроль 200 тыс. м3 газа в сут. (с момента даты ввода этих скважин).';
const model = sandbox.parseScheduleTimeline(fixture, 'schedule.inc');
const spec = sandbox.inferGroupRebindSpec(task, model);
assert.ok(spec, 'inferGroupRebindSpec must parse golden_case_2 task text');
assert.equal(spec.wells.slice().sort().join(','), '1601,1602');
assert.equal(spec.parent_group, 'DKS');
assert.equal(spec.gas_rate, 200000);
assert.equal(spec.well_groups['1601'], 'G1601');

const applied = sandbox.runGroupRebindRevise(fixture, spec, 'schedule.inc');
assert.equal(applied.status, 'applied');
assert.match(applied.generated_schedule, /WELSPECS[\s\S]*1601 G1601 \//);
assert.match(applied.generated_schedule, /GRUPTREE[\s\S]*DKS FIELD \//);
assert.match(applied.generated_schedule, /GCONPROD[\s\S]*DKS GRAT 2\* 200000 \//);
assert.match(applied.generated_schedule, /WECON[\s\S]*1601/);
assert.match(applied.generated_schedule, /WPIMULT[\s\S]*1601/);

const noSpec = sandbox.inferGroupRebindSpec(
  'На основе старого прогнозного schedule и Excel с новыми датами ввода собрать новый schedule.inc',
  model,
);
assert.equal(noSpec, null, 'commissioning-only task must not infer a group rebind');

const basePath = path.join(golden2, 'MONITORING_1_2_2_1_4q25_3_NORTH1_6_FDP.INC');
const masPath = path.join(golden2, 'MONITORING_1_2_2_1_4q25_3_NORTH1_6_FDP_MAS_result.INC');
if (fs.existsSync(basePath) && fs.existsSync(masPath)) {
  const base = fs.readFileSync(basePath, 'utf8');
  const fullModel = sandbox.parseScheduleTimeline(base, 'schedule.inc');
  const fullSpec = sandbox.inferGroupRebindSpec(task, fullModel);
  assert.ok(fullSpec, 'infer from real MONITORING baseline');
  const full = sandbox.runGroupRebindRevise(base, fullSpec, 'schedule.inc');
  assert.equal(full.status, 'applied');
  const cmp = execFileSync(
    path.join(workspace, 'mas-activity-service', '.venv', 'bin', 'python'),
    [
      '-c',
      [
        'import json,sys',
        'from pathlib import Path',
        'sys.path.insert(0, str(Path(sys.argv[1])))',
        'from run_ui_smoke import compare_schedules',
        'got=sys.stdin.read()',
        'exp=Path(sys.argv[2]).read_text(encoding="utf-8")',
        'print(json.dumps(compare_schedules(got,exp,got_name="got",exp_name="mas",ignore_keywords={"WEFAC"})))',
      ].join('; '),
      path.join(workspace, 'simulation-model-example', 'golden-cases'),
      masPath,
    ],
    { input: full.generated_schedule, encoding: 'utf8' },
  );
  const report = JSON.parse(cmp);
  assert.equal(report.ok, true, `golden_case_2 MAS_result mismatch: ${cmp.slice(0, 800)}`);
}

console.log('SCHEDULE group-rebind timeline smoke: passed');
