"""Shared MAS Knowledge Retrieval client.

One Postgres/PGVector corpus. Isolation is ``filters.target_base`` (plus
``knowledge_types``). Callers must set the selector for their role — never query
the whole table.

Selectors (same names as ``NAMESPACES`` in ``schedule_rag_workflows.py``):

- ``orchestrator`` → ``orchestrator_routing`` / ``routing_card`` (thin Orchestrator)
- ``excel`` → ``excel_protocol`` / ``protocol_instruction`` (live Excel Extractor LLM path)
- ``schedule`` → ``schedule_mvp`` / ``keyword_instruction`` + ``worked_example`` (live Schedule Builder LLM path)
- ``specialist`` → ``specialist_template`` (cloned specialists)

The executeWorkflow node is identical for every caller; only the request
``filters`` change. Bind in UI: ``REPLACE_SCHEDULE_RAG_RETRIEVAL_IN_UI`` →
``MAS — Knowledge Retrieval``.

Tag branch in Retrieval is **OR** across ``keyword_families`` / ``topics`` /
``task_patterns`` (not AND). Hard isolation is ``target_base`` +
``knowledge_types`` + ``access_scope``. For ``orchestrator_routing``,
``keyword_families`` are routing tags (``XLSX``, ``INC``, ``COMMISSIONING``),
never SCHEDULE keywords (``DATES``, ``WCONPROD``).
"""

from __future__ import annotations

import json

RETRIEVAL_PLACEHOLDER = "REPLACE_SCHEDULE_RAG_RETRIEVAL_IN_UI"
RETRIEVAL_WF_NAME = "MAS — Knowledge Retrieval"

SELECTORS: dict[str, dict] = {
    "orchestrator": {
        "target_base": "orchestrator_routing",
        "knowledge_types": ["routing_card"],
        "access_scope": "petroleum-engineering",
        "top_k": 12,
        "topics": [],
        "max_cards": 6,
        "text_limit": 700,
    },
    "excel": {
        "target_base": "excel_protocol",
        "knowledge_types": ["protocol_instruction"],
        "access_scope": "petroleum-engineering",
        "top_k": 8,
        "topics": ["протокол", "clarification"],
        "max_cards": 8,
        "text_limit": 1200,
    },
    "schedule": {
        "target_base": "schedule_mvp",
        "knowledge_types": ["keyword_instruction", "worked_example"],
        "access_scope": "petroleum-engineering",
        "top_k": 10,
        "topics": [],
        "max_cards": 6,
        "text_limit": 800,
    },
    "specialist": {
        "target_base": "specialist_template",
        "knowledge_types": ["capability_instruction", "worked_example"],
        "access_scope": "petroleum-engineering",
        "top_k": 8,
        "topics": ["specialist", "capability"],
        "max_cards": 8,
        "text_limit": 1200,
    },
}


def knowledge_retrieval_execute_params() -> dict:
    """executeWorkflow 1.3 parameters. Input is ``schedule_retrieval_request`` on the item."""
    return {
        "source": "database",
        "workflowId": {
            "__rl": True,
            "value": RETRIEVAL_PLACEHOLDER,
            "mode": "list",
            "cachedResultName": RETRIEVAL_WF_NAME,
        },
        "workflowInputs": {
            "mappingMode": "defineBelow",
            "value": {"schedule_retrieval_request": "={{ $json.schedule_retrieval_request }}"},
            "matchingColumns": [],
            "schema": [],
            "attemptToConvertTypes": False,
            "convertFieldsToString": False,
        },
        "mode": "once",
        "options": {"waitForSubWorkflow": True},
    }


