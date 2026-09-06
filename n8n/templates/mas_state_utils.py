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

STATE_SHAPE_MARKER_BEGIN = "/* mas_state_utils begin */"
STATE_SHAPE_MARKER_END = "/* mas_state_utils end */"

STATE_SHAPE_JS = r"""
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
function fileItem(id,value,role){
  if(value==null||value===''||(typeof value==='object'&&!Array.isArray(value)&&!Object.keys(value).length)) return null;
  const resolved=role||roleForArtifactId(id);
  if(resolved==='diff') return null;
  if(typeof value==='string'){
    if(resolved==='schedule_out') return {artifact_id:id||'schedule_out',role:'schedule_out',bytes:value.length,text:value};
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
    if(fallbackId==='diff'){ if(value!=null) out.diff=value; return; }
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
    if(sch.diff!=null) out.diff=sch.diff;
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
    if(id==='diff'){ nested.schedule=nested.schedule||{}; nested.schedule.diff=item; continue; }
    if(!item||typeof item!=='object') continue;
    const role=item.role||roleForArtifactId(id);
    if(role==='excel') nested.excel=item;
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
    if(id==='diff') continue;
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
function hasScheduleOut(arts){
  const item=flattenArtifacts(arts).schedule_out;
  if(!item) return false;
  if(typeof item==='string') return Boolean(String(item).trim());
  if(typeof item==='object') return Boolean(item.text||item.content||item.filename||item.artifact_id);
  return true;
}
function slimExcel(d){
  if(!d||typeof d!=='object'||Array.isArray(d)) return d;
  const out={...d};
  if(Array.isArray(d.facts)){
    out.facts=d.facts.filter(x=>x&&typeof x==='object').map(f=>({well:f.well,date:f.date}));
  }
  if(Array.isArray(d.normalized_rows)){
    out.normalized_rows=d.normalized_rows.filter(t=>t&&typeof t==='object').map(t=>{
      const prev=Array.isArray(t.preview)?t.preview:[];
      const count=t.preview_count!=null?Number(t.preview_count):(t.row_count!=null?Number(t.row_count):prev.length);
      return {table_id:t.table_id,columns:t.columns||[],row_count:t.row_count!=null?t.row_count:count,preview_count:count,preview:prev.slice(0,3)};
    });
  }
  return out;
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
function parseKeepRemove(blob, keyed){
  /* Same rules as Python parse_keep_remove: labeled enum, then gated keep/remove. */
  const s=String(blob||'').toLowerCase();
  const labeled=s.match(/unlisted_wells_policy\s*[:=]\s*(keep|remove)/);
  if(labeled) return labeled[1];
  const keyedOk=keyed===true||s.includes('unlisted')||s.includes('лишн')||s.includes('не из excel');
  if(!keyedOk) return '';
  if(/остав|сохран/.test(s)||/\bkeep\b/.test(s)) return 'keep';
  if(/убер|удал|выкин|выкинь/.test(s)||/\bremove\b/.test(s)) return 'remove';
  return '';
}
function normalizeHitlAnswer(qid, answer, question){
  const decoded=decodeHitlAnswer(answer);
  if(!isUnlistedWellsGate(qid, question)) return decoded;
  if(decoded&&typeof decoded==='object'&&!Array.isArray(decoded)){
    /* Activity option button: {choice:'keep'|'remove', text:'<label>'} */
    const choice=String(decoded.choice||'').toLowerCase();
    if(choice==='keep'||choice==='remove') return {...decoded, unlisted_wells_policy:choice};
    const p=parseKeepRemove(decoded.unlisted_wells_policy||'', true)||parseKeepRemove(decoded.raw!=null?decoded.raw:JSON.stringify(decoded), true);
    if(p) return {unlisted_wells_policy:p, raw:decoded.raw!=null?decoded.raw:decoded};
  }
  const p=parseKeepRemove(decoded, true);
  if(p) return {unlisted_wells_policy:p, raw:decoded};
  return decoded;
}
function readUnlistedWellsPolicy(answers){
  const src=answers&&typeof answers==='object'&&!Array.isArray(answers)?answers:{};
  for(const [key,val] of Object.entries(src)){
    if(val&&typeof val==='object'&&!Array.isArray(val)){
      const direct=String(val.unlisted_wells_policy||'').toLowerCase();
      if(direct==='keep'||direct==='remove') return direct;
      const choice=String(val.choice||'').toLowerCase();
      if(isUnlistedWellsGate(key,'')&&(choice==='keep'||choice==='remove')) return choice;
      const nested=parseKeepRemove(val.raw!=null?val.raw:JSON.stringify(val), isUnlistedWellsGate(key,''));
      if(nested) return nested;
      continue;
    }
    const keyed=isUnlistedWellsGate(key,'');
    const found=parseKeepRemove(val, keyed);
    if(found) return found;
  }
  return null;
}
function inferRetrievalQuery(compact){
  const c=compact&&typeof compact==='object'?compact:{};
  const facts=Number(c.excel_facts||0);
  const parts=[String(c.goal||'').trim()];
  if(facts>0) parts.push('Извлечено '+facts+' скважин из Excel');
  else if(c.has_excel) parts.push('В artifacts есть Excel, фактов ещё нет');
  if(c.has_schedule_source&&!c.has_schedule_out) parts.push('Нужно обновить baseline SCHEDULE');
  if(c.has_schedule_out) parts.push('SCHEDULE уже собран');
  return parts.filter(Boolean).join('\n').slice(0,800)||'маршрутизация инженерной задачи petroleum-engineering';
}
function inferRoutingTopics(compact){
  const c=compact&&typeof compact==='object'?compact:{};
  const files=c.files&&typeof c.files==='object'?c.files:{};
  const out=[];
  if(c.has_excel) out.push('Excel');
  if(c.has_schedule_source) out.push('SCHEDULE');
  if(c.has_excel&&c.has_schedule_source) out.push('handoff','control-plane');
  if((Number(files.trajectories)||0)>0||(Number(files.surface)||0)>0) out.push('geometry');
  if(c.hitl_pending) out.push('HITL','required-evidence');
  if(!out.length) out.push('handoff','control-plane');
  return [...new Set(out)];
}
function inferRoutingKeywordFamilies(compact){
  const c=compact&&typeof compact==='object'?compact:{};
  const files=c.files&&typeof c.files==='object'?c.files:{};
  const goal=String(c.goal||'').toLowerCase();
  const out=[];
  if(c.has_excel) out.push('XLSX','EXCEL_EXTRACTOR');
  if(c.has_schedule_source) out.push('INC','SCHEDULE_BUILDER');
  if((Number(files.trajectories)||0)>0||(Number(files.surface)||0)>0) out.push('CALCULATION_AGENT','DEV','CPS3');
  if(c.hitl_pending) out.push('HITL','REQUIRED-EVIDENCE');
  if(/дат|ввод|запуск|комиссион|commission/.test(goal)) out.push('COMMISSIONING');
  if(/групп|gruptree|gconprod|перепривяз/.test(goal)) out.push('GROUP_CONTROL');
  return [...new Set(out)];
}
function inferTaskPatterns(compact){
  const c=compact&&typeof compact==='object'?compact:{};
  const files=c.files&&typeof c.files==='object'?c.files:{};
  const goal=String(c.goal||'').toLowerCase();
  const out=[];
  const hasDate=/дат|ввод|запуск|комиссион|commission/.test(goal);
  const hasGroup=/групп|gruptree|gconprod|перепривяз/.test(goal);
  const hasTraj=/перфорац|траектори|пересечен|welltrack|compdatmd/.test(goal);
  const hasExtract=/извлечь|extract|таблиц|xlsx|excel/.test(goal);
  if(hasDate) out.push('новые даты ввода скважин','даты ввода','сдвиг дат','собрать новый schedule.inc','построить прогнозный SCHEDULE','revise schedule');
  if(hasGroup) out.push('перепривязка скважин в группу','перепривязка групп','групповой контроль');
  if(hasTraj) out.push('пересечение траектории','well trajectory intersection','перфорация','траектория');
  if(hasExtract||(c.has_excel&&!c.has_schedule_source)) out.push('извлечь таблицу','extract workbook');
  if(!out.length){
    if(c.has_excel&&c.has_schedule_source) out.push('новые даты ввода скважин','даты ввода','сдвиг дат','собрать новый schedule.inc','построить прогнозный SCHEDULE','revise schedule');
    else if(c.has_schedule_source&&!c.has_excel) out.push('перепривязка скважин в группу','перепривязка групп','групповой контроль','построить прогнозный SCHEDULE','revise schedule');
    else if(c.has_excel) out.push('извлечь таблицу','extract workbook');
    if((Number(files.trajectories)||0)>0&&(Number(files.surface)||0)>0) out.push('пересечение траектории','well trajectory intersection','перфорация','траектория');
  }
  if(c.hitl_pending) out.push('не хватает исходных данных');
  return [...new Set(out)];
}
function slimCurrentTask(task,arts,data){
  if(!task||typeof task!=='object') return null;
  if(!task.task_id&&!task.agent_id) return null;
  const ids=Array.isArray(task.artifact_ids)?task.artifact_ids:Object.keys(flattenArtifacts(arts||{})).filter(k=>k!=='diff');
  const keys=Array.isArray(task.data_keys)?task.data_keys:Object.keys(data&&typeof data==='object'?data:{});
  return {task_id:task.task_id||null,agent_id:task.agent_id||null,artifact_ids:ids,data_keys:keys};
}
function slimError(err){
  if(!err||typeof err!=='object') return err||null;
  const count=Number(err.count||0);
  return {message:err.message||'',agent_id:err.agent_id||null,count:Number.isFinite(count)?count:0};
}
function mergeIncomingArtifacts(artifacts,incoming){
  const nested=nestArtifacts(artifacts);
  const src=incoming&&typeof incoming==='object'&&!Array.isArray(incoming)?incoming:{};
  for(const [k,v] of Object.entries(src)){
    if(k==='diff'){ nested.schedule=nested.schedule||{}; nested.schedule.diff=v; continue; }
    if(k==='excel_session'){
      nested.excel=(nested.excel&&typeof nested.excel==='object')?{...nested.excel}:{artifact_id:'excel',role:'excel'};
      nested.excel.session_id=v;
      continue;
    }
    if(Array.isArray(v)&&(k==='includes'||k==='grdecl')){
      nested.schedule=nested.schedule||{};
      nested.schedule[k]=Array.isArray(nested.schedule[k])?nested.schedule[k]:[];
      for(const row of v){
        const item=fileItem(row&&row.artifact_id,row);
        if(item) nested.schedule[k].push(item);
      }
      continue;
    }
    if(k==='schedule_out'){
      nested.schedule=nested.schedule||{};
      nested.schedule.out=typeof v==='string'?{artifact_id:'schedule_out',role:'schedule_out',bytes:String(v).length,text:v}:(fileItem('schedule_out',v,'schedule_out')||v);
      continue;
    }
    const item=fileItem(k,v);
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
  const data=s.data&&typeof s.data==='object'&&!Array.isArray(s.data)?{...s.data}:{};
  delete data.facts;
  if(data.excel) data.excel=slimExcel(data.excel);
  s.data=data;
  const hitl=s.hitl&&typeof s.hitl==='object'?{...s.hitl}:{pending:false,questions:[],answers:{}};
  hitl.answers=decodeHitlAnswers(hitl.answers||{});
  s.hitl=hitl;
  s.current_task=slimCurrentTask(s.current_task,s.artifacts,s.data);
  if(s.last_error&&typeof s.last_error==='object') s.last_error=slimError(s.last_error);
  s.error_count=Number(s.error_count||(s.last_error&&s.last_error.count)||0)||0;
  s.ledger=sanitizeLedger(s.ledger);
  reconcileLedgerAnswers(s);
  return s;
}
function reconcileLedgerAnswers(state){
  /* Activity writes HITL answers straight into state.hitl.answers and then calls action=step
     (the orchestrator's own resume path is only one of two write paths). Any answer that is not
     yet in the journal is appended here, so guards and the LLM see it regardless of the path. */
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
    reviews:Number(l.reviews||0)||0
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
function compactLedger(state){
  const l=sanitizeLedger(state.ledger);
  const rows=l.history.slice(-8).map(e=>{
    if(e.kind==='human'){
      return {step:e.step,kind:'human',question:String(e.question||'').slice(0,120),answer:String(e.answer||'').slice(0,160),...(e.review_accept?{review_accept:true}:{})};
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
function buildCompact(state){
  const artifacts=state.artifacts||{};
  const data=state.data||{};
  const plan=Array.isArray(state.plan)?state.plan:[];
  const hitl=state.hitl||{};
  const counts=fileCounts(artifacts);
  const excel=data.excel&&typeof data.excel==='object'?data.excel:{};
  const facts=Array.isArray(excel.facts)?excel.facts:[];
  const questions=Array.isArray(hitl.questions)?hitl.questions:[];
  const q0=questions[0]&&typeof questions[0]==='object'?questions[0]:{};
  const pending=hitl.pending===true;
  const flat=flattenArtifacts(artifacts);
  const excelMeta=flat.excel&&typeof flat.excel==='object'?flat.excel:{};
  const sourceMeta=flat.schedule_source&&typeof flat.schedule_source==='object'?flat.schedule_source:{};
  const err=state.last_error;
  const cur=state.current_task;
  return {
    goal:String(state.goal||'').slice(0,500),
    task_name:state.task_name||'',
    status:state.status||'',
    files:counts,
    has_excel:counts.excel>0,
    has_schedule_source:counts.schedule_source>0,
    has_schedule_out:counts.schedule_out>0,
    excel_filename:excelMeta.filename||null,
    schedule_source_filename:sourceMeta.filename||null,
    excel_facts:facts.length,
    wells_in_excel:facts.map(f=>f&&f.well).filter(Boolean).slice(0,20),
    schedule_root:state.schedule_root||'',
    plan:plan.map(p=>({id:p.id,status:p.status})),
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
