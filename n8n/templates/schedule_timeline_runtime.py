"""Timeline SCHEDULE model: parse → mutate → emit.

Internal shape (conceptual):
  steps[] where each step has effective_at (DATES clock) and keyword blocks
  with records {keyword, entity, raw_line, ...}.

Commissioning date revise moves the *first* WELOPEN / WCONPROD / WEFAC record
per well onto the Excel target date. DATES steps are never deleted — empty
monthly calculation steps stay as DATES-only.

Policies:
  - unlisted_wells_policy=keep (default): baseline wells absent from Excel keep starts
  - unlisted_wells_policy=remove: strip WELOPEN/WCONPROD/WEFAC for those wells
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
const MOVE_KEYWORDS=new Set(['WELOPEN','WCONPROD','WEFAC']);
const tlClean=v=>typeof v==='string'?v.trim():'';
const parseTnavDate=raw=>{
  const t=tlClean(raw).toUpperCase();
  let m=t.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  let y,mo,d;
  if(m){y=Number(m[1]);mo=Number(m[2])-1;d=Number(m[3]);}
  else{m=t.match(/^(\d{1,2})\s+([A-Z]{3})\s+(\d{4})$/);if(!m||MONTH[m[2]]===undefined)return null;d=Number(m[1]);mo=MONTH[m[2]];y=Number(m[3]);}
  const epoch=Date.UTC(y,mo,d),dt=new Date(epoch);
  if(dt.getUTCFullYear()!==y||dt.getUTCMonth()!==mo||dt.getUTCDate()!==d)return null;
  return{raw:t,iso:`${String(y).padStart(4,'0')}-${String(mo+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`,epoch,day:d,month:mo+1,year:y};
};
const toTnavDate=raw=>{
  const s=String(raw||'').trim();
  const iso=s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if(iso)return `1 ${MONTHS[Number(iso[2])-1]} ${iso[1]}`;
  const already=s.match(/^(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})$/);
  return already?`${Number(already[1])} ${already[2].toUpperCase()} ${already[3]}`:s;
};
const addMonthIso=iso=>{
  const p=parseTnavDate(iso);if(!p)return null;
  let y=p.year,m=p.month+1;if(m>12){y+=1;m=1;}
  return `${String(y).padStart(4,'0')}-${String(m).padStart(2,'0')}-01`;
};
const entityFromRecordLine=(kw,line)=>{
  const t=String(line||'').replace(/--.*$/,'').trim();
  if(!t||t==='/'||t.startsWith('*'))return null;
  const tok=t.split(/\s+/)[0];
  if(!tok||!/^[A-Za-z0-9_.\-]+$/.test(tok))return null;
  if(kw==='DATES')return null;
  return tok;
};

/** Detect keep|remove for baseline wells missing from Excel. Default = keep. */
const detectUnlistedWellsPolicy=blob=>{
  const t=String(blob||'').toLowerCase().replace(/\s+/g,' ');
  const remove=
    /(убрат|удал|remove|drop|delete).{0,120}(нет в (файле|excel|книге|workbook)|not (present |listed )?in (the )?(excel|file|workbook|xlsx))/.test(t)
    ||/(нет в (файле|excel|книге|workbook).{0,80}(убрат|удал)|скважин.{0,40}нет в (файле|excel).{0,40}(убрат|удал))/.test(t)
    ||/(wells?.{0,60}(not|absent).{0,40}(excel|file|workbook).{0,40}(remove|drop|delete|убрат))/.test(t)
    ||/unlisted_wells_policy\s*[:=]\s*remove/.test(t)
    ||/\bremove_unlisted\b/.test(t);
  return remove?'remove':'keep';
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
  let clock=null,clockRaw=null;
  const steps=[];
  const ensureStep=()=>{
    const key=clock?clock.iso:'__preamble__';
    let step=steps.find(s=>s._key===key);
    if(!step){
      step={_key:key,effective_at:clock?clock.iso:null,dates_tnav:clockRaw,dates_header:null,dates_body:null,blocks:[]};
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
      let parsed=null,raw=null;
      for(const line of b.body){
        const cand=line.replace(/--.*$/,'').replace(/\/.*$/,'').trim();
        if(!cand)continue;
        parsed=parseTnavDate(cand);
        if(parsed){raw=cand;break;}
      }
      if(parsed){clock=parsed;clockRaw=raw;}
      const step=ensureStep();
      step.dates_header=b.header;
      step.dates_body=b.body.slice();
      step.dates_tnav=raw||step.dates_tnav;
      step.effective_at=parsed?parsed.iso:step.effective_at;
      continue;
    }
    const step=ensureStep();
    const records=[];
    for(const line of b.body){
      const t=line.trim();
      if(!t||t.startsWith('--')||t==='/')continue;
      records.push({entity:entityFromRecordLine(b.kw,line),raw_line:line,effective_at:step.effective_at});
    }
    step.blocks.push({kind:'keyword',keyword:b.kw,header:b.header,body:b.body.slice(),records});
  }
  const flat=[];
  for(const step of steps){
    for(const blk of step.blocks){
      if(blk.kind!=='keyword')continue;
      for(const rec of blk.records){
        flat.push({keyword:blk.keyword,entity:rec.entity,raw_line:rec.raw_line,effective_at:step.effective_at,dates_tnav:step.dates_tnav});
      }
    }
  }
  return{
    contract:'schedule_timeline_model',
    contract_version:'1.0',
    file_ref:fileRef,
    steps,
    records:flat,
    dates:steps.filter(s=>s.effective_at).map(s=>({iso:s.effective_at,tnav:s.dates_tnav})),
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
    for(const blk of step.blocks){
      if(blk.kind!=='keyword')continue;
      for(const rec of blk.records){
        model.records.push({keyword:blk.keyword,entity:rec.entity,raw_line:rec.raw_line,effective_at:step.effective_at,dates_tnav:step.dates_tnav});
      }
    }
  }
  model.dates=model.steps.filter(s=>s.effective_at).map(s=>({iso:s.effective_at,tnav:s.dates_tnav}));
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

const ensureDatesStep=(model,iso)=>{
  let step=model.steps.find(s=>s.effective_at===iso);
  if(step)return step;
  const parsed=parseTnavDate(iso);
  step={_key:iso,effective_at:iso,dates_tnav:toTnavDate(iso),dates_header:'DATES',dates_body:[`  ${toTnavDate(iso)} /`,'/'],blocks:[]};
  const idx=model.steps.findIndex(s=>s.effective_at&&parseTnavDate(s.effective_at)?.epoch>parsed.epoch);
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

/** (2) Mutate timeline: shift first commissioning records per well */
const shiftCommissioningOnTimeline=(model,wellFacts)=>{
  const findings=[];
  const shifts=new Map();
  for(const f of wellFacts||[]){
    const well=tlClean(f.well||f.entity);
    const date=f.date??f.value??null;
    if(!well||date===null||date===undefined)continue;
    const tnav=toTnavDate(date);
    const parsed=parseTnavDate(tnav);
    if(!parsed){findings.push({code:'COMMISSIONING_DATE_INVALID',severity:'error',well,date:String(date)});continue;}
    shifts.set(well,{tnav,iso:parsed.iso,epoch:parsed.epoch});
  }
  if(!shifts.size)return{status:'noop',model,findings,moved:[]};

  // Ensure target DATES steps exist (should already — we never invent months unless missing).
  for(const [well,sh] of shifts){
    if(!model.steps.some(s=>s.effective_at===sh.iso)){
      findings.push({code:'TARGET_DATES_MISSING',severity:'error',well,dates:sh.tnav});
    }
  }

  const moved=[];
  const takeFirst=new Map(); // well|kw -> {raw_line, from_iso}
  for(const step of model.steps){
    for(const blk of step.blocks){
      if(blk.kind!=='keyword'||!MOVE_KEYWORDS.has(blk.keyword))continue;
      const keep=[];
      for(const rec of blk.records){
        const well=tlClean(rec.entity);
        if(!well||!shifts.has(well)){keep.push(rec);continue;}
        const key=`${well}|${blk.keyword}`;
        if(takeFirst.has(key)){keep.push(rec);continue;}
        takeFirst.set(key,{raw_line:rec.raw_line,from_iso:step.effective_at,keyword:blk.keyword,well});
        moved.push({well,keyword:blk.keyword,from:step.effective_at,to:shifts.get(well).iso,tnav:shifts.get(well).tnav});
      }
      blk.records=keep;
      // rebuild body from remaining records + trailing /
      if(blk.records.length){
        blk.body=blk.records.map(r=>r.raw_line);
        if(!blk.body.some(l=>tlClean(l)==='/'))blk.body.push('/');
      }else{
        blk.body=[];
      }
    }
    // drop emptied move-keyword blocks
    step.blocks=step.blocks.filter(blk=>blk.kind!=='keyword'||!MOVE_KEYWORDS.has(blk.keyword)||blk.records.length>0);
  }

  // Insert onto target steps
  const byTarget=new Map();
  for(const [key,item] of takeFirst){
    const sh=shifts.get(item.well);
    if(!sh)continue;
    if(!byTarget.has(sh.iso))byTarget.set(sh.iso,[]);
    byTarget.get(sh.iso).push(item);
  }
  for(const [iso,items] of byTarget){
    let step=model.steps.find(s=>s.effective_at===iso);
    if(!step){
      // create DATES-only step in chronological order
      const parsed=parseTnavDate(iso);
      step={_key:iso,effective_at:iso,dates_tnav:toTnavDate(iso),dates_header:'DATES',dates_body:[`  ${toTnavDate(iso)} /`,'/'],blocks:[]};
      const idx=model.steps.findIndex(s=>s.effective_at&&parseTnavDate(s.effective_at)?.epoch>parsed.epoch);
      if(idx<0)model.steps.push(step);else model.steps.splice(idx,0,step);
      findings.push({code:'DATES_STEP_CREATED',severity:'warning',iso,tnav:step.dates_tnav});
    }
    const order=['WELOPEN','WCONPROD','WEFAC'];
    for(const kw of order){
      const chunk=items.filter(x=>x.keyword===kw);
      if(!chunk.length)continue;
      let blk=step.blocks.find(b=>b.kind==='keyword'&&b.keyword===kw);
      if(!blk){
        blk={kind:'keyword',keyword:kw,header:kw,body:[],records:[]};
        step.blocks.push(blk);
      }
      for(const it of chunk){
        blk.records.push({entity:it.well,raw_line:it.raw_line,effective_at:iso});
      }
      blk.body=blk.records.map(r=>r.raw_line);
      if(!blk.body.some(l=>tlClean(l)==='/'))blk.body.push('/');
    }
  }

  refreshTimelineFlat(model);
  const hard=findings.filter(f=>f.severity==='error');
  return{status:hard.length?'needs_input':'applied',model,findings,moved,shifts:[...shifts.entries()].map(([well,s])=>({well,...s}))};
};

/** Monthly 1st continuity over the monthly cadence region (stops at annual jumps). */
const checkMonthlyDatesContinuity=dates=>{
  const list=(dates||[]).map(d=>typeof d==='string'?parseTnavDate(d):parseTnavDate(d.iso||d.tnav||d.raw)).filter(Boolean)
    .filter(d=>d.day===1).sort((a,b)=>a.epoch-b.epoch);
  const gaps=[];
  for(let i=0;i<list.length-1;i++){
    const cur=list[i],next=list[i+1];
    const expIso=addMonthIso(cur.iso);
    const exp=parseTnavDate(expIso);
    if(!exp)continue;
    if(next.epoch===exp.epoch)continue;
    // Intentional annual section: Jan→Jan next year (or gap ≥ 12 months)
    const monthDelta=(next.year-cur.year)*12+(next.month-cur.month);
    if(monthDelta>=12)break;
    let walk=exp;
    while(walk.epoch<next.epoch){
      gaps.push({iso:walk.iso,tnav:toTnavDate(walk.iso)});
      walk=parseTnavDate(addMonthIso(walk.iso));
      if(!walk)break;
    }
  }
  return{ok:!gaps.length,gaps,checked_from:list[0]?.iso||null,checked_through:list.length?list[Math.max(0,list.length-1-(gaps.length?0:0))]?.iso:null,monthly_count:list.length};
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
    if(step.dates_header){
      out.push(step.dates_header);
      for(const line of(step.dates_body||[]))out.push(line);
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
 */
const runCommissioningRevise=(text,wellFacts,fileRef='schedule.inc',options={})=>{
  const model=parseScheduleTimeline(text,fileRef);
  const beforeDates=model.dates.map(d=>d.iso);
  const policy=tlClean(options.unlisted_wells_policy)||detectUnlistedWellsPolicy(options.instruction_blob||'');
  const excelWells=[...new Set((wellFacts||[]).map(f=>tlClean(f.well||f.entity)).filter(Boolean))];
  const baselineWells=listBaselineCommissioningWells(model);
  const baselineSet=new Set(baselineWells);
  const excelSet=new Set(excelWells);
  const unlisted=[...baselineSet].filter(w=>!excelSet.has(w)).sort();
  const newWells=excelWells.filter(w=>!baselineSet.has(w)).sort();
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
      {id:'new_wells_policy',question:`В Excel есть новые скважины (${unresolvedNew.join(', ')}), которых нет в baseline schedule. Что делать? Прикрепите WELLTRACK (.inc) и xlsx с COMPDATMD (MD_TOP/MD_BOT) и стартовыми GRAT — через Human Gate files.`,expected_format:'text + file attachments (WELLTRACK + xlsx)',required:true,type:'file'},
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
  const shifted=existingFacts.length?shiftCommissioningOnTimeline(model,existingFacts):{status:'noop',model,findings:[],moved:[],shifts:[]};

  let newApplied=[];
  const newFindings=[];
  if(newWellDefs.length){
    const nw=applyNewWellDefinitions(shifted.model,newWellDefs);
    newApplied=nw.applied;
    newFindings.push(...nw.findings);
  }

  const continuity=checkMonthlyDatesContinuity(shifted.model.dates);
  const findings=[...(shifted.findings||[]),...newFindings];
  if(removed.length)findings.push({code:'UNLISTED_WELLS_REMOVED',severity:'warning',count:removed.length,wells:[...new Set(removed.map(r=>r.well))].slice(0,40)});
  if(policy==='keep'&&unlisted.length)findings.push({code:'UNLISTED_WELLS_KEPT',severity:'warning',wells:unlisted.slice(0,40),note:'Default: preserve starts for wells not in Excel'});
  if(newApplied.length)findings.push({code:'NEW_WELLS_APPLIED',severity:'warning',wells:newApplied.map(a=>a.well)});
  if(!continuity.ok){
    findings.push({code:'MONTHLY_DATES_GAP',severity:'error',gaps:continuity.gaps.slice(0,24),checked_from:continuity.checked_from,gap_count:continuity.gaps.length});
  }
  const after=new Set(shifted.model.dates.map(d=>d.iso));
  const missingBaseline=beforeDates.filter(iso=>!after.has(iso));
  if(missingBaseline.length)findings.push({code:'DATES_STEP_REMOVED',severity:'error',missing:missingBaseline.slice(0,24)});
  const hard=findings.filter(f=>f.severity==='error');
  const generated=hard.length?'':emitScheduleFromTimeline(shifted.model);
  const status=hard.length?'needs_input':((shifted.status==='noop'&&!removed.length&&!newApplied.length)?'noop':'applied');
  return{
    contract:'schedule_commissioning_revise_result',
    contract_version:'1.0',
    status,
    generated_schedule:generated,
    timeline:shifted.model,
    moved:shifted.moved||[],
    shifts:shifted.shifts||[],
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
"""


