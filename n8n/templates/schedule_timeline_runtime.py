"""Timeline SCHEDULE model: parse → edit → emit.

Internal shape (conceptual):
  steps[] where each step has a validated schedule_datetime (DATES clock) and
  keyword blocks with records {keyword, entity, raw_line, ...}.

  schedule_datetime carries a real JS Date (`value`) plus iso/tnav/epoch_ms.
  calculation_start keywords live on the preamble step with date=null.
  Parse accepts tNav DATES ("D MON YYYY"), ISO, and Excel serial; emit rebuilds
  DATES bodies via formatDatesTnav → "D MON YYYY" as required by the DATES keyword.

Commissioning date revise is an *edit* of the timeline schema — not a blind
text shift. Excel/Orchestrator supply {well → new commissioning date}. The
authority for «дата ввода» is the first WCONPROD record of that well (clocked
by its enclosing DATES). Edit = retarget that record (and companion first
WELOPEN / WEFAC for the same well) onto the step whose DATES equals the new
date, then re-emit. DATES steps themselves are never deleted — empty monthly
calculation steps stay as DATES-only.

Policies:
  - unlisted_wells_policy must be an explicit enum keep|remove (options / controls / HITL).
  - Prose in instruction_blob may only *signal* remove → needs_input (never silent mutate).
  - Default without signal or enum = keep (baseline wells absent from Excel keep starts).
  - Excel wells absent from baseline commissioning set → HITL (new-well defs)
"""
from __future__ import annotations

import json

from schedule_emit_order import within_date_order_js


