#!/usr/bin/env python3
"""Generate Form — MAS Deployment Health Check (n8n 2.30.8, UI-only field).

Every probe URL comes from **MAS — Runtime Config** (the same Set the
Orchestrator / Excel / Schedule agents use). The form therefore checks exactly
the addresses the engineer typed once after import — no second copy of URLs,
no Docker DNS names, no lab-only probes (n8n-runners, Data Tables).

Probes:
  * GET  {activity_base_url}/health  and /ready   (Windows → n8n direction too)
  * GET  {excel_tools_url}/health, {schedule_service_url}/health, {math_url}/health
  * POST {orchestrator_step_url}                {"action":"probe"}        (Header Auth)
  * POST {webhook base}/mas-control-plane       {"operation":"list_agents"} (Header Auth)

Regenerate:  python3 n8n/templates/generate_mas_health_check.py
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from generate_mas_runtime_config import PLACEHOLDER as RUNTIME_PLACEHOLDER
from generate_mas_runtime_config import WF_NAME as RUNTIME_WF_NAME
from generate_mas_runtime_config import runtime_config_execute_params

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workflows/core/mas-deployment-health-check.workflow.json"
WF_ID = "mas-deployment-health-check-v1"
WF_NAME = "Form — MAS Deployment Health Check"
FORM_PATH = "mas-deployment-health-check"
CONTRACT_VERSION = "mas_deployment_health_check/v2.0"
ERROR_WORKFLOW_ID = "e1f0a7c2-9b4d-5e8f-a123-4567890abcde"

# Same Header Auth credential as Orchestrator webhook / Control Plane Proxy webhook.
HDR = {
    "httpHeaderAuth": {
        "id": "REPLACE_IN_UI",
        "name": "REPLACE: MAS webhook Header Auth (same as Orchestrator / Control Plane Proxy)",
    }
}

SERVICE_PROBES = (
    # node name, urls.<key>, human label
    ("Probe Activity /health", "activity_health", "Activity"),
    ("Probe Activity /ready", "activity_ready", "Activity"),
    ("Probe Excel Tools /health", "excel_health", "Excel Tools"),
    ("Probe Schedule Builder /health", "schedule_health", "Schedule Builder"),
    ("Probe Math /health", "math_health", "Math"),
)
ORCH_PROBE = "Probe Orchestrator webhook"
PROXY_PROBE = "Probe Control Plane Proxy webhook"


def nid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mas-health-check:{name}"))


def node(name, ntype, ver, pos, params, **extra):
    out = {
        "parameters": params,
        "id": nid(name),
        "name": name,
        "type": ntype,
        "typeVersion": ver,
        "position": list(pos),
    }
    out.update(extra)
    return out


def code(name, pos, js):
    return node(name, "n8n-nodes-base.code", 2, pos, {"jsCode": js})


def http_get(name, pos, url_key):
    return node(
        name,
        "n8n-nodes-base.httpRequest",
        4.2,
        pos,
        {
            "method": "GET",
            "url": f"={{{{ $('Prepare health probes').first().json.urls.{url_key} }}}}",
            "options": {
                "timeout": 8000,
                "response": {
                    "response": {"neverError": True, "responseFormat": "autodetect", "fullResponse": True}
                },
            },
        },
        continueOnFail=True,
        alwaysOutputData=True,
    )


def http_post_auth(name, pos, url_key, body_key):
    return node(
        name,
        "n8n-nodes-base.httpRequest",
        4.2,
        pos,
        {
            "method": "POST",
            "url": f"={{{{ $('Prepare health probes').first().json.urls.{url_key} }}}}",
            "authentication": "genericCredentialType",
            "genericAuthType": "httpHeaderAuth",
            "sendHeaders": True,
            "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": f"={{{{ JSON.stringify($('Prepare health probes').first().json.bodies.{body_key}) }}}}",
            "options": {
                "timeout": 20000,
                "response": {
                    "response": {"neverError": True, "responseFormat": "autodetect", "fullResponse": True}
                },
            },
        },
        credentials=HDR,
        continueOnFail=True,
        alwaysOutputData=True,
    )


def connect(c, src, dst, out="main", si=0, tin="main", ti=0):
    groups = c.setdefault(src, {})
    outputs = groups.setdefault(out, [])
    while len(outputs) <= si:
        outputs.append([])
    outputs[si].append({"node": dst, "type": tin, "index": ti})


PREPARE = r"""
/* All probe URLs come from MAS — Runtime Config (node "Runtime endpoints"). */
const note = String($json.operator_note || '').trim();
let cfg = {};
let cfgError = '';
try {
  const item = $('Runtime endpoints').first() || {};
  cfg = item.json && typeof item.json === 'object' ? item.json : {};
  if (item.error) cfgError = String(item.error.message || item.error);
  if (typeof cfg.error === 'string') cfgError = cfg.error;
  else if (cfg.error && typeof cfg.error === 'object') cfgError = String(cfg.error.message || JSON.stringify(cfg.error));
} catch (e) {
  cfgError = String(e.message || e);
}
const KEYS = ['activity_base_url', 'excel_tools_url', 'schedule_service_url', 'math_url', 'orchestrator_step_url'];
const trim = (v) => String(v || '').trim().replace(/\/+$/, '');
const runtime = {};
for (const k of KEYS) runtime[k] = trim(cfg[k]);
const LAB_DNS = /^https?:\/\/(mas-activity|excel-tools|schedule-builder|math-service)(:\d+)?$/i;
const runtime_issues = [];
if (cfgError) runtime_issues.push({ key: '*', kind: 'unbound', detail: cfgError });
for (const k of KEYS) {
  const v = runtime[k];
  if (!v) runtime_issues.push({ key: k, kind: 'empty', detail: 'empty value' });
  else if (/REPLACE_/i.test(v)) runtime_issues.push({ key: k, kind: 'placeholder', detail: v });
  else if (!/^https?:\/\//i.test(v)) runtime_issues.push({ key: k, kind: 'not_url', detail: v });
  else if (LAB_DNS.test(v)) runtime_issues.push({ key: k, kind: 'lab_dns', detail: v });
}
if (runtime.orchestrator_step_url && !/\/webhook\/mas-orchestrator-step$/.test(runtime.orchestrator_step_url)) {
  runtime_issues.push({ key: 'orchestrator_step_url', kind: 'wrong_path', detail: runtime.orchestrator_step_url });
}
const webhookBase = runtime.orchestrator_step_url.replace(/\/mas-orchestrator-step$/, '');
const join = (base, path) => (base ? `${base}${path}` : '');
const urls = {
  activity_health: join(runtime.activity_base_url, '/health'),
  activity_ready: join(runtime.activity_base_url, '/ready'),
  excel_health: join(runtime.excel_tools_url, '/health'),
  schedule_health: join(runtime.schedule_service_url, '/health'),
  math_health: join(runtime.math_url, '/health'),
  orchestrator_webhook: runtime.orchestrator_step_url,
  control_plane_webhook: webhookBase && webhookBase !== runtime.orchestrator_step_url ? `${webhookBase}/mas-control-plane` : '',
};
const bodies = {
  orchestrator_probe: { action: 'probe', case_id: 'CASE-readiness-probe', requested_by: note || 'mas-health-check' },
  control_plane_list_agents: { operation: 'list_agents' },
};
return [{ json: {
  operator_note: note || null,
  started_at: new Date().toISOString(),
  entrypoint: 'health_check',
  requested_by: note || 'mas-health-check',
  runtime,
  runtime_issues,
  urls,
  bodies,
} }];
"""


REPORT = r"""
const escapeHtml = (value) => String(value ?? '')
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/\"/g, '&quot;')
  .replace(/'/g, '&#39;');

const prepared = $('Prepare health probes').first().json || {};
const runtime = prepared.runtime || {};
const urls = prepared.urls || {};
const issues = Array.isArray(prepared.runtime_issues) ? prepared.runtime_issues : [];

const checks = [];
const push = (check, status, where_to_fix, detail) => { checks.push({ check, status, where_to_fix, detail }); };

const RUNTIME_FIX = 'MAS — Runtime Config → node "Runtime URLs" (field: http://<IP-Windows>:8200 / :8000 / :8090 / :8100; orchestrator_step_url = URL of this n8n + /webhook/mas-orchestrator-step)';
const BIND_FIX = 'Form — MAS Deployment Health Check → node "Runtime endpoints" → select "MAS — Runtime Config"';

/* 1. Runtime Config */
const unbound = issues.find((i) => i.kind === 'unbound');
if (unbound) {
  push('Runtime Config: bound and readable', 'FAIL', BIND_FIX, unbound.detail);
} else {
  push('Runtime Config: bound and readable', 'PASS', BIND_FIX, `${Object.keys(runtime).filter((k) => runtime[k]).length}/5 URLs set`);
}
for (const i of issues) {
  if (i.kind === 'unbound') continue;
  if (i.kind === 'lab_dns') {
    push(`Runtime Config: ${i.key} is a lab Docker DNS name`, 'TODO', RUNTIME_FIX, `${i.detail} — correct on the Compose lab; on the field it must be the Windows IP`);
  } else if (i.kind === 'wrong_path') {
    push(`Runtime Config: ${i.key} path`, 'FAIL', RUNTIME_FIX, `${i.detail} — must end with /webhook/mas-orchestrator-step`);
  } else {
    push(`Runtime Config: ${i.key} is ${i.kind}`, 'FAIL', RUNTIME_FIX, i.detail);
  }
}

/* 2. HTTP probes */
const readHttp = (nodeName) => {
  try {
    const items = $(nodeName).all();
    if (!items.length) return { ok: false, statusCode: 0, body: null, detail: `Node "${nodeName}" produced no items.` };
    const item = items[0];
    const j = item.json || {};
    const errMsg = item.error ? String(item.error.message || item.error) : (typeof j.message === 'string' && !j.statusCode ? j.message : '');
    if (errMsg && !j.statusCode) return { ok: false, statusCode: 0, body: null, detail: errMsg };
    const statusCode = Number(j.statusCode ?? j.status ?? 0);
    let body = j.body ?? j.data ?? null;
    if (typeof body === 'string') { try { body = JSON.parse(body); } catch (e) { /* keep text */ } }
    const short = body == null ? '' : (typeof body === 'string' ? body : JSON.stringify(body));
    return { ok: statusCode >= 200 && statusCode < 300, statusCode, body, detail: `statusCode=${statusCode || 'n/a'} body=${short.slice(0, 200)}` };
  } catch (e) {
    return { ok: false, statusCode: 0, body: null, detail: String(e.message || e) };
  }
};
const netHint = (label, url, dir) => `${label} — ${url || 'URL empty'}. ${dir}`;
const WIN = 'Windows: start-windows.bat running; service listens on 0.0.0.0; Windows Firewall allows the port from the n8n host; URL in MAS — Runtime Config.';

const svc = (nodeName, label, url, bodyOk) => {
  const r = readHttp(nodeName);
  const okBody = r.ok && (bodyOk ? bodyOk(r.body) : true);
  push(`Live: ${label}`, okBody ? 'PASS' : 'FAIL', netHint(label, url, WIN), r.detail);
  return r;
};

const activity = svc('Probe Activity /health', 'Activity /health', urls.activity_health, (b) => b && b.status === 'ok');
if (activity.ok && activity.body && typeof activity.body === 'object') {
  const backend = String(activity.body.control_plane_backend || '');
  push('Live: Activity control plane = n8n_proxy', backend === 'n8n_proxy' ? 'PASS' : 'FAIL',
    'mas-activity.env: CONTROL_PLANE_REQUIRED=true, CONTROL_PLANE_PROXY_URL=<n8n>/webhook/mas-control-plane, CONTROL_PLANE_PROXY_AUTH_* — restart Activity',
    `control_plane_backend=${backend || 'n/a'}`);
}
const ready = readHttp('Probe Activity /ready');
{
  const b = ready.body && typeof ready.body === 'object' ? (ready.body.detail && typeof ready.body.detail === 'object' ? ready.body.detail : ready.body) : {};
  const failing = [];
  const wh = b.webhooks || b.checks || {};
  const scan = (obj) => {
    if (Array.isArray(obj)) { for (const it of obj) if (it && it.ok === false) failing.push(it.name || it.url || it.path || 'webhook'); return; }
    if (obj && typeof obj === 'object') for (const [k, v] of Object.entries(obj)) { if (v && typeof v === 'object' && v.ok === false) failing.push(k); else if (Array.isArray(v)) scan(v); }
  };
  scan(wh);
  const missing = Array.isArray(b.missing_config) ? b.missing_config : [];
  push('Live: Activity /ready (Windows → n8n webhooks)', ready.ok ? 'PASS' : 'FAIL',
    'From the Windows PC Activity must reach this n8n: ORCHESTRATOR_WEBHOOK_URL / CONTROL_PLANE_PROXY_URL / KNOWLEDGE_INGEST_URL in mas-activity.env; corporate TLS → ACTIVITY_CA_BUNDLE; webhooks Active',
    ready.ok ? ready.detail : `${ready.detail}${failing.length ? ` failing=${failing.join(',')}` : ''}${missing.length ? ` missing_config=${missing.join(',')}` : ''}`);
}
svc('Probe Excel Tools /health', 'Excel Tools /health', urls.excel_health, (b) => b && (b.status === 'ok' || b.ok === true));
svc('Probe Schedule Builder /health', 'Schedule Builder /health', urls.schedule_health, (b) => b && (b.status === 'ok' || b.ok === true));
svc('Probe Math /health', 'Math /health', urls.math_health, (b) => b && (b.status === 'ok' || b.ok === true));

/* 3. n8n webhooks (this n8n calling itself — same path the Orchestrator self-POST uses) */
const webhookFix = (wf) => `n8n: ${wf} Active; Header Auth credential on this form's probe = the one on the ${wf} webhook; orchestrator_step_url in MAS — Runtime Config = URL this n8n can reach itself on`;
const orch = readHttp('Probe Orchestrator webhook');
{
  const b = orch.body && typeof orch.body === 'object' ? orch.body : {};
  let status = 'FAIL';
  let detail = orch.detail;
  if (orch.ok && (b.contract === 'mas_orchestrator_ack' || b.status === 'probe' || b.action === 'probe')) status = 'PASS';
  else if (orch.ok) { status = 'PASS'; detail = `HTTP ${orch.statusCode} but no mas_orchestrator_ack — re-import Orchestrator — MAS? ${orch.detail}`; }
  else if (orch.statusCode === 401 || orch.statusCode === 403) detail = `HTTP ${orch.statusCode}: Header Auth mismatch (probe credential vs Orchestrator webhook). ${orch.detail}`;
  else if (orch.statusCode === 404) detail = `HTTP 404: Orchestrator — MAS is not Active or orchestrator_step_url is wrong. ${orch.detail}`;
  push('Live: Orchestrator webhook /webhook/mas-orchestrator-step (action=probe)', status, webhookFix('Orchestrator — MAS'), detail);
}
const proxy = readHttp('Probe Control Plane Proxy webhook');
{
  const b = proxy.body && typeof proxy.body === 'object' ? proxy.body : {};
  const agents = Array.isArray(b.result) ? b.result.map((a) => String((a && a.agent_id) || '')).filter(Boolean) : [];
  const REQUIRED = ['excel_extractor', 'schedule_builder', 'calculation_agent'];
  const missing = REQUIRED.filter((id) => !agents.includes(id));
  let status = 'FAIL';
  let detail = proxy.detail;
  if (proxy.ok && b.ok === true && Array.isArray(b.result)) {
    status = missing.length ? 'FAIL' : 'PASS';
    detail = missing.length
      ? `agent_registry missing ${missing.join(',')} — POST {"operation":"schema"} to /webhook/mas-control-plane (seeds registry)`
      : `agents=${agents.join(',')}`;
  } else if (proxy.statusCode === 401 || proxy.statusCode === 403) detail = `HTTP ${proxy.statusCode}: Header Auth mismatch (probe credential vs Control Plane Proxy webhook). ${proxy.detail}`;
  else if (proxy.statusCode === 404) detail = `HTTP 404: MAS — Control Plane Proxy is not Active or webhook base differs from orchestrator_step_url. ${proxy.detail}`;
  else if (/relation .* does not exist/i.test(proxy.detail)) detail = `Postgres tables missing — POST {"operation":"schema"} first. ${proxy.detail}`;
  push('Live: Control Plane Proxy webhook + Postgres agent_registry (list_agents)', status,
    `${webhookFix('MAS — Control Plane Proxy')}; Postgres credential on "Execute control-plane SQL"; then POST {"operation":"schema"}`, detail);
}

/* 4. Roll-up */
const fail = checks.filter((c) => c.status === 'FAIL');
const pass = checks.filter((c) => c.status === 'PASS');
const todo = checks.filter((c) => c.status === 'TODO');
const overall = fail.length ? 'FAIL' : (todo.length ? 'PASS_WITH_TODO' : 'PASS');

const MANUAL = [
  'Execute Workflow bindings: Orchestrator — MAS → Runtime endpoints / Call Excel Extractor / Call Schedule Builder / Call Knowledge Retrieval; Agent — Excel Extractor and Agent — Schedule Builder → Runtime configuration / Call Knowledge Retrieval.',
  'Credentials: LLM on the three Chat Models; embeddings on Ingestion / Retrieval; Postgres on Ingestion / Retrieval / Orchestrator / Control Plane Proxy; Header Auth on Orchestrator webhook + POST continue run + Control Plane Proxy webhook; Excel Tools X-API-Key on Agent — Excel Extractor HTTP nodes.',
  'Settings → Error workflow = Error — MAS Node Traces on Orchestrator, Excel Extractor, Schedule Builder, Retrieval, Ingestion.',
  'Export each workflow JSON and search REPLACE_ — none may remain.',
  'Activation order: Control Plane Proxy → Ingestion / Retrieval / Excel / Schedule / Error traces → Orchestrator.',
];

const rowHtml = (c) => `<tr><td><strong>${escapeHtml(c.status)}</strong></td><td>${escapeHtml(c.check)}</td><td><code>${escapeHtml(c.where_to_fix)}</code></td><td>${escapeHtml(c.detail)}</td></tr>`;
const section = (title, rows) => rows.length
  ? `<h2>${escapeHtml(title)} (${rows.length})</h2><table border=\"1\" cellpadding=\"6\" cellspacing=\"0\"><thead><tr><th>status</th><th>check</th><th>where_to_fix</th><th>detail</th></tr></thead><tbody>${rows.map(rowHtml).join('')}</tbody></table>`
  : '';
const runtimeHtml = `<table border=\"1\" cellpadding=\"6\" cellspacing=\"0\"><thead><tr><th>Runtime Config key</th><th>value</th></tr></thead><tbody>${Object.keys(runtime).map((k) => `<tr><td><code>${escapeHtml(k)}</code></td><td><code>${escapeHtml(runtime[k] || '(empty)')}</code></td></tr>`).join('')}</tbody></table>`;

const html = `<main>
<h1>MAS deployment health check</h1>
<p>Overall: <strong>${escapeHtml(overall)}</strong></p>
<p>Started: <code>${escapeHtml(prepared.started_at || '')}</code> · Operator: <code>${escapeHtml(prepared.operator_note || prepared.requested_by || '')}</code></p>
<p>All URLs below come from <strong>MAS — Runtime Config</strong>. Probes: the four Windows FastAPI services, Activity → n8n readiness, and this n8n's own Orchestrator / Control Plane Proxy webhooks (Header Auth).</p>
${runtimeHtml}
${section('FAIL — fix these first', fail)}
${section('PASS — live probes', pass)}
${section('TODO — lab values / follow-up', todo)}
<h2>Manual checklist (not probed)</h2><ul>${MANUAL.map((m) => `<li>${escapeHtml(m)}</li>`).join('')}</ul>
<p>Full runbook: <code>docs.md</code></p>
</main>`;

return [{ json: {
  overall,
  fail_count: fail.length,
  pass_count: pass.length,
  todo_count: todo.length,
  checks,
  runtime,
  form_response_html: html,
  where_to_fix: fail.map((c) => c.where_to_fix),
} }];
"""


def main() -> None:
    readme = (
        "## MAS Deployment Health Check (n8n 2.30.8)\n"
        "1. URLs come from **MAS — Runtime Config** (bind **Runtime endpoints**). "
        "No second copy of addresses lives here.\n"
        "2. Probes: Activity `/health` + `/ready`, Excel Tools / Schedule Builder / Math `/health`, "
        "Orchestrator webhook (`action=probe`), Control Plane Proxy webhook (`list_agents`).\n"
        "3. Webhook probes use the same Header Auth credential as the Orchestrator / Control Plane Proxy webhooks.\n"
        "4. Goal on the field: **PASS** with 0 FAIL. Lab Compose DNS names in Runtime Config show as TODO "
        "(`PASS_WITH_TODO`).\n"
        "5. Does not call LLM, Excel, or SCHEDULE Builder.\n\n"
        "Full runbook: docs.md"
    )
    nodes = [
        node(
            "Health Check README",
            "n8n-nodes-base.stickyNote",
            1,
            (-600, -360),
            {"content": readme, "width": 520, "height": 340, "color": 5},
        ),
        node(
            "Health check form",
            "n8n-nodes-base.formTrigger",
            2.6,
            (0, 0),
            {
                "authentication": "n8nUserAuth",
                "formTitle": "MAS — Deployment Health Check",
                "formDescription": (
                    "Probes the four Windows FastAPI services and this n8n's Orchestrator / Control Plane "
                    "Proxy webhooks using the URLs from MAS — Runtime Config. Bind Runtime endpoints and the "
                    "Header Auth credential first (see sticky note / docs.md). Does not call LLM, Excel, or "
                    "SCHEDULE Builder."
                ),
                "formFields": {
                    "values": [
                        {
                            "fieldName": "operator_note",
                            "fieldLabel": "Optional note (who runs the check)",
                            "fieldType": "text",
                            "placeholder": "e.g. deploy-ivanov",
                            "requiredField": False,
                        }
                    ]
                },
                "responseMode": "lastNode",
                "options": {
                    "path": FORM_PATH,
                    "appendAttribution": False,
                    "buttonLabel": "Run health check",
                    "ignoreBots": True,
                    "includeUserInOutput": True,
                },
            },
            webhookId="c1000001-0001-4000-8000-000000000002",
        ),
        node(
            "Runtime endpoints",
            "n8n-nodes-base.executeWorkflow",
            1.3,
            (280, 0),
            runtime_config_execute_params(),
            onError="continueRegularOutput",
            alwaysOutputData=True,
        ),
        code("Prepare health probes", (560, 0), PREPARE),
    ]
    x = 840
    for name, key, _label in SERVICE_PROBES:
        nodes.append(http_get(name, (x, 0), key))
        x += 280
    nodes.append(http_post_auth(ORCH_PROBE, (x, 0), "orchestrator_webhook", "orchestrator_probe"))
    x += 280
    nodes.append(http_post_auth(PROXY_PROBE, (x, 0), "control_plane_webhook", "control_plane_list_agents"))
    x += 280
    nodes.append(code("Build health report", (x, 0), REPORT))
    x += 280
    nodes.append(
        node(
            "Show health report",
            "n8n-nodes-base.form",
            2.5,
            (x, 0),
            {
                "operation": "completion",
                "respondWith": "showText",
                "responseText": "={{ $json.form_response_html }}",
            },
        )
    )

    chain = (
        ["Health check form", "Runtime endpoints", "Prepare health probes"]
        + [name for name, _k, _l in SERVICE_PROBES]
        + [ORCH_PROBE, PROXY_PROBE, "Build health report", "Show health report"]
    )
    connections: dict = {}
    for src, dst in zip(chain, chain[1:]):
        connect(connections, src, dst)

    wf = {
        "id": WF_ID,
        "name": WF_NAME,
        "description": (
            "Native n8n 2.30.8 deployment readiness form. All probe URLs come from MAS — Runtime Config: "
            "Activity /health + /ready, Excel Tools, Schedule Builder, Math /health, Orchestrator webhook "
            "(action=probe), Control Plane Proxy webhook (list_agents). No Docker DNS, no Data Tables."
        ),
        "active": False,
        "settings": {
            "executionOrder": "v1",
            "saveManualExecutions": True,
            "callerPolicy": "workflowsFromSameOwner",
            "errorWorkflow": ERROR_WORKFLOW_ID,
        },
        "nodes": nodes,
        "pinData": {},
        "connections": connections,
        "versionId": "mas-deployment-health-check-v2.0",
        "meta": {
            "templateCredsSetupCompleted": False,
            "targetN8nVersion": "2.30.8",
            "contractVersion": CONTRACT_VERSION,
        },
        "tags": [],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(nodes)} nodes); runtime binding placeholder {RUNTIME_PLACEHOLDER} → {RUNTIME_WF_NAME}")

    import subprocess
    import sys

    script = Path(__file__).resolve().parent / "relayout_core_workflows.py"
    subprocess.check_call([sys.executable, str(script), "--only", OUT.name], cwd=str(ROOT.parent))


if __name__ == "__main__":
    main()
