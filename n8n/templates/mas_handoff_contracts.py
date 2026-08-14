"""Practical MAS handoff contracts: registry, typed gaps, domain packets, chat activity.

Factor rule: each helper must reject or reshape work that previously burned budget
or produced opaque blobs. No fields that only exist for documentation.
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "contracts" / "specialist_registry.v1.json"
EVIDENCE_GAP_REQUIRED = (
    "entity",
    "effective_at",
    "keyword",
    "field",
    "reason",
    "expected_format",
)


def load_registry() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


def specialist_catalog_js(registry: dict | None = None) -> str:
    """Planner-visible catalogue derived from the registry (no workflow IDs)."""
    reg = registry or load_registry()
    rows = []
    for s in reg["specialists"]:
        row = {
            "specialist_id": s["specialist_id"],
            "chat_role": s.get("chat_role"),
            "capabilities": s.get("capabilities") or [],
        }
        if s.get("depends_on"):
            row["depends_on"] = s["depends_on"]
        if s.get("constraints"):
            row["constraints"] = s["constraints"]
        rows.append(row)
    return json.dumps(rows, ensure_ascii=False)


def allowlist_js(registry: dict | None = None) -> str:
    """Deterministic route table derived from the same registry."""
    reg = registry or load_registry()
    lines = []
    for s in reg["specialists"]:
        sid = s["specialist_id"]
        route = int(s["route"])
        configured = "true" if s.get("configured") else "false"
        lines.append(f" {sid}:{{route:{route},configured:{configured}}},")
    return "{\n" + "\n".join(lines) + "\n}"


def chat_role_map_js(registry: dict | None = None) -> str:
    reg = registry or load_registry()
    mapping = {s["specialist_id"]: s.get("chat_role") or s["specialist_id"] for s in reg["specialists"]}
    return json.dumps(mapping, ensure_ascii=False)


# Shared JS fragments injected into Orchestrator / Builder Code nodes.
TYPED_GAP_FILTER_JS = r"""
const EVIDENCE_GAP_REQUIRED=['entity','effective_at','keyword','field','reason','expected_format'];
const typedEvidenceGaps=(gaps)=>{
  const arr=Array.isArray(gaps)?gaps:[];
  return arr.filter(g=>g&&typeof g==='object'&&!Array.isArray(g)&&EVIDENCE_GAP_REQUIRED.every(k=>String(g[k]||'').trim())).map(g=>({
    entity:String(g.entity).trim(),
    effective_at:String(g.effective_at).trim(),
    keyword:String(g.keyword).trim().toUpperCase(),
    field:String(g.field).trim(),
    reason:String(g.reason).trim(),
    expected_format:String(g.expected_format).trim(),
    ...(typeof g.question==='string'&&g.question.trim()?{question:g.question.trim()}:{})
  })).slice(0,100);
};
""".strip()


NORMALIZE_SOURCE_FACTS_JS = r"""
const normalizeSourceFactsPacket=(compact)=>{
  const c=compact&&typeof compact==='object'&&!Array.isArray(compact)?compact:null;
  if(!c)return null;
  const snapshot=String(c.source_snapshot_hash||'').trim();
  const correlation=String(c.correlation_id||'').trim();
  if(!snapshot||!correlation)return null;
  const preview=Array.isArray(c.preview_records)?c.preview_records.filter(v=>v&&typeof v==='object').slice(0,200):[];
  const facts=Array.isArray(c.facts)&&c.facts.length
    ?c.facts.filter(v=>v&&typeof v==='object').slice(0,500)
    :preview.map((row,i)=>({fact_id:`preview_${i+1}`,values:row,provenance:{kind:'excel_preview_row',index:i}}));
  return{
    contract:'source_facts_packet',
    contract_version:'1.0',
    source_snapshot_hash:snapshot,
    correlation_id:correlation,
    facts,
    conflicts:Array.isArray(c.conflicts)?c.conflicts.filter(v=>v&&typeof v==='object').slice(0,100):[],
    columns:Array.isArray(c.columns)?c.columns.slice(0,500):[],
    row_count:Number.isFinite(Number(c.row_count))?Number(c.row_count):facts.length,
    returned_count:Number.isFinite(Number(c.returned_count))?Number(c.returned_count):preview.length,
    truncated:Boolean(c.truncated),
    decision_record:c.decision_record&&typeof c.decision_record==='object'?c.decision_record:undefined,
    trace_summary:Array.isArray(c.trace_summary)?c.trace_summary:undefined,
    stage_scores:Array.isArray(c.stage_scores)?c.stage_scores:undefined,
    gate_decisions:Array.isArray(c.gate_decisions)?c.gate_decisions:undefined,
    agent_tool_trace:Array.isArray(c.agent_tool_trace)?c.agent_tool_trace:undefined,
    overall_score:c.overall_score
  };
};
""".strip()


APPEND_HANDOFF_JS = r"""
const appendHandoffEvent=(runtime,evt)=>{
  const r=runtime&&typeof runtime==='object'&&!Array.isArray(runtime)?runtime:{};
  const prev=Array.isArray(r.handoff_events)?r.handoff_events:[];
  const now=new Date().toISOString();
  const details=evt.details&&typeof evt.details==='object'&&!Array.isArray(evt.details)?{...evt.details}:{};
  const timer=r.specialist_timer&&typeof r.specialist_timer==='object'?r.specialist_timer:null;
  let duration_ms=Number.isFinite(Number(evt.duration_ms))?Math.max(0,Math.trunc(Number(evt.duration_ms))):null;
  const closesSpecialist=Boolean(timer&&timer.specialist_id&&evt.from_specialist&&evt.from_specialist===timer.specialist_id&&evt.from_specialist!=='universal_orchestrator');
  if(duration_ms==null&&closesSpecialist&&timer.started_at){
    const started=Date.parse(String(timer.started_at));
    const ended=Date.parse(now);
    if(Number.isFinite(started)&&Number.isFinite(ended)) duration_ms=Math.max(0,ended-started);
  }
  if(duration_ms!=null) details.duration_ms=duration_ms;
  const brief=String(evt.brief||evt.summary||'').slice(0,800);
  const event={
    contract:'mas_activity_turn',
    contract_version:'1.1',
    event_type:'handoff',
    at:now,
    stage:String(evt.stage||'plan'),
    status:String(evt.status||'observed'),
    from_specialist:evt.from_specialist||null,
    to_specialist:evt.to_specialist||null,
    from_role:evt.from_role||null,
    to_role:evt.to_role||null,
    summary:String(evt.summary||'').slice(0,500),
    brief,
    duration_ms,
    details
  };
  let nextTimer=timer;
  if(String(evt.status||'')==='DELEGATED'&&evt.to_specialist&&evt.to_specialist!=='universal_orchestrator'){
    nextTimer={specialist_id:evt.to_specialist,started_at:now,from_status:'DELEGATED'};
  }else if(closesSpecialist){
    nextTimer=null;
  }else if(evt.to_specialist&&evt.to_specialist!=='universal_orchestrator'&&evt.to_specialist!==(timer&&timer.specialist_id)){
    nextTimer={specialist_id:evt.to_specialist,started_at:now,from_status:String(evt.status||'handoff')};
  }
  return {...r,handoff_events:[...prev,event].slice(-50),specialist_timer:nextTimer||null};
};
""".strip()


def evidence_gap_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:mas:evidence-gap:1.0",
        "title": "Typed SCHEDULE evidence gap",
        "type": "object",
        "additionalProperties": False,
        "required": list(EVIDENCE_GAP_REQUIRED),
        "properties": {
            "entity": {"type": "string", "minLength": 1},
            "effective_at": {"type": "string", "minLength": 1},
            "keyword": {"type": "string", "minLength": 1},
            "field": {"type": "string", "minLength": 1},
            "reason": {"type": "string", "minLength": 1},
            "expected_format": {"type": "string", "minLength": 1},
            "question": {"type": "string"},
        },
    }


def source_facts_packet_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:mas:source-facts-packet:1.0",
        "title": "Excel→Schedule source facts packet",
        "type": "object",
        "additionalProperties": True,
        "required": [
            "contract",
            "contract_version",
            "source_snapshot_hash",
            "correlation_id",
            "facts",
        ],
        "properties": {
            "contract": {"const": "source_facts_packet"},
            "contract_version": {"const": "1.0"},
            "source_snapshot_hash": {"type": "string", "minLength": 1},
            "correlation_id": {"type": "string", "minLength": 1},
            "facts": {"type": "array", "items": {"type": "object"}},
            "conflicts": {"type": "array"},
            "columns": {"type": "array"},
            "row_count": {"type": "number"},
            "returned_count": {"type": "number"},
            "truncated": {"type": "boolean"},
        },
    }
