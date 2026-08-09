"""Portable immutable SCHEDULE artifact publication runtime.

n8n carries the validated in-memory ``schedule_package`` only as far as an
IT-managed artifact service.  These Code-node snippets validate the bounded
package and exact simulator/task binding, build a fixed HTTPS request, and
normalize the service response to a compact immutable reference.  They never
read or write an n8n/server filesystem path.
"""
from __future__ import annotations


NORMALIZE_ARTIFACT_PUBLISH = r"""
const x=$json.schedule_artifact_publish_request??$json,obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'';
const findings=[],profile=obj(x.simulator_profile)?x.simulator_profile:{},wrapper=obj(x.artifact)?x.artifact:{},pkg=obj(wrapper.package)?wrapper.package:{};
const sha=/^sha256:[a-f0-9]{64}$/i,safePath=value=>{const p=clean(value);if(!p||p.length>512||p.startsWith('/')||p.startsWith('\\')||p.includes('\\')||/^[A-Za-z]:/.test(p))return false;const parts=p.split('/');return parts.every(part=>part&&part!=='.'&&part!=='..'&&!part.includes('\0'))};
if(x.contract!=='schedule_artifact_publish_request'||x.contract_version!=='1.0')findings.push({code:'ARTIFACT_PUBLISH_CONTRACT_INVALID',severity:'error'});
if(!clean(x.task_id)||!clean(x.trace_id)||!clean(x.request_id)||!clean(x.idempotency_key))findings.push({code:'ARTIFACT_PUBLISH_IDENTITY_REQUIRED',severity:'error'});
if(clean(profile.vendor)!=='Rock Flow Dynamics'||clean(profile.simulator).toLowerCase()!=='tnavigator'||clean(profile.version)!=='22.2')findings.push({code:'ARTIFACT_PUBLISH_PROFILE_NOT_APPROVED',severity:'error'});
if(clean(wrapper.kind)!=='schedule-package'||pkg.contract!=='schedule_package'||pkg.contract_version!=='1.0')findings.push({code:'SCHEDULE_PACKAGE_CONTRACT_INVALID',severity:'error'});
if(!safePath(pkg.root_path))findings.push({code:'SCHEDULE_PACKAGE_ROOT_PATH_UNSAFE',severity:'error',file_ref:clean(pkg.root_path).slice(0,512)});
if(!sha.test(clean(pkg.package_hash)))findings.push({code:'SCHEDULE_PACKAGE_HASH_INVALID',severity:'error'});
const files=arr(pkg.files)?pkg.files:[];if(!files.length||files.length>200)findings.push({code:'SCHEDULE_PACKAGE_FILE_COUNT_INVALID',severity:'error',file_count:files.length,max_files:200});
const seen=new Set();let totalBytes=0;const safeFiles=[];
for(const file of files.slice(0,201)){const manifest=obj(file?.manifest)?file.manifest:{},path=clean(file?.file_ref),text=typeof file?.text==='string'?file.text:null;let bytes=text===null?-1:new TextEncoder().encode(text).length;
 if(!safePath(path))findings.push({code:'SCHEDULE_FILE_PATH_UNSAFE',severity:'error',file_ref:path.slice(0,512)});if(seen.has(path))findings.push({code:'SCHEDULE_FILE_PATH_DUPLICATE',severity:'error',file_ref:path.slice(0,512)});seen.add(path);
 if(text===null)findings.push({code:'SCHEDULE_FILE_TEXT_REQUIRED',severity:'error',file_ref:path.slice(0,512)});if(!sha.test(clean(manifest.sha256)))findings.push({code:'SCHEDULE_FILE_HASH_INVALID',severity:'error',file_ref:path.slice(0,512)});
 if(!Number.isInteger(Number(manifest.byte_length))||Number(manifest.byte_length)!==bytes)findings.push({code:'SCHEDULE_FILE_BYTE_LENGTH_MISMATCH',severity:'error',file_ref:path.slice(0,512),declared:manifest.byte_length,actual:bytes});
 if(bytes>=0)totalBytes+=bytes;safeFiles.push({file_ref:path,text,manifest:{...manifest,file_ref:path,sha256:clean(manifest.sha256).toLowerCase(),byte_length:bytes}})}
if(totalBytes>10485760)findings.push({code:'SCHEDULE_PACKAGE_SIZE_LIMIT',severity:'error',byte_length:totalBytes,max_bytes:10485760});if(safePath(pkg.root_path)&&!seen.has(clean(pkg.root_path)))findings.push({code:'SCHEDULE_PACKAGE_ROOT_FILE_MISSING',severity:'error',file_ref:clean(pkg.root_path)});
let requestBytes=Number.MAX_SAFE_INTEGER;try{requestBytes=new TextEncoder().encode(JSON.stringify(x)).length}catch{}if(requestBytes>11534336)findings.push({code:'ARTIFACT_PUBLISH_REQUEST_TOO_LARGE',severity:'error',byte_length:requestBytes,max_bytes:11534336});
const hard=findings.filter(f=>f.severity==='error');return[{json:{contract:'schedule_artifact_publish_normalized',contract_version:'1.0',status:hard.length?'needs_input':'accepted',task_id:clean(x.task_id),trace_id:clean(x.trace_id),request_id:clean(x.request_id),idempotency_key:clean(x.idempotency_key),simulator_profile:{vendor:'Rock Flow Dynamics',simulator:'tNavigator',version:'22.2'},artifact:{kind:'schedule-package',package:{contract:'schedule_package',contract_version:'1.0',root_path:clean(pkg.root_path),package_hash:clean(pkg.package_hash).toLowerCase(),files:safeFiles}},limits:{file_count:safeFiles.length,total_bytes:totalBytes,max_files:200,max_bytes:10485760},findings,hard_blockers:[...new Set(hard.map(f=>f.code))]}}];
"""