def timeline_core_js() -> str:
    """Pure helpers shared by n8n Code nodes (no $json / $('…'))."""
    return within_date_order_js() + "\n" + r"""
const MONTH={JAN:0,FEB:1,MAR:2,APR:3,MAY:4,JUN:5,JUL:6,AUG:7,SEP:8,OCT:9,NOV:10,DEC:11};
const MONTHS=['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];
/** Commissioning event on the timeline: WCONPROD owns «дата ввода»; WELOPEN/WEFAC travel with it. */
const COMMISSIONING_PRIMARY_KEYWORD='WCONPROD';
const COMMISSIONING_COMPANION_KEYWORDS=new Set(['WELOPEN','WEFAC']);
const MOVE_KEYWORDS=new Set([COMMISSIONING_PRIMARY_KEYWORD,...COMMISSIONING_COMPANION_KEYWORDS]);
const tlClean=v=>typeof v==='string'?v.trim():'';
const pad2=n=>String(n).padStart(2,'0');

/**
 * Valid schedule datetime for internal timeline work.
 * - `value` is a real JS Date (UTC midnight of the calendar day)
 * - `tnav` is the DATES-keyword emit form: "D MON YYYY"
 * - `iso` is YYYY-MM-DD for stable keys / JSON
 * Across n8n node boundaries Date may stringify; epoch_ms + y/m/d remain authoritative.
 */
const makeScheduleDate=(year,month /*1-12*/,day,sourceRaw=null)=>{
  const y=Number(year),m=Number(month),d=Number(day);
  if(!Number.isInteger(y)||!Number.isInteger(m)||!Number.isInteger(d))return null;
  if(m<1||m>12||d<1||d>31)return null;
  const epoch_ms=Date.UTC(y,m-1,d);
  const value=new Date(epoch_ms);
  if(value.getUTCFullYear()!==y||value.getUTCMonth()!==m-1||value.getUTCDate()!==d)return null;
  const iso=`${String(y).padStart(4,'0')}-${pad2(m)}-${pad2(d)}`;
  const tnav=`${d} ${MONTHS[m-1]} ${y}`;
  return{
    contract:'schedule_datetime',
    contract_version:'1.0',
    year:y,month:m,day:d,
    epoch_ms,
    iso,
    tnav,
    source_raw:sourceRaw!=null?String(sourceRaw):tnav,
    value, // Date
  };
};
const scheduleDateFromJsDate=(dt,sourceRaw=null)=>{
  if(!(dt instanceof Date)||Number.isNaN(dt.getTime()))return null;
  return makeScheduleDate(dt.getUTCFullYear(),dt.getUTCMonth()+1,dt.getUTCDate(),sourceRaw);
};
/** Parse DATES / Excel / ISO into schedule_datetime. */
const parseScheduleDate=raw=>{
  if(raw&&typeof raw==='object'&&raw.contract==='schedule_datetime'&&Number.isFinite(raw.epoch_ms)){
    return makeScheduleDate(raw.year,raw.month,raw.day,raw.source_raw||raw.tnav)||null;
  }
  if(raw instanceof Date)return scheduleDateFromJsDate(raw);
  const s=tlClean(raw);
  if(!s)return null;
  // ISO YYYY-MM-DD (optionally with time)
  let m=s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if(m)return makeScheduleDate(Number(m[1]),Number(m[2]),Number(m[3]),s);
  // tNav / ECLIPSE DATES: D MON YYYY
  m=s.toUpperCase().match(/^(\d{1,2})\s+([A-Z]{3})\s+(\d{4})$/);
  if(m&&MONTH[m[2]]!==undefined)return makeScheduleDate(Number(m[3]),MONTH[m[2]]+1,Number(m[1]),s);
  // Excel serial day (workbook facts sometimes arrive numeric)
  if(/^\d+(\.\d+)?$/.test(s)){
    const serial=Math.floor(Number(s));
    if(serial>=20000&&serial<=80000){
      // Excel epoch 1899-12-30 UTC
      const value=new Date(Date.UTC(1899,11,30)+serial*86400000);
      return scheduleDateFromJsDate(value,s);
    }
  }
  return null;
};
/** @deprecated alias — prefer parseScheduleDate (returns schedule_datetime). */
const parseTnavDate=raw=>{
  const d=parseScheduleDate(raw);
  if(!d)return null;
  // Back-compat shape used by older call sites (epoch was seconds-scale name but ms value).
  return{raw:d.source_raw,iso:d.iso,epoch:d.epoch_ms,day:d.day,month:d.month,year:d.year,tnav:d.tnav,value:d.value,schedule_date:d};
};
/** Emit form for DATES keyword body line (without indent / slash). */
const formatDatesTnav=raw=>{
  const d=parseScheduleDate(raw);
  return d?d.tnav:tlClean(raw);
};
const toTnavDate=formatDatesTnav;
const formatDatesBodyLines=(dateOrRaw,indent='  ')=>{
  const tnav=formatDatesTnav(dateOrRaw);
  return[`${indent}${tnav} /`,'/'];
};
const addMonthIso=iso=>{
  const p=parseScheduleDate(iso);if(!p)return null;
  let y=p.year,m=p.month+1;if(m>12){y+=1;m=1;}
  return `${String(y).padStart(4,'0')}-${pad2(m)}-01`;
};
const bindStepDate=(step,date,{header=null,body=null,preserveBody=false}={})=>{
  if(!step)return step;
  if(!date){
    step.date=null;step.effective_at=null;step.dates_tnav=null;
    return step;
  }
  step.date=date;
  step.effective_at=date.iso;
  step.dates_tnav=date.tnav;
  if(header!=null)step.dates_header=header;
  if(!preserveBody||!step.dates_body){
    if(body)step.dates_body=body;
    else{
      let indent='  ';
      const prev=(step.dates_body||[]).find(l=>/\d/.test(l));
      if(prev){const mm=String(prev).match(/^(\s*)/);if(mm)indent=mm[1];}
      step.dates_body=formatDatesBodyLines(date,indent);
    }
  }
  return step;
};
const entityFromRecordLine=(kw,line)=>{
  const t=String(line||'').replace(/--.*$/,'').trim();
  if(!t||t==='/'||t.startsWith('*'))return null;
  const tok=t.split(/\s+/)[0];
  if(!tok||!/^[A-Za-z0-9_.\-]+$/.test(tok))return null;
  if(kw==='DATES')return null;
  return tok;
};

/** Signal-only detector: prose may *suggest* remove. Never apply as authority — use explicit enum or HITL. */
const detectUnlistedWellsPolicy=blob=>{
  const t=String(blob||'').toLowerCase().replace(/\s+/g,' ');
  const remove=
    /(убрать|убери|убрат|удал|remove|drop|delete).{0,120}(нет в (файле|excel|книге|workbook)|not (present |listed )?in (the )?(excel|file|workbook|xlsx))/.test(t)
    ||/(нет в (файле|excel|книге|workbook).{0,80}(убрать|убери|убрат|удал)|скважин.{0,40}нет в (файле|excel).{0,40}(убрать|убери|убрат|удал))/.test(t)
    ||/(wells?.{0,60}(not|absent).{0,40}(excel|file|workbook).{0,40}(remove|drop|delete|убрать|убери|убрат))/.test(t)
    ||/unlisted_wells_policy\s*[:=]\s*remove/.test(t)
    ||/\bremove_unlisted\b/.test(t);
  return remove?'remove':'keep';
};

const normalizeUnlistedWellsPolicy=raw=>{
  const p=tlClean(raw).toLowerCase();
  return(p==='keep'||p==='remove')?p:'';
};

/** (1) Parse SCHEDULE text → timeline model */
const parseScheduleTimeline=(text,fileRef='schedule.inc')=>{
  const source=String(text??'').replace(/\r\n?/g,'\n');
  const lines=source.split('\n');
  const blocks=[];
  let i=0;
  while(i<lines.length){
    const hm=lines[i].match(/^(\s*)([A-Za-z][A-Za-z0-9_]*)\s*(?:--.*)?$/);
    if(hm){
      const kw=hm[2].toUpperCase(),header=lines[i],start=i;i+=1;
      const body=[];
      while(i<lines.length){
        if(lines[i].match(/^\s*[A-Za-z][A-Za-z0-9_]*\s*(?:--.*)?$/))break;
        body.push(lines[i]);i+=1;
      }
      blocks.push({kw,header,body,start_line:start+1});
    }else{
      if(!blocks.length)blocks.push({kw:null,header:null,body:[],start_line:i+1,trivia:true});
      const last=blocks[blocks.length-1];
      if(last.trivia)last.body.push(lines[i]);
      else last.body.push(lines[i]);
      i+=1;
    }
  }
  let clock=null; // schedule_datetime | null
  const steps=[];
  const ensureStep=()=>{
    const key=clock?clock.iso:'__preamble__';
    let step=steps.find(s=>s._key===key);
    if(!step){
      step={_key:key,date:clock||null,effective_at:clock?clock.iso:null,dates_tnav:clock?clock.tnav:null,dates_header:null,dates_body:null,blocks:[]};
      steps.push(step);
    }
    return step;
  };
  ensureStep();
  for(const b of blocks){
    if(b.trivia){
      ensureStep().blocks.push({kind:'trivia',lines:b.body.slice()});
      continue;
    }
    if(b.kw==='DATES'){
      let date=null;
      for(const line of b.body){
        const cand=line.replace(/--.*$/,'').replace(/\/.*$/,'').trim();
        if(!cand)continue;
        date=parseScheduleDate(cand);
        if(date)break;
      }
      if(date)clock=date;
      const step=ensureStep();
      step.dates_header=b.header;
      step.dates_body=b.body.slice();
      if(date)bindStepDate(step,date,{header:b.header,preserveBody:true});
      continue;
    }
    const step=ensureStep();
    const records=[];
    for(const line of b.body){
      const t=line.trim();
      if(!t||t.startsWith('--')||t==='/')continue;
      records.push({entity:entityFromRecordLine(b.kw,line),raw_line:line,effective_at:step.effective_at,date:step.date||null});
    }
    step.blocks.push({kind:'keyword',keyword:b.kw,header:b.header,body:b.body.slice(),records});
  }
  const flat=[];
  for(const step of steps){
    for(const blk of step.blocks){
      if(blk.kind!=='keyword')continue;
      for(const rec of blk.records){
        flat.push({keyword:blk.keyword,entity:rec.entity,raw_line:rec.raw_line,effective_at:step.effective_at,dates_tnav:step.dates_tnav,date:step.date||null});
      }
    }
  }
  return{
    contract:'schedule_timeline_model',
    contract_version:'1.1',
    file_ref:fileRef,
    steps,
    records:flat,
    dates:steps.filter(s=>s.date).map(s=>({iso:s.date.iso,tnav:s.date.tnav,epoch_ms:s.date.epoch_ms,year:s.date.year,month:s.date.month,day:s.date.day})),
  };
};

const listBaselineCommissioningWells=model=>{
  const wells=new Set();
  for(const rec of model.records||[]){
    if(MOVE_KEYWORDS.has(rec.keyword)&&rec.entity)wells.add(tlClean(rec.entity));
  }
  return[...wells].filter(Boolean).sort();
};

const refreshTimelineFlat=model=>{
  model.records=[];
  for(const step of model.steps){
    // Rehydrate schedule_datetime if JSON round-trip dropped Date.value
    if(step.date&&step.date.contract==='schedule_datetime'){
      const revived=parseScheduleDate(step.date);
      if(revived)bindStepDate(step,revived,{preserveBody:true});
    }else if(step.effective_at&&!step.date){
      const revived=parseScheduleDate(step.effective_at);
      if(revived)bindStepDate(step,revived,{preserveBody:true});
    }
    for(const blk of step.blocks){
      if(blk.kind!=='keyword')continue;
      for(const rec of blk.records){
        rec.effective_at=step.effective_at;
        rec.date=step.date||null;
        model.records.push({keyword:blk.keyword,entity:rec.entity,raw_line:rec.raw_line,effective_at:step.effective_at,dates_tnav:step.dates_tnav,date:step.date||null});
      }
    }
  }
  model.dates=model.steps.filter(s=>s.date).map(s=>({iso:s.date.iso,tnav:s.date.tnav,epoch_ms:s.date.epoch_ms,year:s.date.year,month:s.date.month,day:s.date.day}));
  return model;
};

/** Remove all WELOPEN/WCONPROD/WEFAC rows for wells not listed in Excel. DATES kept. */
const removeUnlistedCommissioning=(model,excelWells)=>{
  const keep=new Set((excelWells||[]).map(w=>tlClean(w)).filter(Boolean));
  const removed=[];
  for(const step of model.steps){
    for(const blk of step.blocks){
      if(blk.kind!=='keyword'||!MOVE_KEYWORDS.has(blk.keyword))continue;
      const next=[];
      for(const rec of blk.records){
        const well=tlClean(rec.entity);
        if(well&&!keep.has(well)){
          removed.push({well,keyword:blk.keyword,from:step.effective_at});
          continue;
        }
        next.push(rec);
      }
      blk.records=next;
      if(blk.records.length){
        blk.body=blk.records.map(r=>r.raw_line);
        if(!blk.body.some(l=>tlClean(l)==='/'))blk.body.push('/');
      }else{
        blk.body=[];
      }
    }
    step.blocks=step.blocks.filter(blk=>blk.kind!=='keyword'||!MOVE_KEYWORDS.has(blk.keyword)||blk.records.length>0);
  }
  refreshTimelineFlat(model);
  return{model,removed};
};

const ensureDatesStep=(model,isoOrDate)=>{
  const date=parseScheduleDate(isoOrDate);
  if(!date)return null;
  let step=model.steps.find(s=>s.effective_at===date.iso||(s.date&&s.date.iso===date.iso));
  if(step){
    if(!step.date)bindStepDate(step,date,{preserveBody:true});
    return step;
  }
  step={_key:date.iso,blocks:[]};
  bindStepDate(step,date,{header:'DATES'});
  const idx=model.steps.findIndex(s=>{
    const od=s.date||parseScheduleDate(s.effective_at);
    return od&&od.epoch_ms>date.epoch_ms;
  });
  if(idx<0)model.steps.push(step);else model.steps.splice(idx,0,step);
  return step;
};

const upsertKeywordRecords=(step,keyword,items)=>{
  if(!items.length)return;
  let blk=step.blocks.find(b=>b.kind==='keyword'&&b.keyword===keyword);
  if(!blk){
    blk={kind:'keyword',keyword,header:keyword,body:[],records:[]};
    step.blocks.push(blk);
  }
  for(const it of items){
    const line=it.raw_line||it;
    const entity=it.entity||entityFromRecordLine(keyword,line);
    blk.records.push({entity,raw_line:line,effective_at:step.effective_at});
  }
  blk.body=blk.records.map(r=>r.raw_line);
  if(!blk.body.some(l=>tlClean(l)==='/'))blk.body.push('/');
};

/** Attach new wells: INCLUDE WELLTRACK + WELSPECS + COMPDATMD/WELOPEN/WCONPROD/WEFAC on target DATES. */
const applyNewWellDefinitions=(model,defs)=>{
  const applied=[];
  const findings=[];
  const preamble=model.steps.find(s=>s._key==='__preamble__')||model.steps[0];
  for(const def of defs||[]){
    const well=tlClean(def.well||def.entity);
    if(!well)continue;
    const date=def.date??def.commissioning_date??null;
    const tnav=toTnavDate(date);
    const parsed=parseTnavDate(tnav);
    if(!parsed){findings.push({code:'NEW_WELL_DATE_INVALID',severity:'error',well,date:String(date||'')});continue;}
    const includePath=tlClean(def.welltrack_include||def.welltrack_path||def.include_path);
    if(includePath&&preamble){
      preamble.blocks.push({kind:'keyword',keyword:'INCLUDE',header:'INCLUDE',body:[`'${includePath}' /`,'/'],records:[{entity:null,raw_line:`'${includePath}' /`,effective_at:null}]});
    }
    if(def.welspecs_line||def.welspecs){
      const line=def.welspecs_line||def.welspecs;
      upsertKeywordRecords(preamble||ensureDatesStep(model,parsed.iso),'WELSPECS',[{entity:well,raw_line:line.endsWith('/')?` ${line}`:` ${line} /`}]);
    }
    const step=ensureDatesStep(model,parsed.iso);
    const compLines=Array.isArray(def.compdatmd_lines)?def.compdatmd_lines:(def.compdatmd_line?[def.compdatmd_line]:[]);
    if(compLines.length)upsertKeywordRecords(step,'COMPDATMD',compLines.map(l=>({entity:well,raw_line:String(l)})));
    const openLine=def.welopen_line||` ${well} OPEN /`;
    upsertKeywordRecords(step,'WELOPEN',[{entity:well,raw_line:openLine}]);
    const wcon=def.wconprod_line||` ${well} OPEN GRAT 1* 1* ${Number(def.grat||def.gas_rate||100000)} 1* 1* 90 1* 30 /`;
    upsertKeywordRecords(step,'WCONPROD',[{entity:well,raw_line:wcon}]);
    const wefac=def.wefac_line||` ${well} 1 /`;
    upsertKeywordRecords(step,'WEFAC',[{entity:well,raw_line:wefac}]);
    applied.push({well,iso:parsed.iso,tnav:parsed.raw||tnav,include:includePath||null});
  }
  refreshTimelineFlat(model);
  return{model,applied,findings};
};

/**
 * (2) Edit timeline schema: retarget first commissioning records per well.
 *
 * Facts: [{well, date|value}, …] from Excel Extractor.
 * For each well, find the first WCONPROD (plus companion WELOPEN/WEFAC) under
 * the current DATES clock and retarget those records onto the DATES step that
 * matches the new commissioning date. Record text is preserved; only the
 * enclosing clock changes. Then emitScheduleFromTimeline rebuilds .INC.
 */
const editCommissioningDatesOnTimeline=(model,wellFacts)=>{
  const findings=[];
  const targets=new Map(); // well -> schedule_datetime (+ aliases)
  for(const f of wellFacts||[]){
    const well=tlClean(f.well||f.entity);
    const dateRaw=f.date??f.value??null;
    if(!well||dateRaw===null||dateRaw===undefined)continue;
    const date=parseScheduleDate(dateRaw);
    if(!date){findings.push({code:'COMMISSIONING_DATE_INVALID',severity:'error',well,date:String(dateRaw)});continue;}
    targets.set(well,{...date,tnav:date.tnav,iso:date.iso,epoch:date.epoch_ms,date});
  }
  if(!targets.size)return{status:'noop',model,findings,edits:[],moved:[],shifts:[]};

  // Missing target DATES is OK for edit: we insert a DATES-only step (see DATES_STEP_CREATED).
  for(const [well,tg] of targets){
    if(!model.steps.some(s=>s.effective_at===tg.iso)){
      findings.push({code:'TARGET_DATES_MISSING',severity:'warning',well,dates:tg.tnav,note:'Will create DATES step for commissioning edit'});
    }
  }

  const edits=[];
  const takeFirst=new Map(); // well|kw -> {raw_line, from_iso, keyword, well}
  for(const step of model.steps){
    for(const blk of step.blocks){
      if(blk.kind!=='keyword'||!MOVE_KEYWORDS.has(blk.keyword))continue;
      const keep=[];
      for(const rec of blk.records){
        const well=tlClean(rec.entity);
        if(!well||!targets.has(well)){keep.push(rec);continue;}
        const key=`${well}|${blk.keyword}`;
        if(takeFirst.has(key)){keep.push(rec);continue;} // only first (= commissioning) occurrence
        const tg=targets.get(well);
        takeFirst.set(key,{raw_line:rec.raw_line,from_iso:step.effective_at,from_tnav:step.dates_tnav,keyword:blk.keyword,well});
        edits.push({
          op:'retarget_record',
          primary:blk.keyword===COMMISSIONING_PRIMARY_KEYWORD,
          keyword:blk.keyword,
          entity:well,
          from_effective_at:step.effective_at,
          from_dates_tnav:step.dates_tnav||null,
          to_effective_at:tg.iso,
          to_dates_tnav:tg.tnav,
          raw_line:rec.raw_line,
        });
      }
      blk.records=keep;
      if(blk.records.length){
        blk.body=blk.records.map(r=>r.raw_line);
        if(!blk.body.some(l=>tlClean(l)==='/'))blk.body.push('/');
      }else{
        blk.body=[];
      }
    }
    step.blocks=step.blocks.filter(blk=>blk.kind!=='keyword'||!MOVE_KEYWORDS.has(blk.keyword)||blk.records.length>0);
  }

  const byTarget=new Map();
  for(const [,item] of takeFirst){
    const tg=targets.get(item.well);
    if(!tg)continue;
    if(!byTarget.has(tg.iso))byTarget.set(tg.iso,[]);
    byTarget.get(tg.iso).push(item);
  }
  for(const [iso,items] of byTarget){
    let step=model.steps.find(s=>s.effective_at===iso);
    const tgDate=items.length?targets.get(items[0].well)?.date:parseScheduleDate(iso);
    if(!step){
      step=ensureDatesStep(model,tgDate||iso);
      findings.push({code:'DATES_STEP_CREATED',severity:'warning',iso:step.effective_at,tnav:step.dates_tnav});
    }else if(tgDate){
      bindStepDate(step,tgDate,{preserveBody:true});
    }
    const order=['WELOPEN',COMMISSIONING_PRIMARY_KEYWORD,'WEFAC'];
    for(const kw of order){
      const chunk=items.filter(x=>x.keyword===kw);
      if(!chunk.length)continue;
      let blk=step.blocks.find(b=>b.kind==='keyword'&&b.keyword===kw);
      if(!blk){
        blk={kind:'keyword',keyword:kw,header:kw,body:[],records:[]};
        step.blocks.push(blk);
      }
      for(const it of chunk){
        blk.records.push({entity:it.well,raw_line:it.raw_line,effective_at:step.effective_at,date:step.date||null});
      }
      blk.body=blk.records.map(r=>r.raw_line);
      if(!blk.body.some(l=>tlClean(l)==='/'))blk.body.push('/');
    }
  }

  refreshTimelineFlat(model);
  const hard=findings.filter(f=>f.severity==='error');
  // Legacy aliases: moved/shifts kept for existing smokes and traces.
  const moved=edits.map(e=>({well:e.entity,keyword:e.keyword,from:e.from_effective_at,to:e.to_effective_at,tnav:e.to_dates_tnav}));
  const shifts=[...targets.entries()].map(([well,s])=>({well,...s}));
  return{status:hard.length?'needs_input':'applied',model,findings,edits,moved,shifts};
};
/** @deprecated name — use editCommissioningDatesOnTimeline (same function). */
const shiftCommissioningOnTimeline=editCommissioningDatesOnTimeline;

/** Monthly 1st continuity over the monthly cadence region (stops at annual jumps). */
const checkMonthlyDatesContinuity=dates=>{
  const list=(dates||[]).map(d=>parseScheduleDate(typeof d==='string'?d:(d.iso||d.tnav||d.raw||d))).filter(Boolean)
    .filter(d=>d.day===1).sort((a,b)=>a.epoch_ms-b.epoch_ms);
  const gaps=[];
  for(let i=0;i<list.length-1;i++){
    const cur=list[i],next=list[i+1];
    const expIso=addMonthIso(cur.iso);
    const exp=parseScheduleDate(expIso);
    if(!exp)continue;
    if(next.epoch_ms===exp.epoch_ms)continue;
    const monthDelta=(next.year-cur.year)*12+(next.month-cur.month);
    if(monthDelta>=12)break;
    let walk=exp;
    while(walk.epoch_ms<next.epoch_ms){
      gaps.push({iso:walk.iso,tnav:walk.tnav,epoch_ms:walk.epoch_ms});
      walk=parseScheduleDate(addMonthIso(walk.iso));
      if(!walk)break;
    }
  }
  return{ok:!gaps.length,gaps,checked_from:list[0]?.iso||null,checked_through:list.length?list[list.length-1]?.iso:null,monthly_count:list.length};
};

/** (3) Emit SCHEDULE.INC text from timeline model */
const orderBlocksWithinDate=blocks=>{
  const leading=[],keywords=[],trailing=[];
  let seenKw=false;
  (blocks||[]).forEach((blk,i)=>{
    if(blk.kind==='keyword'){seenKw=true;keywords.push({blk,i});return}
    if(!seenKw)leading.push(blk);else trailing.push(blk);
  });
  keywords.sort((a,b)=>compareWithinDateKeywords(a.blk.keyword,b.blk.keyword,a.i,b.i));
  return [...leading,...keywords.map(x=>x.blk),...trailing];
};
const emitScheduleFromTimeline=model=>{
  const out=[];
  const ensureBareSlash=()=>{if(tlClean(out[out.length-1])!=='/')out.push('/');};
  // Visual gap after block-closing '/' so keyword tables do not run into the next header.
  const ensureBlankAfterBlock=()=>{if(out.length&&out[out.length-1]!=='')out.push('');};
  for(const step of model.steps||[]){
    const date=step.date||parseScheduleDate(step.effective_at);
    if(step.dates_header||date){
      out.push(step.dates_header||'DATES');
      if(date){
        // Emit DATES body from validated datetime → canonical "D MON YYYY" form.
        let indent='  ';
        const prev=(step.dates_body||[]).find(l=>/\d/.test(String(l)));
        if(prev){const mm=String(prev).match(/^(\s*)/);if(mm)indent=mm[1];}
        for(const line of formatDatesBodyLines(date,indent))out.push(line);
      }else{
        for(const line of(step.dates_body||[]))out.push(line);
      }
      ensureBlankAfterBlock();
    }
    for(const blk of orderBlocksWithinDate(step.blocks||[])){
      if(blk.kind==='trivia'){
        for(const line of(blk.lines||[]))out.push(line);
        continue;
      }
      if(blk.kind==='keyword'){
        out.push(blk.header||blk.keyword);
        if(blk.records&&blk.records.length){
          // Records omit the bare block-closing '/'; always emit it for ECLIPSE/tNav.
          // Do not gate on body containing '/' — body is not re-emitted on this path.
          for(const rec of blk.records)out.push(rec.raw_line);
          ensureBareSlash();
          ensureBlankAfterBlock();
        }else{
          for(const line of(blk.body||[]))out.push(line);
          const nonempty=(blk.body||[]).map(tlClean).filter(Boolean);
          if(nonempty.length&&nonempty[nonempty.length-1]!=='/')out.push('/');
          if(nonempty.length)ensureBlankAfterBlock();
        }
      }
    }
  }
  // Drop a single trailing blank so files end on the last '/' line + final newline from join.
  while(out.length&&out[out.length-1]==='')out.pop();
  return out.join('\n');
};

const buildNewWellEvidenceGaps=(newWells,shifts)=>{
  const gaps=[];
  for(const well of newWells){
    const sh=shifts.get(well)||{};
    const at=sh.iso||sh.tnav||'unknown';
    const common={entity:well,effective_at:at};
    gaps.push({...common,keyword:'WELLTRACK',field:'trajectory_file',reason:'NEW_WELL_MISSING_WELLTRACK',expected_format:'WELLTRACK .inc/.dev file attached via Human Gate (INCLUDE path)',question:`Прикрепите WELLTRACK для новой скважины ${well} (файл траектории). MAS подключит через INCLUDE.`});
    gaps.push({...common,keyword:'WELSPECS',field:'well_header',reason:'NEW_WELL_MISSING_WELSPECS',expected_format:'WELSPECS row or xlsx sheet WELSPECS',question:`Нужны WELSPECS для ${well} (группа, I/J, PHASE и т.д.).`});
    gaps.push({...common,keyword:'COMPDATMD',field:'perforation_md',reason:'NEW_WELL_MISSING_COMPDATMD',expected_format:'xlsx: Скважина, MD_TOP, MD_BOT (или COMPDATMD)',question:`Укажите интервалы перфорации (MD) для ${well} в xlsx.`});
    gaps.push({...common,keyword:'WCONPROD',field:'gas_rate',reason:'NEW_WELL_MISSING_WCONPROD',expected_format:'xlsx: Скважина, GRAT (стартовый дебит газа)',question:`Укажите стартовый дебит газа (GRAT) для ${well} в xlsx.`});
  }
  return gaps;
};

/**
 * Full commissioning revise with keep/remove unlisted + new-well HITL.
 * options: {unlisted_wells_policy:'keep'|'remove', new_well_defs:[], instruction_blob:''}
 * Destructive remove requires explicit enum — prose alone triggers needs_input.
 *
 * Happy path: parse → editCommissioningDatesOnTimeline (retarget WCONPROD+) → emit.
 */
const runCommissioningRevise=(text,wellFacts,fileRef='schedule.inc',options={})=>{
  const model=parseScheduleTimeline(text,fileRef);
  const beforeDates=model.dates.map(d=>d.iso);
  const excelWells=[...new Set((wellFacts||[]).map(f=>tlClean(f.well||f.entity)).filter(Boolean))];
  const baselineWells=listBaselineCommissioningWells(model);
  const baselineSet=new Set(baselineWells);
  const excelSet=new Set(excelWells);
  const unlisted=[...baselineSet].filter(w=>!excelSet.has(w)).sort();
  const newWells=excelWells.filter(w=>!baselineSet.has(w)).sort();
  const explicitPolicy=normalizeUnlistedWellsPolicy(options.unlisted_wells_policy);
  const proseSuggestsRemove=detectUnlistedWellsPolicy(options.instruction_blob||'')==='remove';
  if(!explicitPolicy&&proseSuggestsRemove&&unlisted.length){
    const questions=[{
      id:'unlisted_wells_policy',
      question:`В Excel нет скважин: ${unlisted.slice(0,20).join(', ')}${unlisted.length>20?'…':''}. Сохранить их запуски или убрать?`,
      expected_format:'keep|remove',
      required:true,
      type:'enum',
      enum:['keep','remove'],
    }];
    return{
      contract:'schedule_commissioning_revise_result',
      contract_version:'1.0',
      status:'needs_input',
      generated_schedule:'',
      timeline:model,
      moved:[],
      shifts:[],
      edits:[],
      unlisted_wells_policy:null,
      unlisted_wells:unlisted,
      new_wells:newWells,
      monthly_dates_check:checkMonthlyDatesContinuity(model.dates),
      findings:[{code:'UNLISTED_WELLS_POLICY_REQUIRED',severity:'error',wells:unlisted.slice(0,40),note:'Prose suggested remove but unlisted_wells_policy enum is required; prose is not authority'}],
      evidence_gap:[],
      questions,
      continuation:{protocol:'schedule-builder-hitl-attachment-v1',unlisted_wells:unlisted,unlisted_wells_policy:null,new_wells:newWells},
      human_request:{kind:'needs_input',questions},
    };
  }
  const policy=explicitPolicy||'keep';
  const shiftsPreview=new Map();
  for(const f of wellFacts||[]){
    const well=tlClean(f.well||f.entity);
    const date=f.date??f.value??null;
    if(!well||date===null||date===undefined)continue;
    const parsed=parseTnavDate(toTnavDate(date));
    if(parsed)shiftsPreview.set(well,{iso:parsed.iso,tnav:toTnavDate(date)});
  }

  const newWellDefs=Array.isArray(options.new_well_defs)?options.new_well_defs:[];
  const defWells=new Set(newWellDefs.map(d=>tlClean(d.well||d.entity)).filter(Boolean));
  const unresolvedNew=newWells.filter(w=>!defWells.has(w));

  if(unresolvedNew.length){
    const gaps=buildNewWellEvidenceGaps(unresolvedNew,shiftsPreview);
    const questions=[
      {id:'new_wells_policy',question:`В Excel есть новые скважины (${unresolvedNew.join(', ')}), которых нет в schedule. Прикрепите траектории (WELLTRACK) и таблицу с перфорациями и стартовыми дебитами.`,expected_format:'text + file attachments (WELLTRACK + xlsx)',required:true,type:'file'},
      ...gaps.slice(0,20).map((g,i)=>({id:`new_well_gap_${i+1}`,question:g.question,expected_format:g.expected_format,required:true,type:'file'})),
    ];
    return{
      contract:'schedule_commissioning_revise_result',
      contract_version:'1.0',
      status:'needs_input',
      generated_schedule:'',
      timeline:model,
      moved:[],
      shifts:[],
      edits:[],
      unlisted_wells_policy:policy,
      unlisted_wells:unlisted,
      new_wells:unresolvedNew,
      monthly_dates_check:checkMonthlyDatesContinuity(model.dates),
      findings:[{code:'NEW_WELLS_REQUIRE_HITL',severity:'error',wells:unresolvedNew,note:'Dates present but WELSPECS/WELLTRACK/COMPDATMD/WCONPROD start params missing'}],
      evidence_gap:gaps,
      questions,
      continuation:{protocol:'schedule-builder-hitl-attachment-v1',evidence_gap:gaps,new_wells:unresolvedNew,unlisted_wells_policy:policy},
      human_request:{kind:'needs_input',questions},
    };
  }

  let removed=[];
  if(policy==='remove'&&unlisted.length){
    const rem=removeUnlistedCommissioning(model,excelWells);
    removed=rem.removed;
  }

  const existingFacts=(wellFacts||[]).filter(f=>baselineSet.has(tlClean(f.well||f.entity)));
  const edited=existingFacts.length?editCommissioningDatesOnTimeline(model,existingFacts):{status:'noop',model,findings:[],edits:[],moved:[],shifts:[]};

  let newApplied=[];
  const newFindings=[];
  if(newWellDefs.length){
    const nw=applyNewWellDefinitions(edited.model,newWellDefs);
    newApplied=nw.applied;
    newFindings.push(...nw.findings);
  }

  const continuity=checkMonthlyDatesContinuity(edited.model.dates);
  const findings=[...(edited.findings||[]),...newFindings];
  if(removed.length)findings.push({code:'UNLISTED_WELLS_REMOVED',severity:'warning',count:removed.length,wells:[...new Set(removed.map(r=>r.well))].slice(0,40)});
  if(policy==='keep'&&unlisted.length)findings.push({code:'UNLISTED_WELLS_KEPT',severity:'warning',wells:unlisted.slice(0,40),note:'Default: preserve starts for wells not in Excel'});
  if(newApplied.length)findings.push({code:'NEW_WELLS_APPLIED',severity:'warning',wells:newApplied.map(a=>a.well)});
  if(!continuity.ok){
    findings.push({code:'MONTHLY_DATES_GAP',severity:'error',gaps:continuity.gaps.slice(0,24),checked_from:continuity.checked_from,gap_count:continuity.gaps.length});
  }
  const after=new Set(edited.model.dates.map(d=>d.iso));
  const missingBaseline=beforeDates.filter(iso=>!after.has(iso));
  if(missingBaseline.length)findings.push({code:'DATES_STEP_REMOVED',severity:'error',missing:missingBaseline.slice(0,24)});
  const hard=findings.filter(f=>f.severity==='error');
  const generated=hard.length?'':emitScheduleFromTimeline(edited.model);
  const status=hard.length?'needs_input':((edited.status==='noop'&&!removed.length&&!newApplied.length)?'noop':'applied');
  return{
    contract:'schedule_commissioning_revise_result',
    contract_version:'1.0',
    status,
    generated_schedule:generated,
    timeline:edited.model,
    edits:edited.edits||[],
    moved:edited.moved||[],
    shifts:edited.shifts||[],
    removed,
    new_wells_applied:newApplied,
    unlisted_wells_policy:policy,
    unlisted_wells:unlisted,
    new_wells:[],
    monthly_dates_check:continuity,
    findings,
    evidence_gap:[],
    continuation:null,
  };
};

/** Wells that already have a WCONPROD record (commissioning identity). */
const listWconprodWells=model=>{
  const wells=new Set();
  for(const rec of model.records||[]){
    if(rec.keyword==='WCONPROD'&&rec.entity)wells.add(tlClean(rec.entity));
  }
  return wells;
};

/**
 * Infer text-only group/GCONPROD rebind from the task + HITL blob.
 * Requires: wells that exist in baseline WCONPROD, a parent group name, a gas rate.
 */
const inferGroupRebindSpec=(blob,model)=>{
  const text=String(blob||'');
  const avail=listWconprodWells(model);
  const tokens=[...text.matchAll(/\b([A-Za-z0-9_.\-]{2,16})\b/g)].map(m=>m[1]);
  const wells=[...new Set(tokens.filter(w=>avail.has(w)))];
  let parent='';
  const quoted=text.match(/["«„]([A-Za-z][A-Za-z0-9_]{0,15})["»“]/);
  if(quoted)parent=quoted[1].toUpperCase();
  if(!parent){
    const after=text.match(/групп[аеуы]?\s*[-–—:]?\s*["«]?\s*([A-Za-z][A-Za-z0-9_]{1,15})/i);
    if(after)parent=after[1].toUpperCase();
  }
  if(!parent&&/\bdks\b/i.test(text))parent='DKS';
  const reserved=new Set(['FIELD','GRAT','OPEN','WELL','RATE','DATES','WELSPECS','GRUPTREE','GCONPROD','WECON','WCONPROD','INCLUDE','SCHEDULE','NORTH','CENTR','METRIC']);
  if(reserved.has(parent))parent='';
  let rate=null;
  const tys=text.match(/(\d+(?:[ \u00a0]\d{3})*)\s*тыс/i);
  if(tys)rate=Number(String(tys[1]).replace(/\s+/g,''))*1000;
  if(rate==null){
    const near=text.match(/(?:grat|gconprod|контрол\w*)[^\d]{0,40}(\d[\d\s]{3,8}\d)/i);
    if(near)rate=Number(String(near[1]).replace(/\s+/g,''));
  }
  if(rate==null){
    const raw=text.match(/\b(\d{5,7})\b/);
    if(raw)rate=Number(raw[1]);
  }
  if(!wells.length||!parent||!Number.isFinite(rate)||rate<=0)return null;
  const well_groups={};
  for(const w of wells)well_groups[w]=`G${w}`;
  return{wells,parent_group:parent,parent_of_parent:'FIELD',well_groups,gas_rate:rate,control:'GRAT'};
};

const firstCommissioningDateForWells=(model,wells)=>{
  const set=new Set((wells||[]).map(tlClean).filter(Boolean));
  let best=null;
  for(const step of model.steps||[]){
    if(!step.date)continue;
    for(const blk of step.blocks||[]){
      if(blk.keyword!=='WCONPROD')continue;
      for(const rec of blk.records||[]){
        if(!set.has(tlClean(rec.entity)))continue;
        if(!best||step.date.epoch_ms<best.epoch_ms)best=step.date;
      }
    }
  }
  return best;
};

const copyFirstKeywordRecords=(model,keyword,wells)=>{
  const set=new Set((wells||[]).map(tlClean).filter(Boolean));
  const seen=new Set();
  const out=[];
  for(const rec of model.records||[]){
    const w=tlClean(rec.entity);
    if(rec.keyword!==keyword||!set.has(w)||seen.has(w))continue;
    seen.add(w);
    out.push({entity:w,raw_line:rec.raw_line});
  }
  return out;
};

/**
 * On the first WCONPROD DATES of the named wells, ADD WELSPECS/GRUPTREE/GCONPROD
 * and re-emit existing WECON/WPIMULT rows for those wells (MAS golden_case_2).
 */
const applyGroupRebindOnTimeline=(model,spec)=>{
  const wells=(spec.wells||[]).map(tlClean).filter(Boolean);
  const parent=tlClean(spec.parent_group).toUpperCase();
  const rate=Number(spec.gas_rate);
  const findings=[];
  if(!wells.length||!parent||!Number.isFinite(rate)||rate<=0){
    return{status:'noop',model,findings:[{code:'GROUP_REBIND_SPEC_INVALID',severity:'error'}],edits:[]};
  }
  const date=spec.effective_at?parseScheduleDate(spec.effective_at):firstCommissioningDateForWells(model,wells);
  if(!date){
    return{status:'needs_input',model,findings:[{code:'GROUP_REBIND_COMMISSIONING_DATE_MISSING',severity:'error',wells}],edits:[]};
  }
  const wecon=copyFirstKeywordRecords(model,'WECON',wells);
  const wpimult=copyFirstKeywordRecords(model,'WPIMULT',wells);
  const step=ensureDatesStep(model,date);
  const groups=wells.map(w=>(spec.well_groups&&spec.well_groups[w])||`G${w}`);
  const edits=[];
  upsertKeywordRecords(step,'WELSPECS',wells.map((w,i)=>({entity:w,raw_line:`${w} ${groups[i]} /`})));
  edits.push({op:'add',keyword:'WELSPECS',entities:wells.slice()});
  upsertKeywordRecords(step,'GRUPTREE',[{entity:parent,raw_line:`${parent} FIELD /`},...wells.map((w,i)=>({entity:groups[i],raw_line:`${groups[i]} ${parent} /`}))]);
  edits.push({op:'add',keyword:'GRUPTREE',entity:parent});
  upsertKeywordRecords(step,'GCONPROD',[{entity:parent,raw_line:`${parent} GRAT 2* ${rate} /`}]);
  edits.push({op:'add',keyword:'GCONPROD',entity:parent,gas_rate:rate});
  if(wecon.length){upsertKeywordRecords(step,'WECON',wecon);edits.push({op:'reemit',keyword:'WECON',entities:wecon.map(x=>x.entity)});}
  if(wpimult.length){upsertKeywordRecords(step,'WPIMULT',wpimult);edits.push({op:'reemit',keyword:'WPIMULT',entities:wpimult.map(x=>x.entity)});}
  refreshTimelineFlat(model);
  return{status:'applied',model,findings,edits,effective_at:step.effective_at,dates_tnav:step.dates_tnav,wells,parent_group:parent,gas_rate:rate};
};

const runGroupRebindRevise=(text,spec,fileRef='schedule.inc')=>{
  const model=parseScheduleTimeline(text,fileRef);
  const beforeDates=model.dates.map(d=>d.iso);
  const edited=applyGroupRebindOnTimeline(model,spec||{});
  const continuity=checkMonthlyDatesContinuity(edited.model.dates);
  const findings=[...(edited.findings||[])];
  if(!continuity.ok)findings.push({code:'MONTHLY_DATES_GAP',severity:'error',gaps:continuity.gaps.slice(0,24),checked_from:continuity.checked_from,gap_count:continuity.gaps.length});
  const after=new Set(edited.model.dates.map(d=>d.iso));
  const missingBaseline=beforeDates.filter(iso=>!after.has(iso));
  if(missingBaseline.length)findings.push({code:'DATES_STEP_REMOVED',severity:'error',missing:missingBaseline.slice(0,24)});
  const hard=findings.filter(f=>f.severity==='error');
  const generated=hard.length?'':emitScheduleFromTimeline(edited.model);
  return{
    contract:'schedule_group_rebind_revise_result',
    contract_version:'1.0',
    kind:'group_rebind',
    status:hard.length?'needs_input':edited.status,
    generated_schedule:generated,
    timeline:edited.model,
    edits:edited.edits||[],
    wells:edited.wells||spec.wells||[],
    parent_group:edited.parent_group||spec.parent_group||null,
    gas_rate:edited.gas_rate??spec.gas_rate??null,
    effective_at:edited.effective_at||null,
    dates_tnav:edited.dates_tnav||null,
    monthly_dates_check:continuity,
    findings,
    evidence_gap:[],
    continuation:null,
  };
};
"""


