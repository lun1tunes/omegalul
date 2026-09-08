"""Shared orchestrator state helpers prepended into n8n Code nodes.

n8n 2.30 Code cannot import a JS module at runtime. This Python constant is
concatenated into Apply request extras / Prepare decision context / Parse
decision / Merge agent result so artifact and compact logic is edited once;
regenerate the orchestrator after changes.

Do not add a FastAPI service for this: field FastAPI stays Excel / Schedule /
Math / Activity. A dedicated executeWorkflow hop would still need these
helpers inside Parse/Merge (they call flatten/merge mid-node).
"""

from __future__ import annotations

import json

STATE_SHAPE_MARKER_BEGIN = "/* mas_state_utils begin */"
STATE_SHAPE_MARKER_END = "/* mas_state_utils end */"

# Plan item statuses (O13). Single source for the Decision schema enum, the JS helpers below and the
# Python twin (state_shape.PLAN_STATUSES is asserted equal in tests).
PLAN_STATUSES = ("pending", "active", "done", "blocked", "dropped")
PLAN_OPEN_STATUSES = ("pending", "active", "blocked")

_STATE_SHAPE_JS_TEMPLATE = r"""
/* mas_state_utils begin */
function roleForArtifactId(id){
  const k=String(id||'');
  if(k==='excel'||k.indexOf('excel_')===0) return 'excel';
  if(k==='surface') return 'surface';
  if(k==='schedule_source') return 'schedule_source';
  if(k.indexOf('schedule_source_')===0) return 'schedule_include';
  if(k==='schedule_out') return 'schedule_out';
  if(k==='trajectory'||k.indexOf('trajectory_')===0) return 'trajectory';
  if(k==='diff') return 'diff';
  return 'attachment';
}
function isNestedArtifacts(arts){
  if(!arts||typeof arts!=='object'||Array.isArray(arts)) return false;
  const sch=arts.schedule;
  if(sch&&typeof sch==='object'&&!Array.isArray(sch)&&(sch.source||Array.isArray(sch.includes)||Array.isArray(sch.grdecl)||sch.out||sch.diff!=null)) return true;
  if(Array.isArray(arts.trajectories)||Array.isArray(arts.attachments)) return true;
  return false;
}
/* Artifact card: {artifact_id, role, kind: input|intermediate|deliverable, producer: user|<agent_id>,
   filename, bytes, text?}. Text artifacts (schedule_out, diff) live inline in state. Python twin:
   mas-activity-service/app/state_shape.py. */
const INLINE_TEXT_ROLES=['schedule_out','diff'];
const AGENT_ONLY_ROLES=['schedule_out','diff'];
function utf8Length(s){
  try{return unescape(encodeURIComponent(String(s))).length}catch(_){return String(s).length}
}
function fileItem(id,value,role){
  if(value==null||value===''||(typeof value==='object'&&!Array.isArray(value)&&!Object.keys(value).length)) return null;
  const resolved=role||roleForArtifactId(id);
  if(typeof value==='string'){
    if(INLINE_TEXT_ROLES.indexOf(resolved)>=0){
      if(!value.trim()) return null;
      return {artifact_id:id||resolved,role:resolved,bytes:utf8Length(value),text:value};
    }
    return {artifact_id:id,filename:value,role:resolved};
  }
  if(typeof value!=='object'||Array.isArray(value)) return null;
  const item={...value};
  item.artifact_id=item.artifact_id||id;
  if(!item.artifact_id) return null;
  item.role=item.role||resolved||roleForArtifactId(item.artifact_id);
  return item;
}
function flattenArtifacts(arts){
  const src=arts&&typeof arts==='object'&&!Array.isArray(arts)?arts:{};
  const out={};
  const put=(value,fallbackId)=>{
    const row=fileItem(fallbackId,value);
    if(!row) return;
    out[row.artifact_id]=row;
  };
  if(isNestedArtifacts(src)){
    put(src.excel,'excel');
    put(src.surface,'surface');
    const sch=src.schedule&&typeof src.schedule==='object'?src.schedule:{};
    put(sch.source,'schedule_source');
    const incs=Array.isArray(sch.includes)?sch.includes:[];
    for(const inc of incs) put(inc,inc&&inc.artifact_id);
    const grdecl=Array.isArray(sch.grdecl)?sch.grdecl:[];
    for(const g of grdecl) put(g,g&&g.artifact_id);
    put(sch.out,'schedule_out');
    put(sch.diff,'diff');
    const trajs=Array.isArray(src.trajectories)?src.trajectories:[];
    for(const t of trajs) put(t,t&&t.artifact_id||'trajectory');
    const atts=Array.isArray(src.attachments)?src.attachments:[];
    for(const a of atts) put(a,a&&a.artifact_id);
    for(const [k,v] of Object.entries(src)){
      if(k==='excel'||k==='surface'||k==='schedule'||k==='trajectories'||k==='attachments') continue;
      if(!(k in out)) put(v,k);
    }
    return out;
  }
  for(const [k,v] of Object.entries(src)){
    if(k==='schedule'||k==='trajectories'||k==='attachments') continue;
    put(v,k);
  }
  return out;
}
function nestArtifacts(arts){
  const nested={};
  const includes=[];
  const grdecl=[];
  const trajectories=[];
  const attachments=[];
  for(const [id,item] of Object.entries(flattenArtifacts(arts))){
    if(!item||typeof item!=='object') continue;
    const role=item.role||roleForArtifactId(id);
    if(role==='diff'){ nested.schedule=nested.schedule||{}; nested.schedule.diff=item; }
    else if(role==='excel'){
      // First workbook (id `excel`) owns the slot; further workbooks (`excel_1`…) stay as attachments
      // with role excel so flatten/nest is lossless and every Excel of the case stays reachable.
      const aid=String(item.artifact_id||id);
      if(!nested.excel||(aid==='excel'&&String(nested.excel.artifact_id||'')!=='excel')){
        if(nested.excel) attachments.push(nested.excel);
        nested.excel=item;
      } else attachments.push(item);
    }
    else if(role==='surface') nested.surface=item;
    else if(role==='schedule_source'){ nested.schedule=nested.schedule||{}; nested.schedule.source=item; }
    else if(role==='schedule_include'){
      const name=String(item.filename||'').toLowerCase();
      if(name.endsWith('.grdecl')) grdecl.push(item);
      else includes.push(item);
    }
    else if(role==='schedule_out'){ nested.schedule=nested.schedule||{}; nested.schedule.out=item; }
    else if(role==='trajectory') trajectories.push(item);
    else attachments.push(item);
  }
  if(includes.length){ nested.schedule=nested.schedule||{}; nested.schedule.includes=includes; }
  if(grdecl.length){ nested.schedule=nested.schedule||{}; nested.schedule.grdecl=grdecl; }
  if(trajectories.length) nested.trajectories=trajectories;
  if(attachments.length) nested.attachments=attachments;
  return nested;
}
function fileCounts(arts){
  const counts={excel:0,schedule_source:0,includes:0,grdecl:0,trajectories:0,surface:0,schedule_out:0};
  for(const [id,item] of Object.entries(flattenArtifacts(arts))){
    const role=(item&&item.role)||roleForArtifactId(id);
    if(role==='schedule_include'){
      const name=String((item&&item.filename)||'').toLowerCase();
      if(name.endsWith('.grdecl')) counts.grdecl+=1;
      else counts.includes+=1;
    }
    else if(role in counts) counts[role]+=1;
  }
  return counts;
}
function artifactKind(item){
  if(!item||typeof item!=='object') return 'input';
  const k=String(item.kind||'').trim().toLowerCase();
  if(k==='input'||k==='intermediate'||k==='deliverable') return k;
  const role=String(item.role||roleForArtifactId(item.artifact_id||''));
  return AGENT_ONLY_ROLES.indexOf(role)>=0?'deliverable':'input';
}
function artifactProducer(item){
  if(!item||typeof item!=='object') return 'user';
  const p=String(item.producer||'').trim();
  if(p) return p;
  return artifactKind(item)==='input'?'user':'';
}
function cardFilename(item){
  const name=String(item.filename||'').trim();
  if(name) return name;
  const role=String(item.role||'');
  if(role==='schedule_out') return 'schedule_result.inc';
  if(role==='diff') return 'schedule_changes.diff';
  return String(item.artifact_id||role||'artifact');
}
function isExcelName(name){
  return /\.(xlsx|xls|xlsm|xltx|xltm)$/i.test(String(name||''));
}
function inputRank(role, filename){
  if(String(role||'')==='excel'||isExcelName(filename)) return 0;
  if(String(role||'')==='schedule_source') return 1;
  return 2;
}
function artifactCards(arts){
  /* Inputs first (Excel before INCLUDE stubs), then what agents produced. No inline text. */
  const inputs=[];
  const produced=[];
  for(const [id,item] of Object.entries(flattenArtifacts(arts))){
    if(!item||typeof item!=='object') continue;
    const kind=artifactKind(item);
    const size=item.bytes!=null?Number(item.bytes):(typeof item.text==='string'?utf8Length(item.text):null);
    const card={artifact_id:id,role:String(item.role||roleForArtifactId(id)),kind,producer:artifactProducer(item),filename:cardFilename(item),bytes:Number.isFinite(size)?size:null,summary:String(item.summary||'').trim()};
    (kind==='input'?inputs:produced).push(card);
  }
  inputs.sort((a,b)=>inputRank(a.role,a.filename)-inputRank(b.role,b.filename)||String(a.artifact_id).localeCompare(String(b.artifact_id)));
  return inputs.concat(produced);
}
function deliverables(arts){
  return artifactCards(arts).filter(c=>c.kind==='deliverable');
}
/* --- Agent results (Phase 2): state.agents[<agent_id>] = {status, summary, task_id, step, data, data_keys}.
   The orchestrator does not know what an agent's data means; it keeps it within a byte budget so the
   next agent can read it from the case state served by Activity (Python twin: state_shape.slim_agent_data). */
const AGENT_DATA_BUDGET=24000;
const AGENT_DATA_KEY_BUDGET=12000;
function jsonSize(v){
  try{return JSON.stringify(v===undefined?null:v).length}catch(_){return Infinity}
}
function slimAgentData(data){
  if(!data||typeof data!=='object'||Array.isArray(data)) return {};
  const entries=Object.entries(data).filter(([k])=>k!=='omitted_keys').map(([k,v])=>({k,v,size:jsonSize(v)}));
  const keep=new Set();
  const omitted=[];
  let total=0;
  /* Smaller keys first: many small facts survive one oversized blob. */
  for(const e of entries.slice().sort((a,b)=>a.size-b.size)){
    if(e.size>AGENT_DATA_KEY_BUDGET||total+e.size>AGENT_DATA_BUDGET){omitted.push(e.k);continue;}
    keep.add(e.k);
    total+=e.size;
  }
  const out={};
  for(const e of entries){ if(keep.has(e.k)) out[e.k]=e.v; }
  const prevOmitted=Array.isArray(data.omitted_keys)?data.omitted_keys.map(String):[];
  const allOmitted=[...new Set([...prevOmitted,...omitted])];
  if(allOmitted.length) out.omitted_keys=allOmitted;
  return out;
}
function sanitizeAgents(agents){
  const src=agents&&typeof agents==='object'&&!Array.isArray(agents)?agents:{};
  const out={};
  for(const [id,raw] of Object.entries(src)){
    if(!raw||typeof raw!=='object'||Array.isArray(raw)) continue;
    const data=slimAgentData(raw.data);
    out[String(id)]={
      status:String(raw.status||''),
      summary:String(raw.summary||'').slice(0,400),
      task_id:String(raw.task_id||''),
      step:Number(raw.step||0)||0,
      data,
      data_keys:Object.keys(data).filter(k=>k!=='omitted_keys')
    };
  }
  return out;
}
function parseJsonish(v, fallback){
  if(v&&typeof v==='object'&&!Array.isArray(v)) return v;
  if(typeof v==='string'&&v.trim()){
    try{const p=JSON.parse(v); return p&&typeof p==='object'&&!Array.isArray(p)?p:fallback}catch(_){return fallback}
  }
  return fallback;
}
/* Registry row → how to call the agent. Deterministic; no agent name appears here.
   invoke {kind:'n8n_workflow', workflow_id} → route 'workflow' (executeWorkflow by id, id from an expression);
   invoke {kind:'http', url:'{math_url}/agent/run'} → route 'http' ({key} placeholders = Runtime Config fields);
   Runtime Config agent_workflow_ids {"<agent_id>":"<workflow id>"} overrides the workflow id (field binding
   after a UI import, where n8n assigns a new id). Anything else → 'unbound' with a reason code. */
function resolveInvoke(agentId, registry, endpoints){
  const id=String(agentId||'').trim();
  const rows=Array.isArray(registry)?registry:[];
  const row=rows.find(r=>r&&String(r.agent_id||'')===id)||null;
  const cfg=endpoints&&typeof endpoints==='object'?endpoints:{};
  const overrides=parseJsonish(cfg.agent_workflow_ids,{});
  const override=String(overrides[id]||'').trim();
  if(!row) return {route:'unbound',agent_id:id,reason:'not_in_registry'};
  if(row.enabled===false||String(row.enabled).toLowerCase()==='false') return {route:'unbound',agent_id:id,reason:'disabled'};
  const inv=parseJsonish(row.invoke,{});
  const kind=String(inv.kind||'').trim().toLowerCase();
  if(override||kind==='n8n_workflow'||(!kind&&inv.workflow_id)){
    const wid=override||String(inv.workflow_id||'').trim();
    if(!wid) return {route:'unbound',agent_id:id,reason:'no_workflow_id'};
    return {route:'workflow',agent_id:id,workflow_id:wid,workflow_name:String(inv.workflow_name||'')};
  }
  if(kind==='http'){
    const url=String(inv.url||'').trim().replace(/\{([a-z0-9_]+)\}/gi,(m,key)=>{
      const v=cfg[key];
      return v==null||v===''?m:String(v).replace(/\/$/,'');
    });
    if(!url||/\{[a-z0-9_]+\}/i.test(url)||!/^https?:\/\//i.test(url)) return {route:'unbound',agent_id:id,reason:'no_url'};
    return {route:'http',agent_id:id,url};
  }
  return {route:'unbound',agent_id:id,reason:'no_invoke'};
}
function unboundAgentMessage(resolved, registry){
  const r=resolved&&typeof resolved==='object'?resolved:{};
  const title=agentTitle(r.agent_id, registry);
  const known=Array.isArray(registry)&&registry.some(x=>x&&String(x.agent_id||'')===String(r.agent_id||''));
  const who=known?`Агент «${title}»`:'Выбранный агент';
  switch(String(r.reason||'')){
    case 'not_in_registry': return `${who} не найден в реестре агентов — вызвать его нельзя.`;
    case 'disabled': return `${who} отключён в реестре агентов.`;
    case 'no_workflow_id': return `${who} не привязан: в реестре нет идентификатора его рабочего процесса. Укажите его в реестре агентов или в настройках среды.`;
    case 'no_url': return `${who} не привязан: адрес его сервиса не задан в настройках среды.`;
    default: return `${who} не привязан: в реестре не описано, как его вызывать.`;
  }
}
function decodeHitlAnswer(v){
  if(v==null||typeof v!=='string') return v;
  const s=v.trim();
  if(!s) return v;
  if(!((s[0]==='{'&&s[s.length-1]==='}')||(s[0]==='['&&s[s.length-1]===']')||(s[0]==='"'&&s[s.length-1]==='"'))) return v;
  try{
    const p=JSON.parse(s);
    if(typeof p==='string'){
      const t=p.trim();
      if((t[0]==='{'&&t[t.length-1]==='}')||(t[0]==='['&&t[t.length-1]===']')){
        try{return JSON.parse(t);}catch{return p;}
      }
      return p;
    }
    return p;
  }catch{return v;}
}
function decodeHitlAnswers(answers){
  const src=answers&&typeof answers==='object'&&!Array.isArray(answers)?answers:{};
  const out={};
  for(const [k,v] of Object.entries(src)) out[k]=decodeHitlAnswer(v);
  return out;
}
function isUnlistedWellsGate(qid, question){
  const id=String(qid||'').toLowerCase();
  const q=String(question||'').toLowerCase();
  return id.includes('unlisted')||q.includes('unlisted')||q.includes('не из excel')||q.includes('лишн');
}
function optionValues(question){
  const opts=question&&Array.isArray(question.options)?question.options:[];
  return opts.map(o=>{
    if(o&&typeof o==='object'&&!Array.isArray(o)) return String(o.value||'').trim()||String(o.label||'').trim();
    return String(o||'').trim();
  }).filter(Boolean);
}
const INTERPRET_CONFIDENCE_FLOOR=0.8;
function applyInterpretedDecision(qid, question, rawAnswer, parsed){
  const conf=Number(parsed&&parsed.confidence);
  const decision=String((parsed&&parsed.decision)||'').trim().toLowerCase();
  const values=optionValues(question).map(v=>String(v||'').toLowerCase());
  const ok=decision&&values.indexOf(decision)>=0&&Number.isFinite(conf)&&conf>=INTERPRET_CONFIDENCE_FLOOR;
  if(!ok) return {accepted:false};
  const text=String((parsed&&parsed.paraphrase)||humanAnswerText(rawAnswer)||'').trim()||decision;
  const stored={choice:decision, text, label:text};
  if(isUnlistedWellsGate(qid, (question&&question.question)||'')&&(decision==='keep'||decision==='remove')) stored.unlisted_wells_policy=decision;
  return {accepted:true, answer:stored, confidence:conf};
}
function buildClarifyQuestion(question){
  const q=question&&typeof question==='object'?question:{};
  return {
    question_id:String(q.question_id||q.id||'Q-1'),
    kind:String(q.kind||'needs_input'),
    question:'Не удалось однозначно понять ответ. Выберите один из вариантов — или напишите короче, что сделать.',
    options:Array.isArray(q.options)?q.options:[]
  };
}
function normalizeHitlAnswer(qid, answer, question){
  /* Phase 1.3: no regex on free text. A button sets choice; anything else stays as the engineer wrote it
     until the Interpret free-text answer pass (or is stored as prose when the question has no options). */
  const decoded=decodeHitlAnswer(answer);
  if(decoded&&typeof decoded==='object'&&!Array.isArray(decoded)){
    const choice=String(decoded.choice||'').toLowerCase();
    if(isUnlistedWellsGate(qid, question)&&(choice==='keep'||choice==='remove')) return {...decoded, unlisted_wells_policy:choice};
    return decoded;
  }
  return decoded;
}
function readUnlistedWellsPolicy(answers){
  const src=answers&&typeof answers==='object'&&!Array.isArray(answers)?answers:{};
  for(const [key,val] of Object.entries(src)){
    if(!(val&&typeof val==='object'&&!Array.isArray(val))) continue;
    const direct=String(val.unlisted_wells_policy||'').toLowerCase();
    if(direct==='keep'||direct==='remove') return direct;
    const choice=String(val.choice||'').toLowerCase();
    if(isUnlistedWellsGate(key,'')&&(choice==='keep'||choice==='remove')) return choice;
  }
  return null;
}
/* Retrieval query for the routing slice: the task as the engineer, the orchestrator's plan and the journal
   describe it — plain text for the lexical + semantic branches. No regex-derived tags (O4): the tag branch
   gets nothing; the plan titles (LLM-written) are the topical hint. */
function buildRetrievalQuery(compact){
  const c=compact&&typeof compact==='object'?compact:{};
  const parts=[String(c.goal||'').trim()];
  const inputs=Array.isArray(c.inputs)?c.inputs:[];
  if(inputs.length) parts.push('Приложены файлы: '+inputs.map(i=>i&&(i.filename||i.artifact_id)).filter(Boolean).slice(0,8).join(', '));
  const planTitles=(Array.isArray(c.plan)?c.plan:[]).map(p=>p&&String(p.title||'').trim()).filter(Boolean);
  if(planTitles.length) parts.push('План: '+planTitles.slice(0,6).join('; '));
  const agents=c.agents&&typeof c.agents==='object'?c.agents:{};
  for(const [id,a] of Object.entries(agents)){
    if(a&&a.summary) parts.push(`${id}: ${String(a.summary).slice(0,160)}`);
  }
  const dels=Array.isArray(c.deliverables)?c.deliverables:[];
  if(dels.length) parts.push('Уже получены результаты: '+dels.map(d=>d.filename||d.artifact_id).filter(Boolean).join(', '));
  if(c.hitl_pending&&c.hitl_question) parts.push('Открытый вопрос инженеру: '+String(c.hitl_question).slice(0,160));
  return parts.filter(Boolean).join('\n').slice(0,800)||'маршрутизация инженерной задачи';
}
function slimCurrentTask(task,arts,agents){
  if(!task||typeof task!=='object') return null;
  if(!task.task_id&&!task.agent_id) return null;
  const ids=Array.isArray(task.artifact_ids)?task.artifact_ids:Object.keys(flattenArtifacts(arts||{})).filter(k=>k!=='diff');
  const keys=Array.isArray(task.data_keys)?task.data_keys:Object.keys(agents&&typeof agents==='object'&&!Array.isArray(agents)?agents:{});
  return {task_id:task.task_id||null,agent_id:task.agent_id||null,artifact_ids:ids,data_keys:keys};
}
function slimError(err){
  if(!err||typeof err!=='object') return err||null;
  const count=Number(err.count||0);
  return {message:err.message||'',agent_id:err.agent_id||null,count:Number.isFinite(count)?count:0};
}
/* Developer log («Лог» tab): which n8n execution wrote an event. Empty outside n8n (Node smokes). */
function execRef(){
  try{
    const ex=typeof $execution!=='undefined'&&$execution?$execution:{};
    const wf=typeof $workflow!=='undefined'&&$workflow?$workflow:{};
    return {execution_id:String(ex.id||''),workflow_id:String(wf.id||''),workflow_name:String(wf.name||'')};
  }catch{return {execution_id:'',workflow_id:'',workflow_name:''};}
}
/* Size-bounded copy for event payloads: full value when it fits, otherwise a JSON preview. */
function boundedForLog(value, limit){
  let text='';
  try{text=JSON.stringify(value===undefined?null:value);}catch{text=String(value);}
  if(text==null) return null;
  if(text.length<=limit) return value;
  return {truncated:true,length:text.length,preview:text.slice(0,limit)};
}
function mergeIncomingArtifacts(artifacts,incoming,producer){
  /* Everything an agent returns under `artifacts` is a deliverable of that agent (kind/producer are
     stamped here, agents do not know these fields). `excel_session` is a service handle, not an artifact. */
  const nested=nestArtifacts(artifacts);
  const src=incoming&&typeof incoming==='object'&&!Array.isArray(incoming)?incoming:{};
  const by=String(producer||'').trim();
  const stamp=(item)=>{
    if(!item||typeof item!=='object') return item;
    if(by){ item.producer=item.producer||by; item.kind=item.kind||'deliverable'; }
    return item;
  };
  for(const [k,v] of Object.entries(src)){
    if(k==='excel_session'){
      nested.excel=(nested.excel&&typeof nested.excel==='object')?{...nested.excel}:{artifact_id:'excel',role:'excel'};
      nested.excel.session_id=v;
      continue;
    }
    if(Array.isArray(v)&&(k==='includes'||k==='grdecl')){
      nested.schedule=nested.schedule||{};
      nested.schedule[k]=Array.isArray(nested.schedule[k])?nested.schedule[k]:[];
      for(const row of v){
        const item=stamp(fileItem(row&&row.artifact_id,row));
        if(item) nested.schedule[k].push(item);
      }
      continue;
    }
    if(k==='schedule_out'||k==='diff'){
      const item=stamp(fileItem(k,v,k));
      if(!item) continue;
      nested.schedule=nested.schedule||{};
      if(k==='schedule_out') nested.schedule.out=item; else nested.schedule.diff=item;
      continue;
    }
    const item=stamp(fileItem(k,v));
    if(!item) continue;
    const role=item.role||roleForArtifactId(k);
    if(role==='excel') nested.excel=item;
    else if(role==='surface') nested.surface=item;
    else if(role==='schedule_source'){ nested.schedule=nested.schedule||{}; nested.schedule.source=item; }
    else if(role==='schedule_include'){
      nested.schedule=nested.schedule||{};
      const name=String(item.filename||'').toLowerCase();
      if(name.endsWith('.grdecl')){
        nested.schedule.grdecl=Array.isArray(nested.schedule.grdecl)?nested.schedule.grdecl:[];
        nested.schedule.grdecl.push(item);
      } else {
        nested.schedule.includes=Array.isArray(nested.schedule.includes)?nested.schedule.includes:[];
        nested.schedule.includes.push(item);
      }
    }
    else if(role==='trajectory'){ nested.trajectories=Array.isArray(nested.trajectories)?nested.trajectories:[]; nested.trajectories.push(item); }
    else { nested.attachments=Array.isArray(nested.attachments)?nested.attachments:[]; nested.attachments.push(item); }
  }
  return nested;
}
function sanitizeState(state){
  const s=state&&typeof state==='object'?{...state}:{};
  const arts=s.artifacts&&typeof s.artifacts==='object'?{...s.artifacts}:{};
  if(arts.file&&!arts.excel) arts.excel=arts.file;
  if(arts.schedule_files&&!arts.schedule_source) arts.schedule_source=arts.schedule_files;
  s.artifacts=nestArtifacts(arts);
  /* Phase 2: agent results live in state.agents[<agent_id>]. state.data keeps only the case result
     (finish) and, for cases from before Phase 2, whatever legacy buckets they had — slimmed the same way. */
  s.agents=sanitizeAgents(s.agents);
  const data=s.data&&typeof s.data==='object'&&!Array.isArray(s.data)?{...s.data}:{};
  delete data.facts;
  for(const [k,v] of Object.entries(data)){
    if(k!=='result'&&v&&typeof v==='object'&&!Array.isArray(v)) data[k]=slimAgentData(v);
  }
  s.data=data;
  const hitl=s.hitl&&typeof s.hitl==='object'?{...s.hitl}:{pending:false,questions:[],answers:{}};
  hitl.answers=decodeHitlAnswers(hitl.answers||{});
  s.hitl=hitl;
  s.current_task=slimCurrentTask(s.current_task,s.artifacts,s.agents);
  if(s.last_error&&typeof s.last_error==='object') s.last_error=slimError(s.last_error);
  s.error_count=Number(s.error_count||(s.last_error&&s.last_error.count)||0)||0;
  s.ledger=sanitizeLedger(s.ledger);
  s.plan=sanitizePlan(s.plan);
  reconcileLedgerAnswers(s);
  return s;
}
function reconcileLedgerAnswers(state){
  /* Safety net for leftover cases from before Phase 1.3: if answers landed in state without a
     journal row (old Activity write-path), copy them in. The live path is orchestrator resume. */
  const hitl=state.hitl&&typeof state.hitl==='object'?state.hitl:{};
  const answers=hitl.answers&&typeof hitl.answers==='object'&&!Array.isArray(hitl.answers)?hitl.answers:{};
  const questions=Array.isArray(hitl.questions)?hitl.questions:[];
  const l=sanitizeLedger(state.ledger);
  const seen=new Set(l.history.filter(e=>e.kind==='human').map(e=>String(e.question_id||'')));
  let changed=false;
  for(const [qid,ans] of Object.entries(answers)){
    if(seen.has(String(qid))) continue;
    const q=questions.find(x=>x&&String(x.question_id||x.id||'')===String(qid))||{};
    const reviewAccept=isResultReviewGate(qid)&&humanAnswerChoice(ans)==='accept';
    ledgerPush(state,{kind:'human',step:Number(state.step_count||0),question_id:String(qid),question:String(q.question||'').slice(0,200),answer:humanAnswerText(ans).slice(0,300),...(reviewAccept?{review_accept:true}:{})});
    state.ledger.last_human_step=Number(state.step_count||0);
    state.ledger.stall_count=0;
    changed=true;
  }
  return changed;
}
function ledgerAnsweredAgentQuestion(state){
  /* An agent asked (needs_input), the human answered, and that agent has not run since:
     the answer has not been applied yet. Finishing now would drop the human's decision. */
  const h=sanitizeLedger(state.ledger).history;
  let pending=null;
  for(const e of h){
    if(e.kind==='agent'){
      if(e.status==='needs_input') pending={agent_id:e.agent_id,task_id:e.task_id||'',question:String(e.summary||''),answered:false,answer:''};
      else if(pending&&String(e.agent_id||'')===String(pending.agent_id)) pending=null;
    } else if(e.kind==='human'&&pending&&!isResultReviewGate(e.question_id)){
      pending.answered=true;
      pending.answer=String(e.answer||'');
    }
  }
  return pending&&pending.answered?pending:null;
}
/* --- Progress ledger (task journal) ---
   Domain-free record of what happened in the case: every agent result and every human answer.
   The Decision LLM reasons about completion from this journal (not from boolean flags), and
   Parse decision uses it for deterministic loop protection (same agent re-delegated after a
   completed result without new inputs → result review with the human, never a silent retry). */
const LEDGER_HISTORY_MAX=30;
function sanitizeLedger(raw){
  const l=raw&&typeof raw==='object'&&!Array.isArray(raw)?{...raw}:{};
  const history=Array.isArray(l.history)?l.history.filter(e=>e&&typeof e==='object'):[];
  return {
    history:history.slice(-LEDGER_HISTORY_MAX),
    stall_count:Number(l.stall_count||0)||0,
    last_human_step:Number(l.last_human_step||0)||0,
    reviews:Number(l.reviews||0)||0,
    verify_rejections:Number(l.verify_rejections||0)||0
  };
}
function ledgerPush(state, entry){
  const l=sanitizeLedger(state.ledger);
  l.history=[...l.history,{...entry,step:Number(entry.step||state.step_count||0)}].slice(-LEDGER_HISTORY_MAX);
  state.ledger=l;
  return l;
}
function ledgerAgentEntries(state, agentId){
  const l=sanitizeLedger(state.ledger);
  return l.history.filter(e=>e.kind==='agent'&&(!agentId||String(e.agent_id||'')===String(agentId)));
}
function ledgerLastAgentEntry(state, agentId){
  const rows=ledgerAgentEntries(state, agentId);
  return rows.length?rows[rows.length-1]:null;
}
function ledgerHasNewInputsSince(state, entry){
  /* True when a human answered (text / choice / files) after the given journal entry. */
  const l=sanitizeLedger(state.ledger);
  const idx=l.history.indexOf(entry);
  const tail=idx>=0?l.history.slice(idx+1):l.history;
  return tail.some(e=>e.kind==='human');
}
function ledgerHumanAccepted(state){
  const l=sanitizeLedger(state.ledger);
  for(let i=l.history.length-1;i>=0;i--){
    const e=l.history[i];
    if(e.kind==='agent') return false;
    if(e.kind==='human'&&e.review_accept===true) return true;
  }
  return false;
}
function humanAnswerText(answer){
  if(answer==null) return '';
  if(typeof answer==='string') return answer;
  if(typeof answer!=='object'||Array.isArray(answer)) return String(answer);
  for(const key of ['text','label','answer','value','choice']){
    if(answer[key]!=null&&answer[key]!=='') return String(answer[key]);
  }
  try{return JSON.stringify(answer).slice(0,200)}catch{return ''}
}
function humanAnswerChoice(answer){
  if(!answer||typeof answer!=='object'||Array.isArray(answer)) return '';
  return String(answer.choice||'').trim().toLowerCase();
}
function isResultReviewGate(qid){
  return String(qid||'').indexOf('result_review')===0;
}
function agentTitle(agentId, registry){
  const rows=Array.isArray(registry)?registry:[];
  const hit=rows.find(r=>r&&String(r.agent_id||'')===String(agentId||''));
  return (hit&&hit.title)||String(agentId||'агент');
}
/* Engineer-facing text must be prose: no snake_case ids, key=value pairs, JSON braces or pipes. */
function looksMachineText(text){
  const s=String(text||'');
  if(!s.trim()) return false;
  return /[a-z]+_[a-z_]+|[a-z_]+=[a-z0-9]+|[{}\[\]]|\w\|\w/.test(s)||!/[А-Яа-яЁё]{3,}/.test(s);
}
function composeDoneSummary(state, registry){
  const done=ledgerAgentEntries(state).filter(e=>e.status==='completed'&&String(e.summary||'').trim());
  if(!done.length) return '';
  const seen=new Set();
  const parts=[];
  for(const e of done){
    const s=String(e.summary||'').trim().replace(/\s+/g,' ');
    if(seen.has(s)) continue;
    seen.add(s);
    parts.push(`${agentTitle(e.agent_id, registry)}: ${s}`);
  }
  return parts.join(' ');
}
function buildResultReviewQuestion(state, agentId, registry, reason){
  const last=ledgerLastAgentEntry(state, agentId);
  const summary=String((last&&last.summary)||'').trim();
  const title=agentTitle(agentId, registry);
  const lead=reason==='step_limit'
    ?'Оркестратор исчерпал лимит шагов. '
    :`${title} уже выполнил эту задачу, новых данных с тех пор не появилось. `;
  const body=summary?`Результат: ${summary}. `:'';
  return {
    question_id:`result_review_${Number(state.step_count||0)}`,
    kind:'result_approval',
    question:`${lead}${body}Принять результат как итог задачи или нужна доработка? Если доработка — напишите, что именно не так.`,
    options:[
      {value:'accept',label:'Принять результат'},
      {value:'rework',label:'Нужна доработка — опишу ниже'}
    ]
  };
}
/* Completion check (a second LLM pass) said the proposed finish leaves parts of the goal uncovered,
   and the orchestrator already had one more step to close them: let the engineer decide. */
function buildCompletionReviewQuestion(state, registry, uncovered, verdict){
  const done=composeDoneSummary(state, registry);
  const gaps=(Array.isArray(uncovered)?uncovered:[]).map(s=>String(s||'').trim()).filter(Boolean);
  const gapText=gaps.length?`Не хватает: ${gaps.join('; ')}. `:'';
  const doneText=done?`Сделано: ${done}. `:'';
  const lead=String(verdict||'').trim()&&!looksMachineText(verdict)?`${String(verdict).trim().replace(/\.?$/,'.')} `:'';
  return {
    question_id:`result_review_${Number(state.step_count||0)}`,
    kind:'result_approval',
    question:`Оркестратор считает задачу выполненной, но проверка нашла пробел. ${lead}${doneText}${gapText}Принять результат как есть или продолжить работу? Если продолжить — напишите, что именно нужно получить.`,
    options:[
      {value:'accept',label:'Принять результат как есть'},
      {value:'rework',label:'Продолжить — опишу ниже'}
    ]
  };
}
function compactLedger(state){
  const l=sanitizeLedger(state.ledger);
  const rows=l.history.slice(-8).map(e=>{
    if(e.kind==='human'){
      return {step:e.step,kind:'human',question:String(e.question||'').slice(0,120),answer:String(e.answer||'').slice(0,160),...(e.review_accept?{review_accept:true}:{})};
    }
    if(e.kind==='verification'){
      return {step:e.step,kind:'verification',verdict:'rejected',uncovered:(Array.isArray(e.uncovered)?e.uncovered:[]).slice(0,6).map(s=>String(s).slice(0,160))};
    }
    return {
      step:e.step,kind:'agent',agent_id:e.agent_id||null,task_id:e.task_id||null,status:e.status||null,
      summary:String(e.summary||'').slice(0,240),
      artifacts_added:Array.isArray(e.artifacts_added)?e.artifacts_added.slice(0,8):[],
      ...(e.rework_reason?{rework_reason:String(e.rework_reason).slice(0,160)}:{})
    };
  });
  return {history:rows,stall_count:l.stall_count,completed_agents:[...new Set(l.history.filter(e=>e.kind==='agent'&&e.status==='completed').map(e=>e.agent_id).filter(Boolean))]};
}
/* Plan (O13): the orchestrator's own decomposition of the goal — a short list of results to obtain, each
   optionally owned by an agent. The Decision LLM writes it through `plan_update`; deterministic code keeps
   it in step with what actually happened (call_agent → active, agent completed → done) and the finish guard
   treats open items as uncovered parts. Python twin: state_shape.sanitize_plan / plan_open_items. */
const PLAN_STATUSES=__PLAN_STATUSES__;
const PLAN_OPEN_STATUSES=__PLAN_OPEN_STATUSES__;
const PLAN_MAX_ITEMS=12;
function sanitizePlanItem(item){
  if(!item||typeof item!=='object'||Array.isArray(item)) return null;
  const id=String(item.id==null?'':item.id).trim().slice(0,40);
  if(!id) return null;
  const out={id,title:String(item.title||'').trim().slice(0,200),status:PLAN_STATUSES.includes(String(item.status||''))?String(item.status):'pending'};
  const agentId=String(item.agent_id||'').trim();
  if(agentId) out.agent_id=agentId;
  const deps=(Array.isArray(item.depends_on)?item.depends_on:[]).map(d=>String(d==null?'':d).trim()).filter(Boolean).slice(0,8);
  if(deps.length) out.depends_on=deps;
  const note=String(item.note||'').trim().slice(0,300);
  if(note) out.note=note;
  return out;
}
function sanitizePlan(plan){
  const out=[];const seen=new Set();
  for(const raw of (Array.isArray(plan)?plan:[])){
    const item=sanitizePlanItem(raw);
    if(!item||seen.has(item.id)) continue;
    seen.add(item.id); out.push(item);
    if(out.length>=PLAN_MAX_ITEMS) break;
  }
  return out;
}
/* agent_id as the model wrote it → registry id (the model tends to write the agent's title instead). */
function planAgentId(value, registry){
  const v=String(value||'').trim();
  if(!v) return '';
  const rows=Array.isArray(registry)?registry:[];
  if(!rows.length) return v;
  const byId=rows.find(r=>r&&String(r.agent_id||'')===v);
  if(byId) return v;
  const byTitle=rows.find(r=>r&&String(r.title||'').trim().toLowerCase()===v.toLowerCase());
  return byTitle?String(byTitle.agent_id):'';
}
/* Merge `plan_update` into state.plan. Items match by id; an item without id is rejected — the model wrote
   «step 1 — agent X» prose instead of a plan row (CASE-6a9fa129-5ae077: the whole plan was lost that way).
   Fields the update omits keep their previous value. Returns {accepted, rejected}. */
function applyPlanUpdate(state, updates, registry){
  const plan=sanitizePlan(state.plan);
  const by=new Map(plan.map(p=>[p.id,p]));
  let accepted=0, rejected=0;
  for(const raw of (Array.isArray(updates)?updates:[])){
    const item=sanitizePlanItem(raw);
    if(!item){rejected+=1;continue;}
    const prev=by.get(item.id);
    if(!prev&&by.size>=PLAN_MAX_ITEMS){rejected+=1;continue;}
    const merged={...(prev||{}),...item};
    if(!item.title&&prev&&prev.title) merged.title=prev.title;
    if((raw.status===undefined||raw.status===null||raw.status==='')&&prev) merged.status=prev.status;
    if(!merged.title) merged.title=merged.id;
    const agentId=planAgentId(merged.agent_id, registry);
    if(agentId) merged.agent_id=agentId; else delete merged.agent_id;
    by.set(item.id,merged); accepted+=1;
  }
  state.plan=[...by.values()];
  return {accepted,rejected};
}
/* Actions drive statuses (flags are the model's habit, actions are its intent): the first pending item of
   the agent being called becomes active; when that agent returns completed its active items are done. */
function planMarkAgentActive(state, agentId){
  const plan=sanitizePlan(state.plan);
  const id=String(agentId||'').trim();
  if(id&&!plan.some(p=>p.agent_id===id&&p.status==='active')){
    const next=plan.find(p=>p.agent_id===id&&p.status==='pending');
    if(next) next.status='active';
  }
  state.plan=plan;
}
function planMarkAgentDone(state, agentId){
  const plan=sanitizePlan(state.plan);
  const id=String(agentId||'').trim();
  for(const p of plan){ if(id&&p.agent_id===id&&p.status==='active') p.status='done'; }
  state.plan=plan;
}
function planOpenItems(plan){
  return sanitizePlan(plan).filter(p=>PLAN_OPEN_STATUSES.includes(p.status));
}
/* The plan as the Decision LLM (and the completion check) read it in planner_input. */
function planLines(plan){
  return sanitizePlan(plan).map(p=>{
    const agent=p.agent_id?` — agent_id: ${p.agent_id}`:'';
    const deps=(p.depends_on||[]).length?` (после: ${p.depends_on.join(', ')})`:'';
    const note=p.note?` — ${p.note}`:'';
    return `- ${p.id} [${p.status}] ${p.title}${agent}${deps}${note}`;
  });
}
/* RAG tag branch from the plan (no regex over the goal): the open plan items name agents, the registry
   names what those agents consume/produce (input_required / output_provides — artifact roles and data
   keys). Routing cards carry the same role words in `topics`, so the cards for the agents the model
   intends to use are boosted. Empty plan → empty tags → lexical/semantic branches only. */
function planRetrievalTopics(plan, registry){
  const rows=Array.isArray(registry)?registry:[];
  const out=[];
  for(const p of planOpenItems(plan)){
    const row=p.agent_id?rows.find(r=>r&&String(r.agent_id||'')===p.agent_id):null;
    if(!row) continue;
    for(const key of ['input_required','output_provides']){
      let vals=row[key];
      if(typeof vals==='string'){ try{vals=JSON.parse(vals);}catch{vals=[];} }
      for(const v of (Array.isArray(vals)?vals:[])){
        const tag=String(v||'').trim().toLowerCase();
        if(tag&&!out.includes(tag)) out.push(tag);
      }
    }
  }
  return out.slice(0,12);
}
/* One agent_result → case state. Used by `Merge agent result` (synchronous agents) and by the
   `resume source=agent` path (long agents that returned `in_progress` and finish later through the
   Activity run endpoint). Domain-free: it knows statuses and slots, not agents.
   Returns {status, next_status, should_continue, events, message}; the caller persists state/events. */
function applyAgentResult(state, agentId, taskId, result, registry){
  const obj=v=>v&&typeof v==='object'&&!Array.isArray(v);
  const res=obj(result)?result:{};
  const status=String(res.status||'completed');
  const title=agentTitle(agentId, registry);
  const fallback=status==='completed'?`Агент «${title}» завершил задачу.`
    :status==='needs_input'?`Агент «${title}» запросил дополнительные данные.`
    :status==='in_progress'?`Агент «${title}» работает над задачей; результат придёт позже.`
    :`Агент «${title}» вернул ошибку.`;
  const message=String(res.message||'').trim()||fallback;
  const agents=obj(state.agents)?{...state.agents}:{};
  const prevSlot=obj(agents[agentId])?agents[agentId]:{};
  const step=Number(state.step_count||0);
  const artifactsBefore=new Set(Object.keys(flattenArtifacts(state.artifacts||{})));
  const events=[];
  let nextStatus='running';
  let shouldContinue=true;
  if(status==='completed'){
    agents[agentId]={status,summary:message.slice(0,400),task_id:taskId,step,data:obj(res.data)?res.data:{}};
    state.artifacts=mergeIncomingArtifacts(state.artifacts, res.artifacts||{}, agentId);
    state.current_task=null;
    state.last_error=null;
    state.error_count=0;
    events.push({kind:'agent.result',actor:agentId||'agent',agent_id:agentId,task_id:taskId,status:'completed',status_message:message,
      payload:{data_keys:Object.keys(obj(res.data)?res.data:{}),artifacts:Object.keys(obj(res.artifacts)?res.artifacts:{}),deliverables:deliverables(state.artifacts).filter(d=>d.producer===agentId),
        issues:(Array.isArray(res.issues)?res.issues:[]).slice(0,6),assumptions:(Array.isArray(res.assumptions)?res.assumptions:[]).slice(0,6),...execRef()}});
  } else if(status==='in_progress'){
    /* Long job: the case waits for an external event, no step is spent, finish is impossible until the
       agent reports completed. `watch` is whatever the agent wants shown/polled (kind, ref, poll_hint). */
    nextStatus='waiting_agent';
    shouldContinue=false;
    agents[agentId]={status,summary:message.slice(0,400),task_id:taskId,step,data:obj(prevSlot.data)?prevSlot.data:{}};
    state.current_task={task_id:taskId,agent_id:agentId};
    events.push({kind:'agent.progress',actor:agentId||'agent',agent_id:agentId,task_id:taskId,status:'waiting_agent',status_message:message,payload:{watch:obj(res.watch)?res.watch:{},...execRef()}});
  } else if(status==='needs_input'){
    nextStatus='waiting_user';
    shouldContinue=false;
    const reqs=Array.isArray(res.requests)?res.requests:[];
    const q=reqs[0]||{question_id:'Q-agent',question:message,options:[]};
    state.hitl={pending:true,questions:reqs.length?reqs:[q],answers:(state.hitl&&state.hitl.answers)||{}};
    events.push({kind:'hitl.request',actor:'orchestrator',agent_id:agentId,status:'waiting_user',status_message:q.question,payload:{...q,...execRef()}});
  } else {
    const prevErr=obj(state.last_error)?state.last_error:{};
    const sameAgent=Boolean(agentId)&&String(prevErr.agent_id||'')===agentId;
    const errorCount=(sameAgent?Number(prevErr.count||state.error_count||0):0)+1;
    state.error_count=errorCount;
    state.last_error={message,agent_id:agentId,count:errorCount};
    /* A failed attempt is recorded in the slot (status + summary) but never overwrites data a previous
       completed run of the same agent produced. */
    agents[agentId]={...prevSlot,status:'failed',summary:message.slice(0,400),task_id:taskId,step,data:obj(prevSlot.data)?prevSlot.data:{}};
    events.push({kind:'agent.failed',actor:agentId||'agent',agent_id:agentId,status:'failed',status_message:message,payload:{message,issues:res.issues||[],error_count:errorCount,...execRef()}});
    if(errorCount>=3){
      nextStatus='failed';
      shouldContinue=false;
      events.push({kind:'case.failed',actor:'orchestrator',status:'failed',status_message:`Агент «${title}» вернул ошибку ${errorCount} раза подряд`,payload:{agent_id:agentId,error_count:errorCount,...execRef()}});
    }
  }
  const artifactsAdded=Object.keys(flattenArtifacts(state.artifacts||{})).filter(k=>!artifactsBefore.has(k));
  ledgerPush(state,{kind:'agent',step,agent_id:agentId,task_id:taskId,status,summary:message.slice(0,400),artifacts_added:artifactsAdded,data_keys:Object.keys(obj(res.data)?res.data:{}).slice(0,12)});
  if(status==='completed') planMarkAgentDone(state, agentId);
  state.agents=sanitizeAgents(agents);
  return {status,next_status:nextStatus,should_continue:shouldContinue,events,message};
}
const COMPACT_INPUTS_MAX=12;
function compactAgents(agents){
  const out={};
  for(const [id,a] of Object.entries(sanitizeAgents(agents))){
    out[id]={status:a.status,step:a.step,summary:String(a.summary||'').slice(0,240),data_keys:a.data_keys};
  }
  return out;
}
/* What the Decision LLM sees about the case. Domain-free: files by role (data-model vocabulary, not
   rules), the engineer's inputs by name, agent results by agent_id, the journal. Python twin:
   state_shape.compact_decision_context. */
function buildCompact(state){
  const artifacts=state.artifacts||{};
  const plan=sanitizePlan(state.plan);
  const hitl=state.hitl||{};
  const counts=fileCounts(artifacts);
  const questions=Array.isArray(hitl.questions)?hitl.questions:[];
  const q0=questions[0]&&typeof questions[0]==='object'?questions[0]:{};
  const pending=hitl.pending===true;
  const inputCards=artifactCards(artifacts).filter(c=>c.kind==='input');
  const err=state.last_error;
  const cur=state.current_task;
  return {
    goal:String(state.goal||'').slice(0,500),
    task_name:state.task_name||'',
    status:state.status||'',
    files:counts,
    inputs:inputCards.slice(0,COMPACT_INPUTS_MAX).map(c=>({artifact_id:c.artifact_id,role:c.role,filename:c.filename})),
    inputs_total:inputCards.length,
    deliverables:deliverables(artifacts).map(d=>({producer:d.producer,artifact_id:d.artifact_id,filename:d.filename})),
    agents:compactAgents(state.agents),
    plan:plan.map(p=>({id:p.id,title:p.title,status:p.status,...(p.agent_id?{agent_id:p.agent_id}:{})})),
    current_task:cur&&typeof cur==='object'?{task_id:cur.task_id||null,agent_id:cur.agent_id||null}:null,
    hitl_pending:pending,
    hitl_question:pending?(String(q0.question||'').slice(0,200)||null):null,
    hitl_answer_ids:Object.keys(hitl.answers||{}),
    unlisted_wells_policy:readUnlistedWellsPolicy(hitl.answers),
    step_count:Number(state.step_count||0),
    version:Number(state.version||0),
    last_error:(err&&typeof err==='object'?err.message:null)||null,
    journal:compactLedger(state)
  };
}
/* mas_state_utils end */
"""

STATE_SHAPE_JS = _STATE_SHAPE_JS_TEMPLATE.replace("__PLAN_STATUSES__", json.dumps(list(PLAN_STATUSES))).replace(
    "__PLAN_OPEN_STATUSES__", json.dumps(list(PLAN_OPEN_STATUSES))
)
