"""Deterministic targeted retrieval over decoded SCHEDULE baseline records.

The query runtime is intentionally vendor-neutral.  It filters only the typed
records produced by the approved-catalogue decoder and returns mutation-safe
identities, hashes and provenance.  It never parses raw Schedule text or asks
an LLM to select records.
"""
from __future__ import annotations

import json

from schedule_lossless_runtime import SHA256_JS


def build_baseline_query_js(keywords: list[str]) -> str:
    allowed = json.dumps(keywords, ensure_ascii=False)
    return r"""
const x=$json.baseline_query_request??$json;
const obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'';
const allowed=new Set(__KEYWORDS__),findings=[];
__SHA256_JS__
const hash=contentHash,shaPattern=/^sha256:[a-f0-9]{64}$/i,decoded=obj(x.baseline_decode_result)?x.baseline_decode_result:{},query=obj(x.query)?x.query:{};
const purpose=clean(query.purpose||'BUILD').toUpperCase(),phase=clean(query.phase||'ALL').toUpperCase(),summaryOnly=query.summary_only===true,requireComplete=query.require_complete!==false;
const all=arr(decoded.decoded_records)?decoded.decoded_records.filter(obj):[],prefix=arr(decoded.prefix_records)?decoded.prefix_records.filter(obj):[],suffix=arr(decoded.suffix_records)?decoded.suffix_records.filter(obj):[];
const source=phase==='PREFIX'?prefix:phase==='SUFFIX'?suffix:all,MAX_RECORDS=50000,MAX_LIMIT=2000,DEFAULT_LIMIT=purpose==='PLANNING'?250:1000;
if(decoded.contract!=='baseline_decode_result'||decoded.contract_version!=='1.0'||decoded.status!=='decoded')findings.push({code:'BASELINE_DECODE_RESULT_REQUIRED',severity:'error'});
if(!shaPattern.test(clean(decoded.decoded_hash))||!shaPattern.test(clean(decoded.baseline_package_hash))||!shaPattern.test(clean(decoded.catalogue_hash)))findings.push({code:'BASELINE_QUERY_SOURCE_HASH_INVALID',severity:'error'});
if(clean(x.expected_decoded_hash)&&clean(x.expected_decoded_hash)!==clean(decoded.decoded_hash))findings.push({code:'BASELINE_QUERY_STALE_DECODED_HASH',severity:'error'});
if(!['PLANNING','BUILD','DIAGNOSTIC'].includes(purpose))findings.push({code:'BASELINE_QUERY_PURPOSE_INVALID',severity:'error'});
if(!['ALL','PREFIX','SUFFIX'].includes(phase))findings.push({code:'BASELINE_QUERY_PHASE_INVALID',severity:'error'});
if(source.length>MAX_RECORDS)findings.push({code:'BASELINE_QUERY_SOURCE_LIMIT',severity:'error',count:source.length,limit:MAX_RECORDS});
for(const r of source){if(!clean(r.event_id)||!clean(r.target_node_id||r.source_node_id)||!shaPattern.test(clean(r.expected_raw_hash))||!shaPattern.test(clean(r.record_hash))||!Number.isInteger(Number(r.execution_sequence))||!arr(r.provenance)||!r.provenance.length){findings.push({code:'BASELINE_QUERY_RECORD_IDENTITY_INVALID',severity:'error',event_id:r.event_id||null});break}}
const keywords=[...new Set((arr(query.keywords)?query.keywords:[]).map(v=>clean(v).toUpperCase()).filter(Boolean))],unsupported=keywords.filter(k=>!allowed.has(k));
if(unsupported.length)findings.push({code:'BASELINE_QUERY_KEYWORD_UNSUPPORTED',severity:'error',keywords:unsupported});
const nodeIds=new Set((arr(query.source_node_ids)?query.source_node_ids:[]).map(clean).filter(Boolean)),files=new Set((arr(query.file_refs)?query.file_refs:[]).map(clean).filter(Boolean));
const entityValues=new Set((arr(query.entity_values)?query.entity_values:[]).map(v=>clean(String(v)).toUpperCase()).filter(Boolean));
const fieldFilters=arr(query.field_filters)?query.field_filters.filter(obj):[];
for(const f of fieldFilters){const field=clean(f.field),op=clean(f.operator||'EQ').toUpperCase(),values=arr(f.values)?f.values:[f.value];if(!/^[A-Za-z][A-Za-z0-9_]*$/.test(field)||!['EQ','IN','CONTAINS','EXISTS'].includes(op)||(!values.length&&op!=='EXISTS'))findings.push({code:'BASELINE_QUERY_FIELD_FILTER_INVALID',severity:'error',field,operator:op})}
const month={JAN:0,FEB:1,MAR:2,APR:3,MAY:4,JUN:5,JUL:6,AUG:7,SEP:8,OCT:9,NOV:10,DEC:11};
const parseDate=raw=>{const t=clean(raw).toUpperCase();let m=t.match(/^(\d{4})-(\d{2})-(\d{2})$/),y,mo,d;if(m){y=Number(m[1]);mo=Number(m[2])-1;d=Number(m[3])}else{m=t.match(/^(\d{1,2})\s+([A-Z]{3})\s+(\d{4})$/);if(!m||month[m[2]]===undefined)return null;d=Number(m[1]);mo=month[m[2]];y=Number(m[3])}const epoch=Date.UTC(y,mo,d),v=new Date(epoch);return v.getUTCFullYear()===y&&v.getUTCMonth()===mo&&v.getUTCDate()===d?{iso:`${String(y).padStart(4,'0')}-${String(mo+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`,epoch}:null};
const from=clean(query.effective_from)?parseDate(query.effective_from):null,to=clean(query.effective_to)?parseDate(query.effective_to):null;
if(clean(query.effective_from)&&!from)findings.push({code:'BASELINE_QUERY_DATE_INVALID',severity:'error',field:'effective_from'});if(clean(query.effective_to)&&!to)findings.push({code:'BASELINE_QUERY_DATE_INVALID',severity:'error',field:'effective_to'});if(from&&to&&from.epoch>to.epoch)findings.push({code:'BASELINE_QUERY_DATE_RANGE_INVALID',severity:'error'});
const canonical=v=>obj(v)?JSON.stringify(v):String(v??''),scalarValues=fields=>Object.values(obj(fields)?fields:{}).filter(v=>!obj(v)&&!arr(v)).map(v=>clean(String(v)).toUpperCase()).filter(Boolean);
const matchesField=(r,f)=>{const fields=obj(r.fields)?r.fields:{},name=clean(f.field),op=clean(f.operator||'EQ').toUpperCase();if(op==='EXISTS')return Object.prototype.hasOwnProperty.call(fields,name);if(!Object.prototype.hasOwnProperty.call(fields,name))return false;const actual=canonical(fields[name]),values=(arr(f.values)?f.values:[f.value]).map(canonical);if(op==='CONTAINS')return values.some(v=>actual.toUpperCase().includes(v.toUpperCase()));return values.some(v=>actual.toUpperCase()===v.toUpperCase())};
const matched=source.filter(r=>{const kw=clean(r.keyword).toUpperCase(),d=parseDate(r.effective_at);if(keywords.length&&!keywords.includes(kw))return false;if(nodeIds.size&&!nodeIds.has(clean(r.source_node_id)))return false;if(files.size&&!files.has(clean(r.file_ref)))return false;if(entityValues.size&&![...entityValues].some(v=>scalarValues(r.fields).includes(v)))return false;if(fieldFilters.length&&!fieldFilters.every(f=>matchesField(r,f)))return false;if(from&&(!d||d.epoch<from.epoch))return false;if(to&&(!d||d.epoch>to.epoch))return false;return true}).sort((a,b)=>Number(a.execution_sequence??0)-Number(b.execution_sequence??0)||Number(a.record_index??0)-Number(b.record_index??0));
const rawCursor=Number(query.cursor??0),cursor=Number.isInteger(rawCursor)&&rawCursor>=0?rawCursor:0,rawLimit=Number(query.limit??DEFAULT_LIMIT),limit=Number.isInteger(rawLimit)&&rawLimit>=1&&rawLimit<=MAX_LIMIT?rawLimit:DEFAULT_LIMIT;
if(rawCursor!==cursor)findings.push({code:'BASELINE_QUERY_CURSOR_INVALID',severity:'error'});if(rawLimit!==limit&&query.limit!==undefined)findings.push({code:'BASELINE_QUERY_LIMIT_INVALID',severity:'error',max:MAX_LIMIT});
const page=summaryOnly?[]:matched.slice(cursor,cursor+limit),next=summaryOnly||cursor+limit>=matched.length?null:cursor+limit,truncated=summaryOnly?false:next!==null;
const counts=matched.reduce((a,r)=>(a[clean(r.keyword).toUpperCase()]=(a[clean(r.keyword).toUpperCase()]||0)+1,a),{}),variants=matched.reduce((a,r)=>{const key=`${clean(r.keyword).toUpperCase()}::${clean(r.variant)||'default'}`;a[key]=(a[key]||0)+1;return a},{});
const fieldNames={};for(const r of matched){const kw=clean(r.keyword).toUpperCase();fieldNames[kw]=[...new Set([...(fieldNames[kw]||[]),...Object.keys(obj(r.fields)?r.fields:{})])].sort()}
const dated=matched.map(r=>parseDate(r.effective_at)).filter(Boolean),dateRange=dated.length?{from:new Date(Math.min(...dated.map(d=>d.epoch))).toISOString().slice(0,10),to:new Date(Math.max(...dated.map(d=>d.epoch))).toISOString().slice(0,10)}:null;
const sampleLimit=Math.min(100,Math.max(0,Number(query.sample_limit??25)||0)),samples=[];if(summaryOnly&&sampleLimit){const perKeyword=new Map();for(const r of matched){const kw=clean(r.keyword).toUpperCase(),n=perKeyword.get(kw)||0;if(n>=5)continue;samples.push(r);perKeyword.set(kw,n+1);if(samples.length>=sampleLimit)break}}
const queryBody={purpose,phase,keywords:[...keywords].sort(),source_node_ids:[...nodeIds].sort(),file_refs:[...files].sort(),entity_values:[...entityValues].sort(),field_filters:fieldFilters,effective_from:from?.iso||null,effective_to:to?.iso||null,cursor,limit,summary_only:summaryOnly,require_complete:requireComplete,decoded_hash:decoded.decoded_hash},queryHash=hash(JSON.stringify(queryBody));
const hard=findings.filter(f=>f.severity==='error');if(!hard.length&&requireComplete&&!summaryOnly&&truncated)findings.push({code:'BASELINE_QUERY_REFINEMENT_REQUIRED',severity:'error',matches:matched.length,returned:page.length,limit,next_cursor:next});
const errors=findings.filter(f=>f.severity==='error'),status=errors.length?'needs_input':truncated?'partial':'succeeded';
return[{json:{contract:'baseline_inventory_query_result',contract_version:'1.0',status,purpose,phase,query_hash:queryHash,decoded_hash:decoded.decoded_hash||null,baseline_package_hash:decoded.baseline_package_hash||null,catalogue_hash:decoded.catalogue_hash||null,total_source_records:source.length,total_matches:matched.length,cursor,limit,next_cursor:next,truncated,records:errors.length?[]:page,samples:errors.length?[]:samples,summary:{keyword_counts:counts,variant_counts:variants,field_names:fieldNames,effective_range:dateRange},findings,hard_blockers:[...new Set(errors.map(f=>f.code))]}}];
""".replace("__KEYWORDS__", allowed).replace("__SHA256_JS__", SHA256_JS)