PREPARE_ARTIFACT_HTTP = r"""
const x=$json,clean=v=>typeof v==='string'?v.trim():'';const findings=Array.isArray(x.findings)?x.findings.slice():[],base=clean(x.service_url).replace(/\/+$/,''),collection=clean(x.collection_id),local=/^http:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?$/i.test(base),secure=/^https:\/\/[A-Za-z0-9.-]+(?::\d+)?(?:\/[^?#]*)?$/i.test(base);
if(!secure&&!(x.allow_insecure_localhost===true&&local))findings.push({code:'ARTIFACT_SERVICE_URL_NOT_APPROVED',severity:'error'});if(base.includes('REPLACE_IN_UI')||!collection||collection.includes('REPLACE_IN_UI'))findings.push({code:'ARTIFACT_SERVICE_CONFIGURATION_REQUIRED',severity:'error'});
const hard=findings.filter(f=>f.severity==='error'),body={contract:'schedule_artifact_service_request',contract_version:'1.0',task_id:x.task_id,trace_id:x.trace_id,request_id:x.request_id,idempotency_key:x.idempotency_key,collection_id:collection,simulator_profile:x.simulator_profile,artifact:x.artifact};
return[{json:{...x,status:hard.length?'needs_input':'request_ready',collection_id:collection,findings,hard_blockers:[...new Set(hard.map(f=>f.code))],http_request:hard.length?null:{method:'POST',url:`${base}/v1/schedule-artifacts`,send_body:true,body,timeout_ms:Math.max(1000,Math.min(120000,Number(x.timeout_ms)||60000))}}}];
"""