def build_commissioning_revise_js() -> str:
    """n8n Code node: apply parse→edit(retarget WCONPROD)→emit when Excel facts exist."""
    core = timeline_core_js()
    return (
        r"""
const root=$('Normalize SCHEDULE pipeline packet').first().json;
const intake=$('Run deterministic SCHEDULE intake').first().json;
const b=$('Validate SCHEDULE builder stage').first().json;
const m=$('Merge SCHEDULE draft deterministically').first().json;
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray;
"""
        + core
        + r"""
const req=root.request||{};
const packet=obj(req.source_facts_packet)?req.source_facts_packet:{};
const facts=arr(packet.facts)?packet.facts:(arr(req.source_facts)?req.source_facts:[]);
const wellFacts=facts.map(f=>{
  const values=obj(f.values)?f.values:{};
  const well=tlClean(f.well||f.entity||f.entity_id||values['Скважина']||values.скважина||values.WELL||values.well);
  const date=f.value??f.raw_value??values['Дата ввода']??values.date??null;
  return{well,date,fact_id:f.fact_id||null};
}).filter(f=>f.well&&f.date!==null&&f.date!==undefined);

const instructionBlob=[req.objective,req.problem_statement,req.user_goal,req.task,req.instruction,intake.objective,root.packet?.objective,JSON.stringify(req.requested_change_scope||{}),JSON.stringify(req.controls||{}),JSON.stringify(root.latest_human_response||{})].filter(Boolean).join('\n');
// Explicit enum only — prose remove is resolved inside runCommissioningRevise as needs_input.
const hr=obj(root.latest_human_response)?root.latest_human_response:{};
const hrAnswerPolicy=(()=>{const answers=arr(hr.answers)?hr.answers:[];for(const a of answers){const id=tlClean(a?.question_id||a?.id).toLowerCase();const ans=tlClean(a?.answer||a?.value);if(id.includes('unlisted')&&/\b(keep|remove)\b/i.test(ans)){const m=ans.match(/\b(keep|remove)\b/i);return m?m[1].toLowerCase():''}}return ''})();
const unlistedPolicy=normalizeUnlistedWellsPolicy(req.unlisted_wells_policy)||normalizeUnlistedWellsPolicy(req.controls?.unlisted_wells_policy)||normalizeUnlistedWellsPolicy(hr.unlisted_wells_policy)||normalizeUnlistedWellsPolicy(hrAnswerPolicy)||normalizeUnlistedWellsPolicy(hr.text)||'';
const newWellDefs=arr(req.new_well_defs)?req.new_well_defs:(arr(hr.new_well_defs)?hr.new_well_defs:(arr(req.hitl_new_well_defs)?req.hitl_new_well_defs:(arr(packet.new_well_defs)?packet.new_well_defs:[])));

const rootPath=tlClean(m.output_package?.root_path||req.root_path||'schedule.inc')||'schedule.inc';
let baselineText='';
try{
  const analysis=$('Analyze lossless baseline inventory').first().json;
  const files=arr(analysis?.package?.files)?analysis.package.files:[];
  const hit=files.find(f=>tlClean(f.file_ref)===rootPath)||files[0];
  baselineText=hit?String(hit.text||''):'';
}catch{}
if(!baselineText)baselineText=String(req.baseline_schedule_text||m.generated_schedule||'');

if(intake.build_mode!=='REVISE'||!wellFacts.length){
  if(intake.build_mode==='REVISE'&&!wellFacts.length&&baselineText){
    const spec=inferGroupRebindSpec(instructionBlob,parseScheduleTimeline(baselineText,rootPath));
    if(spec){
      const result=runGroupRebindRevise(baselineText,spec,rootPath);
      if(result.status==='applied'){
        const contentHash=s=>{let h=2166136261;for(const ch of String(s??'')){h^=ch.charCodeAt(0);h=Math.imul(h,16777619)}return`fnv1a32:${(h>>>0).toString(16).padStart(8,'0')}`};
        const hash=contentHash;
        const text=result.generated_schedule;
        const files=arr(m.output_package?.files)?m.output_package.files.map(f=>{
          if(tlClean(f.file_ref)!==rootPath)return f;
          return{...f,text,manifest:{...(obj(f.manifest)?f.manifest:{}),file_ref:rootPath,sha256:hash(text),char_length:text.length,line_count:text.split(/\r\n|\n|\r/).length}};
        }):[{file_ref:rootPath,text,manifest:{file_ref:rootPath,sha256:hash(text)}}];
        const packageHash=hash(files.slice().sort((a,b)=>String(a.file_ref).localeCompare(String(b.file_ref))).map(f=>`${f.file_ref}|${f.manifest?.sha256||hash(f.text)}`).join('\n'));
        return[{json:{
          ...m,
          status:'merged',
          generated_schedule:text,
          output_hash:hash(text),
          output_package:{...(obj(m.output_package)?m.output_package:{}),root_path:rootPath,package_hash:packageHash,files},
          commissioning_revise:result,
          preservation_report:{...(obj(m.preservation_report)?m.preservation_report:{}),policy:'preserve_unmentioned',zero_change_byte_identical:false,group_rebind_applied:true,edit_count:(result.edits||[]).length},
          semantic_diff:{changed_keywords:[...new Set((result.edits||[]).map(x=>x.keyword).filter(Boolean))],include_graph_changed:false,group_rebind_wells:result.wells||[],edits:(result.edits||[]).slice(0,40)},
          findings:[...(arr(m.findings)?m.findings:[]),{code:'GROUP_REBIND_TIMELINE_REVISE_APPLIED',severity:'warning',edits:(result.edits||[]).length,wells:result.wells,parent_group:result.parent_group,gas_rate:result.gas_rate},...((result.findings||[]).filter(f=>f.severity==='warning'))],
        }}];
      }
    }
  }
  return[{json:{...m,commissioning_revise:{status:'skipped',reason:!wellFacts.length?'no_well_facts':'not_revise'},monthly_dates_check:checkMonthlyDatesContinuity(parseScheduleTimeline(m.generated_schedule||baselineText).dates)}}];
}

const result=runCommissioningRevise(baselineText,wellFacts,rootPath,{unlisted_wells_policy:unlistedPolicy,new_well_defs:newWellDefs,instruction_blob:instructionBlob});
if(result.status!=='applied'){
  const gaps=arr(result.evidence_gap)?result.evidence_gap:[];
  return[{json:{
    ...m,
    status:result.status==='noop'?'merged':'needs_input',
    contract:result.status==='needs_input'?'schedule_commissioning_revise_result':m.contract,
    commissioning_revise:result,
    evidence_gap:gaps,
    questions:arr(result.questions)?result.questions:[],
    continuation:result.continuation||null,
    human_request:result.human_request||null,
    summary:result.status==='needs_input'?(result.findings||[]).some(f=>f.code==='UNLISTED_WELLS_POLICY_REQUIRED')?`Нужно решение: сохранить или убрать скважины вне Excel (${(result.unlisted_wells||[]).slice(0,12).join(', ')})`:`Нужны траектории и параметры для новых скважин: ${(result.new_wells||[]).join(', ')}`:'Сдвиг дат ввода остановлен',
    findings:[...(arr(m.findings)?m.findings:[]),...result.findings],
  }}];
}

const contentHash=s=>{let h=2166136261;for(const ch of String(s??'')){h^=ch.charCodeAt(0);h=Math.imul(h,16777619)}return`fnv1a32:${(h>>>0).toString(16).padStart(8,'0')}`};
const hash=contentHash;
const text=result.generated_schedule;
const files=arr(m.output_package?.files)?m.output_package.files.map(f=>{
  if(tlClean(f.file_ref)!==rootPath)return f;
  return{...f,text,manifest:{...(obj(f.manifest)?f.manifest:{}),file_ref:rootPath,sha256:hash(text),char_length:text.length,line_count:text.split(/\r\n|\n|\r/).length}};
}):[{file_ref:rootPath,text,manifest:{file_ref:rootPath,sha256:hash(text)}}];
const packageHash=hash(files.slice().sort((a,b)=>String(a.file_ref).localeCompare(String(b.file_ref))).map(f=>`${f.file_ref}|${f.manifest?.sha256||hash(f.text)}`).join('\n'));
return[{json:{
  ...m,
  status:'merged',
  generated_schedule:text,
  output_hash:hash(text),
  output_package:{...(obj(m.output_package)?m.output_package:{}),root_path:rootPath,package_hash:packageHash,files},
  commissioning_revise:result,
  preservation_report:{...(obj(m.preservation_report)?m.preservation_report:{}),policy:result.unlisted_wells_policy==='remove'?'remove_unlisted_commissioning':'preserve_unmentioned',zero_change_byte_identical:false,commissioning_edit_applied:true,edit_count:(result.edits||result.moved||[]).length,moved_count:(result.moved||[]).length,removed_count:(result.removed||[]).length},
  semantic_diff:{changed_keywords:[...new Set([...(result.edits||result.moved||[]).map(x=>x.keyword||x.entity),...((result.removed||[]).map(x=>x.keyword))])],include_graph_changed:Boolean((result.new_wells_applied||[]).length),commissioning_wells:(result.shifts||[]).map(s=>s.well),edits:(result.edits||[]).slice(0,40)},
  findings:[...(arr(m.findings)?m.findings:[]),{code:'COMMISSIONING_TIMELINE_REVISE_APPLIED',severity:'warning',edits:(result.edits||[]).length,moved:(result.moved||[]).length,wells:(result.shifts||[]).map(s=>s.well),unlisted_policy:result.unlisted_wells_policy},...result.findings.filter(f=>f.severity==='warning')],
}}];
"""
    )


def build_timeline_validate_addon_js() -> str:
    """Snippet conceptually documenting monthly check codes (used inline in semantic runtime)."""
    return json.dumps(
        {
            "MONTHLY_DATES_GAP": "error",
            "DATES_STEP_REMOVED": "error",
            "TARGET_DATES_MISSING": "error",
            "NEW_WELLS_REQUIRE_HITL": "error",
            "UNLISTED_WELLS_REMOVED": "warning",
            "UNLISTED_WELLS_KEPT": "warning",
        }
    )
