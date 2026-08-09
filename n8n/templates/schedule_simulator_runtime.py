"""Portable asynchronous tNavigator check adapter runtime.

n8n never executes a simulator command.  It talks to an IT-managed runner by
an allowlisted logical check profile and immutable artifact reference.  These
Code-node snippets validate the portable request, build the fixed REST call,
and normalize a bounded service response without exposing host paths/logs.
"""
from __future__ import annotations


NORMALIZE_SIMULATOR_CHECK = r"""
const x=$json.simulator_check_request??$json,obj=v=>v&&typeof v==='object'&&!Array.isArray(v),clean=v=>typeof v==='string'?v.trim():'';
const findings=[],action=clean(x.action).toUpperCase(),profile=obj(x.simulator_profile)?x.simulator_profile:{},artifact=obj(x.artifact)?x.artifact:{};
const sha=/^sha256:[a-f0-9]{64}$/i,job=/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;
if(x.contract!=='simulator_check_request'||x.contract_version!=='1.0')findings.push({code:'SIMULATOR_CHECK_CONTRACT_INVALID',severity:'error'});
if(!['SUBMIT','STATUS','CANCEL','RESULT'].includes(action))findings.push({code:'SIMULATOR_CHECK_ACTION_INVALID',severity:'error'});
if(!clean(x.task_id)||!clean(x.trace_id)||!clean(x.request_id)||!clean(x.idempotency_key))findings.push({code:'SIMULATOR_CHECK_IDENTITY_REQUIRED',severity:'error'});
if(clean(profile.vendor)!=='Rock Flow Dynamics'||clean(profile.simulator).toLowerCase()!=='tnavigator'||clean(profile.version)!=='22.2')findings.push({code:'SIMULATOR_PROFILE_NOT_APPROVED',severity:'error'});
if(action==='SUBMIT'){if(!clean(artifact.ref)||!sha.test(clean(artifact.manifest_hash))||artifact.immutable!==true)findings.push({code:'IMMUTABLE_SCHEDULE_ARTIFACT_REQUIRED',severity:'error'});if(!['schedule-package','schedule-draft'].includes(clean(artifact.kind)))findings.push({code:'SCHEDULE_ARTIFACT_KIND_INVALID',severity:'error'})}
if(action!=='SUBMIT'&&!job.test(clean(x.job_id)))findings.push({code:'SIMULATOR_JOB_ID_REQUIRED',severity:'error'});
const waitRaw=Number(x.wait_for_terminal_seconds??0),wait=Number.isInteger(waitRaw)&&waitRaw>=0&&waitRaw<=120?waitRaw:0;if(waitRaw!==wait)findings.push({code:'SIMULATOR_WAIT_LIMIT_INVALID',severity:'error',max:120});
let size=Number.MAX_SAFE_INTEGER;try{size=JSON.stringify(x).length}catch{}if(size>131072)findings.push({code:'SIMULATOR_CHECK_REQUEST_TOO_LARGE',severity:'error',bytes:size});
const hard=findings.filter(f=>f.severity==='error');
return[{json:{contract:'simulator_check_normalized',contract_version:'1.0',status:hard.length?'needs_input':'accepted',action,task_id:clean(x.task_id),trace_id:clean(x.trace_id),request_id:clean(x.request_id),idempotency_key:clean(x.idempotency_key),job_id:clean(x.job_id)||null,expected_job_version:Number.isInteger(Number(x.expected_job_version))?Number(x.expected_job_version):null,simulator_profile:{vendor:'Rock Flow Dynamics',simulator:'tNavigator',version:'22.2'},artifact:action==='SUBMIT'?{ref:clean(artifact.ref),manifest_hash:clean(artifact.manifest_hash).toLowerCase(),kind:clean(artifact.kind),immutable:true}:null,wait_for_terminal_seconds:wait,findings,hard_blockers:[...new Set(hard.map(f=>f.code))]}}];
"""


