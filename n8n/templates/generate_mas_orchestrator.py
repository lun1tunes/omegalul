#!/usr/bin/env python3
"""Generate the thin MAS orchestrator (one n8n execution = one step)."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from llm_runtime_options import (
    CHAT_THINKING_OFF,
    PARSE_CHAT_EXTRA_JS,
    SAMPLING,
    DEFAULT_CHAT_MODEL,
    LLM_HTTP_TIMEOUT_MS,
)
from generate_mas_runtime_config import runtime_config_execute_params
from mas_agent_registry import PLANNER_COLUMNS, list_agents_sql
from mas_retrieval_client import (
    SELECTORS,
    attach_orchestrator_rag_js,
    knowledge_retrieval_execute_params,
)
from mas_state_utils import PLAN_STATUSES, STATE_SHAPE_JS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workflows/core/mas-orchestrator.workflow.json"
WF_ID = "e9bbdb6e-3b7c-5dc0-851a-30bd9f2eb0d6"
WF_NAME = "Orchestrator — MAS"
ERROR_WF_ID = "63116836-8724-595e-bc5e-dd6e743e2586"
PG = {"postgres": {"id": "REPLACE_IN_UI", "name": "REPLACE: SCHEDULE PostgreSQL / PGVector credential"}}
OA = {
    "openAiApi": {
        "id": "REPLACE_IN_UI",
        "name": "REPLACE: Qwen OpenAI-compatible planner chat credential",
    }
}
HDR = {"httpHeaderAuth": {"id": "REPLACE_IN_UI", "name": "REPLACE: engineering orchestrator inbound key"}}

# Parser contract for Decision LLM. Only fields the model must *choose* are required.
# Technical leftovers stay out of the schema (Parse decision already derives / defaults them):
# - action.task — discarded, never copied to agent_task.inputs (CASE-6a9ec6b3-74e34e)
# - action.question_id — Q-${step_count}
# - progress.is_repeating — repeatDelegation() from the journal
# - progress.goal_satisfied — the action type is the verdict; guards still read it if sent
# - expected_output.datasets[].fields — the callee maps columns from its inventory
# extra keys remain allowed (additionalProperties) so a verbose model does not fail the parser.
DECISION_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["status_message", "action"],
    "properties": {
        "progress": {
            "type": "object",
            "properties": {
                "evidence": {"type": "string"},
                "missing": {"type": "string"},
                "goal_satisfied": {"type": "boolean"},
            },
        },
        "status_message": {"type": "string"},
        # Plan (O13): the LLM's decomposition of the goal, merged by `id` in Parse decision. Only `id` is
        # required by the parser (a missing title falls back to the id); an item without id is rejected there.
        "plan_update": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id"],
                "properties": {
                    "id": {"type": "string"},
                    "title": {"type": "string"},
                    "agent_id": {"type": "string"},
                    "status": {"enum": PLAN_STATUSES},
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                },
            },
        },
        "action": {
            "type": "object",
            "required": ["type"],
            "properties": {
                "type": {"enum": ["call_agent", "ask_user", "finish"]},
                "agent_id": {"type": "string"},
                "task_id": {"type": "string"},
                "handoff_message": {"type": "string"},
                "rework_reason": {"type": "string"},
                "question": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
                "result": {
                    "type": "object",
                    "properties": {"summary_for_human": {"type": "string"}},
                },
                "expected_output": {
                    "type": "object",
                    "properties": {
                        "datasets": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "additionalProperties": True,
                                "properties": {
                                    "name": {"type": "string"},
                                    "description": {"type": "string"},
                                },
                            },
                        }
                    },
                },
            },
        },
    },
}

SYSTEM = """Ты оркестратор инженерной задачи. Предметной области ты не знаешь заранее: что умеют агенты, написано в реестре («Доступные агенты» в planner_input), а политика декомпозиции — в карточках знаний (срез target_base=orchestrator_routing, knowledge_type=routing_card). Другие срезы базы знаний сюда не подмешивают.

Как читать реестр:
- agent_id — значение для action.agent_id; title — так агента называют инженеру.
- when_to_use — что агент делает и когда его звать.
- input_required — роли файлов, которые ему нужны; сравнивай с ролями приложенных файлов (inputs[].role, files) в текущем состоянии.
- output_provides — каталог возможностей: какие данные и файлы агент умеет вернуть (в журнале — «данные: …», «артефакты: …»). Это не чек-лист на каждую задачу.
- input_schema.data — данные, которые агент берёт из результатов других агентов; output_schema — что он отдаёт. Условные поля («нужны, если…») заказывай только когда цель это условие выполняет.
- hitl_policy=agent_asks — агент сам спросит инженера, если ему не хватит данных; не спрашивай за него.

Правила выбора агента:
- Вызывай только агентов из реестра и только тех, чьи input_required есть среди приложенных файлов.
- Если следующему агенту для этой цели нужны данные из input_schema.data, которых ещё нет в журнале — сначала вызови агента, у которого они в output_provides. Не заказывай ключ только потому, что он есть в каталоге.
- Если карточки знаний недоступны (status=empty или unavailable) — решай по реестру и состоянию. Не спрашивай инженера про базу знаний.

Политика из базы знаний (блок «Политика из базы знаний» в planner_input) имеет приоритет над догадкой: пункты плана и выбор действия следуют ей, если она их даёт. Если политика требует уточнения у инженера — ask_user, не выдумывай недостающие факты.

Сначала заполни progress — короткую оценку хода по журналу (compact.journal.history: итоги агентов, данные, артефакты, ответы человека). Это пояснение, не флаги для программы:
- evidence: что в журнале это подтверждает (или чего не хватает).
- missing: что ещё нужно сделать (пусто, если выбираешь finish).
Действие (call_agent / ask_user / finish) и есть решение. Повтор агента оркестратор видит по журналу сам — не пиши служебные флаги.

Затем веди план (plan_update) — чёткую декомпозицию цели инженера на результаты, которые он должен получить (1–5 пунктов), а не список вызовов и не перечень всего, что агенты умеют:
- На первом шаге составь план целиком по тексту цели; дальше присылай в plan_update только изменившиеся пункты. Текущий план показан в блоке «План задачи» planner_input.
- Один агент — один пункт на его результат по этой цели. Не ставь два пункта с одним agent_id. Заголовок пункта — то, что инженер просил получить, а не все ключи output_provides из реестра. Если цель просит только часть того, что агент умеет — в пункте только эта часть.
- Пункт: id (короткий стабильный идентификатор латиницей: p1, p2, …), title (по-русски: какой результат будет получен), agent_id (агент из реестра, который даёт этот результат — обязателен, если результат даёт агент), status (pending — не начат, active — в работе, done — результат есть в журнале, blocked — нельзя получить без инженера, dropped — оказался не нужен), depends_on (id пунктов, без которых этот не начать), note (коротко, почему blocked или dropped).
- Пункт без id отбрасывается. Не добавляй пункты «проверить результат», «завершить задачу» и работу, о которой инженер не просил.
- Статусы pending → active → done по вызовам агентов оркестратор проставляет сам. Меняй статус вручную только для blocked, dropped и для пунктов без агента.
- При call_agent укажи в action.task_id id пункта плана, который этот вызов выполняет.
- finish возможен, только когда в плане нет пунктов pending, active или blocked. Если пункт по журналу выполнен, но остался открытым — закрой его в plan_update в том же ответе, что и finish.

Затем выбери одно действие:
1. call_agent
2. ask_user
3. finish

Завершение:
- Если выбираешь finish — цель покрыта журналом. В action.result.summary_for_human напиши по-русски, что фактически сделано, опираясь на summary агентов из журнала (не на шаблон «вызвал агента»).
- summary_for_human и question читает инженер, не программа: обычные русские фразы без технических идентификаторов — ни agent_id, ни имён артефактов из журнала, ни ключей JSON. Агентов называй по title из реестра, результат — файлом («новый schedule.inc»), скважины и даты — как в summary агентов.
- Агент, который вернул completed, свою часть сделал: его результат уже в артефактах. Не вызывай его снова «для проверки» или «чтобы применить ещё раз».
- Повторный call_agent того же агента допустим только если появились новые данные (ответ человека, новые файлы), ты нашёл конкретный недостаток в его результате относительно цели (тогда обязательно заполни action.rework_reason и опиши недостаток в handoff_message) или в плане у этого агента есть другой ещё не выполненный пункт по цели и в журнале ещё нет этого результата — тогда в action.task_id укажи id этого пункта. Не считай недостатком отсутствие ключа output_provides, которого цель не требовала. Если агент completed и пункт цели закрыт — вызывай следующего, не читай исходник повторно. (Лишние пункты того же агента закроются сами, когда в данных уже есть все ключи output_provides — это защита от цикла, не требование «достать все ключи».)
- Если человек в журнале принял результат как итог задачи (review_accept) — finish. Если он принял результат одного агента и просил продолжить остальные шаги — не вызывай этого агента снова, иди дальше по плану.
- Если агент вернул needs_input (задал вопрос) и человек ответил — верни задачу этому же агенту (call_agent) и передай ответ в handoff_message. Пока этот агент не вернул completed, цель не достигнута и finish невозможен.
- finish проверяется отдельно: каждая часть цели должна быть покрыта записью журнала со статусом completed. Если в журнале есть «проверка завершения отклонила finish — не покрыто: …», цель не достигнута: закрой именно эти части через call_agent. Не повторяй finish без новых результатов агентов.

Правила:
- Если не хватает данных, которых нет ни в приложенных файлах, ни в результатах агентов и ни один агент их не даст — выбери ask_user. Не придумывай факты (даты, имена объектов, параметры) и не пиши содержимое файлов сам.
- Имена приложенных файлов — это состав пакета, а не постановка задачи: делай только то, о чём просит цель, и не дописывай в handoff_message работу, которую инженер не просил.
- Если следующий шаг очевиден, выбери call_agent.
- Не разбирай содержимое файлов сам. Retrieval уже выполнен до тебя (только срез orchestrator_routing).
- Возвращай только JSON.
- status_message пиши по-русски, коротко, в стиле текущего шага.
- handoff_message — человекопонятное обращение к агенту (по title): что сделать, на основании каких файлов и данных, что уже известно из журнала (ответы инженера, результаты других агентов).
- action.expected_output.datasets — только если цели не хватает канона агента: [{name, description}]. Имя — латинский идентификатор (то же, что в input_schema.data потребителя, либо своё для произвольного набора). Не копируй в datasets все ключи output_provides вызываемого агента и не добавляй набор «на всякий случай». Если цели хватает канона агента по when_to_use — не заполняй expected_output. Не выдумывай значения ячеек и не перечисляй колонки: агент сопоставит их с инвентарём. Consumers оркестратор допишет сам из плана.
"""

# Completion check: a second, sceptical LLM pass that runs only when the Decision LLM proposes
# `finish`. It decomposes the goal into deliverables and demands a completed journal entry for
# each one. Domain-free: it knows nothing about Excel or SCHEDULE, only goal vs. journal.
VERIFY_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["goal_parts", "verdict_for_human"],
    "properties": {
        "goal_parts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["part", "covered"],
                "properties": {
                    "part": {"type": "string"},
                    "covered": {"type": "boolean"},
                    "evidence": {"type": "string"},
                },
            },
        },
        "unsupported_claims": {"type": "array", "items": {"type": "string"}},
        "all_covered": {"type": "boolean"},
        "verdict_for_human": {"type": "string"},
    },
}

VERIFY_SYSTEM = """Ты проверяющий завершения инженерной задачи. Оркестратор предлагает завершить задачу; твоя работа — проверить это по журналу, а не поверить на слово.

Тебе даны: цель (текст инженера), журнал (что агенты фактически сделали — их итоги со статусом и добавленные артефакты, ответы инженера), план оркестратора (его собственная разбивка цели на результаты со статусами) и предложенный итог. Если есть блок «Политика завершения» — это критерии готовности из базы знаний, не факты журнала: сверяй части цели с журналом, а политику используй как правило, что считать покрытым.

1. Разбей цель инженера на части — результаты, которые он должен получить на выходе (обычно 1–3). Часть — это результат («получен обновлённый файл X»), не действие («вызвать агента»). План оркестратора — подсказка, не источник требований: пункт плана, которого нет в цели, не делай обязательной частью (оркестратор мог дописать то, что агент умеет, но инженер не просил). Добавь то, что цель требует, а план упустил. Файлы, которые инженер приложил сам (шаг 0 журнала), — исходные данные, а не результат: «получить/использовать исходный файл» не выделяй в отдельную часть.
2. Для каждой части решай, покрыта ли она записью журнала со статусом completed, чей итог по смыслу говорит, что это сделано. Промежуточный шаг не покрывает конечный результат: извлечь данные ≠ построить на их основе итог; «исходный файл есть во вложении» ≠ «получен новый файл». Но не придирайся к формулировкам: если агент отчитался о сделанных изменениях (сдвинул даты, добавил/убрал скважины, перепривязал группы) и/или в записи есть добавленные артефакты (новый файл, diff), результат получен. Если агент completed и пишет, что запрошенного набора в исходных файлах нет — это покрытие «набора не было», а не дырка цели, когда этот набор из цели не следует. evidence — пересказ записи журнала (шаг N) либо «в журнале нет».
3. unsupported_claims — утверждения предложенного итога, которых нет в журнале (например «файл собран», когда ни один агент об этом не отчитался).
4. all_covered = true только если покрыты все части — и обязательно true, если все части covered=true. Не выдумывай записей журнала и не додумывай, что «наверняка сделано».
5. verdict_for_human — одна русская фраза для инженера без идентификаторов и имён полей: что сделано и чего не хватает.