def build_commissioning_revise_js() -> str:
    """n8n Code node: apply parse→mutate→emit when Excel commissioning facts exist."""
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

const instructionBlob=[req.objective,req.problem_statement,req.user_goal,req.task,req.instruction,intake.objective,root.packet?.objective,JSON.stringify(req.requested_change_scope||{}),JSON.stringify(req.controls||{})].filter(Boolean).join('\n');
const unlistedPolicy=tlClean(req.unlisted_wells_policy)||tlClean(req.controls?.unlisted_wells_policy)||detectUnlistedWellsPolicy(instructionBlob);
const newWellDefs=arr(req.new_well_defs)?req.new_well_defs:(arr(req.hitl_new_well_defs)?req.hitl_new_well_defs:(arr(packet.new_well_defs)?packet.new_well_defs:[]));

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
    summary:result.status==='needs_input'?`HITL: new wells need WELLTRACK/WELSPECS/COMPDATMD/WCONPROD (${(result.new_wells||[]).join(', ')})`:'Commissioning timeline revise blocked',
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
  preservation_report:{...(obj(m.preservation_report)?m.preservation_report:{}),policy:result.unlisted_wells_policy==='remove'?'remove_unlisted_commissioning':'preserve_unmentioned',zero_change_byte_identical:false,commissioning_shift_applied:true,moved_count:(result.moved||[]).length,removed_count:(result.removed||[]).length},
  semantic_diff:{changed_keywords:[...new Set([...(result.moved||[]).map(x=>x.keyword),...((result.removed||[]).map(x=>x.keyword))])],include_graph_changed:Boolean((result.new_wells_applied||[]).length),commissioning_wells:(result.shifts||[]).map(s=>s.well)},
  findings:[...(arr(m.findings)?m.findings:[]),{code:'COMMISSIONING_TIMELINE_REVISE_APPLIED',severity:'warning',moved:(result.moved||[]).length,wells:(result.shifts||[]).map(s=>s.well),unlisted_policy:result.unlisted_wells_policy},...result.findings.filter(f=>f.severity==='warning')],
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