PREPARE_SIMULATOR_HTTP = r"""
const x=$json,obj=v=>v&&typeof v==='object'&&!Array.isArray(v),clean=v=>typeof v==='string'?v.trim():'';
const findings=Array.isArray(x.findings)?x.findings.slice():[],base=clean(x.service_url).replace(/\/+$/,''),profileId=clean(x.check_profile_id),local=/^http:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?$/i.test(base),secure=/^https:\/\/[A-Za-z0-9.-]+(?::\d+)?(?:\/[^?#]*)?$/i.test(base);
if(!secure&&!(x.allow_insecure_localhost===true&&local))findings.push({code:'SIMULATOR_SERVICE_URL_NOT_APPROVED',severity:'error'});if(base.includes('REPLACE_IN_UI')||!profileId||profileId.includes('REPLACE_IN_UI'))findings.push({code:'SIMULATOR_SERVICE_CONFIGURATION_REQUIRED',severity:'error'});
const encoded=x.job_id?encodeURIComponent(x.job_id):'',paths={SUBMIT:'/v1/simulator-checks',STATUS:`/v1/simulator-checks/${encoded}`,CANCEL:`/v1/simulator-checks/${encoded}/cancel`,RESULT:`/v1/simulator-checks/${encoded}/result`},methods={SUBMIT:'POST',STATUS:'GET',CANCEL:'POST',RESULT:'GET'},sendBody=['SUBMIT','CANCEL'].includes(x.action);
const body=x.action==='SUBMIT'?{contract:'simulator_check_service_request',contract_version:'1.0',request_id:x.request_id,idempotency_key:x.idempotency_key,task_id:x.task_id,trace_id:x.trace_id,check_profile_id:profileId,simulator_profile:x.simulator_profile,artifact:x.artifact,wait_for_terminal_seconds:Math.min(Number(x.wait_for_terminal_seconds||0),Number(x.max_wait_seconds||120))}:x.action==='CANCEL'?{contract:'simulator_check_cancel_request',contract_version:'1.0',request_id:x.request_id,idempotency_key:x.idempotency_key,expected_job_version:x.expected_job_version}:null;
const hard=findings.filter(f=>f.severity==='error');
return[{json:{...x,status:hard.length?'needs_input':'request_ready',findings,hard_blockers:[...new Set(hard.map(f=>f.code))],http_request:hard.length?null:{method:methods[x.action],url:`${base}${paths[x.action]}`,send_body:sendBody,body,timeout_ms:Math.max(1000,Math.min(130000,(Number(x.max_wait_seconds||120)+10)*1000))}}}];
"""


