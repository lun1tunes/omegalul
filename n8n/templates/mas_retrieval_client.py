"""Shared MAS Knowledge Retrieval client.

One Postgres/PGVector corpus. Isolation is ``filters.target_base`` (plus
``knowledge_types``). Callers must set the selector for their role — never query
the whole table.

Selectors (same names as ``NAMESPACES`` in ``mas_knowledge_spaces.py``):

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

from mas_knowledge_spaces import SELECTORS


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
function retrievalOutage(result, raw){
  if(Boolean(raw&&raw.error)||Number(raw&&raw.statusCode)>=400) return 'failed';
  const st=String((result&&result.status)||'').toLowerCase();
  if(st==='failed'||st==='error') return 'failed';
  if(st==='needs_input') return 'needs_input';
  if(st==='abstain') return 'abstain';
  if(st==='unavailable') return 'unavailable';
  return '';
}
function compactRetrievalCards(result, selector, limit, textLimit, preferFull){
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
    const summary=String(body.summary||r.summary||'').trim();
    const full=String(body.text||body.instruction||r.text||'').trim();
    const text=preferFull?(full||summary):(summary||full);
    if(text.length<minText) continue;
    const score=r.rrf_score!=null?Number(r.rrf_score):(body.rrf_score!=null?Number(body.rrf_score):null);
    if(Number.isFinite(score)&&score<=0) continue;
    const topics=Array.isArray(r.topics)?r.topics:(Array.isArray(body.topics)?body.topics:[]);
    scored.push({
      knowledge_id:String(r.knowledge_id||body.knowledge_id||''),
      title:String(r.title||body.title||'').slice(0,160),
      knowledge_type:kt||null,
      text:text.slice(0,cut),
      revision:(r.revision!=null&&r.revision!=='')?r.revision:((body.revision!=null&&body.revision!=='')?body.revision:null),
      rrf_score:Number.isFinite(score)?score:null,
      branches:Array.isArray(r.branches)?r.branches.slice(0,6).map(b=>String(b)):(Array.isArray(body.branches)?body.branches.slice(0,6).map(b=>String(b)):[]),
      topics:topics.slice(0,12).map(t=>String(t||'').trim()).filter(Boolean),
      _score:Number.isFinite(score)?score:null
    });
  }
  const finite=scored.map(c=>c._score).filter(s=>s!=null);
  const best=finite.length?Math.max.apply(null,finite):0;
  const floor=best>0?best*relKeep:0;
  const want=new Set((Array.isArray(selector.topics)?selector.topics:[]).map(t=>String(t||'').toLowerCase()).filter(Boolean));
  const orch=String(selector.target_base||'')==='orchestrator_routing';
  const kept=scored.filter(c=>{
    if(c._score==null||floor<=0) return true;
    const cardTopics=(Array.isArray(c.topics)?c.topics:[]).map(t=>String(t||'').toLowerCase());
    if(want.size&&cardTopics.some(t=>want.has(t))) return true;
    const n=Array.isArray(c.branches)?c.branches.length:0;
    if(n<2){
      if(orch&&want.size) return false;
      return true;
    }
    return c._score>=floor;
  });
  kept.sort((a,b)=>{
    if(a._score==null&&b._score==null) return 0;
    if(a._score==null) return 1;
    if(b._score==null) return -1;
    return b._score-a._score;
  });
  return kept.slice(0,cap).map(c=>({knowledge_id:c.knowledge_id,title:c.title,knowledge_type:c.knowledge_type,text:c.text,revision:c.revision,rrf_score:c.rrf_score,branches:c.branches,topics:c.topics}));
}
""".strip()