Возвращай только JSON.
"""

FORMAT_OPENAI_CHAT = r"""
const http=$json||{};
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const choice=obj((http.choices||[])[0])?(http.choices||[])[0]:{};
const msg=obj(choice.message)?choice.message:{};
const content=typeof msg.content==='string'?msg.content:'';
const usage=obj(http.usage)?http.usage:{};
const details=obj(usage.completion_tokens_details)?usage.completion_tokens_details:{};
const errObj=obj(http.error)?http.error:null;
const errText=typeof http.error==='string'?http.error:String((errObj&&(errObj.message||errObj.code))||http.message||'');
const llm_parse={
  finish_reason:String(choice.finish_reason||''),
  content_len:String(content||'').length,
  completion_tokens:Number(usage.completion_tokens||0),
  prompt_tokens:Number(usage.prompt_tokens||0),
  reasoning_tokens:Number(details.reasoning_tokens||0)
};
let error='';
let output={};
if(errText||(!http.choices&&http.statusCode)){
  error=errText.slice(0,400)||'decision_chat_failed';
} else if(!String(content||'').trim()){
  error=llm_parse.finish_reason==='length'?'llm_truncated_empty':'llm_empty_content';
} else {
  try{
    const p=JSON.parse(String(content));
    if(obj(p)) output=p; else error='llm_json_not_object';
  }catch(e){
    error='llm_json_invalid';
    llm_parse.sample=String(content).slice(0,240);
  }
}
if(error) llm_parse.error=error;
return [{json:{output,error,llm_parse}}];
"""

BUILD_DECISION_CHAT = (
    "const SYSTEM = "
    + json.dumps(SYSTEM, ensure_ascii=False)
    + ";\nconst DECISION_SCHEMA = "
    + json.dumps(DECISION_SCHEMA, ensure_ascii=False)
    + ";\nconst DEFAULT_CHAT_MODEL = "
    + json.dumps(DEFAULT_CHAT_MODEL)
    + ";\nconst THINKING_OFF = "
    + json.dumps(CHAT_THINKING_OFF)
    + ";\nconst SAMPLING = "
    + json.dumps(SAMPLING["decision"])
    + ";\n"
    + PARSE_CHAT_EXTRA_JS
    + """
const prev=$json||{};
let cfg={};
try{cfg=$('Runtime endpoints').first().json||{}}catch(e){cfg={}}
const model=String(cfg.chat_model||'').trim()||DEFAULT_CHAT_MODEL;
const base=String(cfg.chat_base_url||'').replace(/[/]+$/,'');
const chat_url=base?base+'/chat/completions':'';
const extra=parseChatExtra(cfg);
const chat_request={
  model,
  response_format:{type:'json_object'},
  ...SAMPLING,
  ...THINKING_OFF,
  messages:[
    {role:'system',content:SYSTEM},
    {role:'user',content:String(prev.planner_input||'')}
  ],
  ...extra
};
return [{json:{...prev,chat_request,chat_url,decision_schema:DECISION_SCHEMA}}];
"""
)

BUILD_VERIFY_CHAT = (
    "const VERIFY_SYSTEM = "
    + json.dumps(VERIFY_SYSTEM, ensure_ascii=False)
    + ";\nconst VERIFY_SCHEMA = "
    + json.dumps(VERIFY_SCHEMA, ensure_ascii=False)
    + ";\nconst DEFAULT_CHAT_MODEL = "
    + json.dumps(DEFAULT_CHAT_MODEL)
    + ";\nconst THINKING_OFF = "
    + json.dumps(CHAT_THINKING_OFF)
    + ";\nconst SAMPLING = "
    + json.dumps(SAMPLING["decision"])
    + ";\n"
    + PARSE_CHAT_EXTRA_JS
    + """
const prev=$json||{};
let cfg={};
try{cfg=$('Runtime endpoints').first().json||{}}catch(e){cfg={}}
const model=String(cfg.chat_model||'').trim()||DEFAULT_CHAT_MODEL;
const base=String(cfg.chat_base_url||'').replace(/[/]+$/,'');
const chat_url=base?base+'/chat/completions':'';
const extra=parseChatExtra(cfg);
const chat_request={
  model,
  response_format:{type:'json_object'},
  ...SAMPLING,
  ...THINKING_OFF,
  messages:[
    {role:'system',content:VERIFY_SYSTEM},
    {role:'user',content:String(prev.verify_input||'')}
  ],
  ...extra
};
return [{json:{...prev,chat_request,chat_url,verify_schema:VERIFY_SCHEMA}}];
"""
)

PREPARE_VERIFY = (
    "const PLANNER_HEADING_AGENT_DATA = "
    + json.dumps("Данные агентов:")
    + ";\nconst PLANNER_HEADING_COMPLETION = "
    + json.dumps("Политика завершения:")
    + ";\n"
    + r"""
const ctx=$('Prepare decision context').first().json||{};
const llm=$('Decision LLM').first().json||{};
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const parse=v=>{if(obj(v))return v;try{const p=JSON.parse(String(v||''));return obj(p)?p:{}}catch{return {}}};
const nodeJson=(name)=>{try{const n=$(name).first().json;return obj(n)?n:{}}catch{return {}}};
const attach=nodeJson('Attach orchestrator RAG evidence');
const decision=parse(llm.output||llm.text||llm);
const action=obj(decision.action)?decision.action:{};
const proposed=String((obj(action.result)&&action.result.summary_for_human)||'').trim();
const progress=obj(decision.progress)?decision.progress:{};
const planner=String(attach.planner_input||ctx.planner_input||'');
const marker='\n'+PLANNER_HEADING_AGENT_DATA;
const cut=planner.indexOf(marker);
const goalAndJournal=cut>0?planner.slice(0,cut):planner;
const rag=obj(attach.rag)?attach.rag:(obj(ctx.rag)?ctx.rag:{});
const completionCards=(Array.isArray(rag.cards)?rag.cards:[]).filter(c=>{
  const topics=Array.isArray(c&&c.topics)?c.topics:[];
  return topics.some(t=>String(t||'').toLowerCase()==='completion');
});
const policy=completionCards.length
  ? `\n\n${PLANNER_HEADING_COMPLETION}\n`+completionCards.map(c=>`- ${String((c&&c.title)||(c&&c.knowledge_id)||'').trim()}: ${String((c&&c.text)||'').trim()}`).join('\n')
  : '';
const verify_input=`${goalAndJournal}${policy}\n\nПредложенный итог оркестратора:\n${proposed||'(итог не написан)'}\n\nОбоснование оркестратора:\n${String(progress.evidence||'').slice(0,600)||'(нет)'}\n`;
return [{json:{...ctx, rag, verify_input, proposed_summary:proposed}}];
"""
)

INTERPRET_SCHEMA = {
    "type": "object",
    "additionalProperties": True,
    "required": ["decision", "confidence", "paraphrase"],
    "properties": {
        "decision": {"type": "string"},
        "confidence": {"type": "number"},
        "paraphrase": {"type": "string"},
    },
}

INTERPRET_SYSTEM = """Ты понимаешь свободный ответ инженера на вопрос, у которого уже есть варианты.

Тебе даны: текст вопроса, варианты (подпись и токен в скобках) и его слова.

Верни JSON:
- decision — токен в скобках у выбранного варианта, не подпись кнопки. Не придумывай токен, которого нет в списке.
- confidence — число от 0 до 1. Если ответ двусмысленный, двусмысленно-вежливый или не про этот вопрос — ниже 0.8.
- paraphrase — одна русская фраза, что инженер имел в виду, без идентификаторов и без JSON.

Если не уверен — decision оставь пустым или unclear, confidence ниже 0.8. Не угадывай деструктив (убрать, удалить), если инженер этого явно не сказал.

Возвращай только JSON.
"""

BUILD_INTERPRET_CHAT = (
    "const INTERPRET_SYSTEM = "
    + json.dumps(INTERPRET_SYSTEM, ensure_ascii=False)
    + ";\nconst INTERPRET_SCHEMA = "
    + json.dumps(INTERPRET_SCHEMA, ensure_ascii=False)
    + ";\nconst DEFAULT_CHAT_MODEL = "
    + json.dumps(DEFAULT_CHAT_MODEL)
    + ";\nconst THINKING_OFF = "
    + json.dumps(CHAT_THINKING_OFF)
    + ";\nconst SAMPLING = "
    + json.dumps(SAMPLING["decision"])
    + ";\n"
    + PARSE_CHAT_EXTRA_JS
    + """