NORMALIZE_ARTIFACT_RESPONSE = r"""
const prepared=$('Prepare artifact service request').first().json,raw=$json?.body??$json,obj=v=>v&&typeof v==='object'&&!Array.isArray(v),arr=Array.isArray,clean=v=>typeof v==='string'?v.trim():'';const x=obj(raw)?raw:{},findings=[],sha=/^sha256:[a-f0-9]{64}$/i;
if(x.contract!=='schedule_artifact_service_result'||x.contract_version!=='1.0')findings.push({code:'ARTIFACT_SERVICE_CONTRACT_INVALID',severity:'error'});const status=clean(x.status).toLowerCase();if(!['published','existing'].includes(status))findings.push({code:'ARTIFACT_SERVICE_STATUS_INVALID',severity:'error'});
if(clean(x.task_id)!==prepared.task_id)findings.push({code:'ARTIFACT_SERVICE_TASK_MISMATCH',severity:'error'});if(clean(x.request_id)!==prepared.request_id)findings.push({code:'ARTIFACT_SERVICE_REQUEST_MISMATCH',severity:'error'});if(clean(x.collection_id)!==prepared.collection_id)findings.push({code:'ARTIFACT_SERVICE_COLLECTION_MISMATCH',severity:'error'});
const profile=obj(x.simulator_profile)?x.simulator_profile:{};if(clean(profile.vendor)!=='Rock Flow Dynamics'||clean(profile.simulator).toLowerCase()!=='tnavigator'||clean(profile.version)!=='22.2')findings.push({code:'ARTIFACT_SERVICE_PROFILE_MISMATCH',severity:'error'});
const artifact=obj(x.artifact)?x.artifact:{},ref=clean(artifact.ref),expectedHash=clean(prepared.artifact?.package?.package_hash).toLowerCase(),manifestHash=clean(artifact.manifest_hash||artifact.revision).toLowerCase();if(!/^artifact:\/\/[A-Za-z0-9][A-Za-z0-9._~:/-]{0,1000}$/.test(ref)||ref.startsWith('inline-schedule://'))findings.push({code:'GOVERNED_ARTIFACT_REF_INVALID',severity:'error'});if(clean(artifact.kind)!=='schedule-package')findings.push({code:'PUBLISHED_ARTIFACT_KIND_INVALID',severity:'error'});if(artifact.immutable!==true)findings.push({code:'PUBLISHED_ARTIFACT_NOT_IMMUTABLE',severity:'error'});if(!sha.test(manifestHash)||manifestHash!==expectedHash)findings.push({code:'PUBLISHED_ARTIFACT_HASH_MISMATCH',severity:'error',expected:expectedHash,actual:manifestHash});
const safeFindings=(arr(x.findings)?x.findings:[]).slice(0,100).map(f=>obj(f)?{code:clean(f.code).slice(0,120),severity:clean(f.severity).slice(0,30),message:clean(f.message).slice(0,1000)}:{code:'ARTIFACT_SERVICE_FINDING_INVALID',severity:'error',message:clean(String(f)).slice(0,1000)});const hard=[...findings,...safeFindings].filter(f=>f.severity==='error');
return[{json:{contract:'schedule_artifact_publish_result',contract_version:'1.0',status:hard.length?'retryable_error':'published',task_id:prepared.task_id,trace_id:prepared.trace_id,request_id:prepared.request_id,idempotency_key:prepared.idempotency_key,simulator_profile:prepared.simulator_profile,artifact:hard.length?null:{ref,kind:'schedule-package',revision:manifestHash,manifest_hash:manifestHash,immutable:true,description:clean(artifact.description||'Governed immutable tNavigator SCHEDULE package.').slice(0,500)},package_summary:{root_path:prepared.artifact.package.root_path,package_hash:expectedHash,file_count:prepared.limits.file_count,total_bytes:prepared.limits.total_bytes},findings:[...safeFindings,...findings],hard_blockers:[...new Set(hard.map(f=>f.code))]}}];
"""


INVALID_ARTIFACT_RESULT = r"""
const x=$json,findings=Array.isArray(x.findings)?x.findings:[];return[{json:{contract:'schedule_artifact_publish_result',contract_version:'1.0',status:'needs_input',task_id:x.task_id||null,trace_id:x.trace_id||null,request_id:x.request_id||null,idempotency_key:x.idempotency_key||null,simulator_profile:x.simulator_profile||null,artifact:null,package_summary:x.artifact?.package?{root_path:x.artifact.package.root_path||null,package_hash:x.artifact.package.package_hash||null,file_count:x.limits?.file_count||0,total_bytes:x.limits?.total_bytes||0}:null,findings,hard_blockers:[...new Set(findings.filter(f=>f.severity==='error').map(f=>f.code))]}}];
"""
