"""Apply unified hybrid-RAG wiring without destroying Orchestrator swimlanes.

Regenerates Knowledge Ingestion + Retrieval, writes the specialist template,
patches the swimlane Orchestrator and Excel Extractor, and seeds portable
knowledge-block JSON for excel_protocol / orchestrator_routing / specialist_template.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from generate_schedule_workflows import (  # noqa: E402
    build_ingestion,
    build_retrieval,
    code,
    connect,
    ifnode,
    node,
    note,
    set_fields,
    trigger,
    workflow,
)
from generate_universal_engineering_workflows import (  # noqa: E402
    ATTACH_EXCEL_RAG,
    ATTACH_ROUTING_RAG,
    BUILD_EXCEL_RAG_GATE,
    BUILD_ROUTING_RAG_GATE,
    PLANNER_INPUT,
    PREPARE_EXCEL_RAG,
    PREPARE_ROUTING_RAG,
    build_specialist,
    call_hybrid_retrieval,
    code as u_code,
    if_node,
    uid,
)
from schedule_rag_workflows import FINALIZE_INGEST_SQL, PARENT_UPSERT_SQL  # noqa: E402

CORE = ROOT / "n8n" / "workflows" / "core"
SUPPORT = ROOT / "n8n" / "workflows" / "support"
RAG = ROOT / "n8n" / "rag"


def _dump(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path.relative_to(ROOT)}")


def upsert_node(wf: dict, new_node: dict, *, position: list[int] | None = None) -> None:
    by_name = {n["name"]: i for i, n in enumerate(wf["nodes"])}
    if position:
        new_node["position"] = position
    if new_node["name"] in by_name:
        existing = wf["nodes"][by_name[new_node["name"]]]
        if not position:
            new_node["position"] = existing.get("position", new_node.get("position"))
        wf["nodes"][by_name[new_node["name"]]] = new_node
    else:
        wf["nodes"].append(new_node)


def set_main(wf: dict, source: str, *outputs: list[str]) -> None:
    conns = wf.setdefault("connections", {})
    conns[source] = {
        "main": [
            [{"node": name, "type": "main", "index": 0} for name in group]
            for group in outputs
        ]
    }


def add_main(wf: dict, source: str, target: str, source_index: int = 0) -> None:
    conns = wf.setdefault("connections", {})
    groups = conns.setdefault(source, {}).setdefault("main", [])
    while len(groups) <= source_index:
        groups.append([])
    if not any(edge.get("node") == target for edge in groups[source_index]):
        groups[source_index].append({"node": target, "type": "main", "index": 0})


def replace_target(wf: dict, source: str, old: str, new: str) -> None:
    groups = wf.get("connections", {}).get(source, {}).get("main", [])
    for group in groups:
        for edge in group:
            if edge.get("node") == old:
                edge["node"] = new


def _portable_knowledge_block(raw: dict) -> dict | None:
    if raw.get("role") == "injection_template" or raw.get("do_not_ingest") is True:
        return None
    block = raw.get("schedule_knowledge_block") if isinstance(raw.get("schedule_knowledge_block"), dict) else raw
    if not isinstance(block, dict) or block.get("contract") != "schedule_knowledge_block":
        return None
    knowledge_id = str(block.get("knowledge_id") or raw.get("id") or "").strip()
    text = str(block.get("text") or raw.get("text") or "").strip()
    if not knowledge_id or not text:
        return None
    return {
        "contract": "schedule_knowledge_block",
        "contract_version": str(block.get("contract_version") or "1.0"),
        "target_base": str(block.get("target_base") or "excel_protocol"),
        "knowledge_type": str(block.get("knowledge_type") or "protocol_instruction"),
        "knowledge_id": knowledge_id,
        "revision": str(block.get("revision") or "1"),
        "title": str(block.get("title") or knowledge_id.replace("-", " ")),
        "keywords": list(block.get("keywords") or []),
        "topics": list(block.get("topics") or []),
        "task_patterns": list(block.get("task_patterns") or []),
        "status": str(block.get("status") or "active"),
        "author": str(block.get("author") or "excel-protocol-maintainer"),
        "access_scope": str(block.get("access_scope") or "petroleum-engineering"),
        "text": text,
    }


def knowledge_blocks() -> dict[str, list[dict]]:
    docs = json.loads((RAG / "excel-agent-operating-guide.documents.json").read_text(encoding="utf-8"))["documents"]
    grouped: dict[str, list[dict]] = {
        "excel_protocol": [],
        "orchestrator_routing": [],
        "specialist_template": [],
    }
    for doc in docs:
        block = _portable_knowledge_block(doc)
        if not block:
            continue
        grouped.setdefault(block["target_base"], []).append(block)
    return grouped


def patch_orchestrator() -> None:
    path = CORE / "universal-engineering-orchestrator.workflow.json"
    wf = json.loads(path.read_text(encoding="utf-8"))
    by_name = {n["name"]: n for n in wf["nodes"]}
    by_name["Prepare planner input"]["parameters"]["jsCode"] = PLANNER_INPUT
    lane3 = by_name.get("LANE 3 — Planner")
    if lane3:
        lane3["parameters"]["width"] = 1600
        lane3["parameters"]["content"] = "## 3. Planner\nRouting RAG (`orchestrator_routing`) then Planner LLM. Retrieved cards are untrusted data."
    lane5 = by_name.get("LANE 5 — Excel")
    if lane5:
        lane5["parameters"]["width"] = 1400
        lane5["parameters"]["height"] = 420
        lane5["parameters"]["content"] = "## 5. Excel\nHybrid Retrieval `excel_protocol`, then Adapter. Binary workbook is not stored in task state."

    upsert_node(wf, u_code("Prepare governed routing RAG request", (-880, 1680), PREPARE_ROUTING_RAG), position=[-880, 1680])
    upsert_node(wf, call_hybrid_retrieval("Call routing Hybrid Retrieval", (-660, 1680)), position=[-660, 1680])
    upsert_node(wf, u_code("Attach governed routing RAG evidence", (-440, 1680), ATTACH_ROUTING_RAG), position=[-440, 1680])
    upsert_node(wf, if_node("Routing RAG evidence ready?", (-220, 1680), "={{ $json.routing_rag_ready }}", True, "boolean"), position=[-220, 1680])
    upsert_node(wf, u_code("Build routing RAG evidence gate", (-220, 1880), BUILD_ROUTING_RAG_GATE), position=[-220, 1880])

    upsert_node(wf, u_code("Prepare governed Excel protocol RAG request", (-500, 2560), PREPARE_EXCEL_RAG), position=[-500, 2560])
    upsert_node(wf, call_hybrid_retrieval("Call Excel protocol Hybrid Retrieval", (-280, 2560)), position=[-280, 2560])
    upsert_node(wf, u_code("Attach governed Excel protocol RAG evidence", (-60, 2560), ATTACH_EXCEL_RAG), position=[-60, 2560])
    upsert_node(wf, if_node("Excel protocol RAG evidence ready?", (160, 2320), "={{ $json.excel_rag_ready }}", True, "boolean"), position=[160, 2320])
    upsert_node(wf, u_code("Build Excel protocol RAG evidence gate", (380, 2320), BUILD_EXCEL_RAG_GATE), position=[380, 2320])

    replace_target(wf, "Should new task be planned?", "Prepare planner input", "Prepare governed routing RAG request")
    replace_target(wf, "Approved or continued task delegates directly?", "Prepare planner input", "Prepare governed routing RAG request")
    replace_target(wf, "Successful specialist next stage", "Prepare planner input", "Prepare governed routing RAG request")
    replace_target(wf, "Verification requests replan?", "Prepare planner input", "Prepare governed routing RAG request")
    replace_target(wf, "Specialist error requests replan?", "Prepare planner input", "Prepare governed routing RAG request")

    set_main(
        wf,
        "Prepare governed routing RAG request",
        ["Call routing Hybrid Retrieval"],
    )
    set_main(wf, "Call routing Hybrid Retrieval", ["Attach governed routing RAG evidence"])
    set_main(wf, "Attach governed routing RAG evidence", ["Routing RAG evidence ready?"])
    set_main(
        wf,
        "Routing RAG evidence ready?",
        ["Prepare planner input"],
        ["Build routing RAG evidence gate"],
    )
    add_main(wf, "Build routing RAG evidence gate", "Call CAS persist — routing gate")

    replace_target(wf, "Configured specialist router", "Call Excel Extraction Specialist Adapter", "Prepare governed Excel protocol RAG request")
    set_main(wf, "Prepare governed Excel protocol RAG request", ["Call Excel protocol Hybrid Retrieval"])
    set_main(wf, "Call Excel protocol Hybrid Retrieval", ["Attach governed Excel protocol RAG evidence"])
    set_main(wf, "Attach governed Excel protocol RAG evidence", ["Excel protocol RAG evidence ready?"])
    set_main(
        wf,
        "Excel protocol RAG evidence ready?",
        ["Call Excel Extraction Specialist Adapter"],
        ["Build Excel protocol RAG evidence gate"],
    )
    add_main(wf, "Build Excel protocol RAG evidence gate", "Normalize specialist result")
    _dump(path, wf)


def patch_excel_agent() -> None:
    path = CORE / "excel-extraction-agent.workflow.json"
    wf = json.loads(path.read_text(encoding="utf-8"))
    by_name = {n["name"]: n for n in wf["nodes"]}
    prepare = by_name["Prepare AI Agent input"]
    js = prepare["parameters"]["jsCode"]
    if "rag_evidence" not in js:
        prepare["parameters"]["jsCode"] = js.replace(
            "instruction: continuation",
            "rag_evidence: request.rag_evidence || null,\n    instruction: continuation",
        )
    agent = by_name["Excel Extractor AI Agent"]
    system = agent["parameters"]["options"]["systemMessage"]
    if "rag_evidence" not in system:
        agent["parameters"]["options"]["systemMessage"] = (
            "Протокол extraction приходит во входе как rag_evidence (target_base=excel_protocol). "
            "Ищите правила только там. Не используйте tool поиска по руководству.\n\n"
            + system
        )

    prepare_rag = """