const prev=$json||{};
let cfg={};
try{cfg=$('Runtime endpoints').first().json||{}}catch(e){cfg={}}
const model=String(cfg.chat_model||'').trim()||DEFAULT_CHAT_MODEL;
const base=String(cfg.chat_base_url||'').replace(/[/]+$/,'');
const chat_url=base?base+'/chat/completions':'';
const extra=parseChatExtra(cfg);
const chat_request={
  model,
  response_format:{type:'json_object'},
  ...SAMPLING,
  ...THINKING_OFF,
  messages:[
    {role:'system',content:INTERPRET_SYSTEM},
    {role:'user',content:String(prev.interpret_input||'')}
  ],
  ...extra
};
return [{json:{...prev,chat_request,chat_url,interpret_schema:INTERPRET_SCHEMA}}];
"""
)

APPLY_INTERPRETED = STATE_SHAPE_JS + r"""
const prev=$('Apply request extras').first().json||{};
const llm=$('Interpret free-text answer').first().json||{};
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const parse=v=>{if(obj(v))return v;try{const p=JSON.parse(String(v||''));return obj(p)?p:{}}catch{return {}}};
const decision=parse(llm.output||llm.text||llm);
let state=sanitizeState(prev.state);
const qid=String(prev.interpret_qid||'Q-1');
const q0=obj(prev.interpret_question)?prev.interpret_question:{};
const raw=prev.interpret_raw;
const applied=applyInterpretedDecision(qid,q0,raw,decision);
const interpretTrace=()=>{
  const parseLlm=obj(llm.llm_parse)?llm.llm_parse:{};
  let chat={};
  try{chat=$('Build interpret chat').first().json||{}}catch{chat={}}
  const msgs=obj(chat.chat_request)&&Array.isArray(chat.chat_request.messages)?chat.chat_request.messages:[];
  const tokens=Number(parseLlm.prompt_tokens||0)+Number(parseLlm.completion_tokens||0);
  const finish=String(parseLlm.finish_reason||parseLlm.error||'');
  let content='';
  try{content=typeof llm.output==='string'?llm.output:JSON.stringify(llm.output||llm.text||'');}catch{content=String(llm.output||llm.text||'');}
  return [prev.case_id,'','trace.llm','orchestrator','','running',`interpret: ${finish||'stop'} · ${tokens} tok`,'',JSON.stringify({
    role:'interpret',
    model:String((obj(chat.chat_request)&&chat.chat_request.model)||''),
    prompt_tokens:Number(parseLlm.prompt_tokens||0),
    completion_tokens:Number(parseLlm.completion_tokens||0),
    reasoning_tokens:Number(parseLlm.reasoning_tokens||0),
    finish_reason:finish,
    prompt_preview:previewChatMessages(msgs),
    content_preview:String(content||'').slice(0,2000),
    step_count:Number(state.step_count||0),
    ...execRef()
  })];
};
const hitl=state.hitl&&typeof state.hitl==='object'?{...state.hitl}:{pending:false,questions:[],answers:{}};
const questions=Array.isArray(hitl.questions)?hitl.questions:[];
const answers={...(hitl.answers&&typeof hitl.answers==='object'?hitl.answers:{})};
if(!applied.accepted){
  const q=buildClarifyQuestion(q0);
  state.hitl={pending:true,questions:[q],answers};
  state.status='waiting_user';
  state.version=Number(state.version||0)+1;
  const persistEvents=[interpretTrace(), [prev.case_id,'','hitl.request','orchestrator','','waiting_user',q.question,'',JSON.stringify({...q,...execRef()})]];
  return [{json:{
    ...prev,
    state,
    did_resume:true,
    needs_interpret:false,
    next_status:'waiting_user',
    should_continue:false,
    update_sql_parameters:[JSON.stringify(state),'waiting_user',prev.case_id],
    persist_events:persistEvents,
    event_sql_parameters:persistEvents[0],
    human_gate:{gate_id:q.question_id,kind:q.kind,reason:q.question,expected_version:state.version,questions:[q]}
  }}];
}
answers[qid]=applied.answer;
state.hitl={pending:false,questions,answers};
state.status='running';
state.version=Number(state.version||0)+1;
const reviewAccept=isResultReviewGate(qid)&&humanAnswerChoice(applied.answer)==='accept';
ledgerPush(state,{kind:'human',step:Number(state.step_count||0),question_id:qid,question:String(q0.question||'').slice(0,200),answer:humanAnswerText(applied.answer).slice(0,300),...reviewAcceptFields(reviewAccept, q0)});
state.ledger.last_human_step=Number(state.step_count||0);
state.ledger.stall_count=0;
const said=humanAnswerText(applied.answer);
const persistEvents=[interpretTrace(), [prev.case_id,'','hitl.answered','user','','answered',said?('Пользователь ответил: '+said):'Пользователь ответил','',JSON.stringify({question_id:qid,answer:applied.answer,...execRef()})]];
return [{json:{
  ...prev,
  state,
  did_resume:true,
  needs_interpret:false,
  next_status:'running',
  update_sql_parameters:[JSON.stringify(state),'running',prev.case_id],
  persist_events:persistEvents,
  event_sql_parameters:persistEvents[0]
}}];
"""


def nid(name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"mas-orch:{name}"))


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


def note(name, pos, content, w=440, h=280, color=5):
    return node(name, "n8n-nodes-base.stickyNote", 1, pos, {"content": content, "width": w, "height": h, "color": color})


def code(name, pos, js, **extra):
    return node(name, "n8n-nodes-base.code", 2, pos, {"jsCode": js}, **extra)


def set_fields(name, pos, fields):
    assignments = [
        {
            "id": nid(f"{name}/field/{i}"),
            "name": field,
            "value": value,
            "type": type_,
        }
        for i, (field, value, type_) in enumerate(fields, 1)
    ]
    return node(
        name,
        "n8n-nodes-base.set",
        3.4,
        pos,
        {"assignments": {"assignments": assignments}, "options": {}, "includeOtherFields": True},
    )


def postgres(name, pos, query, params_expr, batching="single"):
    return node(
        name,
        "n8n-nodes-base.postgres",
        2.6,
        pos,
        {
            "operation": "executeQuery",
            "query": query,
            "options": {
                "queryReplacement": params_expr,
                "queryBatching": batching,
                "largeNumbersOutput": "text",
                "replaceEmptyStrings": False,
            },
        },
        credentials=PG,
        alwaysOutputData=True,
    )


def http_json(name, pos, url, body, timeout=180000):
    return node(
        name,
        "n8n-nodes-base.httpRequest",
        4.4,
        pos,
        {
            "method": "POST",
            "url": url,
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [
                    {"name": "Content-Type", "value": "application/json"},
                ]
            },
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": body,
            "options": {"timeout": timeout, "response": {"response": {"fullResponse": False}}},
        },
        onError="continueRegularOutput",
    )


def openai_chat_http(name, pos):
    """OpenAI-compatible /chat/completions using the same Qwen credential as Chat Model.

    Body is built in the previous Code node (model, messages, CHAT_THINKING_OFF).
    n8n 2.30.8 lmChatOpenAi cannot send reasoning.enabled=false; this node can.
    """
    return node(
        name,
        "n8n-nodes-base.httpRequest",
        4.4,
        pos,
        {
            "method": "POST",
            "url": "={{ $json.chat_url }}",
            "authentication": "predefinedCredentialType",
            "nodeCredentialType": "openAiApi",
            "sendHeaders": True,
            "headerParameters": {
                "parameters": [
                    {"name": "Content-Type", "value": "application/json"},
                ]
            },
            "sendBody": True,
            "specifyBody": "json",
            "jsonBody": "={{ $json.chat_request }}",
            "options": {
                "timeout": LLM_HTTP_TIMEOUT_MS,
                "response": {"response": {"fullResponse": False, "neverError": True}},
            },
        },
        credentials=OA,
        retryOnFail=True,
        maxTries=3,
        waitBetweenTries=2000,
        onError="continueRegularOutput",
        alwaysOutputData=True,
    )


def execute_workflow_by_expression(name, pos, workflow_id_expr, inputs):
    """executeWorkflow whose target is decided at runtime (Phase 2: id comes from ``agent_registry.invoke``).

    Verified on n8n 2.30.8: ``workflowId`` accepts ``{"__rl": True, "mode": "id", "value": "={{ … }}"}``;
    an unknown id fails the node cleanly (caught by ``onError=continueRegularOutput`` → Merge records a
    failed agent result). No ``cachedResultName`` — nothing to bind in the UI.
    """
    return node(
        name,
        "n8n-nodes-base.executeWorkflow",
        1.3,
        pos,
        {
            "source": "database",
            "workflowId": {"__rl": True, "value": workflow_id_expr, "mode": "id"},
            "workflowInputs": {
                "mappingMode": "defineBelow",
                "value": inputs,
                "matchingColumns": [],
                "schema": [],
                "attemptToConvertTypes": False,
                "convertFieldsToString": False,
            },
            "mode": "once",
            "options": {"waitForSubWorkflow": True},
        },
        onError="continueRegularOutput",
    )


def if_true(name, pos, left):
    return node(
        name,
        "n8n-nodes-base.if",
        2.3,
        pos,
        {
            "conditions": {
                "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
                "conditions": [
                    {
                        "id": nid(f"{name}-cond"),
                        "leftValue": left,
                        "rightValue": True,
                        "operator": {"type": "boolean", "operation": "true"},
                    }
                ],
                "combinator": "and",
            },
            "options": {},
        },
    )


def connect(c, src, dst, out="main", si=0, tin="main", ti=0):
    groups = c.setdefault(src, {})
    outputs = groups.setdefault(out, [])
    while len(outputs) <= si:
        outputs.append([])
    outputs[si].append({"node": dst, "type": tin, "index": ti})


NORMALIZE = r"""
const cfg=(()=>{try{return $('Runtime endpoints').first().json||{}}catch{return {}}})();
const raw=(()=>{try{return $('Authenticated MAS webhook').first().json||{}}catch{return $input.first().json||{}}})();
const body=raw.body&&typeof raw.body==='object'?raw.body:raw;
const action=String(body.action||raw.action||'step').trim().toLowerCase();
let caseId=String(body.case_id||raw.case_id||'').trim();
const goal=String(body.task_description||body.goal||raw.task_description||raw.goal||'').trim();
const taskName=String(body.task_name||raw.task_name||'').trim();
const hrRaw=body.human_response!=null?body.human_response:(raw.human_response!=null?raw.human_response:'');
const humanResponse=typeof hrRaw==='string'?hrRaw:(hrRaw==null?'':JSON.stringify(hrRaw));
const gateId=String(body.gate_id||raw.gate_id||'');
const expectedVersion=body.expected_version!=null?Number(body.expected_version):(raw.expected_version!=null?Number(raw.expected_version):null);
const requestedBy=String(body.requested_by||raw.requested_by||'');
const resumeSource=String(body.source||raw.source||(action==='resume'?'human':'')).trim().toLowerCase();
const resumeTaskId=String(body.task_id||raw.task_id||'');
const resumeAgentId=String(body.agent_id||raw.agent_id||'');
const activityBaseUrl=String(cfg.activity_base_url||raw.activity_base_url||'').trim();
const executionId=String($execution.id||'');
const readinessId=!caseId||caseId==='CASE-readiness-probe';
if(action==='probe'||(action==='status'&&readinessId)){
  return [{json:{
    case_id:caseId||'CASE-readiness-probe',
    action:'probe',
    is_probe:true,
    is_status:false,
    is_resume:false,
    needs_create:false,
    next_status:'probe',
    status:'probe',
    action_type:'probe',
    should_continue:false
  }}];
}
let needsCreate=false;
if(action==='create'){
  if(!caseId) caseId='CASE-'+Date.now().toString(16)+'-'+Math.random().toString(16).slice(2,8);
  if(!goal) throw new Error('task_description is required for action=create');
  needsCreate=true;
} else if(!caseId){
  if(action!=='start') throw new Error('case_id is required');
  if(!goal) throw new Error('task_description is required for action=start');
  caseId='CASE-'+Date.now().toString(16)+'-'+Math.random().toString(16).slice(2,8);
  needsCreate=true;
}
return [{json:{
  case_id:caseId,
  action,
  goal,
  task_name:taskName,
  human_response:humanResponse,
  gate_id:gateId,
  expected_version:Number.isFinite(expectedVersion)?expectedVersion:null,
  requested_by:requestedBy,
  source:resumeSource,
  task_id:resumeTaskId,
  agent_id:resumeAgentId,
  activity_base_url:activityBaseUrl,
  is_probe:false,
  is_status:action==='status',
  is_resume:action==='resume',
  needs_create:needsCreate,
  execution_id:executionId,
  workflow_name:'orchestrator',
  sql_parameters:[caseId],
  exec_sql_parameters:[executionId, caseId, 'orchestrator'],
}}];
"""

CREATE_CASE = r"""
const req=$json||{};
const goal=String(req.goal||'').trim();
const state={
  case_id:req.case_id,
  goal,
  task_name:req.task_name||'',
  status:'running',
  plan:[],
  artifacts:{},
  data:{},
  current_task:null,
  hitl:{pending:false,questions:[],answers:{}},
  last_error:null,
  error_count:0,
  step_count:0,
  version:1
};
/* `case.created` is written by Activity (`POST /cases` initial_event) before it calls action=create;
   the orchestrator must not add a second one (CASE-6a9ee318: two «Принял задачу» rows). */
return [{json:{
  ...req,
  state,
  create_sql_parameters:[req.case_id, JSON.stringify(state), 'running'],
  persist_events:[],
  event_sql_parameters:[]
}}];
"""

APPLY_EXTRAS = STATE_SHAPE_JS + r"""
const req=$('Normalize step request').first().json||{};
const load=$json||{};
const parse=v=>{if(v&&typeof v==='object'&&!Array.isArray(v))return v;try{const p=JSON.parse(String(v||'{}'));return p&&typeof p==='object'&&!Array.isArray(p)?p:{}}catch{return {}}};
let state=parse(load.state||load.State||{});
if(req.is_status===true){
  const hitl=parse(state.hitl);
  const questions=Array.isArray(hitl.questions)?hitl.questions:[];
  const status=String(load.status||state.status||'');
  const version=Number(state.version||state.step_count||0);
  let human_gate=null;
  if(status==='waiting_user'&&(hitl.pending===true||questions.length)){
    const q0=questions[0]&&typeof questions[0]==='object'?questions[0]:{};
    human_gate={
      gate_id:q0.question_id||'hitl',
      kind:String(q0.kind||'needs_input'),
      reason:q0.question||state.goal||'Нужен ответ',
      expected_version:version,
      questions
    };
  }
  return [{json:{
    ...req,
    case_id:req.case_id,
    is_status:true,
    did_resume:false,
    next_status:status,
    status,
    action_type:'status',
    should_continue:false,
    human_gate,
    version,
    restartable:status==='done'||status==='failed',
    deliverables:deliverables(state.artifacts)
  }}];
}
if(req.is_resume===true){
  state=sanitizeState(state);
  const expected=Number(req.expected_version);
  const current=Number(state.version||0);
  if(Number.isFinite(expected)&&expected>0&&current>0&&expected!==current){
    const hitl=state.hitl&&typeof state.hitl==='object'?state.hitl:{};
    const questions=Array.isArray(hitl.questions)?hitl.questions:[];
    return [{json:{
      ...req,
      state,
      did_resume:false,
      is_status:false,
      next_status:String(state.status||'waiting_user'),
      status:String(state.status||'waiting_user'),
      action_type:'version_mismatch',
      should_continue:false,
      message:`Version mismatch: expected ${expected}, got ${current}. Reload status.`,
      version:current,
      human_gate:questions.length?{
        gate_id:(questions[0]&&questions[0].question_id)||'hitl',
        kind:String((questions[0]&&questions[0].kind)||'needs_input'),
        reason:questions[0]&&questions[0].question||state.goal||'Нужен ответ',
        expected_version:current,
        questions
      }:null
    }}];
  }
  const source=String(req.source||'human').toLowerCase();
  if(source==='agent'||source==='system'){
    const taskId=String(req.task_id||'');
    const slot=state.agents&&state.agents[String(req.agent_id||'')];
    const waiting=state.current_task&&typeof state.current_task==='object'?state.current_task:{};
    const agentId=String(req.agent_id||waiting.agent_id||'');
    const payload=parseJsonish(String(req.human_response||'').trim(),null);
    const events=[];
    let nextStatus='running';
    if(source==='agent'&&payload&&typeof payload.status==='string'){
      /* A long agent finished (or asked / failed) after returning in_progress: same merge as a synchronous result. */
      const applied=applyAgentResult(state, agentId, taskId||String(waiting.task_id||(slot&&slot.task_id)||''), payload, Array.isArray(req.registry)?req.registry:[]);
      nextStatus=applied.next_status;
      events.push(...applied.events);
    } else {
      const summary=humanAnswerText(decodeHitlAnswer(String(req.human_response||'').trim()))||'событие';
      ledgerPush(state,{kind:'external',source,task_id:taskId,agent_id:agentId,step:Number(state.step_count||0),summary:String(summary).slice(0,300)});
    }
    state.status=nextStatus;
    state.version=Number(state.version||0)+1;
    state.ledger.last_human_step=Number(state.step_count||0);
    state.ledger.stall_count=0;
    const persistEvents=[[req.case_id,taskId,'orchestrator.resume',source,agentId,nextStatus,'Продолжение по событию агента или системы','',JSON.stringify({source,task_id:taskId,...execRef()})],
      ...events.map(e=>[req.case_id,e.task_id||'',e.kind,e.actor,e.agent_id||'',e.status||'',e.status_message||'',e.handoff_message||'',JSON.stringify(e.payload||{})])];
    return [{json:{
      ...req,
      state,
      did_resume:true,
      needs_interpret:false,
      is_status:false,
      next_status:nextStatus,
      update_sql_parameters:[JSON.stringify(state),nextStatus,req.case_id],
      persist_events:persistEvents,
      event_sql_parameters:persistEvents[0]
    }}];
  }
  const answer=decodeHitlAnswer(String(req.human_response||'').trim());
  const hitl=state.hitl&&typeof state.hitl==='object'?state.hitl:{pending:false,questions:[],answers:{}};
  const questions=Array.isArray(hitl.questions)?hitl.questions:[];
  const qid=String(req.gate_id||(questions[0]&&questions[0].question_id)||'Q-1');
  const q0=questions.find(q=>q&&String(q.question_id||'')===qid)||questions[0]||{};
  const stored=normalizeHitlAnswer(qid, answer, q0.question);
  const choice=humanAnswerChoice(stored);
  const opts=optionValues(q0);
  const needsInterpret=!choice&&opts.length>0&&Boolean(humanAnswerText(stored).trim());
  if(needsInterpret){
    const optionLines=(Array.isArray(q0.options)?q0.options:[]).map(o=>{
      if(o&&typeof o==='object'&&!Array.isArray(o)){
        const label=String(o.label||o.value||'').trim();
        const value=String(o.value||'').trim();
        return value&&value!==label?`- ${label} (${value})`:`- ${label||value}`;
      }
      return `- ${o}`;
    }).join('\n');
    const interpret_input=`Вопрос инженеру:\n${String(q0.question||'')}\n\nВарианты:\n${optionLines}\n\nОтвет инженера:\n${humanAnswerText(stored)}\n`;
    return [{json:{
      ...req,
      state,
      did_resume:true,
      needs_interpret:true,
      is_status:false,
      next_status:String(state.status||'waiting_user'),
      interpret_qid:qid,
      interpret_raw:stored,
      interpret_question:q0,
      interpret_input,
      should_continue:false
    }}];
  }
  const answers={...(hitl.answers&&typeof hitl.answers==='object'?hitl.answers:{})};
  answers[qid]=stored;
  state.hitl={pending:false,questions,answers};
  state.status='running';
  state.version=Number(state.version||0)+1;
  const reviewAccept=isResultReviewGate(qid)&&humanAnswerChoice(stored)==='accept';
  ledgerPush(state,{kind:'human',step:Number(state.step_count||0),question_id:qid,question:String(q0.question||'').slice(0,200),answer:humanAnswerText(stored).slice(0,300),...reviewAcceptFields(reviewAccept, q0)});
  state.ledger.last_human_step=Number(state.step_count||0);
  state.ledger.stall_count=0;
  const said=humanAnswerText(stored);
  const persistEvents=[[req.case_id,'','hitl.answered','user','','answered',said?('Пользователь ответил: '+said):'Пользователь ответил','',JSON.stringify({question_id:qid,answer:stored,...execRef()})]];
  return [{json:{
    ...req,
    state,
    did_resume:true,
    needs_interpret:false,
    is_status:false,
    next_status:'running',
    update_sql_parameters:[JSON.stringify(state),'running',req.case_id],
    persist_events:persistEvents,
    event_sql_parameters:persistEvents[0]
  }}];
}
return [{json:{...req,...load,state,did_resume:false,is_status:false}}];
"""

VALIDATE_CASE = r"""
const req=$('Normalize step request').first().json||{};
const row=$json&&typeof $json==='object'?$json:{};
const requested=String(req.case_id||'').trim();
const loaded=String(row.case_id||row.Case_id||'').trim();
if(!requested||loaded!==requested){
  return [{json:{
    ...req,
    status:'not_found',
    next_status:'not_found',
    action_type:'not_found',
    should_continue:false,
    case_loaded:false,
    message:`Кейс ${requested||'(без case_id)'} не найден в control plane`
  }}];
}
return [{json:{...req,...row,case_loaded:true}}];
"""

_ORCH_SEL = SELECTORS["orchestrator"]
PREPARE_DECISION = STATE_SHAPE_JS + (
    "const ORCH_RAG_TARGET_BASE=" + json.dumps(_ORCH_SEL["target_base"]) + ";\n"
    "const ORCH_RAG_ACCESS_SCOPE=" + json.dumps(_ORCH_SEL["access_scope"]) + ";\n"
    "const ORCH_RAG_KNOWLEDGE_TYPES=" + json.dumps(_ORCH_SEL["knowledge_types"]) + ";\n"
    "const ORCH_RAG_TOP_K=" + str(int(_ORCH_SEL["top_k"])) + ";\n"
    "const PLANNER_COLUMNS=" + json.dumps(list(PLANNER_COLUMNS)) + ";\n"
    "const PLANNER_HEADING_GOAL=" + json.dumps("Цель:") + ";\n"
    "const PLANNER_HEADING_PLAN=" + json.dumps("План задачи (твоя декомпозиция; статусы pending, active, done, blocked, dropped):") + ";\n"
    "const PLANNER_HEADING_JOURNAL=" + json.dumps("Журнал задачи (что уже сделано, по шагам):") + ";\n"
    "const PLANNER_HEADING_AGENT_DATA=" + json.dumps("Данные агентов:") + ";\n"
    "const PLANNER_HEADING_OPEN_QUESTIONS=" + json.dumps("Открытые вопросы:") + ";\n"
    "const PLANNER_HEADING_REGISTRY=" + json.dumps("Доступные агенты (реестр):") + ";\n"
    + r"""