def attach_retrieval_js(
    *,
    prepare_node: str,
    selector_key: str,
    fail_open: bool = True,
    ready_note: str = "",
    empty_note: str = "",
    activity_caller: str = "",
    phase: str = "initial",
    policy_heading: str = "",
) -> str:
    """Restore the pre-retrieval item and attach a compact RAG slice.

    executeWorkflow replaces the item; callers must pass the Code node that
    still holds case state (``prepare_node``). Wrong-namespace hits are dropped.

    Compact keeps cards with missing RRF (fail-open), drops ``rrf_score<=0`` and
    text shorter than 50 chars. The 45% floor applies when a card has at least
    two retrieval branches. On ``orchestrator_routing``, if the caller sent
    ``topics``, a one-branch card is kept only when it overlaps those topics
    (RRF ``1/(60+rank)`` never fails a 45% floor inside ``top_k``). Attach prefers ``body.summary``; on-demand
    ``retrieve_knowledge`` passes ``preferFull`` so the model gets when-to-use
    and pitfalls, not the 600-char summary.
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
        + "const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);\n"
        + "const result=unwrapRetrieval(raw);\n"
        + f"const fallbackSelector={selector_json};\n"
        + "const fromPrev=prev.retrieval_selector;\n"
        + "const req=obj(prev.schedule_retrieval_request)?prev.schedule_retrieval_request:{};\n"
        + "const wantTopics=Array.isArray(req.filters&&req.filters.topics)?req.filters.topics:[];\n"
        + "const selector=(fromPrev&&fromPrev.target_base)?{target_base:String(fromPrev.target_base),knowledge_types:Array.isArray(fromPrev.knowledge_types)&&fromPrev.knowledge_types.length?fromPrev.knowledge_types:fallbackSelector.knowledge_types,topics:wantTopics}:Object.assign({},fallbackSelector,{topics:wantTopics});\n"
        + "const outage=retrievalOutage(result,raw);\n"
        + "const cards=(outage==='failed'||outage==='needs_input')?[]:compactRetrievalCards(result,selector,"
        + f"{int(sel['max_cards'])},{int(sel['text_limit'])});\n"
        + "const rag={contract:'mas_rag_evidence',contract_version:'1.0',target_base:selector.target_base,knowledge_types:selector.knowledge_types,status:outage||(cards.length?'ready':'empty'),phase:"
        + json.dumps(phase)
        + ",cards,findings:(Array.isArray(result.findings)?result.findings:[]).slice(0,6).map(f=>f&&f.code).filter(Boolean)};\n"
        + fail_js
        + f"const note=rag.status==='ready'?{json.dumps(ready, ensure_ascii=False)}:{json.dumps(empty, ensure_ascii=False)};\n"
        + (
            "const policyHeading="
            + json.dumps(policy_heading, ensure_ascii=False)
            + ";\nconst policyLines=(Array.isArray(cards)?cards:[]).map(c=>`- ${String((c&&c.title)||(c&&c.knowledge_id)||'карточка').trim()}: ${String((c&&c.text)||'').trim()}`).join('\\n')||'- нет карточек';\n"
            + "const planner=`${prev.planner_input||prev.agent_input||''}\\n\\n${policyHeading}\\n${policyLines}\\n\\n${note}\\n`;\n"
            if policy_heading
            else "const planner=`${prev.planner_input||prev.agent_input||''}\\n\\nRetrieved knowledge (target_base=${selector.target_base}):\\n${JSON.stringify(rag,null,2)}\\n\\n${note}\\n`;\n"
        )
        + "const logCards=(Array.isArray(cards)?cards:[]).map(c=>({knowledge_id:String((c&&c.knowledge_id)||''),revision:(c&&c.revision)!=null?c.revision:null,rrf_score:Number.isFinite(Number(c&&c.rrf_score))?Number(c.rrf_score):null,branches:Array.isArray(c&&c.branches)?c.branches.slice(0,6):[]}));\n"
        + "const logFindings=(Array.isArray(result.findings)?result.findings:[]).slice(0,8).map(f=>{if(obj(f)&&f.code)return{code:String(f.code)};const code=String(f||'').trim();return code?{code}:null}).filter(Boolean);\n"
        + "const activity={activity_kind:'trace.rag',status_message:`База знаний: ${rag.status} · ${logCards.length} карточек`,activity_payload:{caller:"
        + json.dumps(activity_caller or "", ensure_ascii=False)
        + ",query:String(req.query||result.query||'').slice(0,800),filters:obj(req.filters)?req.filters:{},status:rag.status,phase:rag.phase,findings:logFindings,cards:logCards}};\n"
        + (
            "return [{json:{...prev,rag,planner_input:planner,agent_input:planner,...activity}}];\n"
            if activity_caller
            else "return [{json:{...prev,rag,planner_input:planner,agent_input:planner}}];\n"
        )
    )


def attach_orchestrator_rag_js(*, policy_heading: str = "Политика из базы знаний:") -> str:
    return attach_retrieval_js(
        prepare_node="Prepare decision context",
        selector_key="orchestrator",
        fail_open=True,
        policy_heading=policy_heading,
        ready_note=(
            "Карточки — срез orchestrator_routing (routing_card): политика декомпозиции задачи, не протокол "
            "инструментов и не инструкции по ключевым словам. Используй их для plan_update, порядка вызова "
            "агентов и handoff_message. Кого именно вызывать (agent_id) и что агент умеет — только из реестра "
            "«Доступные агенты»; если карточка ссылается на агента, которого в реестре нет, — не вызывай его."
        ),
        empty_note=(
            "Срез orchestrator_routing пуст или недоступен — решай по реестру и состоянию. "
            "Не спрашивай инженера про базу знаний и не ходи в другие target_base."
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