RAG_HELPERS_JS = r"""
function unwrapRetrieval(raw){
  const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
  const src=obj(raw)?raw:{};
  if(obj(src.schedule_retrieval_result)) return src.schedule_retrieval_result;
  if(obj(src.body)&&obj(src.body.schedule_retrieval_result)) return src.body.schedule_retrieval_result;
  if(String(src.contract||'')==='schedule_retrieval_result') return src;
  if(src.error||Number(src.statusCode)>=400) return {status:'failed',findings:[{code:'RETRIEVAL_CALL_FAILED'}],results:[]};
  return src;
}
function compactRetrievalCards(result, selector, limit, textLimit){
  const rows=Array.isArray(result.results)?result.results:[];
  const wantedBase=String(selector.target_base||'');
  const wantedTypes=new Set((selector.knowledge_types||[]).map(v=>String(v).toLowerCase()));
  const cap=Number(limit)||6;
  const cut=Number(textLimit)||700;
  const minText=50;
  const relKeep=0.45;
  const scored=[];
  for(const r of rows){
    if(!r||typeof r!=='object') continue;
    const body=r.body&&typeof r.body==='object'?r.body:{};
    const base=String(r.target_base||body.target_base||(result.filters&&result.filters.target_base)||'');
    const kt=String(r.knowledge_type||body.knowledge_type||'').toLowerCase();
    if(wantedBase&&base&&base!==wantedBase) continue;
    if(wantedTypes.size&&kt&&!wantedTypes.has(kt)) continue;
    const text=String(body.text||body.instruction||r.text||'').trim();
    if(text.length<minText) continue;
    const score=r.rrf_score!=null?Number(r.rrf_score):(body.rrf_score!=null?Number(body.rrf_score):null);
    if(Number.isFinite(score)&&score<=0) continue;
    scored.push({
      knowledge_id:String(r.knowledge_id||body.knowledge_id||''),
      title:String(r.title||body.title||'').slice(0,160),
      knowledge_type:kt||null,
      text:text.slice(0,cut),
      _score:Number.isFinite(score)?score:null
    });
  }
  const finite=scored.map(c=>c._score).filter(s=>s!=null);
  const best=finite.length?Math.max.apply(null,finite):0;
  const floor=best>0?best*relKeep:0;
  const kept=scored.filter(c=>c._score==null||floor<=0||c._score>=floor);
  kept.sort((a,b)=>{
    if(a._score==null&&b._score==null) return 0;
    if(a._score==null) return 1;
    if(b._score==null) return -1;
    return b._score-a._score;
  });
  return kept.slice(0,cap).map(c=>({knowledge_id:c.knowledge_id,title:c.title,knowledge_type:c.knowledge_type,text:c.text}));
}
""".strip()