const req=$('Normalize step request').first().json||{};
const interpreted=(()=>{try{return $('Apply interpreted answer').first().json}catch{return null}})();
const extras=(()=>{try{return $('Apply request extras').first().json}catch{return null}})();
const load=$('Load case').first().json||{};
const parse=v=>{if(v&&typeof v==='object')return v;try{return JSON.parse(String(v||'{}'))}catch{return {}}};
const fromResume=(interpreted&&interpreted.state)?interpreted:extras;
const rawState=(fromResume&&fromResume.state&&typeof fromResume.state==='object')?fromResume.state:parse(load.state||load.State||{});
const state=sanitizeState(rawState);
const status=String((fromResume&&fromResume.next_status)||load.status||state.status||'running');
/* Phase 2: the registry is the only source of "who can do what". Rows come from Postgres (enabled=true);
   the Decision LLM sees PLANNER_COLUMNS only — `invoke` is a deployment detail resolved in Prepare agent call. */
const registry=$('Load agent registry').all().map(i=>i.json||{}).filter(r=>r&&r.agent_id);
if(!registry.length) throw new Error('agent_registry has no enabled agents — seed it via Control Plane Proxy (upsert_agent) before running cases');
const plannerRegistry=registry.map(r=>{
  const o={};
  for(const c of PLANNER_COLUMNS){ if(r[c]!==undefined&&r[c]!==null) o[c]=parseJsonish(r[c],r[c]); }
  return o;
});
const compact=buildCompact(state);
/* No rule-based routing hint here: the Decision LLM reasons from goal + journal + state + registry + RAG policy cards. */
const journalLines=(compact.journal&&Array.isArray(compact.journal.history)?compact.journal.history:[]).map(e=>{
  if(e.kind==='human') return `- шаг ${e.step}: человек ответил${e.review_accept?(e.review_scope==='agent'?' (принял результат этого агента, остальные шаги плана продолжать)':' (принял результат как итог задачи)'):''}: «${e.answer}»${e.question?` — на вопрос «${e.question}»`:''}`;
  if(e.kind==='verification') return `- шаг ${e.step}: проверка завершения отклонила finish — не покрыто: ${(e.uncovered||[]).join('; ')||'(не указано)'}`;
  const data=(e.data_keys||[]).length?`; данные: ${e.data_keys.join(', ')}`:'';
  const arts=(e.artifacts_added||[]).length?`; артефакты: ${e.artifacts_added.join(', ')}`:'';
  return `- шаг ${e.step}: ${e.agent_id} → ${e.status}: ${e.summary||'(без описания)'}${data}${arts}${e.rework_reason?` (доработка: ${e.rework_reason})`:''}`;
});
/* Step 0 of the journal is what the engineer handed in (artifact cards with kind=input). Without it the completion
   check has no evidence that the source files exist and may invent an uncoverable "get the source file" part
   (CASE-6a9ec5ef-905bb0). Filenames only — no roles, no domain words. */
const inputCards=artifactCards(state.artifacts||{}).filter(c=>c.kind==='input');
const inputNames=inputCards.map(c=>String(c.filename||c.artifact_id||'').trim()).filter(Boolean);
const inputsLine=inputNames.length?`- шаг 0: инженер приложил ${inputNames.length} файл(ов): ${inputNames.slice(0,6).join(', ')}${inputNames.length>6?` и ещё ${inputNames.length-6}`:''}`:'';
const journalText=[inputsLine,...journalLines].filter(Boolean).join('\n')||'- пока ничего не сделано';
/* O13/O29: Goal → Plan → Journal → Agent data → Open questions → Registry; Attach appends Policy.
   Prepare verify cuts planner_input before «Данные агентов». */
const planText=planLines(state.plan).join('\n')||'- план ещё не составлен — запиши пункты с идентификатором, заголовком и статусом';
const agentData=JSON.stringify((compact.agents&&typeof compact.agents==='object')?compact.agents:{},null,2);
const openQ=(compact.hitl_pending&&compact.hitl_question)?`- ${String(compact.hitl_question).trim()}`:'- нет';
const prompt=`${PLANNER_HEADING_GOAL}\n${compact.goal}\n\n${PLANNER_HEADING_PLAN}\n${planText}\n\n${PLANNER_HEADING_JOURNAL}\n${journalText}\n\n${PLANNER_HEADING_AGENT_DATA}\n${agentData}\n\n${PLANNER_HEADING_OPEN_QUESTIONS}\n${openQ}\n\n${PLANNER_HEADING_REGISTRY}\n${JSON.stringify(plannerRegistry,null,2)}\n`;
const retrieval_selector={target_base:ORCH_RAG_TARGET_BASE,knowledge_types:ORCH_RAG_KNOWLEDGE_TYPES};
/* Plain-text query: goal + plan titles + open HITL (no files, no journal). Tag branch: registry
   roles of open plan agents, or at step 0 the file roles (`input_required`) of covered agents. */
const schedule_retrieval_request={
  query:buildRetrievalQuery(compact),
  filters:{
    target_base:ORCH_RAG_TARGET_BASE,
    access_scope:ORCH_RAG_ACCESS_SCOPE,
    knowledge_types:ORCH_RAG_KNOWLEDGE_TYPES,
    topics:planRetrievalTopics(state.plan, registry, compact)
  },
  top_k:ORCH_RAG_TOP_K
};
const endpoints=$('Runtime endpoints').first().json||{};
return [{json:{
  ...req,
  ...endpoints,
  activity_base_url:String(endpoints.activity_base_url||req.activity_base_url||'http://mas-activity:8200').replace(/\/$/,''),
  orchestrator_step_url:String(endpoints.orchestrator_step_url||req.orchestrator_step_url||'http://127.0.0.1:5678/webhook/mas-orchestrator-step').replace(/\/$/,''),
  case_id:req.case_id,
  state,
  status,
  registry,
  compact,
  planner_input:prompt,
  schedule_retrieval_request,
  retrieval_selector,
  step_count:compact.step_count
}}];
"""
)

PARSE_DECISION = STATE_SHAPE_JS + r"""
const prev=$('Prepare decision context').first().json||{};
/* Input arrives either straight from Decision LLM or via the completion check (Verify completion). */
const raw=(()=>{try{return $('Decision LLM').first().json||$json}catch{return $json}})();
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const parse=v=>{if(obj(v))return v;try{const p=JSON.parse(String(v||''));return obj(p)?p:{}}catch{return {}}};
/* Developer log: n8n Structured Output onError leaves {error:"Model output doesn't fit required format"}
   with no engineer-facing text (CASE-6aa50b14-bc43c5). Name the parser/timeout, do not guess a route. */
const describeLlmRaw=(node)=>{
  const o=obj(node)?node:{};
  const prior=obj(o.llm_parse)?o.llm_parse:{};
  const err=o.error;
  const errText=typeof err==='string'?err:(obj(err)?String(err.message||err.description||err.name||''):'');
  const ctx=obj(err)&&obj(err.context)?err.context:(obj(o.context)?o.context:{});
  return {
    ...prior,
    error:(errText||prior.error||'').slice(0,400),
    output_parser:String(ctx.outputParserFailReason||o.outputParserFailReason||prior.output_parser||'').slice(0,240),
    keys:Object.keys(o).slice(0,16),
    sample:prior.sample||boundedForLog({output:o.output,text:o.text,error:errText||null},1200)
  };
};
const llmParse=describeLlmRaw(raw);
const nodeJson=(name)=>{try{const n=$(name).first().json;return obj(n)?n:{}}catch{return {}}};
const attach=nodeJson('Attach orchestrator RAG evidence');
const ragSrc=obj(attach.rag)?attach.rag:(obj(prev.rag)?prev.rag:{});
const req=obj(attach.schedule_retrieval_request)?attach.schedule_retrieval_request:(obj(prev.schedule_retrieval_request)?prev.schedule_retrieval_request:{});
const ragCards=(Array.isArray(ragSrc.cards)?ragSrc.cards:[]).map(c=>({
  knowledge_id:String((c&&c.knowledge_id)||''),
  revision:(c&&c.revision)!=null?c.revision:null,
  rrf_score:Number.isFinite(Number(c&&c.rrf_score))?Number(c.rrf_score):null,
  branches:Array.isArray(c&&c.branches)?c.branches.slice(0,6):[]
}));
const ragFindings=(Array.isArray(ragSrc.findings)?ragSrc.findings:[]).slice(0,8).map(f=>{
  if(obj(f)&&f.code) return {code:String(f.code)};
  const code=String(f||'').trim();
  return code?{code}:null;
}).filter(Boolean);
const ragLog={
  query:String(req.query||ragSrc.query||'').slice(0,800),
  filters:obj(req.filters)?req.filters:(obj(ragSrc.filters)?ragSrc.filters:{}),
  status:String(ragSrc.status||'empty'),
  phase:String(ragSrc.phase||'initial'),
  cards:ragCards,
  findings:ragFindings
};
const chatMessages=(chat)=>{
  const cr=obj(chat.chat_request)?chat.chat_request:chat;
  return Array.isArray(cr.messages)?cr.messages:[];
};
const previewContent=(v)=>{
  if(typeof v==='string') return v.slice(0,2000);
  if(v==null) return '';
  try{return JSON.stringify(v).slice(0,2000);}catch{return String(v).slice(0,2000);}
};
const decisionChat=nodeJson('Build decision chat');
const llmLog={
  model:String((obj(decisionChat.chat_request)&&decisionChat.chat_request.model)||''),
  prompt_tokens:Number(llmParse.prompt_tokens||0),
  completion_tokens:Number(llmParse.completion_tokens||0),
  reasoning_tokens:Number(llmParse.reasoning_tokens||0),
  finish_reason:String(llmParse.finish_reason||llmParse.error||'')
};
const out=parse(raw.output||raw.text||raw);
const decision=obj(out.action)?out:(obj(raw)?raw:{});
const verification=(()=>{
  try{
    const v=$('Verify completion').first().json||{};
    const p=parse(v.output||v.text||v);
    /* The per-part judgements are the reasoning; the summary flag is derived from them (the model
       can contradict itself: every part covered:true yet all_covered:false). No parts listed → flag.
       Neither a usable part nor a boolean flag → no verification (null), never a verdict from nothing.
       Deliberately lenient (either field suffices): a partial reply with an uncovered part must still
       reject — discarding it would let the finish through unchecked. */
    const parts=(Array.isArray(p.goal_parts)?p.goal_parts:[]).filter(x=>obj(x)&&String(x.part||'').trim());
    const hasFlag=typeof p.all_covered==='boolean';
    if(!hasFlag&&!parts.length) return null;
    const uncovered=parts.filter(x=>x.covered!==true).map(x=>String(x.part||'').trim()).slice(0,6);
    const allCovered=parts.length?uncovered.length===0:p.all_covered===true;
    return {...p,goal_parts:parts,uncovered,all_covered:allCovered,
      unsupported_claims:(Array.isArray(p.unsupported_claims)?p.unsupported_claims:[]).map(s=>String(s||'').trim()).filter(Boolean).slice(0,6)};
  }catch{return null}
})();
const KNOWN_ACTIONS=['call_agent','ask_user','continue','finish'];
const parsedAction=obj(decision.action);
let action=parsedAction?{...decision.action}:{};
let type=String(action.type||'').trim();
const unparsed=!parsedAction||!KNOWN_ACTIONS.includes(type);
const progress=obj(decision.progress)?decision.progress:{};
const registry=Array.isArray(prev.registry)?prev.registry:[];
const state=sanitizeState(obj(prev.state)?{...prev.state}:{});
state.step_count=Number(state.step_count||0)+1;
state.version=Number(state.version||0)+1;
/* O13: merge the plan before the guards — the model may close items in the same reply as finish.
   Items without id are rejected and counted (developer log), never silently dropped. */
