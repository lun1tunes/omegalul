'use strict';
/**
 * Error — MAS Node Traces: the native n8n Error Trigger → error_traces + system.node_error.
 *
 * Runs the three Code nodes with the payload n8n 2.30.8 hands to an Error Trigger
 * ({execution:{id,url,error:{message,stack,name,node},lastNodeExecuted,mode}, workflow:{id,name}})
 * and checks what reaches the developer log («Лог»): execution link, workflow, node, message, bounded stack.
 * Orchestrator *and* agent workflows point at this workflow (settings.errorWorkflow), and agent workflows
 * map their execution id to the case on `agent.accepted`, so a failed agent node is attributed to the case.
 */
const assert = require('node:assert/strict');
const path = require('node:path');
const { readWorkflow } = require('./_workflow');

const wf = readWorkflow('mas-error-traces.workflow.json');
const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;

assert.equal(wf.name, 'Error — MAS Node Traces');
assert.ok(wf.nodes.some((n) => n.type === 'n8n-nodes-base.errorTrigger'), 'native Error Trigger');
assert.deepEqual(
  wf.nodes.filter((n) => n.type === 'n8n-nodes-base.postgres').map((n) => n.name),
  ['Lookup case by execution', 'Insert error_traces', 'Insert system.node_error event'],
);

function code(name) {
  const node = wf.nodes.find((n) => n.name === name);
  assert.ok(node && node.parameters.jsCode, name);
  return node.parameters.jsCode;
}
async function run(name, json, nodes = {}) {
  const lookup = (nodeName) => {
    if (!Object.prototype.hasOwnProperty.call(nodes, nodeName)) throw new Error(`node not executed: ${nodeName}`);
    const items = [{ json: nodes[nodeName] }];
    return { first: () => items[0], all: () => items };
  };
  const fn = new AsyncFunction('$json', '$', '$input', code(name));
  const out = await fn(json, lookup, { first: () => ({ json }), all: () => [{ json }] });
  assert.ok(Array.isArray(out) && out[0]?.json);
  return out[0].json;
}

(async () => {
  const trigger = {
    execution: {
      id: '5150',
      url: 'https://n8n.corp/workflow/wf-excel/executions/5150',
      retryOf: null,
      mode: 'integrated',
      lastNodeExecuted: 'Excel Extractor AI Agent',
      error: {
        message: 'Bad request - please check your parameters',
        name: 'NodeApiError',
        stack: 'NodeApiError: Bad request\n' + '    at frame\n'.repeat(200),
        node: { name: 'Excel Extractor AI Agent', type: '@n8n/n8n-nodes-langchain.agent' },
      },
    },
    workflow: { id: 'wf-excel', name: 'Agent — Excel Extractor' },
  };
  const normalized = await run('Normalize n8n error trigger', trigger);
  assert.equal(normalized.execution_id, '5150');
  assert.equal(normalized.execution_url, 'https://n8n.corp/workflow/wf-excel/executions/5150');
  assert.equal(normalized.workflow_name, 'Agent — Excel Extractor');
  assert.equal(normalized.workflow_id, 'wf-excel');
  assert.equal(normalized.node_name, 'Excel Extractor AI Agent');
  assert.equal(normalized.node_type, '@n8n/n8n-nodes-langchain.agent');
  assert.equal(normalized.error_type, 'NodeApiError');
  assert.deepEqual(normalized.lookup_sql_parameters, ['5150']);

  // Case found through the executions table (agent workflows register their execution on agent.accepted).
  const attached = await run('Attach case_id', { case_id: 'CASE-1' }, { 'Normalize n8n error trigger': normalized });
  assert.equal(attached.case_id, 'CASE-1');
  assert.equal(attached.trace_sql_parameters[0], 'CASE-1');
  assert.equal(attached.trace_sql_parameters[3], 'Excel Extractor AI Agent');
  assert.deepEqual(JSON.parse(attached.trace_sql_parameters[7]), {
    execution_id: '5150',
    execution_url: 'https://n8n.corp/workflow/wf-excel/executions/5150',
    workflow_id: 'wf-excel',
    node_type: '@n8n/n8n-nodes-langchain.agent',
  });

  const event = await run('Prepare system.node_error', { ...attached, error_id: 42 });
  assert.equal(event.skip_event, false);
  const [caseId, taskId, kind, actor, agentId, status, message, handoff, payloadJson] = event.event_sql_parameters;
  assert.deepEqual([caseId, taskId, kind, actor, agentId, status, handoff], ['CASE-1', null, 'system.node_error', 'n8n', null, 'error', null]);
  assert.equal(message, 'Упал узел Excel Extractor AI Agent');
  const payload = JSON.parse(payloadJson);
  assert.equal(payload.error_id, 42);
  assert.equal(payload.execution_url, 'https://n8n.corp/workflow/wf-excel/executions/5150', 'the log links straight to the failed execution');
  assert.equal(payload.workflow_name, 'Agent — Excel Extractor');
  assert.equal(payload.node_name, 'Excel Extractor AI Agent');
  assert.equal(payload.error_message, 'Bad request - please check your parameters');
  assert.ok(payload.stack.length <= 1500 && payload.stack.startsWith('NodeApiError'), 'bounded stack in the event; the full one is in error_traces');

  // Unknown execution (no case): the trace row is still written, the case event is skipped.
  const orphan = await run('Attach case_id', {}, { 'Normalize n8n error trigger': normalized });
  assert.equal(orphan.case_id, null);
  const skipped = await run('Prepare system.node_error', { ...orphan, error_id: 43 });
  assert.equal(skipped.skip_event, true);
  assert.equal(skipped.event_sql_parameters, null);

  // Sticky/settings: the workflow itself must not recurse into an error workflow.
  assert.equal(wf.settings.errorWorkflow, '');
  console.log(`${path.basename(__filename)}: ok`);
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