def attach_retrieval_js(
    *,
    prepare_node: str,
    selector_key: str,
    fail_open: bool = True,
    ready_note: str = "",
    empty_note: str = "",
) -> str:
    """Restore the pre-retrieval item and attach a compact RAG slice.

    executeWorkflow replaces the item; callers must pass the Code node that
    still holds case state (``prepare_node``). Wrong-namespace hits are dropped.

    Compact keeps cards with missing RRF (fail-open), drops ``rrf_score<=0`` and
    text shorter than 50 chars, then drops scores below 45% of the best hit.
    Absolute 0.05 is wrong for this retriever: RRF is ``1/(60+rank)`` ≈ 0.016
    at rank 1, so 0.05 would drop semantic-only cards.
    Selector comes from ``prev.retrieval_selector`` (Prepare) with a hardcoded
    fallback so Attach cannot drift from a different target_base.
    """
    sel = SELECTORS[selector_key]
    selector_json = json.dumps(
        {
            "target_base": sel["target_base"],
            "knowledge_types": sel["knowledge_types"],
        },
        ensure_ascii=False,
    )
    ready = ready_note or (
        f"Карточки — срез {sel['target_base']}. Используй для своей роли. "
        "Не подмешивай другие target_base."
    )
    empty = empty_note or (
        f"Срез {sel['target_base']} пуст или недоступен. "
        "Не спрашивай HITL про RAG и не ходи в другие target_base."
    )
    fail_js = ""
    if not fail_open:
        fail_js = (
            "if(rag.status!=='ready') return [{json:{...prev,rag,planner_input:prev.planner_input||''}}];\n"
        )
    return (
        RAG_HELPERS_JS
        + "\n"
        + f"const prev=$({json.dumps(prepare_node)}).first().json||{{}};\n"
        + "const raw=$json||{};\n"
        + "const result=unwrapRetrieval(raw);\n"
        + f"const fallbackSelector={selector_json};\n"
        + "const fromPrev=prev.retrieval_selector;\n"
        + "const selector=(fromPrev&&fromPrev.target_base)?{target_base:String(fromPrev.target_base),knowledge_types:Array.isArray(fromPrev.knowledge_types)&&fromPrev.knowledge_types.length?fromPrev.knowledge_types:fallbackSelector.knowledge_types}:fallbackSelector;\n"
        + "const failed=result.status==='abstain'||result.status==='failed'||result.status==='needs_input'||Boolean(raw.error);\n"
        + f"const cards=failed?[]:compactRetrievalCards(result,selector,{int(sel['max_cards'])},{int(sel['text_limit'])});\n"
        + "const rag={contract:'mas_rag_evidence',contract_version:'1.0',target_base:selector.target_base,knowledge_types:selector.knowledge_types,status:failed?'unavailable':(cards.length?'ready':'empty'),cards,findings:(Array.isArray(result.findings)?result.findings:[]).slice(0,6).map(f=>f&&f.code).filter(Boolean)};\n"
        + fail_js
        + f"const note=rag.status==='ready'?{json.dumps(ready, ensure_ascii=False)}:{json.dumps(empty, ensure_ascii=False)};\n"
        + "const planner=`${prev.planner_input||prev.agent_input||''}\\n\\nRetrieved knowledge (target_base=${selector.target_base}):\\n${JSON.stringify(rag,null,2)}\\n\\n${note}\\n`;\n"
        + "return [{json:{...prev,rag,planner_input:planner,agent_input:planner}}];\n"
    )


def attach_orchestrator_rag_js() -> str:
    return attach_retrieval_js(
        prepare_node="Prepare decision context",
        selector_key="orchestrator",
        fail_open=True,
        ready_note=(
            "Карточки — срез orchestrator_routing (routing_card), не excel_protocol и не schedule_mvp. "
            "Используй для декомпозиции, plan_update, выбора агента и handoff_message. "
            "agent_id только из реестра; старые имена маппинг: excel_extraction_specialist→excel_extractor, "
            "schedule_builder_specialist→schedule_builder, engineering_calculation_specialist→calculation_agent. "
            "cluster/binary/presentation в live registry нет — не вызывай."
        ),
        empty_note=(
            "Срез orchestrator_routing пуст или недоступен — решай по реестру и compact. "
            "Не спрашивай HITL про RAG и не ходи в другие target_base."
        ),
    )


def attach_excel_rag_js() -> str:
    return attach_retrieval_js(
        prepare_node="Prepare AI Agent input",
        selector_key="excel",
        fail_open=True,
        ready_note=(
            "Карточки — срез excel_protocol (protocol_instruction), не schedule_mvp и не orchestrator_routing. "
            "Это протокол инструментов (opaque id, query_table, clarification). "
            "Строки workbook только из Excel-tools. Не спрашивай HITL про базу знаний."
        ),
        empty_note=(
            "Срез excel_protocol пуст или недоступен — работай правилами инструментов. "
            "Не спрашивай HITL про RAG и не ходи в другие target_base."
        ),
    )


def attach_schedule_rag_js() -> str:
    return attach_retrieval_js(
        prepare_node="Prepare AI Agent input",
        selector_key="schedule",
        fail_open=True,
        ready_note=(
            "Карточки — срез schedule_mvp (keyword_instruction / worked_example), "
            "не excel_protocol и не orchestrator_routing. "
            "Это when-to-use и pitfalls. Расклад полей — get_keyword.details / render_ir, "
            "не schema_catalogue из RAG."
        ),
        empty_note=(
            "Срез schedule_mvp пуст или недоступен — работай инструментами. "
            "Не спрашивай HITL про RAG и не ходи в другие target_base."
        ),
    )