const planMerge=applyPlanUpdate(state, decision.plan_update, registry);
/* --- Completion guards (deterministic safety around the LLM decision; no domain knowledge) ---
   1. The human accepted the result in a review gate as the outcome of the task → finished, whatever the
      LLM picked. Accepted one agent's result while the plan has steps for agents that never ran
      (review_scope 'agent') → the LLM's decision stands; that agent is not re-delegated.
   2. The LLM says goal_satisfied yet re-delegates to an agent that already completed (habit) → finish.
      A first delegation with a wrong goal_satisfied flag is the opposite case: the action is the
      intent, the flag is the slip — the flag is ignored and the agent is called.
   3. Re-delegating to an agent that already returned completed, with no new human input since:
      allowed once with an explicit rework_reason; otherwise → result review with the human. A call that
      names another open plan item of that agent is new work, not a repeat (planNamesNewWork). Completing
      the agent closes leftover items it owns when its data already has every asked key (or, if none
      were asked, every output_provides key). */
let guard=null;
let reworkReason='';
const answered=ledgerAnsweredAgentQuestion(state);
const accepted=ledgerHumanAccepted(state);
if(unparsed){
  const fails=Number(state.ledger.parse_failures||0)+1;
  state.ledger.parse_failures=fails;
  ledgerPush(state,{kind:'verification',verdict:'rejected',uncovered:['решение оркестратора не разобрано']});
  guard='decision_unparsed';
  if(fails<3){
    type='continue';
    action={type:'continue'};
  } else {
    type='fail_case';
    action={type:'fail_case'};
  }
} else {
  state.ledger.parse_failures=0;
}
/* Open plan items (pending / active / blocked) are uncovered parts of the goal by definition: a finish
   with them behaves like a rejected completion check (one continue step, then the engineer). */
const planOpen=type==='finish'?planOpenItems(state.plan):[];
const repeatDelegation=(agentId, taskId)=>{
  const id=String(agentId||'').trim();
  const last=ledgerLastAgentEntry(state, id);
  if(!last||String(last.status||'')!=='completed'||ledgerHasNewInputsSince(state, last)) return false;
  return !planNamesNewWork(state, id, taskId);
};
if(unparsed){
  /* Unparseable / unknown action: one continue so Decision retries; second consecutive → case.failed.
     Never a HITL question — the engineer cannot repair a broken LLM envelope. */
} else if(type==='finish'&&answered){
  /* 0. An agent asked, the human answered, the agent has not run since → the answer must reach the
        agent that asked. Finishing here would silently drop the engineer's decision. */
  guard='answer_not_applied';
  type='call_agent';
  action={
    type:'call_agent',
    agent_id:answered.agent_id,
    handoff_message:`Инженер ответил на ваш вопрос («${answered.question.slice(0,160)}»): «${answered.answer.slice(0,200)}». Продолжите задачу с учётом этого ответа.`,
    task:{objective:String(state.goal||'')}
  };
} else if(type!=='finish'&&accepted==='case'){
  guard='human_accepted';
  type='finish';
  action={type:'finish',result:{summary_for_human:String((obj(action.result)&&action.result.summary_for_human)||'')}};
} else if(type==='finish'&&accepted!=='case'&&((verification&&verification.all_covered===false)||planOpen.length)){
  /* 4. Verified completion: the LLM proposed finish, but the completion check found parts of the goal
        without a completed journal entry and/or its own plan still has open items. First time — one
        more step with the gaps in the journal (the Decision LLM must close them); second time — the
        engineer decides. */
  const uncovered=[...new Set([...(verification?verification.uncovered||[]:[]), ...planOpen.map(p=>p.title||p.id)])].slice(0,8);
  const unsupported=verification?verification.unsupported_claims||[]:[];
  const verdict=(verification&&verification.all_covered===false)?String(verification.verdict_for_human||'').trim():'';
  const rejections=Number(state.ledger.verify_rejections||0);
  ledgerPush(state,{kind:'verification',verdict:'rejected',uncovered,unsupported,proposed:String((obj(action.result)&&action.result.summary_for_human)||'').slice(0,300)});
  state.ledger.verify_rejections=rejections+1;
  if(rejections<1){
    guard='completion_unverified';
    type='continue';
    action={type:'continue',uncovered,verdict};
  } else {
    guard='completion_review';
    state.ledger.reviews=Number(state.ledger.reviews||0)+1;
    const review=buildCompletionReviewQuestion(state, registry, uncovered, verdict);
    type='ask_user';
    action={type:'ask_user',...review};
  }
} else if(type==='call_agent'&&progress.goal_satisfied===true&&!answered&&repeatDelegation(action.agent_id, action.task_id)){
  guard='goal_satisfied';
  type='finish';
  action={type:'finish',result:{summary_for_human:String(progress.evidence||'')}};
} else if(type==='call_agent'){
  const agentId=String(action.agent_id||'').trim();
  const repeat=repeatDelegation(agentId, action.task_id);
  if(progress.goal_satisfied===true&&!repeat) guard='goal_flag_ignored';
  if(repeat){
    reworkReason=String(action.rework_reason||'').trim();
    const stall=Number(state.ledger.stall_count||0);
    if(reworkReason&&stall<1){
      guard='rework_allowed';
      state.ledger.stall_count=stall+1;
    } else {
      guard=reworkReason?'stall_review':'repeat_review';
      state.ledger.stall_count=stall+1;
      state.ledger.reviews=Number(state.ledger.reviews||0)+1;
      const review=buildResultReviewQuestion(state, agentId, registry, 'repeat');
      type='ask_user';
      action={type:'ask_user',...review};
    }
  }
}
let statusMessage=String(decision.status_message||'').trim()||'Шаг оркестратора';
if(guard==='decision_unparsed'&&type==='continue'){
  const why=String((llmParse&&llmParse.error)||'');
  if(why==='llm_truncated_empty') statusMessage='Модель исчерпала лимит ответа размышлением и не вернула JSON. Формулирую решение заново.';
  else if(why==='llm_empty_content') statusMessage='Модель вернула пустой ответ. Формулирую решение заново.';
  else if(why==='llm_json_invalid'||why==='llm_json_not_object') statusMessage='Ответ модели не разобрался как JSON. Формулирую решение заново.';
  else statusMessage='Не удалось разобрать следующий шаг. Формулирую решение заново.';
}
else if(guard==='decision_unparsed'&&type==='fail_case') statusMessage='Оркестратор три раза не смог разобрать следующий шаг. Перезапустите задачу или уточните формулировку.';
else if(guard==='repeat_review'||guard==='stall_review') statusMessage='Результат уже получен — прошу инженера принять его или описать доработку.';
else if(guard==='answer_not_applied') statusMessage=`Передаю ответ инженера агенту ${agentTitle(action.agent_id, registry)}.`;
else if(guard==='human_accepted') statusMessage='Инженер принял результат — завершаю задачу.';
else if(guard==='completion_unverified'){
  const gaps=(action.uncovered||[]).join('; ');
  const verdict=String(action.verdict||'').trim();
  statusMessage=(verdict&&!looksMachineText(verdict)?verdict.replace(/\.?$/,'. '):'Проверка завершения: задача ещё не выполнена целиком. ')+(gaps?`Не хватает: ${gaps}. `:'')+'Продолжаю работу.';
}
else if(guard==='completion_review') statusMessage='Проверка завершения снова нашла пробел — прошу инженера принять результат или уточнить, что нужно получить.';
/* Developer log: the LLM's decision as it came (before guards), bounded — this is what a developer
   compares with the guard/verification fields when a step went wrong. */
