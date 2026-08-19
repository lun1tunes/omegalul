"""Catalogue-driven SCHEDULE IR renderer for portable n8n Code nodes.

No field layout is embedded here.  The runtime accepts a content-addressed,
version-pinned machine-readable catalogue maintained by the department expert
and renders typed IR only after validating every field against that catalogue.
"""
from __future__ import annotations

from schedule_emit_order import within_date_order_js
from schedule_lossless_runtime import SHA256_JS


def build_schema_renderer_js(keywords: list[str]) -> str:
    import json

    allowed = json.dumps(keywords, ensure_ascii=False)
    return r"""
const x=$json.schedule_render_request??$json;
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'';
const allowed=new Set(__KEYWORDS__),findings=[];
const mode=clean(x.mode||x.build_mode).toUpperCase(),catalogue=obj(x.schema_catalogue)?x.schema_catalogue:{},events=arr(x.ir_events)?x.ir_events.filter(obj):[];
__SHA256_JS__
__WITHIN_DATE_ORDER_JS__
const hash=contentHash,shaPattern=/^sha256:[a-f0-9]{64}$/i;
const profile=obj(catalogue.simulator_profile)?catalogue.simulator_profile:{};
const schemas=arr(catalogue.schemas)?catalogue.schemas.filter(obj):[];
const schemaMap=new Map(),eventIds=new Set(),renderedChanges=[],renderedRecords=[];
const sourceHash=clean(catalogue.source_hash).toLowerCase(),catalogueHash=clean(catalogue.catalogue_hash).toLowerCase();
if(!['CREATE','REVISE'].includes(mode))findings.push({code:'RENDER_MODE_INVALID',severity:'error'});
if(catalogue.contract!=='schedule_schema_catalogue'||catalogue.contract_version!=='1.0')findings.push({code:'SCHEMA_CATALOGUE_CONTRACT_INVALID',severity:'error'});
if(clean(profile.vendor)!=='Rock Flow Dynamics'||clean(profile.simulator).toLowerCase()!=='tnavigator'||clean(profile.version)!=='22.2')findings.push({code:'SCHEMA_PROFILE_NOT_APPROVED',severity:'error'});
if(!clean(catalogue.approved_by||catalogue.author))findings.push({code:'SCHEMA_EXPERT_AUTHOR_REQUIRED',severity:'error'});
if(!shaPattern.test(sourceHash))findings.push({code:'SCHEMA_SOURCE_HASH_INVALID',severity:'error'});
if(!shaPattern.test(catalogueHash))findings.push({code:'SCHEMA_CATALOGUE_HASH_INVALID',severity:'error'});
if(!schemas.length)findings.push({code:'SCHEMA_CATALOGUE_EMPTY',severity:'error'});
const citationValid=c=>obj(c)&&clean(c.document_id||c.knowledge_id)&&clean(c.document_revision||c.revision);
// Merged catalogues recompute source_hash; do not require citation.source_hash === catalogue.source_hash.
const citationAcceptable=c=>!c||citationValid(c); // missing citation: warn below; invalid shape: error

const normalizeLayout=raw=>{const l=obj(raw)?raw:{},newline=clean(l.newline).toUpperCase()==='CRLF'?'\r\n':'\n';return{newline,indent:l.indent==='    '?'    ':'  ',delimiter:l.delimiter==='TAB'?'\t':' ',record_terminator:clean(l.record_terminator).toUpperCase()==='NONE'?'':' /',block_terminator:clean(l.block_terminator).toUpperCase()==='NONE'?'none':'slash_line'}};
for(let i=0;i<schemas.length;i++){
  const s=schemas[i],kw=clean(s.keyword).toUpperCase(),variant=clean(s.variant)||'default',fields=arr(s.fields)?s.fields.filter(obj):[];
  const key=`${kw}::${variant}`;
  if(!allowed.has(kw)){findings.push({code:'SCHEMA_KEYWORD_UNSUPPORTED',severity:'error',index:i,keyword:kw});continue}
  if(!clean(s.schema_id)||!clean(s.schema_revision)){findings.push({code:'SCHEMA_IDENTITY_REQUIRED',severity:'error',index:i,keyword:kw});continue}
  if(!citationValid(s.citation)){
    if(s.citation==null||s.citation===undefined)findings.push({code:'SCHEMA_CITATION_MISSING',severity:'warning',index:i,keyword:kw});
    else {findings.push({code:'SCHEMA_CITATION_INVALID',severity:'error',index:i,keyword:kw});continue}
  }
  if(!obj(s.semantics)){findings.push({code:'SCHEMA_SEMANTICS_REQUIRED',severity:'error',index:i,keyword:kw,variant});continue}
  if(schemaMap.has(key)){findings.push({code:'SCHEMA_VARIANT_DUPLICATE',severity:'error',index:i,keyword:kw,variant});continue}
  const positions=fields.map(f=>Number(f.position)),names=fields.map(f=>clean(f.name));
  const parser=obj(s.parser)?s.parser:{},layoutRaw=obj(s.layout)?s.layout:{};
  const recordless=!fields.length&&Number(parser.token_width)===0&&clean(layoutRaw.record_terminator).toUpperCase()==='NONE'&&clean(layoutRaw.block_terminator).toUpperCase()==='NONE';
  if(!recordless&&(!fields.length||positions.some((p,j)=>!Number.isInteger(p)||p!==j+1)||names.some(v=>!v)||new Set(names).size!==names.length)){findings.push({code:'SCHEMA_FIELDS_INVALID',severity:'error',index:i,keyword:kw});continue}
  const badType=fields.find(f=>!['string','integer','number','date','enum','boolean'].includes(clean(f.type).toLowerCase()));
  if(badType){findings.push({code:'SCHEMA_FIELD_TYPE_UNSUPPORTED',severity:'error',index:i,keyword:kw,field:badType.name});continue}
  schemaMap.set(key,{...s,keyword:kw,variant,fields:fields.slice().sort((a,b)=>Number(a.position)-Number(b.position)),layout:normalizeLayout(s.layout)});
}
const decimal=v=>{if(typeof v==='number')return Number.isFinite(v)?String(v):null;if(typeof v!=='string')return null;const t=v.trim();return /^[-+]?(?:\d+(?:\.\d*)?|\.\d+)$/.test(t)?t:null};
const integer=v=>{if(typeof v==='number')return Number.isSafeInteger(v)?String(v):null;if(typeof v!=='string')return null;const t=v.trim();return /^[-+]?\d+$/.test(t)?t:null};
const quote=v=>`'${String(v).replace(/'/g,"''")}'`;
const renderDate=(v,format)=>{const t=clean(v),m=t.match(/^(\d{4})-(\d{2})-(\d{2})$/);if(!m)return null;const y=Number(m[1]),mo=Number(m[2]),d=Number(m[3]),dt=new Date(Date.UTC(y,mo-1,d));if(dt.getUTCFullYear()!==y||dt.getUTCMonth()!==mo-1||dt.getUTCDate()!==d)return null;const months=['JAN','FEB','MAR','APR','MAY','JUN','JUL','AUG','SEP','OCT','NOV','DEC'];return clean(format).toUpperCase()==='DD MON YYYY'?`${d} ${months[mo-1]} ${y}`:t};
const defaultToken=(raw,f)=>{if(!(obj(raw)&&clean(raw.state).toLowerCase()==='default'))return null;if(f.default_allowed!==true){findings.push({code:'IR_DEFAULT_NOT_ALLOWED',severity:'error',field:f.name});return null}const n=Number(raw.count||1);return Number.isInteger(n)&&n>1?`${n}*`:'*'};
const renderValue=(raw,f,event)=>{const def=defaultToken(raw,f);if(def!==null)return def;const type=clean(f.type).toLowerCase();let out=null;
  if(type==='integer')out=integer(raw);else if(type==='number')out=decimal(raw);else if(type==='date')out=renderDate(raw,f.format);else if(type==='boolean'){if(raw===true||clean(raw).toLowerCase()==='true')out=clean(f.true_token)||'YES';else if(raw===false||clean(raw).toLowerCase()==='false')out=clean(f.false_token)||'NO'}else if(type==='enum'){const vals=arr(f.enum)?f.enum.map(String):[],candidate=String(raw??''),hit=vals.find(v=>v.toUpperCase()===candidate.toUpperCase());out=hit??null}else if(type==='string'){const candidate=String(raw??'');if(candidate&&!/[\r\n\0]/.test(candidate))out=candidate}
  if(out===null){findings.push({code:'IR_FIELD_VALUE_INVALID',severity:'error',event_id:event.event_id,keyword:event.keyword,field:f.name,type});return null}
  if(type==='string'||f.quote==='single')return quote(out);return f.case==='upper'?String(out).toUpperCase():String(out);
};
for(let i=0;i<events.length;i++){
  const e=events[i],eventId=clean(e.event_id),op=clean(e.operation).toUpperCase(),kw=clean(e.keyword).toUpperCase(),variant=clean(e.variant)||'default';
  if(!eventId||eventIds.has(eventId)){findings.push({code:'IR_EVENT_ID_INVALID',severity:'error',index:i,event_id:eventId});continue}eventIds.add(eventId);
  if(!['KEEP','MODIFY','ADD','REMOVE'].includes(op)){findings.push({code:'IR_OPERATION_INVALID',severity:'error',index:i,event_id:eventId});continue}
  if(!allowed.has(kw)){findings.push({code:'IR_KEYWORD_UNSUPPORTED',severity:'error',index:i,event_id:eventId,keyword:kw});continue}
  if(mode==='CREATE'&&op!=='ADD'){findings.push({code:'CREATE_REQUIRES_ADD_ONLY',severity:'error',index:i,event_id:eventId,operation:op});continue}
  if(mode==='REVISE'&&['MODIFY','REMOVE'].includes(op)&&(!clean(e.target_node_id)||!shaPattern.test(clean(e.expected_raw_hash)))){findings.push({code:'IR_TARGET_IDENTITY_REQUIRED',severity:'error',index:i,event_id:eventId});continue}
  const base={event_id:eventId,operation:op,keyword:kw,variant,target_node_id:clean(e.target_node_id)||null,expected_raw_hash:clean(e.expected_raw_hash)||null,file_ref:clean(e.file_ref)||null,before_node_id:clean(e.before_node_id)||null,after_node_id:clean(e.after_node_id)||null,provenance:arr(e.provenance)?e.provenance.slice(0,100):[],_ir_index:i};
  if(['KEEP','REMOVE'].includes(op)){renderedChanges.push(base);continue}
  const schema=schemaMap.get(`${kw}::${variant}`);if(!schema){findings.push({code:'IR_SCHEMA_VARIANT_NOT_FOUND',severity:'error',index:i,event_id:eventId,keyword:kw,variant});continue}
  if(!base.provenance.length){findings.push({code:'IR_PROVENANCE_REQUIRED',severity:'error',index:i,event_id:eventId});continue}
  const values=obj(e.fields)?e.fields:{},known=new Set(schema.fields.map(f=>clean(f.name))),unknown=Object.keys(values).filter(k=>!known.has(k));if(unknown.length){findings.push({code:'IR_UNKNOWN_FIELD',severity:'error',index:i,event_id:eventId,fields:unknown});continue}
  const tokens=[];let fieldError=false;for(const f of schema.fields){const name=clean(f.name),present=Object.prototype.hasOwnProperty.call(values,name);if(!present){if(f.required===true){findings.push({code:'IR_REQUIRED_FIELD_MISSING',severity:'error',index:i,event_id:eventId,keyword:kw,field:name});fieldError=true;continue}if(f.default_allowed===true){tokens.push('*');continue}findings.push({code:'IR_OPTIONAL_FIELD_HAS_NO_DEFAULT_POLICY',severity:'error',index:i,event_id:eventId,keyword:kw,field:name});fieldError=true;continue}const token=renderValue(values[name],f,e);if(token===null){fieldError=true;continue}tokens.push(token)}if(fieldError)continue;
  const l=schema.layout,text=!schema.fields.length?`${kw}${l.newline}`:`${kw}${l.newline}${l.indent}${tokens.join(l.delimiter)}${l.record_terminator}${l.newline}${l.block_terminator==='slash_line'?'/'+l.newline+l.newline:''}`;
  const change={...base,rendered_text:text,schema_id:schema.schema_id,schema_revision:schema.schema_revision,citation:schema.citation,render_hash:hash(text)};renderedChanges.push(change);renderedRecords.push({event_id:eventId,keyword:kw,variant,field_count:tokens.length,render_hash:change.render_hash,schema_id:schema.schema_id,_ir_index:i});
}
// CREATE: assemble by DATES segments, then within-date keyword order (stage-3 algorithm — not RAG).
if(mode==='CREATE'&&!findings.some(f=>f.severity==='error')&&renderedChanges.length){
  const segments=[];let cur={dates:null,items:[]};
  const flush=()=>{if(cur.dates||cur.items.length)segments.push(cur);cur={dates:null,items:[]}};
  for(let i=0;i<renderedChanges.length;i++){
    const c=renderedChanges[i];
    if(c.keyword==='DATES'){flush();cur={dates:c,items:[]};continue}
    cur.items.push({change:c,orig:c._ir_index??i});
  }
  flush();
  const ordered=[];
  for(const seg of segments){
    if(seg.dates)ordered.push(seg.dates);
    seg.items.sort((a,b)=>compareWithinDateKeywords(a.change.keyword,b.change.keyword,a.orig,b.orig));
    for(const it of seg.items)ordered.push(it.change);
  }
  renderedChanges.length=0;for(const c of ordered){const{_ir_index,...rest}=c;renderedChanges.push(rest)}
  const byId=new Map(renderedRecords.map(r=>[r.event_id,r]));
  const orderedRecords=[];
  for(const c of renderedChanges){const r=byId.get(c.event_id);if(r){const{_ir_index,...rest}=r;orderedRecords.push(rest)}}
  renderedRecords.length=0;for(const r of orderedRecords)renderedRecords.push(r);
}else{
  for(const c of renderedChanges)delete c._ir_index;
  for(const r of renderedRecords)delete r._ir_index;
}
const hard=findings.filter(f=>f.severity==='error'),catalogueFingerprint=hash(schemas.slice().sort((a,b)=>`${a.keyword}:${a.variant||'default'}`.localeCompare(`${b.keyword}:${b.variant||'default'}`)).map(s=>`${s.schema_id}|${s.schema_revision}|${s.keyword}|${s.variant||'default'}`).join('\n'));
return[{json:{contract:'schedule_render_result',contract_version:'1.0',status:hard.length?'needs_input':'rendered',mode,changes:hard.length?[]:renderedChanges,rendered_records:hard.length?[]:renderedRecords,catalogue_ref:clean(catalogue.catalogue_ref)||null,catalogue_hash:catalogueHash||null,catalogue_fingerprint:catalogueFingerprint,source_hash:sourceHash||null,findings,hard_blockers:hard.map(f=>f.code),metrics:{ir_events:events.length,schemas:schemas.length,rendered_records:hard.length?0:renderedRecords.length,passed:hard.length===0}}}];
""".replace("__KEYWORDS__", allowed).replace("__SHA256_JS__", SHA256_JS).replace(
        "__WITHIN_DATE_ORDER_JS__", within_date_order_js()
    )