NORMALIZE_SIMULATOR_RESPONSE = r"""
const prepared=$('Prepare simulator service request').first().json,raw=$json?.body??$json,obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'';
const x=obj(raw)?raw:{},findings=[],sha=/^sha256:[a-f0-9]{64}$/i,job=/^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$/;
const state=clean(x.state||x.status).toLowerCase(),allowed=new Set(['queued','running','passed','failed','cancelled']);
if(x.contract!=='simulator_check_service_result'||x.contract_version!=='1.0')findings.push({code:'SIMULATOR_SERVICE_CONTRACT_INVALID',severity:'error'});
if(!allowed.has(state))findings.push({code:'SIMULATOR_SERVICE_STATE_INVALID',severity:'error'});if(!job.test(clean(x.job_id)))findings.push({code:'SIMULATOR_SERVICE_JOB_ID_INVALID',severity:'error'});
if(clean(x.request_id)!==prepared.request_id)findings.push({code:'SIMULATOR_SERVICE_REQUEST_MISMATCH',severity:'error'});if(clean(x.check_profile_id)!==clean(prepared.check_profile_id))findings.push({code:'SIMULATOR_CHECK_PROFILE_MISMATCH',severity:'error'});
const profile=obj(x.simulator_profile)?x.simulator_profile:{};if(clean(profile.vendor)!=='Rock Flow Dynamics'||clean(profile.simulator).toLowerCase()!=='tnavigator'||clean(profile.version)!=='22.2')findings.push({code:'SIMULATOR_RESULT_PROFILE_MISMATCH',severity:'error'});
if(prepared.artifact&&clean(x.artifact_manifest_hash).toLowerCase()!==clean(prepared.artifact.manifest_hash).toLowerCase())findings.push({code:'SIMULATOR_RESULT_ARTIFACT_MISMATCH',severity:'error'});
const diagnostics=obj(x.diagnostics)?x.diagnostics:{},errors=Number(diagnostics.error_count??0),warnings=Number(diagnostics.warning_count??0);if(!Number.isInteger(errors)||errors<0||!Number.isInteger(warnings)||warnings<0)findings.push({code:'SIMULATOR_DIAGNOSTIC_COUNTS_INVALID',severity:'error'});
if(state==='passed'&&(errors!==0||!sha.test(clean(x.result_hash))))findings.push({code:'SIMULATOR_PASS_EVIDENCE_INVALID',severity:'error'});
const safeFindings=(arr(x.findings)?x.findings:[]).slice(0,200).map(f=>obj(f)?{code:clean(f.code).slice(0,120),severity:clean(f.severity).slice(0,30),keyword:clean(f.keyword).slice(0,80)||null,file_ref:clean(f.file_ref).slice(0,240)||null,line:Number.isInteger(Number(f.line))?Number(f.line):null,message:clean(f.message).slice(0,1000)}:{code:'SIMULATOR_FINDING_INVALID',severity:'error',message:clean(String(f)).slice(0,1000)});
if(safeFindings.some(f=>f.severity==='error')&&state==='passed')findings.push({code:'SIMULATOR_PASS_CONTAINS_ERRORS',severity:'error'});
const hard=findings.filter(f=>f.severity==='error'),status=hard.length?'retryable_error':state;
return[{json:{contract:'simulator_check_result',contract_version:'1.0',status,action:prepared.action,task_id:prepared.task_id,trace_id:prepared.trace_id,request_id:prepared.request_id,job_id:clean(x.job_id)||null,job_version:Number.isInteger(Number(x.job_version))?Number(x.job_version):null,check_profile_id:clean(x.check_profile_id)||null,simulator_profile:{vendor:'Rock Flow Dynamics',simulator:'tNavigator',version:'22.2'},artifact_manifest_hash:clean(x.artifact_manifest_hash).toLowerCase()||prepared.artifact?.manifest_hash||null,result_hash:sha.test(clean(x.result_hash))?clean(x.result_hash).toLowerCase():null,diagnostics:{error_count:Number.isInteger(errors)?errors:null,warning_count:Number.isInteger(warnings)?warnings:null,summary:clean(diagnostics.summary).slice(0,2000)},findings:[...safeFindings,...findings],hard_blockers:[...new Set(hard.map(f=>f.code))],terminal:['passed','failed','cancelled'].includes(state)&&!hard.length,release_gate_passed:state==='passed'&&!hard.length&&errors===0,submitted_at:clean(x.submitted_at)||null,finished_at:clean(x.finished_at)||null,poll_after_seconds:Math.max(0,Math.min(300,Number(x.poll_after_seconds)||0)),result_artifact_refs:(arr(x.result_artifact_refs)?x.result_artifact_refs:[]).filter(obj).slice(0,20).map(r=>({ref:clean(r.ref),kind:clean(r.kind),revision:clean(r.revision),description:clean(r.description).slice(0,500)}))}}];
"""


INVALID_SIMULATOR_RESULT = r"""
const x=$json,findings=Array.isArray(x.findings)?x.findings:[];return[{json:{contract:'simulator_check_result',contract_version:'1.0',status:'needs_input',action:x.action||null,task_id:x.task_id||null,trace_id:x.trace_id||null,request_id:x.request_id||null,job_id:x.job_id||null,job_version:null,check_profile_id:null,simulator_profile:x.simulator_profile||null,artifact_manifest_hash:x.artifact?.manifest_hash||null,result_hash:null,diagnostics:{error_count:null,warning_count:null,summary:'Simulator check request/configuration did not pass deterministic validation.'},findings,hard_blockers:[...new Set(findings.filter(f=>f.severity==='error').map(f=>f.code))],terminal:false,release_gate_passed:false,submitted_at:null,finished_at:null,poll_after_seconds:0,result_artifact_refs:[]}}];
"""