const decisionRaw=boundedForLog({...decision,action:obj(decision.action)?decision.action:null},4000);
const decisionEvent={
  kind:'orchestrator.decision',
  actor:'orchestrator',
  status_message:statusMessage,
  payload:{action_type:type,agent_id:action.agent_id||null,step_count:state.step_count,version:state.version,progress:{goal_satisfied:progress.goal_satisfied===true,evidence:String(progress.evidence||'').slice(0,300),missing:String(progress.missing||'').slice(0,300)},...(guard?{guard}:{}),llm:llmLog,rag:{query:ragLog.query,status:ragLog.status,cards:ragLog.cards,findings:ragLog.findings},...(unparsed?{llm_parse:{...llmParse,attempt:state.ledger.parse_failures}}:{}),plan:{total:state.plan.length,open:planOpenItems(state.plan).length,accepted:planMerge.accepted,rejected:planMerge.rejected},...(verification?{verification:{all_covered:verification.all_covered===true,goal_parts:(Array.isArray(verification.goal_parts)?verification.goal_parts:[]).slice(0,6).map(p=>({part:String((p&&p.part)||'').slice(0,160),covered:Boolean(p&&p.covered)}))}}:{}),decision:decisionRaw,...execRef()}
};
const events=[];
let nextStatus='running';
let agentTask=null;
if(type==='call_agent'){
  const taskId=String(action.task_id||`TASK-${state.step_count}`).trim();
  const flatArts=flattenArtifacts(state.artifacts||{});
  const artifactIds=Object.keys(flatArts).filter(k=>k!=='diff');
  const hitlState=state.hitl&&typeof state.hitl==='object'?state.hitl:{};
  const hitlAnswers=hitlState.answers&&typeof hitlState.answers==='object'?hitlState.answers:{};
  const unlistedPolicy=readUnlistedWellsPolicy(hitlAnswers)||'';
  /* inputs are references owned by the orchestrator (artifact ids, data buckets, HITL policy). The LLM talks to
     the agent through handoff_message only: anything it puts into action.task (e.g. its own `facts` with dates)
     is a paraphrase, and an agent that trusted it would override the deterministic result of a previous agent
     (CASE-6a9ec6b3-74e34e: invented per-well dates replaced upstream facts). expected_output is the shape
     of what to extract (field names, types) — not cell values. */
  const calleeId=String(action.agent_id||'').trim();
  if(!reworkReason) reworkReason=ledgerPendingRework(state);
  const expected=expectedOutputForCall(action, state, registry, calleeId);
  agentTask={
    case_id:prev.case_id,
    task_id:taskId,
    agent_id:calleeId,
    objective:String(action.task&&action.task.objective||state.goal||''),
    handoff_message:String(action.handoff_message||''),
    inputs:{
      activity_base_url:String(prev.activity_base_url||'http://mas-activity:8200').replace(/\/$/,''),
      schedule_root:state.schedule_root||'',
      artifact_ids:artifactIds,
      /* Phase 2: results of earlier agents live in state.agents[<agent_id>].data; the callee reads them from
         GET /cases/{id}/state. data_refs lists which agents already produced data (not domain buckets). */
      data_refs:Object.keys(state.agents||{}),
      ...(unlistedPolicy?{unlisted_wells_policy:unlistedPolicy}:{}),
      ...(reworkReason?{rework_reason:reworkReason}:{}),
      ...(expected?{expected_output:expected}:{})
    },
    context:{hitl:{pending:Boolean(hitlState.pending),answer_ids:Object.keys(hitlAnswers),answers:hitlAnswers}},
    constraints:{units:'METRIC'}
  };
  state.current_task=slimCurrentTask({task_id:taskId,agent_id:agentTask.agent_id},state.artifacts,state.agents);
  planMarkAgentActive(state, agentTask.agent_id, taskId, expected?expected.datasets.map(d=>d.name):[]);
  events.push(decisionEvent, {
    kind:'agent.handoff',
    actor:'orchestrator',
    agent_id:agentTask.agent_id,
    task_id:taskId,
    status_message:statusMessage,
    handoff_message:agentTask.handoff_message,
    /* Developer log: the exact agent_task the agent received (inputs are references, so it is small). */
    payload:{task_id:taskId,agent_task:boundedForLog(agentTask,6000),...execRef()}
  });
} else if(type==='ask_user'){
  nextStatus='waiting_user';
  const incomingQid=String(action.question_id||'');
  const reviewOwned=String(action.kind||'')==='result_approval'&&incomingQid.startsWith('result_review_');
  const q={question_id:reviewOwned?incomingQid:`Q-${state.step_count}`,question:String(action.question||'Нужно уточнение'),options:Array.isArray(action.options)?action.options:[],...(action.kind?{kind:action.kind}:{}),...(action.review_scope?{review_scope:action.review_scope}:{})};
  state.hitl={pending:true,questions:[q],answers:(state.hitl&&state.hitl.answers)||{}};
  state.current_task=null;
  events.push(decisionEvent, {kind:'hitl.request',actor:'orchestrator',status:'waiting_user',status_message:statusMessage,payload:{...q,...execRef()}});
} else if(type==='continue'){
  /* Completion check rejected the finish: no agent, no question — the next step re-decides with the
     gaps written into the journal. The loop is continued by "No agent this step" → "Continue loop?". */
  nextStatus='running';
  state.current_task=null;
  events.push(decisionEvent, {kind:'orchestrator.status',actor:'orchestrator',status:'running',status_message:statusMessage,payload:{action_type:'continue',uncovered:action.uncovered||[],...execRef()}});
} else if(type==='finish'){
  nextStatus='done';
  state.current_task=null;
  /* Honest completion: what agents actually did (their summaries), not "called N agents". */
  const llmSummary=String((obj(action.result)&&action.result.summary_for_human)||'').trim();
  const journalSummary=composeDoneSummary(state, registry);
  /* The engineer reads this line: if the LLM leaked ids (agent ids, artifact ids, JSON) or the
     completion check found claims the journal does not support, prefer the journal. */
  const unsupported=verification&&Array.isArray(verification.unsupported_claims)&&verification.unsupported_claims.length>0;
  const summary=(llmSummary&&!looksMachineText(llmSummary)&&!(unsupported&&journalSummary))?llmSummary:(journalSummary||llmSummary||statusMessage);
  /* The case result is the set of deliverables agents produced (cards with producer), not one file. */
  const result={...(obj(action.result)?action.result:{}),summary_for_human:summary,done_by_agents:journalSummary,deliverables:deliverables(state.artifacts),...(guard?{guard}:{}),...(verification?{completion_verified:verification.all_covered===true}:{})};
  state.data={...(state.data||{}),result};
  statusMessage=summary;
  /* The finish is a decision too: the developer log gets the LLM's raw finish (plan counters, verification,
     guard) as the last step's decision; the chat collapses it into case.finished (same status_message). */
  decisionEvent.status_message=summary;
  events.push(decisionEvent, {kind:'case.finished',actor:'orchestrator',status:'done',status_message:summary,payload:{...result,action_type:'finish',plan:state.plan,...execRef()}});
} else if(type==='fail_case'){
  nextStatus='failed';
  state.current_task=null;
  decisionEvent.status_message=statusMessage;
  events.push(decisionEvent, {kind:'case.failed',actor:'orchestrator',status:'failed',status_message:statusMessage,payload:{reason:'decision_unparsed',llm_parse:{...llmParse,attempt:state.ledger.parse_failures},...execRef()}});
} else {
  nextStatus='failed';
  state.current_task=null;
  statusMessage='Оркестратор три раза не смог разобрать следующий шаг. Перезапустите задачу или уточните формулировку.';
  decisionEvent.status_message=statusMessage;
  events.push(decisionEvent, {kind:'case.failed',actor:'orchestrator',status:'failed',status_message:statusMessage,payload:{reason:'decision_unparsed',llm_parse:{...llmParse,attempt:state.ledger.parse_failures},...execRef()}});
}
const stepNo=Number(state.step_count||0);
const traceEvents=[{
  kind:'trace.rag',
  actor:'orchestrator',
  status_message:`База знаний: ${ragLog.status} · ${ragLog.cards.length} карточек`,
  payload:{caller:'orchestrator',...ragLog,step_count:stepNo,...execRef()}
},{
  kind:'trace.llm',
  actor:'orchestrator',
  status_message:`decision: ${llmLog.finish_reason||'stop'} · ${Number(llmLog.prompt_tokens||0)+Number(llmLog.completion_tokens||0)} tok`,
  payload:{role:'decision',...llmLog,prompt_preview:previewChatMessages(chatMessages(decisionChat)),content_preview:previewContent(raw.output||raw.text||llmParse.sample||''),step_count:stepNo,...execRef()}
}];
if(verification){
  const verifyChat=nodeJson('Build verify chat');
  const verifyRaw=nodeJson('Verify completion');
  const vParse=obj(verifyRaw.llm_parse)?verifyRaw.llm_parse:describeLlmRaw(verifyRaw);
  const vTok=Number(vParse.prompt_tokens||0)+Number(vParse.completion_tokens||0);
  const vFinish=String(vParse.finish_reason||vParse.error||'');
  traceEvents.push({
    kind:'trace.llm',
    actor:'orchestrator',
    status_message:`verify: ${vFinish||'stop'} · ${vTok} tok`,
    payload:{
      role:'verify',
      model:String((obj(verifyChat.chat_request)&&verifyChat.chat_request.model)||llmLog.model),
      prompt_tokens:Number(vParse.prompt_tokens||0),
      completion_tokens:Number(vParse.completion_tokens||0),
      reasoning_tokens:Number(vParse.reasoning_tokens||0),
      finish_reason:vFinish,
      prompt_preview:previewChatMessages(chatMessages(verifyChat)),
      content_preview:previewContent(verifyRaw.output||verifyRaw.text||''),
      step_count:stepNo,
      ...execRef()
    }
  });
}
events.unshift(...traceEvents);
state.status=nextStatus;
const persistEvents=events.map(e=>[
  prev.case_id,
  e.task_id||'',
  e.kind,
  e.actor,
  e.agent_id||'',
  e.status||'',
  e.status_message||'',
  e.handoff_message||'',
  JSON.stringify(e.payload||{})
]);
return [{json:{
  ...prev,
  decision,
  action_type:type,
  agent_id:agentTask?agentTask.agent_id:null,
  agent_task:agentTask,
  state,
  next_status:nextStatus,
  should_call_agent:type==='call_agent'&&Boolean(agentTask&&agentTask.agent_id),
  should_continue:false,
  events,
  update_sql_parameters:[JSON.stringify(state), nextStatus, prev.case_id],
  event_sql_parameters:persistEvents[0],
  persist_events:persistEvents
}}];
"""

WRITE_EVENTS = r"""
const fromParse=(()=>{try{return $('Parse decision').first().json}catch{return null}})();
const fromMerge=(()=>{try{return $('Merge agent result').first().json}catch{return null}})();
const fromInterp=(()=>{try{return $('Apply interpreted answer').first().json}catch{return null}})();
const fromExtras=(()=>{try{return $('Apply request extras').first().json}catch{return null}})();
const withEvents=v=>v&&Array.isArray(v.persist_events)&&v.persist_events.length?v:null;
/* Resume events (hitl.answered / hitl.request re-ask / orchestrator.resume) come from Apply interpreted answer or
   Apply request extras. `case.created` is Activity's — Prepare start case is deliberately not a source here. */
const prev=fromMerge||fromParse||withEvents(fromInterp)||withEvents(fromExtras)||$json||{};
const rows=Array.isArray(prev.persist_events)?prev.persist_events:[];
if(!rows.length) return [{json:{...prev,p1:'',p2:'',p3:'',p4:'',p5:'',p6:'',p7:'',p8:'',p9:'{}'}}];
return rows.map(params=>{
  const vals=(Array.isArray(params)?params:[]).map(v=>v===null||v===undefined?'':String(v));
  return {json:{
    ...prev,
    p1:vals[0]||'',
    p2:vals[1]||'',
    p3:vals[2]||'',
    p4:vals[3]||'',
    p5:vals[4]||'',
    p6:vals[5]||'',
    p7:vals[6]||'',
    p8:vals[7]||'',
    p9:vals[8]||'{}',
    event_sql_parameters:vals
  }};
});
"""

ROUTE = STATE_SHAPE_JS + r"""
/* Phase 2: no agent names here. The registry row (`invoke`) says how the agent is reached:
   n8n sub-workflow by id (executeWorkflow with expression workflowId) or HTTP POST. Runtime Config may
   override workflow ids per agent (agent_workflow_ids JSON) — field engineers re-point without regen. */
const x=$json;
const id=String(x.agent_id||'').trim();
if(x.should_call_agent!==true||!id) return [{json:{...x,route:'none'}}];
const endpoints=(()=>{try{return $('Runtime endpoints').first().json||{}}catch{return {}}})();
const registry=Array.isArray(x.registry)?x.registry:[];
const inv=resolveInvoke(id, registry, endpoints);
return [{json:{
  ...x,
  route:inv.route,
  invoke_workflow_id:inv.workflow_id||'',
  invoke_workflow_name:inv.workflow_name||'',
  invoke_url:inv.url||'',
  invoke_reason:inv.reason||'',
  invoke_message:inv.route==='unbound'?unboundAgentMessage(inv,registry):''
}}];
"""

AGENT_UNBOUND = r"""
/* An agent the Decision LLM picked cannot be reached (not in registry, disabled, no invoke). Shaped as a
   failed agent_result so Merge agent result records it in the journal and the loop re-decides or fails. */
const x=$json||{};
return [{json:{
  status:'failed',
  agent_id:String(x.agent_id||''),
  message:String(x.invoke_message||'Агент недоступен для вызова.'),
  data:{},
  artifacts:{},
  issues:[{code:'agent_unbound',reason:String(x.invoke_reason||'')}],
  assumptions:[],
  requests:[]
}}];
"""

MERGE = STATE_SHAPE_JS + r"""
const prev=$('Prepare agent call').first().json||$('Parse decision').first().json||{};
const http=$json||{};
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
const clean=v=>{
  const s=String(v??'').trim();
  return /^(none|null|undefined)$/i.test(s)?'':s;
};
const unwrap=v=>{
  if(obj(v)) return v;
  if(Array.isArray(v)&&v.length&&obj(v[0])) return v[0];
  return {};
};
/* 1. Unwrap what Execute Workflow / HTTP Request returned into one agent_result object. */
const execError=http.error||http.executionError||null;
const result=unwrap(
  (http.body&&typeof http.body==='object')?http.body
  :(http.result&&typeof http.result==='object')?http.result
  :(http.output&&typeof http.output==='object')?http.output
  :http
);
const rawStatus=clean(result.status);
const errorMessage=clean((execError&&(execError.message||execError.description))||(result.error&&(result.error.message||result.error.description))||http.message);
const failed=!rawStatus&&Boolean(execError||result.error||errorMessage&&!result.task_id);
const agentResult={...result,status:rawStatus||(failed?'failed':'completed'),message:clean(result.message)||errorMessage};
/* 2. Apply it to the case (slot, artifacts, journal, events) — shared with the resume source=agent path. */
const state=sanitizeState(obj(prev.state)?{...prev.state}:{});
const data=obj(state.data)?{...state.data}:{};
delete data.facts;
const agentId=String(prev.agent_id||result.agent_id||'');
const registry=Array.isArray(prev.registry)?prev.registry:[];
const agentTaskIn=obj(prev.agent_task)?prev.agent_task:{};
const taskId=String(agentTaskIn.task_id||'');
const applied=applyAgentResult(state, agentId, taskId, agentResult, registry);
if(agentTaskIn.inputs&&agentTaskIn.inputs.rework_reason){
  const last=state.ledger.history[state.ledger.history.length-1];
  if(last&&last.kind==='agent') last.rework_reason=String(agentTaskIn.inputs.rework_reason);
}
let nextStatus=applied.next_status;
let shouldContinue=applied.should_continue;
const events=applied.events;
/* 3. Step budget comes from MAS — Runtime Config (max_steps, UI-editable). Hitting it is not a silent
   failure: if there is a completed result, the engineer reviews it; only with nothing to show we fail. */
const maxSteps=Math.max(1,Number(prev.max_steps)||12);
if(Number(state.step_count||0)>=maxSteps&&nextStatus==='running'){
  shouldContinue=false;
  const done=composeDoneSummary(state, registry);
  if(done){
    nextStatus='waiting_user';
    const review=buildResultReviewQuestion(state, agentId, registry, 'step_limit');
    state.hitl={pending:true,questions:[review],answers:(state.hitl&&state.hitl.answers)||{}};
    state.ledger.reviews=Number(state.ledger.reviews||0)+1;
    events.push({kind:'hitl.request',actor:'orchestrator',status:'waiting_user',status_message:`Лимит шагов (${maxSteps}) исчерпан — прошу инженера принять результат или описать доработку.`,payload:{...review,...execRef()}});
  } else {
    nextStatus='failed';
    events.push({kind:'case.failed',actor:'orchestrator',status:'failed',status_message:`Превышен лимит шагов оркестратора (${maxSteps}), результата нет.`,payload:{reason:'step_limit',max_steps:maxSteps,...execRef()}});
  }
}
state.data=data;
state.status=nextStatus;
state.version=Number(state.version||0)+1;
const persistEvents=events.map(e=>[
  prev.case_id, e.task_id||'', e.kind, e.actor, e.agent_id||'', e.status||'', e.status_message||'', e.handoff_message||'', JSON.stringify(e.payload||{})
]);
return [{json:{
  ...prev,
  agent_result:agentResult,
  state,
  next_status:nextStatus,
  should_continue:shouldContinue&&nextStatus==='running',
  update_sql_parameters:[JSON.stringify(state), nextStatus, prev.case_id],
  persist_events:persistEvents,
  events,
  event_sql_parameters:persistEvents[0],
  continue_url:String(prev.orchestrator_step_url||'http://127.0.0.1:5678/webhook/mas-orchestrator-step').replace(/\/$/,'')
}}];
"""

FINISH_NONE = r"""
const x=$json;
/* Only a `continue` decision (completion check rejected a finish) triggers the next step here;
   ask_user / finish end the run. */
