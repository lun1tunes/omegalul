'use strict';
/**
 * MAS — Ensure Control Plane: DDL from n8n UI, no DROP.
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const workspace = process.env.WORKSPACE_ROOT || path.resolve(__dirname, '../..');
const wf = JSON.parse(
  fs.readFileSync(path.join(workspace, 'n8n/workflows/core/mas-ensure-control-plane.workflow.json'), 'utf8'),
);

assert.equal(wf.name, 'MAS — Ensure Control Plane');
assert.equal(wf.active, false);
assert.ok(wf.nodes.some((n) => n.type === 'n8n-nodes-base.manualTrigger'));
const pg = wf.nodes.filter((n) => n.type === 'n8n-nodes-base.postgres');
assert.ok(pg.length >= 8, `expected DDL nodes, got ${pg.length}`);
const sql = pg.map((n) => n.parameters.query).join('\n');
assert.ok(wf.nodes.some((n) => n.name === 'Seed agent_registry schedule_builder'));
assert.equal(wf.nodes.some((n) => /^Ensure statement /.test(n.name)), false);
assert.match(sql, /CREATE TABLE IF NOT EXISTS cases/);
assert.match(sql, /CREATE TABLE IF NOT EXISTS events/);
assert.match(sql, /CREATE TABLE IF NOT EXISTS error_traces/);
assert.match(sql, /CREATE TABLE IF NOT EXISTS executions/);
assert.match(sql, /CREATE TABLE IF NOT EXISTS agent_registry/);
assert.match(sql, /schedule_builder/);
assert.match(sql, /agent_id TEXT/);
assert.equal(sql.includes('DROP TABLE'), false);
assert.equal(sql.includes('DROP SCHEMA'), false);
assert.match(sql, /to_regclass\('public.cases'\)/);
assert.ok(wf.connections['When clicking Execute workflow']);
console.log('mas-ensure-control-plane-smoke: ok');
