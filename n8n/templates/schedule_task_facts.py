"""Observe SCHEDULE packet facts from the task — not combat fixtures.

Petroleum profile defaults (tNavigator 22.2 METRIC) fill empty fields only.
Allowlisted keywords and change operation come from task prose / structured
scope already present. Baseline .inc bodies are not scanned for scope.
"""
from __future__ import annotations

import json


def build_schedule_task_facts_js(keywords: list[str]) -> str:
    allowed = json.dumps(keywords, ensure_ascii=False)
    return SCHEDULE_TASK_FACTS_JS.replace("__KEYWORDS__", allowed).strip()


SCHEDULE_TASK_FACTS_JS = r"""
const SCHEDULE_KEYWORD_ORDER=__KEYWORDS__;
const SCHEDULE_ALLOWLIST=new Set(SCHEDULE_KEYWORD_ORDER);
const SCHEDULE_ENGLISH_COLLISION=new Set(['DATES','INCLUDE']);
const petroleumProfileDefaults=()=>({vendor:'Rock Flow Dynamics',simulator:'tNavigator',version:'22.2',unit_system:'METRIC'});
const applyPetroleumProfile=profile=>{
  const p=profile&&typeof profile==='object'&&!Array.isArray(profile)?profile:{};
  const d=petroleumProfileDefaults();
  const unit=String(p.unit_system||'').trim().toUpperCase();
  return{
    vendor:String(p.vendor||'').trim()||d.vendor,
    simulator:String(p.simulator||'').trim()||d.simulator,
    version:String(p.version||'').trim()||d.version,
    unit_system:unit||d.unit_system,
  };
};
const observeAllowlistedKeywords=text=>{
  const blob=String(text||'');
  const out=[];
  for(const k of SCHEDULE_KEYWORD_ORDER){
    const flags=SCHEDULE_ENGLISH_COLLISION.has(k)?'':'i';
    const re=new RegExp('\\b'+k.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\b',flags);
    if(re.test(blob)) out.push(k);
  }
  return out;
};
const taskProseBlob=x=>{
  const src=x&&typeof x==='object'&&!Array.isArray(x)?x:{};
  const parts=[];
  const push=v=>{if(typeof v==='string'&&v.trim()) parts.push(v)};
  push(src.objective);push(src.problem_statement);push(src.request_text);push(src.task_description);
  push(src.user_goal);push(src.instruction);push(src.human_instruction);push(src.hitl_reply_text);
  if(src.task&&typeof src.task==='object') push(src.task.objective);
  return parts.join('\n');
};
const COMMISSIONING_KEYWORDS=['DATES','WELOPEN','WCONPROD','WEFAC'];
const GROUP_REBIND_KEYWORDS=['WELSPECS','GRUPTREE','GCONPROD','WECON','WPIMULT'];
const COMMISSIONING_CAP_IDS=['commissioning_date_retarget','shift_commissioning_dates','commissioning_revise','timeline_revise'];
const GROUP_REBIND_CAP_IDS=['group_membership_rebind','group_rebind'];
const capToken=v=>String(v==null?'':v).trim().toLowerCase().replace(/-/g,'_');
const withoutCapIds=(list,drop)=>list.filter(v=>!drop.includes(capToken(v)));
const inferChangeOperation=text=>{
  const t=String(text||'');
  if(/(убер[а-яё]*|удал[а-яё]*|вырез[а-яё]*|исключ[а-яё]*|\bremove\b|\bdelete\b|\bstrip\b|\bdrop\b)/i.test(t)) return 'remove';
  if(/(добав[а-яё]*|встав[а-яё]*|созда[а-яё]*|\badd\b|\bcreate\b|\binsert\b)/i.test(t)) return 'add';
  if(/(измен[а-яё]*|замен[а-яё]*|сдвин[а-яё]*|поменя[а-яё]*|правк[а-яё]*|\bchange\b|\brevise\b|\breplace\b|\bshift\b|\bupdate\b|собрать)/i.test(t)) return 'change';
  return '';
};
const changeScopeMeaningful=change=>{
  if(!change||typeof change!=='object'||Array.isArray(change)) return false;
  if(String(change.operation||change.intent||change.kind||change.capability_id||'').trim()) return true;
  if(String(change.summary||change.description||change.human_correction||'').trim()) return true;
  if(change.parent_group||change.rate||change.gas_rate||change.group_rebind) return true;
  return ['must_remove','must_add','must_change','must_preserve','keywords','wells','groups'].some(k=>Array.isArray(change[k])&&change[k].length);
};
const looksCommissioningProse=t=>/дат[а-яё]*\s*ввод|ввод[аы]?\s+скважин|commissioning/i.test(String(t||''));
const looksGroupRebindProse=t=>{
  const s=String(t||'');
  if(!/групп/i.test(s)) return false;
  return /контрол|gconprod|gruptree|помести|перевед|перенес|rebind|отдельн/i.test(s);
};
const extractWellsFromProse=t=>{
  const s=String(t||'');
  const m=s.match(/скважин[аыуе]{0,3}\s+((?:[A-Za-z0-9][A-Za-z0-9_.\-]{0,15})(?:\s*(?:,|;|и|and|&)\s*[A-Za-z0-9][A-Za-z0-9_.\-]{0,15})*)/i)
    ||s.match(/\bwells?\s+((?:[A-Za-z0-9][A-Za-z0-9_.\-]{0,15})(?:\s*(?:,|;|и|and|&)\s*[A-Za-z0-9][A-Za-z0-9_.\-]{0,15})*)/i);
  if(!m) return [];
  return [...new Set(m[1].split(/\s*(?:,|;|и|and|&)\s*/i).map(v=>String(v||'').trim()).filter(v=>/^[A-Za-z0-9][A-Za-z0-9_.\-]{0,15}$/.test(v)))];
};
const extractParentGroupFromProse=t=>{
  const s=String(t||'');
  const quoted=s.match(/групп[аыуе]{0,2}[^«"'\n]{0,40}[«"']([A-Za-z][A-Za-z0-9_]{0,24})[»"']/i)
    ||s.match(/[«"']([A-Za-z][A-Za-z0-9_]{0,24})[»"'][^.\n]{0,40}групп/i);
  if(quoted) return quoted[1].toUpperCase();
  const named=s.match(/групп[аыуе]{0,2}\s*[-—:]\s*([A-Za-z][A-Za-z0-9_]{1,24})/i);
  return named?named[1].toUpperCase():'';
};
const extractGasRateFromProse=t=>{
  const m=String(t||'').match(/(\d+(?:[.,]\d+)?)\s*(тыс\.?|тыс|thousand)?\s*(м³|м3|m3)/i);
  if(!m) return null;
  let n=Number(String(m[1]).replace(',', '.'));
  if(!Number.isFinite(n)||n<=0) return null;
  if(m[2]) n=n*1000;
  return n;
};
const extractControlFromProse=t=>{
  const s=String(t||'');
  if(/\bORAT\b|нефть|oil/i.test(s)&&!/газ|gas/i.test(s)) return 'ORAT';
  if(/\bWRAT\b|вод[аыеу]|water/i.test(s)&&!/газ|gas/i.test(s)) return 'WRAT';
  if(/\bGRAT\b|газ|gas|м3|м³|m3/i.test(s)) return 'GRAT';
  return '';
};
const observeGroupRebindFromProse=t=>{
  if(!looksGroupRebindProse(t)) return null;
  const wells=extractWellsFromProse(t);
  const parent=extractParentGroupFromProse(t);
  const rate=extractGasRateFromProse(t);
  const control=extractControlFromProse(t)||(rate?'GRAT':'');
  if(!wells.length||!parent||!rate||!control) return null;
  const well_groups={};
  for(const w of wells) well_groups[w]='G'+w;
  return {
    wells,
    parent_group:parent,
    parent_of_parent:'FIELD',
    well_groups,
    rate,
    gas_rate:rate,
    control,
  };
};
const pickDateFromFactRow=(row,values)=>{
  const coerce=v=>{
    if(v==null||v==='') return null;
    if(v instanceof Date&&!Number.isNaN(v.getTime())) return v.toISOString().slice(0,10);
    if(typeof v==='object'&&!Array.isArray(v)){
      if(Number.isFinite(Number(v.year))&&Number.isFinite(Number(v.month))&&Number.isFinite(Number(v.day))){
        const y=String(Number(v.year)).padStart(4,'0'),m=String(Number(v.month)).padStart(2,'0'),d=String(Number(v.day)).padStart(2,'0');
        return `${y}-${m}-${d}`;
      }
      if(v.value!=null&&v.value!==v) return coerce(v.value);
      if(v.iso) return coerce(v.iso);
      if(v.tnav) return coerce(v.tnav);
    }
    const s=String(v).trim();
    return s===''?null:s;
  };
  const named=coerce(row.value??row.raw_value??values['Дата ввода']??values['дата ввода']??values.date??values.Date??values.commissioning_date??null);
  if(named) return {date:named,field:String(row.field||row.column||'Дата ввода')};
  const hit=Object.keys(values).find(k=>/дата|date|commission|ввод/i.test(String(k||'')));
  if(hit!=null){
    const d=coerce(values[hit]);
    if(d) return {date:d,field:String(hit)};
  }
  const others=Object.keys(values).filter(k=>!/скважин|well|групп|group|факт|id$/i.test(String(k||'')));
  if(others.length===1){
    const d=coerce(values[others[0]]);
    if(d) return {date:d,field:String(others[0])};
  }
  return {date:null,field:String(row.field||row.column||'')};
};
const commissioningFactsFromPacket=packet=>{
  const src=packet&&typeof packet==='object'&&!Array.isArray(packet)?packet:{};
  const facts=Array.isArray(src.facts)?src.facts:[];
  return facts.map(f=>{
    const row=f&&typeof f==='object'?f:{};
    const values=row.values&&typeof row.values==='object'&&!Array.isArray(row.values)?row.values:(row.row&&typeof row.row==='object'?row.row:{});
    const well=String(row.well||row.entity||row.entity_id||values['Скважина']||values.скважина||values.WELL||values.well||'').trim();
    const picked=pickDateFromFactRow(row,values);
    return {well,date:picked.date,field:picked.field};
  }).filter(f=>f.well&&f.date!=null&&String(f.date).trim()!=='');
};
const looksCommissioningFacts=facts=>facts.some(f=>/дата|date|commission|ввод/i.test(String(f.field||'')));
const observeKeywordScope=x=>{
  const src=x&&typeof x==='object'&&!Array.isArray(x)?x:{};
  const existing=(Array.isArray(src.requested_keyword_scope)?src.requested_keyword_scope:[]).map(v=>String(v||'').trim().toUpperCase()).filter(k=>SCHEDULE_ALLOWLIST.has(k));
  return [...new Set([...existing,...observeAllowlistedKeywords(taskProseBlob(src))])];
};
const observeChangeScope=(x,keywords)=>{
  const src=x&&typeof x==='object'&&!Array.isArray(x)?x:{};
  if(changeScopeMeaningful(src.requested_change_scope)) return src.requested_change_scope;
  const prose=taskProseBlob(src);
  const op=inferChangeOperation(prose);
  const summary=String(src.objective||src.problem_statement||src.request_text||prose).trim().slice(0,500);
  if(!keywords.length&&!op&&!summary) return src.requested_change_scope||null;
  const scope={source:'task_text'};
  if(op) scope.operation=op;
  if(keywords.length) scope.keywords=keywords;
  if(summary) scope.summary=summary;
  const rows=keywords.map(keyword=>({keyword}));
  if(op==='remove'&&rows.length) scope.must_remove=rows;
  else if(op==='add'&&rows.length) scope.must_add=rows;
  else if(op==='change'&&rows.length) scope.must_change=rows;
  return scope;
};
const observeSchedulePacketFacts=x=>{
  const src=x&&typeof x==='object'&&!Array.isArray(x)?x:{};
  const simulator_profile=applyPetroleumProfile(src.simulator_profile);
  const prose=taskProseBlob(src);
  const wellDateFacts=commissioningFactsFromPacket(src.source_facts_packet);
  const groupSpec=observeGroupRebindFromProse(prose);
  const commissioning=looksCommissioningProse(prose)||(wellDateFacts.length>0&&looksCommissioningFacts(wellDateFacts));
  let requested_keyword_scope=observeKeywordScope(src);
  let requested_capability_scope=(Array.isArray(src.requested_capability_scope)?src.requested_capability_scope:[]).map(v=>String(v||'').trim()).filter(Boolean);
  let requested_change_scope=src.requested_change_scope&&changeScopeMeaningful(src.requested_change_scope)?src.requested_change_scope:null;
  if(groupSpec){
    requested_capability_scope=[...new Set([...withoutCapIds(requested_capability_scope,COMMISSIONING_CAP_IDS),'group_membership_rebind'])];
    requested_keyword_scope=[...new Set([...requested_keyword_scope,...GROUP_REBIND_KEYWORDS.filter(k=>SCHEDULE_ALLOWLIST.has(k))])];
    const prev=requested_change_scope&&typeof requested_change_scope==='object'&&!Array.isArray(requested_change_scope)?requested_change_scope:{};
    const prevGroups=prev.well_groups&&typeof prev.well_groups==='object'&&!Array.isArray(prev.well_groups)?prev.well_groups:null;
    requested_change_scope={
      ...prev,
      source:prev.source||'task_text',
      capability_id:'group_membership_rebind',
      intent:'group_membership_rebind',
      operation:prev.operation||'change',
      keywords:[...new Set([...(Array.isArray(prev.keywords)?prev.keywords:[]),...GROUP_REBIND_KEYWORDS.filter(k=>SCHEDULE_ALLOWLIST.has(k))])],
      summary:prev.summary||prose.slice(0,500),
      wells:(Array.isArray(prev.wells)&&prev.wells.length)?prev.wells:groupSpec.wells,
      parent_group:prev.parent_group||groupSpec.parent_group,
      parent_of_parent:prev.parent_of_parent||groupSpec.parent_of_parent,
      well_groups:prevGroups||groupSpec.well_groups,
      rate:prev.rate??groupSpec.rate,
      gas_rate:prev.gas_rate??groupSpec.gas_rate,
      control:prev.control||groupSpec.control,
      group_rebind:prev.group_rebind||groupSpec,
    };
  }else if(commissioning){
    requested_capability_scope=[...new Set([...withoutCapIds(requested_capability_scope,GROUP_REBIND_CAP_IDS),'commissioning_date_retarget'])];
    requested_keyword_scope=[...new Set([...requested_keyword_scope,...COMMISSIONING_KEYWORDS.filter(k=>SCHEDULE_ALLOWLIST.has(k))])];
    const prev=requested_change_scope&&typeof requested_change_scope==='object'&&!Array.isArray(requested_change_scope)?requested_change_scope:{};
    const prevCap=capToken(prev.capability_id);
    requested_change_scope={
      ...prev,
      source:prev.source||'task_text',
      capability_id:COMMISSIONING_CAP_IDS.includes(prevCap)?prev.capability_id:'commissioning_date_retarget',
      intent:COMMISSIONING_CAP_IDS.includes(capToken(prev.intent))?prev.intent:'shift_commissioning_dates',
      operation:prev.operation||'change',
      keywords:[...new Set([...(Array.isArray(prev.keywords)?prev.keywords:[]),...COMMISSIONING_KEYWORDS.filter(k=>SCHEDULE_ALLOWLIST.has(k))])],
      summary:prev.summary||prose.slice(0,500),
    };
    delete requested_change_scope.group_rebind;
  }else if(!requested_change_scope){
    requested_change_scope=observeChangeScope(src,requested_keyword_scope);
  }
  if(!requested_keyword_scope.length&&requested_change_scope&&Array.isArray(requested_change_scope.keywords)){
    requested_keyword_scope=requested_change_scope.keywords.map(v=>String(v||'').trim().toUpperCase()).filter(k=>SCHEDULE_ALLOWLIST.has(k));
  }
  return {simulator_profile,requested_keyword_scope,requested_change_scope,requested_capability_scope};
};
"""