const prepared=$json;
const request=prepared.request&&typeof prepared.request==='object'?prepared.request:{};
const existing=request.rag_evidence&&typeof request.rag_evidence==='object'?request.rag_evidence:null;
const continuation=Boolean(prepared.is_clarification_continuation);
const tags=continuation?['TRUST-BOUNDARY','CLARIFICATION','CLARIFICATION-CONTINUATION']:['TRUST-BOUNDARY','WORKBOOK_INTROSPECT','DETECT_TABLES','DESCRIBE_TABLE','QUERY_TABLE','DISCOVERY-AND-TABLES','QUERY-RESULT-PROTOCOL'];
const query=[typeof request.prompt==='string'?request.prompt:JSON.stringify(request),'Excel Extractor operating protocol',tags.join(' ')].join('\\n');
return[{json:{...prepared,schedule_retrieval_request:{query,filters:{target_base:'excel_protocol',access_scope:'petroleum-engineering',knowledge_types:['protocol_instruction'],keyword_families:tags,topics:['протокол','clarification'],task_patterns:[]},top_k:8},excel_protocol_already_attached:Boolean(existing&&existing.contract==='mas_rag_evidence'&&Array.isArray(existing.results)&&existing.results.length)}}];
""".strip()
    attach_rag = """
const prepared=$('Prepare excel protocol RAG request').first().json;
if(prepared.excel_protocol_already_attached) return[{json:{...prepared,excel_protocol_rag_ready:true}}];
const result=$json.schedule_retrieval_result??$json;
const valid=result&&result.contract==='schedule_retrieval_result'&&result.status==='succeeded'&&result.evidence_ready===true&&Array.isArray(result.results)&&result.results.some(v=>v&&v.knowledge_type==='protocol_instruction'&&v.body);
const evidence={contract:'mas_rag_evidence',contract_version:'1.0',target_base:'excel_protocol',query:result.query,filters:result.filters,citations:Array.isArray(result.citations)?result.citations:[],results:Array.isArray(result.results)?result.results:[],retrieval:result.retrieval||{},findings:Array.isArray(result.findings)?result.findings:[]};
let agentPayload={};try{agentPayload=JSON.parse(prepared.agent_input||'{}')}catch{agentPayload={}}
agentPayload.rag_evidence=evidence;
const request=prepared.request&&typeof prepared.request==='object'?prepared.request:{};
return[{json:{...prepared,request:{...request,rag_evidence:evidence},agent_input:JSON.stringify(agentPayload),excel_protocol_rag_ready:valid,excel_rag_result:result}}];
""".strip()
    gate = """