const shouldContinue=String(x.action_type||'')==='continue'&&String(x.next_status||'')==='running';
return [{json:{...x, should_continue:shouldContinue, continue_url:String(x.orchestrator_step_url||'http://127.0.0.1:5678/webhook/mas-orchestrator-step').replace(/\/$/,'')}}];
"""

# Swimlanes, not a Kahn line. Numbered banners above each band — keep this for later workflows.
# Grid 260×170; node ~200×88; IF nodes need the extra 60px so they do not sit on neighbours.
CX, CY = 260, 170
NODE_W, NODE_H = 200, 88
LAYOUT_PAD = 24
BANNER_H = 56
ROW0 = 80
BAND_W = 6 * CX + NODE_W  # 1760 — seven-wide decide row still < 2200


def _cells(y: int, names: tuple[str, ...], x0: int = 0) -> dict[str, tuple[int, int]]:
    return {name: (x0 + i * CX, y) for i, name in enumerate(names)}


def _band(banner_y: int, rows: tuple[tuple[str, ...], ...]) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    for i, names in enumerate(rows):
        out.update(_cells(banner_y + ROW0 + i * CY, names))
    return out


# (content, color) — width/height applied in apply_orchestrator_layout
LANE_NOTES: dict[str, tuple[str, int]] = {
    "lane intake": ("### 1. Вход и загрузка кейса", 6),
    "lane start": ("### 2. Новый кейс", 4),
    "lane interpret": ("### 3. Ответ инженера", 2),
    "lane decide": ("### 4. Решение LLM", 5),
    "lane agents": ("### 5. Агенты и цикл", 7),
    "lane ack": ("### 6. Ответ в Activity", 3),
}

# Banner y: previous last-row bottom + PAD, then ROW0 to first node (no overlap with pad=24).
_S1, _S2, _S3, _S4, _S5, _S6 = 0, 540, 740, 1110, 1650, 2190
ORCH_LAYOUT: dict[str, tuple[int, int]] = {
    "edit after import": (0, -580),
    "lane intake": (0, _S1),
    "lane start": (0, _S2),
    "lane interpret": (0, _S3),
    "lane decide": (0, _S4),
    "lane agents": (0, _S5),
    "lane ack": (0, _S6),
}
ORCH_LAYOUT.update(
    _band(
        _S1,
        (
            ("Authenticated MAS webhook", "Runtime endpoints", "Normalize step request", "Probe ping?", "Needs create?"),
            ("Insert execution map", "Load case", "Validate loaded case", "Case found?", "Apply request extras"),
            ("Status only?", "Resume persist?", "Not a resume?"),
        ),
    )
)
ORCH_LAYOUT.update(
    _band(
        _S2,
        (("Prepare start case", "Insert new case", "Expand start events", "Insert start events", "Restore after start"),),
    )
)
ORCH_LAYOUT.update(
    _band(
        _S3,
        (
            ("Needs interpret?", "Build interpret chat", "Interpret chat", "Interpret free-text answer", "Apply interpreted answer"),
            ("Update case after resume", "Expand resume events", "Insert resume events", "Restore after resume", "Resume to decision?"),
        ),
    )
)
ORCH_LAYOUT.update(
    _band(
        _S4,
        (
            ("Load agent registry", "Prepare decision context", "Call Knowledge Retrieval", "Attach orchestrator RAG evidence", "Build decision chat", "Decision chat"),
            ("Decision LLM", "Finish proposed?", "Parse decision", "Update case after decision", "Expand decision events", "Insert decision events"),
            ("Prepare completion check", "Build verify chat", "Verify chat", "Verify completion", "Restore parsed decision", "Prepare agent call", "Action router"),
        ),
    )
)
ORCH_LAYOUT.update(
    _band(
        _S5,
        (
            ("Call agent (n8n)", "Call agent (HTTP)", "Agent not bound", "No agent this step"),
            ("Merge agent result", "Update case after agent", "Expand agent events", "Insert agent events", "Restore after agent events", "Continue loop?"),
            ("POST continue run",),
        ),
    )
)
ORCH_LAYOUT.update(
    _band(
        _S6,
        (("Prepare Activity ack", "Activity sync?", "POST step ack to MAS Activity", "Format step ack"),),
    )
)


def _layout_box(name: str, pos: tuple[int, int]) -> tuple[int, int, int, int]:
    x, y = pos
    if name == "edit after import":
        return x, y, 480, 420
    if name in LANE_NOTES:
        return x, y, BAND_W, BANNER_H
    return x, y, NODE_W, NODE_H


def _boxes_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return not (ax + aw + LAYOUT_PAD <= bx or bx + bw + LAYOUT_PAD <= ax or ay + ah + LAYOUT_PAD <= by or by + bh + LAYOUT_PAD <= ay)


def assert_orchestrator_layout() -> None:
    names = list(ORCH_LAYOUT)
    hits: list[str] = []
    for i, left in enumerate(names):
        for right in names[i + 1 :]:
            if _boxes_overlap(_layout_box(left, ORCH_LAYOUT[left]), _layout_box(right, ORCH_LAYOUT[right])):
                hits.append(f"{left} × {right}")
    exec_x = [ORCH_LAYOUT[n][0] for n in names if n != "edit after import" and n not in LANE_NOTES]
    width = max(exec_x) - min(exec_x)
    if hits:
        raise SystemExit("orchestrator layout overlaps:\n  " + "\n  ".join(hits))
    if width >= 2200:
        raise SystemExit(f"orchestrator layout too wide: {width}")


def apply_orchestrator_layout(wf: dict) -> None:
    """Keep the swimlane board; generic layered relayout must not flatten this graph."""
    for node in wf.get("nodes") or []:
        name = node.get("name")
        if name not in ORCH_LAYOUT:
            continue
        x, y = ORCH_LAYOUT[name]
        node["position"] = [int(x), int(y)]
        if name in LANE_NOTES:
            title, color = LANE_NOTES[name]
            params = node.setdefault("parameters", {})
            params["content"] = title
            params["width"] = BAND_W
            params["height"] = BANNER_H
            params["color"] = color


def main() -> None:
    nodes = [
        note(
            "edit after import",
            (-200, -420),
            "## edit after import\n\n**Orchestrator — MAS** — thin loop:\n- Bind Postgres on load/insert/update\n- Bind **Qwen** OpenAI-compatible credential on **Decision chat**, **Verify chat**, and **Interpret chat**\n- Bind inbound header auth on webhook **and** POST continue run\n- Bind **Runtime endpoints** → `MAS — Runtime Config` (один Set URL на весь контур; `chat_model` + `chat_base_url` — модель и Base URL Decision/Verify/Interpret)\n- **Call agent (n8n)** — universal: workflowId is an expression from `agent_registry.invoke.workflow_id` (or Runtime Config `agent_workflow_ids` JSON `{\"<agent_id>\":\"<id>\"}` when the field import assigned new ids). No per-agent nodes; adding an agent = `upsert_agent` in Control Plane Proxy + RAG routing card.\n- **Call agent (HTTP)** — `invoke.kind=http`, url with `{math_url}`-style placeholders from Runtime Config\n- Bind **Call Knowledge Retrieval** → `MAS — Knowledge Retrieval` (срез `orchestrator_routing` / `routing_card`; не excel_protocol и не schedule_mvp)\n- Excel `X-API-Key` — credential на Agent — Excel Extractor, не этот workflow\n\n`action`: probe | status | start | create | step | resume\n- **resume** `source`: human (Activity `/answer`) | agent | system (`POST /cases/{id}/run`). Кнопка HITL = `choice`; свободный текст при вариантах — Interpret free-text answer (тот же Qwen).\n- Activity `/cases` stores files; specialists fetch `/cases/{id}/artifacts/{id}` from their FastAPI tools. n8n never carries binaries.\n- **status** loads case and returns `human_gate` without LLM\n- Decision / Verify / Interpret — HTTP `/chat/completions` with thinking off (`reasoning.enabled=false`); JSON.parse, no Structured Output parser\n- Loop: **POST continue run** → own webhook (`orchestrator_step_url` in Runtime Config). Activity starts/resumes cases; it does not drive the step loop.\n\nOne execution = one step.",
            480,
            420,
            1,
        ),
        note("lane intake", (0, _S1), LANE_NOTES["lane intake"][0], BAND_W, BANNER_H, LANE_NOTES["lane intake"][1]),
        note("lane start", (0, _S2), LANE_NOTES["lane start"][0], BAND_W, BANNER_H, LANE_NOTES["lane start"][1]),
        note("lane interpret", (0, _S3), LANE_NOTES["lane interpret"][0], BAND_W, BANNER_H, LANE_NOTES["lane interpret"][1]),
        note("lane decide", (0, _S4), LANE_NOTES["lane decide"][0], BAND_W, BANNER_H, LANE_NOTES["lane decide"][1]),
        note("lane agents", (0, _S5), LANE_NOTES["lane agents"][0], BAND_W, BANNER_H, LANE_NOTES["lane agents"][1]),
        note("lane ack", (0, _S6), LANE_NOTES["lane ack"][0], BAND_W, BANNER_H, LANE_NOTES["lane ack"][1]),
        node(
            "Authenticated MAS webhook",
            "n8n-nodes-base.webhook",
            2.1,
            (0, 0),
            {
                "httpMethod": "POST",
                "path": "mas-orchestrator-step",
                "authentication": "headerAuth",
                "responseMode": "lastNode",
                "options": {},
            },
            credentials=HDR,
            webhookId="a1000003-mas-orch-wh-0001-800000000001",
        ),
        node(
            "Runtime endpoints",
            "n8n-nodes-base.executeWorkflow",
            1.3,
            (240, 0),
            runtime_config_execute_params(),
        ),
        code("Normalize step request", (480, 0), NORMALIZE),
        if_true("Probe ping?", (640, 0), "={{ Boolean($json.is_probe) }}"),
        if_true("Needs create?", (640, 160), "={{ Boolean($json.needs_create) }}"),
        code("Prepare start case", (800, 280), CREATE_CASE),
        postgres(
            "Insert new case",
            (1000, 280),
            "INSERT INTO cases (case_id, state, status, updated_at) VALUES ($1, $2::jsonb, $3, now()) ON CONFLICT (case_id) DO NOTHING",
            "={{ $json.create_sql_parameters }}",
        ),
        code("Expand start events", (1180, 280), WRITE_EVENTS),
        postgres(
            "Insert start events",
            (1360, 280),
            "INSERT INTO events(case_id, task_id, kind, actor, agent_id, status, status_message, handoff_message, payload) SELECT $1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb WHERE NULLIF($3, '') IS NOT NULL",
            "={{ [$json.p1, $json.p2, $json.p3, $json.p4, $json.p5, $json.p6, $json.p7, $json.p8, $json.p9] }}",
            batching="independently",
        ),
        code(
            "Restore after start",
            (1540, 280),
            "const prev=$('Prepare start case').first().json||{};\nreturn [{json:prev}];",
            executeOnce=True,
        ),
        postgres(
            "Insert execution map",
            (720, -120),
            "INSERT INTO executions(execution_id, case_id, workflow_name) VALUES ($1, $2, $3) ON CONFLICT (execution_id) DO UPDATE SET case_id = EXCLUDED.case_id",
            "={{ $json.exec_sql_parameters }}",
        ),
        postgres(
            "Load case",
            (960, -120),
            "SELECT case_id, state, status, updated_at FROM cases WHERE case_id = $1",
            "={{ $('Normalize step request').first().json.sql_parameters }}",
        ),
        code("Validate loaded case", (1120, -240), VALIDATE_CASE),
        if_true("Case found?", (1280, -240), "={{ Boolean($json.case_loaded) }}"),
        code("Apply request extras", (1120, -120), APPLY_EXTRAS),
        if_true("Status only?", (1280, -120), "={{ Boolean($json.is_status) }}"),
        if_true("Resume persist?", (1280, 40), "={{ Boolean($json.did_resume) }}"),
        if_true("Needs interpret?", (1480, -80), "={{ Boolean($json.needs_interpret) }}"),
        code("Build interpret chat", (1560, -80), BUILD_INTERPRET_CHAT),
        openai_chat_http("Interpret chat", (1680, -80)),
        code("Interpret free-text answer", (1840, -80), FORMAT_OPENAI_CHAT, alwaysOutputData=True),
        code("Apply interpreted answer", (2040, -80), APPLY_INTERPRETED),
        postgres(
            "Update case after resume",
            (1480, 40),
            "UPDATE cases SET state = $1::jsonb, status = $2, updated_at = now() WHERE case_id = $3",
            "={{ $json.update_sql_parameters }}",
        ),
        code("Expand resume events", (1660, 40), WRITE_EVENTS),
        postgres(
            "Insert resume events",
            (1840, 40),
            "INSERT INTO events(case_id, task_id, kind, actor, agent_id, status, status_message, handoff_message, payload) SELECT $1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb WHERE NULLIF($3, '') IS NOT NULL",
            "={{ [$json.p1, $json.p2, $json.p3, $json.p4, $json.p5, $json.p6, $json.p7, $json.p8, $json.p9] }}",
            batching="independently",
        ),
        code(
            "Restore after resume",
            (2020, 40),
            "let prev=null;\ntry{const item=$('Apply interpreted answer').first(); if(item&&item.json&&item.json.did_resume) prev=item.json;}catch(e){}\nif(!prev){try{prev=$('Apply request extras').first().json||{}}catch(e){prev={}}}\nreturn [{json:prev}];",
            executeOnce=True,
        ),
        if_true("Resume to decision?", (1840, 200), "={{ String($json.next_status || '') === 'running' }}"),
        if_true("Not a resume?", (1120, 200), "={{ Boolean($json.is_resume) !== true }}"),
        postgres(
            "Load agent registry",
            (960, 80),
            list_agents_sql(enabled_only=True),
            "={{ [] }}",
        ),
        code("Prepare decision context", (1200, 0), PREPARE_DECISION),
        node(
            "Call Knowledge Retrieval",
            "n8n-nodes-base.executeWorkflow",
            1.3,
            (1200, 180),
            knowledge_retrieval_execute_params(),
            onError="continueRegularOutput",
        ),
        code("Attach orchestrator RAG evidence", (1440, 180), attach_orchestrator_rag_js(policy_heading="Политика из базы знаний:")),
        code("Build decision chat", (1440, 0), BUILD_DECISION_CHAT),
        openai_chat_http("Decision chat", (1560, 0)),
        code("Decision LLM", (1680, 0), FORMAT_OPENAI_CHAT, alwaysOutputData=True),
        # Verified completion: only a proposed `finish` takes the detour through the completion check.
        if_true(
            "Finish proposed?",
            (1680, 0),
            "={{ String(((($json.output || {}).action) || {}).type || '') === 'finish' }}",
        ),
        code("Prepare completion check", (1800, 120), PREPARE_VERIFY),
        code("Build verify chat", (1920, 120), BUILD_VERIFY_CHAT),
        openai_chat_http("Verify chat", (2040, 120)),
        code("Verify completion", (2160, 120), FORMAT_OPENAI_CHAT, alwaysOutputData=True),
        code("Parse decision", (2040, 0), PARSE_DECISION),
        postgres(
            "Update case after decision",
            (2160, -120),
            "UPDATE cases SET state = $1::jsonb, status = $2, updated_at = now() WHERE case_id = $3",
            "={{ $json.update_sql_parameters }}",
        ),
        code("Expand decision events", (2160, 80), WRITE_EVENTS),
        postgres(
            "Insert decision events",
            (2400, 80),
            "INSERT INTO events(case_id, task_id, kind, actor, agent_id, status, status_message, handoff_message, payload) SELECT $1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb WHERE NULLIF($3, '') IS NOT NULL",
            "={{ [$json.p1, $json.p2, $json.p3, $json.p4, $json.p5, $json.p6, $json.p7, $json.p8, $json.p9] }}",
            batching="independently",
        ),
        code(
            "Restore parsed decision",
            (2640, 0),
            "const prev=$('Parse decision').first().json||{};\nreturn [{json:prev}];",
            executeOnce=True,
        ),
        code("Prepare agent call", (2760, 0), ROUTE),
        node(
            "Action router",
            "n8n-nodes-base.switch",
            3.4,
            (2880, 0),
            {"mode": "expression", "numberOutputs": 4, "output": "={{ ({workflow:0,http:1,unbound:2,none:3})[$json.route] ?? 3 }}"},
        ),
        # Phase 2: one call node per invocation kind, target resolved from the registry row at runtime.
        execute_workflow_by_expression(
            "Call agent (n8n)",
            (3120, -240),
            "={{ $json.invoke_workflow_id }}",
            {"agent_task": "={{ $json.agent_task }}"},
        ),
        http_json("Call agent (HTTP)", (3120, -40), "={{ $json.invoke_url }}", "={{ $json.agent_task }}"),
        code("Agent not bound", (3120, 160), AGENT_UNBOUND),
        code("No agent this step", (3120, 360), FINISH_NONE),
        code("Merge agent result", (3360, -40), MERGE),
        postgres(
            "Update case after agent",
            (3600, -160),
            "UPDATE cases SET state = $1::jsonb, status = $2, updated_at = now() WHERE case_id = $3",
            "={{ $json.update_sql_parameters }}",
        ),
        code("Expand agent events", (3600, 40), WRITE_EVENTS),
        postgres(
            "Insert agent events",
            (3840, 40),
            "INSERT INTO events(case_id, task_id, kind, actor, agent_id, status, status_message, handoff_message, payload) SELECT $1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb WHERE NULLIF($3, '') IS NOT NULL",
            "={{ [$json.p1, $json.p2, $json.p3, $json.p4, $json.p5, $json.p6, $json.p7, $json.p8, $json.p9] }}",
            batching="independently",
        ),
        code(
            "Restore after agent events",
            (3960, 0),
            "const prev=$('Merge agent result').first().json||{};\nreturn [{json:prev}];",
            executeOnce=True,
        ),
        if_true("Continue loop?", (4080, 0), "={{ $json.should_continue }}"),
        node(
            "POST continue run",
            "n8n-nodes-base.httpRequest",
            4.4,
            (4320, -80),
            {
                "method": "POST",
                "url": "={{ $json.continue_url }}",
                "authentication": "genericCredentialType",
                "genericAuthType": "httpHeaderAuth",
                "sendHeaders": True,
                "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ ({action: 'step', case_id: $json.case_id, source: 'orchestrator-self'}) }}",
                "options": {"timeout": 8000, "response": {"response": {"fullResponse": False, "neverError": True}}},
            },
            credentials=HDR,
            onError="continueRegularOutput",
            alwaysOutputData=True,
        ),
        code(
            "Prepare Activity ack",
            (4320, 120),
            r"""const pick=()=>{
  try{const m=$('Merge agent result').first().json; if(m&&m.case_id) return m;}catch{}
  try{const n=$('No agent this step').first().json; if(n&&n.case_id) return n;}catch{}
  /* Interpret path: Apply interpreted answer owns the persisted events (hitl.answered / re-ask). Reading
     Apply request extras here (needs_interpret, no events) posted an empty hitl.request ack (CASE-6a9ee76b). */
  try{const i=$('Apply interpreted answer').first().json; if(i&&i.case_id&&i.did_resume) return i;}catch{}
  try{const a=$('Apply request extras').first().json; if(a&&a.case_id) return a;}catch{}
  try{const r=$('Normalize step request').first().json; if(r&&r.case_id) return r;}catch{}
  return $json||{};
};
const x=pick();
const persisted=Array.isArray(x.persist_events)&&x.persist_events.length>0;
const kind=x.action_type==='finish'?'case.finished':(x.action_type==='ask_user'||x.next_status==='waiting_user'?'hitl.request':'orchestrator.status');
const payload={contract:'mas_orchestrator_ack',case_id:x.case_id,status:x.next_status||x.status,action_type:x.action_type||null,message:x.message||null,should_continue:x.should_continue===true,human_gate:x.human_gate||null,version:x.version??null,restartable:x.restartable===true};
const statusMessage=String(payload.message||'').trim()||String(x.status_message||'').trim();
return [{json:{...x,activity_sync:Boolean(x.case_id&&!x.is_probe&&x.activity_base_url&&!persisted),activity_url:`${String(x.activity_base_url||'').replace(/\/$/,'')}/cases/${encodeURIComponent(String(x.case_id||''))}/events`,activity_event:{kind,actor:'orchestrator',status:payload.status,status_message:statusMessage,payload}}}];""",
        ),
        if_true("Activity sync?", (4480, 220), "={{ Boolean($json.activity_sync) }}"),
        node(
            "POST step ack to MAS Activity",
            "n8n-nodes-base.httpRequest",
            4.4,
            (4640, 120),
            {
                "method": "POST",
                "url": "={{ $json.activity_url }}",
                "sendHeaders": True,
                "headerParameters": {"parameters": [{"name": "Content-Type", "value": "application/json"}]},
                "sendBody": True,
                "specifyBody": "json",
                "jsonBody": "={{ $json.activity_event }}",
                "options": {"timeout": 2000, "response": {"response": {"fullResponse": False, "neverError": True}}},
            },
            onError="continueRegularOutput",
        ),
        code(
            "Format step ack",
            (4800, 220),
            "const source=(()=>{try{return $('Prepare Activity ack').first().json}catch{return $json}})();\nconst x=source||$json;return[{json:{contract:'mas_orchestrator_ack',case_id:x.case_id,status:x.next_status||x.status,action_type:x.action_type||null,message:x.message||null,should_continue:x.should_continue===true,human_gate:x.human_gate||null,version:x.version??null,restartable:x.restartable===true}}];",
        ),
    ]

    connections = {}
    connect(connections, "Authenticated MAS webhook", "Runtime endpoints")
    connect(connections, "Runtime endpoints", "Normalize step request")
    connect(connections, "Normalize step request", "Probe ping?")
    connect(connections, "Probe ping?", "Prepare Activity ack", si=0)
    connect(connections, "Probe ping?", "Needs create?", si=1)
    connect(connections, "Needs create?", "Prepare start case", si=0)
    connect(connections, "Needs create?", "Insert execution map", si=1)
    connect(connections, "Prepare start case", "Insert new case")
    connect(connections, "Insert new case", "Expand start events")
    connect(connections, "Expand start events", "Insert start events")
    connect(connections, "Insert start events", "Restore after start")
    connect(connections, "Restore after start", "Insert execution map")
    connect(connections, "Insert execution map", "Load case")
    connect(connections, "Load case", "Validate loaded case")
    connect(connections, "Validate loaded case", "Case found?")
    connect(connections, "Case found?", "Apply request extras", si=0)
    connect(connections, "Case found?", "Prepare Activity ack", si=1)
    connect(connections, "Apply request extras", "Status only?")
    connect(connections, "Status only?", "Prepare Activity ack", si=0)
    connect(connections, "Status only?", "Resume persist?", si=1)
    connect(connections, "Resume persist?", "Needs interpret?", si=0)
    connect(connections, "Resume persist?", "Not a resume?", si=1)
    connect(connections, "Not a resume?", "Load agent registry", si=0)
    connect(connections, "Not a resume?", "Prepare Activity ack", si=1)
    connect(connections, "Needs interpret?", "Build interpret chat", si=0)
    connect(connections, "Needs interpret?", "Update case after resume", si=1)
    connect(connections, "Build interpret chat", "Interpret chat")
    connect(connections, "Interpret chat", "Interpret free-text answer")
    connect(connections, "Interpret free-text answer", "Apply interpreted answer")
    connect(connections, "Apply interpreted answer", "Update case after resume")
    connect(connections, "Update case after resume", "Expand resume events")
    connect(connections, "Expand resume events", "Insert resume events")
    connect(connections, "Insert resume events", "Restore after resume")
    connect(connections, "Restore after resume", "Resume to decision?")
    connect(connections, "Resume to decision?", "Load agent registry", si=0)
    connect(connections, "Resume to decision?", "Prepare Activity ack", si=1)
    connect(connections, "Load agent registry", "Prepare decision context")
    connect(connections, "Prepare decision context", "Call Knowledge Retrieval")
    connect(connections, "Call Knowledge Retrieval", "Attach orchestrator RAG evidence")
    connect(connections, "Attach orchestrator RAG evidence", "Build decision chat")
    connect(connections, "Build decision chat", "Decision chat")
    connect(connections, "Decision chat", "Decision LLM")
    # Verified completion: finish → completion check (second LLM pass) → Parse decision; anything else → Parse decision.
    connect(connections, "Decision LLM", "Finish proposed?")
    connect(connections, "Finish proposed?", "Prepare completion check", si=0)
    connect(connections, "Finish proposed?", "Parse decision", si=1)
    connect(connections, "Prepare completion check", "Build verify chat")
    connect(connections, "Build verify chat", "Verify chat")
    connect(connections, "Verify chat", "Verify completion")
    connect(connections, "Verify completion", "Parse decision")
    connect(connections, "Parse decision", "Update case after decision")
    connect(connections, "Update case after decision", "Expand decision events")
    connect(connections, "Expand decision events", "Insert decision events")
    connect(connections, "Insert decision events", "Restore parsed decision")
    connect(connections, "Restore parsed decision", "Prepare agent call")
    connect(connections, "Prepare agent call", "Action router")
    connect(connections, "Action router", "Call agent (n8n)", si=0)
    connect(connections, "Action router", "Call agent (HTTP)", si=1)
    connect(connections, "Action router", "Agent not bound", si=2)
    connect(connections, "Action router", "No agent this step", si=3)
    connect(connections, "Call agent (n8n)", "Merge agent result")
    connect(connections, "Call agent (HTTP)", "Merge agent result")
    connect(connections, "Agent not bound", "Merge agent result")
    # A `continue` step (completion check rejected a finish) re-enters the loop like an agent step would.
    connect(connections, "No agent this step", "Continue loop?")
    connect(connections, "Merge agent result", "Update case after agent")
    connect(connections, "Update case after agent", "Expand agent events")
    connect(connections, "Expand agent events", "Insert agent events")
    connect(connections, "Insert agent events", "Restore after agent events")
    connect(connections, "Restore after agent events", "Continue loop?")
    connect(connections, "Continue loop?", "POST continue run", si=0)
    connect(connections, "Continue loop?", "Prepare Activity ack", si=1)
    connect(connections, "POST continue run", "Prepare Activity ack")
    connect(connections, "Prepare Activity ack", "Activity sync?", si=0)
    connect(connections, "Activity sync?", "POST step ack to MAS Activity", si=0)
    connect(connections, "Activity sync?", "Format step ack", si=1)
    connect(connections, "POST step ack to MAS Activity", "Format step ack")

    wf = {
        "id": WF_ID,
        "name": WF_NAME,
        "active": False,
        "isArchived": False,
        "nodes": nodes,
        "connections": connections,
        "settings": {
            "executionOrder": "v1",
            "callerPolicy": "workflowsFromSameOwner",
            "errorWorkflow": ERROR_WF_ID,
        },
        "meta": {"templateCredsSetupCompleted": True, "targetN8nVersion": "2.30.8"},
        "tags": [],
        "pinData": {},
        "versionId": str(uuid.uuid4()),
    }
    apply_orchestrator_layout(wf)
    assert_orchestrator_layout()
    missing = [n["name"] for n in wf["nodes"] if n.get("name") not in ORCH_LAYOUT]
    if missing:
        raise SystemExit(f"orchestrator layout missing nodes: {missing}")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(wf, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT} ({len(nodes)} nodes)")


if __name__ == "__main__":
    main()