const prepared=$('Prepare excel protocol RAG request').first().json||{};
return[{json:{
 status:'error', message:'Excel protocol RAG evidence is missing.', next_action:'handle_error',
 data:{result_id:null,artifact_ref:null,columns:[],records:[],row_count:0,returned_count:0,truncated:false,provenance:[]},
 filters_applied:[],field_mapping:{},assumptions:[],warnings:[],
 errors:[{code:'EXCEL_PROTOCOL_RAG_REQUIRED',message:'Ingest protocol_instruction into target_base=excel_protocol and retry.'}],clarification:null,
 meta:{session_id:prepared.session_id,entrypoint:prepared.entrypoint}
}}];
""".strip()

    def code_node(name: str, nid: str, pos: list[int], js_code: str) -> dict:
        return {
            "parameters": {"jsCode": js_code},
            "id": nid,
            "name": name,
            "type": "n8n-nodes-base.code",
            "typeVersion": 2,
            "position": pos,
        }

    upsert_node(wf, code_node("Prepare excel protocol RAG request", "0faac0c7-01e9-451f-87a8-000000000090", [1180, -160], prepare_rag))
    upsert_node(wf, call_hybrid_retrieval("Call Excel protocol Hybrid Retrieval", (1400, -160)))
    by_name = {n["name"]: n for n in wf["nodes"]}
    by_name["Call Excel protocol Hybrid Retrieval"]["id"] = "0faac0c7-01e9-451f-87a8-000000000091"
    by_name["Call Excel protocol Hybrid Retrieval"]["position"] = [1400, -160]
    upsert_node(wf, code_node("Attach excel protocol RAG evidence", "0faac0c7-01e9-451f-87a8-000000000092", [1620, -160], attach_rag))
    upsert_node(wf, {
        "parameters": if_node("Excel protocol RAG ready?", (0, 0), "={{ $json.excel_protocol_rag_ready }}", True, "boolean")["parameters"],
        "id": "0faac0c7-01e9-451f-87a8-000000000093",
        "name": "Excel protocol RAG ready?",
        "type": "n8n-nodes-base.if",
        "typeVersion": 2.2,
        "position": [1840, -160],
    })
    upsert_node(wf, code_node("Build excel protocol RAG gate", "0faac0c7-01e9-451f-87a8-000000000094", [1840, 40], gate))

    wf["description"] = (
        "Production Excel extractor. Three entry points normalize to one OpenAI gpt-5.4-nano "
        "AI Agent with PostgreSQL session memory, governed Hybrid Retrieval (excel_protocol), "
        "and one constrained FastAPI tool node per Excel tool. Files remain in FastAPI; only "
        "compact tool results enter the model."
    )
    wf["nodes"] = [n for n in wf["nodes"] if n["name"] not in {"PGVector operating context", "OpenAI Embeddings — text-embedding-3-small"}]
    wf["connections"].pop("PGVector operating context", None)
    wf["connections"].pop("OpenAI Embeddings — text-embedding-3-small", None)

    set_main(wf, "Prepare AI Agent input", ["Prepare excel protocol RAG request"])
    set_main(wf, "Prepare excel protocol RAG request", ["Call Excel protocol Hybrid Retrieval"])
    set_main(wf, "Call Excel protocol Hybrid Retrieval", ["Attach excel protocol RAG evidence"])
    set_main(wf, "Attach excel protocol RAG evidence", ["Excel protocol RAG ready?"])
    set_main(
        wf,
        "Excel protocol RAG ready?",
        ["Is clarification continuation before preflight?"],
        ["Build excel protocol RAG gate"],
    )
    add_main(wf, "Build excel protocol RAG gate", "Return workflow result")
    _dump(path, wf)


def patch_excel_ingestion(blocks: dict[str, list[dict]]) -> None:
    path = SUPPORT / "excel-rag-ingestion.workflow.json"
    if not path.is_file():
        return
    wf = json.loads(path.read_text(encoding="utf-8"))
    by_name = {n["name"]: n for n in wf["nodes"]}
    cfg = by_name["RAG runtime configuration"]
    for assignment in cfg["parameters"]["assignments"]["assignments"]:
        if assignment.get("name") == "rag_table_name":
            assignment["value"] = "tnavigator_schedule_knowledge_v1"
    portable = []
    for group in blocks.values():
        portable.extend(group)
    by_name["RAG documents — portable operating guide"]["parameters"]["jsCode"] = (
        "// Portable MAS namespace seed. Keep in sync with n8n/rag/*.knowledge-blocks.json and excel-agent-operating-guide.documents.json.\n"
        "const documents = "
        + json.dumps(portable, ensure_ascii=False)
        + ";\n"
        "return documents.map((block) => ({ json: {\n"
        "  document_id: block.knowledge_id,\n"
        "  text: [block.title, block.text, (block.keywords||[]).join(' '), (block.topics||[]).join(' ')].filter(Boolean).join('\\n\\n'),\n"
        "  metadata: {\n"
        "    document_id: block.knowledge_id, document_revision: block.revision, knowledge_id: block.knowledge_id,\n"
        "    revision: block.revision, target_base: block.target_base, knowledge_type: block.knowledge_type,\n"
        "    keyword_families: block.keywords, topics: block.topics, task_patterns: block.task_patterns || [],\n"
        "    parent_key: `${block.target_base}:${block.knowledge_id}:${block.revision}`,\n"
        "    ingest_key: `${block.target_base}:${block.knowledge_id}:${block.revision}`,\n"
        "    access_scope: block.access_scope, author: block.author, knowledge_status: 'current', status: 'active',\n"
        "    slug: block.knowledge_id, topic: (block.topics||[])[0] || '', version: '2026-08-13',\n"
        "    title: block.title, section: block.target_base,\n"
        "  },\n"
        "  knowledge_block: block,\n"
        "} }));\n"
    )
    loader = by_name["Default Data Loader — guide text"]
    meta_values = loader["parameters"]["options"]["metadata"]["metadataValues"]
    extra = [
        ("target_base", "={{ $json.metadata.target_base }}"),
        ("knowledge_type", "={{ $json.metadata.knowledge_type }}"),
        ("knowledge_id", "={{ $json.metadata.knowledge_id }}"),
        ("revision", "={{ $json.metadata.revision }}"),
        ("keyword_families", "={{ $json.metadata.keyword_families }}"),
        ("topics", "={{ $json.metadata.topics }}"),
        ("task_patterns", "={{ $json.metadata.task_patterns }}"),
        ("parent_key", "={{ $json.metadata.parent_key }}"),
        ("ingest_key", "={{ $json.metadata.ingest_key }}"),
        ("access_scope", "={{ $json.metadata.access_scope }}"),
        ("author", "={{ $json.metadata.author }}"),
        ("knowledge_status", "={{ $json.metadata.knowledge_status }}"),
        ("status", "={{ $json.metadata.status }}"),
        ("title", "={{ $json.metadata.title }}"),
        ("section", "={{ $json.metadata.section }}"),
    ]
    have = {item["name"] for item in meta_values}
    for name, value in extra:
        if name not in have:
            meta_values.append({"name": name, "value": value})
    wf["description"] = (
        "Manual UI-only seed of portable excel_protocol, orchestrator_routing and "
        "specialist_template knowledge into the shared PGVector/parent tables. Compatible with n8n 2.30.8."
    )
    cfg["notes"] = (
        "UI EDIT: Keep this table name identical to MAS — Knowledge Ingestion / MAS — Knowledge Retrieval "
        "(tnavigator_schedule_knowledge_v1). Isolation is metadata target_base, not a new table. "
        "If the corporate embedding model has different dimensions, choose a NEW table name here "
        "and in SCHEDULE ingestion/retrieval before the first ingest."
    )
    inspect = by_name["Postgres — inspect RAG table contents"]
    inspect["notes"] = (
        "UI REQUIRED: select the SAME PostgreSQL credential as “PGVector — insert operating guide” "
        "and MAS — Knowledge Ingestion / MAS — Knowledge Retrieval. A different credential here silently inspects "
        "the wrong database."
    )
    notes = by_name["OpenAI Embeddings — REPLACE WITH CORPORATE EMBEDDING"].get("notes", "")
    by_name["OpenAI Embeddings — REPLACE WITH CORPORATE EMBEDDING"]["notes"] = notes.replace(
        "Use the SAME model on core → “PGVector operating context”.",
        "Use the SAME model as MAS — Knowledge Ingestion / MAS — Knowledge Retrieval.",
    )
    expected_ids = [block["knowledge_id"] for block in portable]
    prepare_inv = by_name["Prepare RAG inventory query"]["parameters"]["jsCode"]
    prepare_inv = prepare_inv.replace(
        "coalesce(metadata->>'document_id', '(missing document_id)') AS document_id",
        "coalesce(metadata->>'document_id', metadata->>'knowledge_id', '(missing document_id)') AS document_id",
    )
    prepare_inv = prepare_inv.replace(
        "count(DISTINCT nullif(coalesce(metadata->>'document_id', ''), ''))",
        "count(DISTINCT nullif(coalesce(metadata->>'document_id', metadata->>'knowledge_id', ''), ''))",
    )
    old_expected_start = prepare_inv.find("const expected = [")
    old_expected_end = prepare_inv.find("];", old_expected_start)
    if old_expected_start < 0 or old_expected_end < 0:
        raise RuntimeError("Prepare RAG inventory query is missing the expected document id list")
    expected_js = "const expected = [\n  " + ",\n  ".join(json.dumps(item) for item in expected_ids) + ",\n]"
    by_name["Prepare RAG inventory query"]["parameters"]["jsCode"] = (
        prepare_inv[:old_expected_start] + expected_js + ";" + prepare_inv[old_expected_end + 2 :]
    )
    by_name["Summarize RAG inventory"]["notes"] = (
        "Open this node’s output after Test workflow. status=rag_inventory_ok means every portable "
        "excel_protocol / orchestrator_routing / specialist_template knowledge_id is present."
    )
    prepare_parent = {
        "parameters": {
            "jsCode": (
                "const docs=$('RAG documents — portable operating guide').all();\n"
                "return docs.map((entry)=>{\n"
                "  const item=entry.json||{}; const m=item.metadata||{},b=item.knowledge_block||{};\n"
                "  return {json:{...item,sql_parameters:[m.target_base,m.knowledge_id,m.revision,m.knowledge_type,'active',"
                "JSON.stringify(m.keyword_families||[]),JSON.stringify(m.topics||[]),JSON.stringify(m.task_patterns||[]),"
                "m.title||m.knowledge_id,JSON.stringify(b),item.text,m.ingest_key,m.access_scope,m.author]}};\n"
                "});"
            )
        },
        "id": "4b9a63fe-6200-4bd1-8e4c-000000000020",
        "name": "Prepare portable parent knowledge persistence",
        "type": "n8n-nodes-base.code",
        "typeVersion": 2,
        "position": [1260, 180],
    }
    parent_pg = {
        "parameters": {
            "operation": "executeQuery",
            "query": PARENT_UPSERT_SQL,
            "options": {
                "queryReplacement": "={{ $json.sql_parameters }}",
                "queryBatching": "single",
                "largeNumbersOutput": "text",
                "replaceEmptyStrings": False,
            },
        },
        "id": "4b9a63fe-6200-4bd1-8e4c-000000000021",
        "name": "PostgreSQL — upsert portable parent knowledge",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.6,
        "position": [1500, 180],
        "credentials": by_name["PGVector — insert operating guide"].get("credentials", {}),
        "alwaysOutputData": True,
    }
    finalize = {
        "parameters": {
            "operation": "executeQuery",
            "query": FINALIZE_INGEST_SQL,
            "options": {
                "queryBatching": "single",
                "largeNumbersOutput": "text",
                "replaceEmptyStrings": False,
            },
        },
        "id": "4b9a63fe-6200-4bd1-8e4c-000000000022",
        "name": "Finalize portable indexes and deduplicate chunks",
        "type": "n8n-nodes-base.postgres",
        "typeVersion": 2.6,
        "position": [1100, 180],
        "credentials": by_name["PGVector — insert operating guide"].get("credentials", {}),
        "alwaysOutputData": True,
        "executeOnce": True,
        "notes": "Creates parent/schema tables if needed, indexes hybrid-RAG metadata, and removes duplicate chunks after a re-seed.",
        "notesInFlow": True,
    }
    upsert_node(wf, finalize)
    upsert_node(wf, prepare_parent)
    upsert_node(wf, parent_pg)
    insert = "PGVector — insert operating guide"
    inventory = "Prepare RAG inventory query"
    wf["connections"][insert] = {"main": [[{"node": "Finalize portable indexes and deduplicate chunks", "type": "main", "index": 0}]]}
    set_main(wf, "Finalize portable indexes and deduplicate chunks", ["Prepare portable parent knowledge persistence"])
    set_main(wf, "Prepare portable parent knowledge persistence", ["PostgreSQL — upsert portable parent knowledge"])
    set_main(wf, "PostgreSQL — upsert portable parent knowledge", [inventory])
    _dump(path, wf)


def main() -> None:
    ingest = build_ingestion(node=node, note=note, code=code, trigger=trigger, ifnode=ifnode, connect=connect, workflow=workflow, set_fields=set_fields)
    retrieval = build_retrieval(node=node, note=note, code=code, trigger=trigger, ifnode=ifnode, connect=connect, workflow=workflow)
    _dump(CORE / "tnavigator-schedule-knowledge-ingestion.workflow.json", ingest)
    _dump(CORE / "tnavigator-schedule-hybrid-retrieval.workflow.json", retrieval)
    _dump(SUPPORT / "engineering-specialist-template.workflow.json", build_specialist())
    patch_orchestrator()
    patch_excel_agent()
    print("MAS hybrid RAG wiring applied")


if __name__ == "__main__":
    main()
