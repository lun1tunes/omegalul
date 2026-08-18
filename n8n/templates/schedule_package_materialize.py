"""Materialize multi-file SCHEDULE uploads into Builder package fields.

Drag-and-drop often yields flat basenames while INCLUDE keeps Petrel-relative
paths (`../../INCLUDE/.../X.GRDECL`). We never rewrite INCLUDE text: instead we
place each uploaded body at the package-relative path that Builder resolvePath
would compute from the root INCLUDE string, matching uploads by unique basename.
"""
from __future__ import annotations


# Pure function used by smokes and embedded into n8n Code nodes.
MATERIALIZE_CORE_JS = r"""
const SCHEDULE_EXT=/\.(?:data|inc|sch|txt|grdecl)$/i;
const MAX_SCHEDULE_FILES=100,MAX_SCHEDULE_FILE_BYTES=2097152,MAX_SCHEDULE_TOTAL_BYTES=4194304;
const cleanPath=v=>typeof v==='string'?v.trim():'';
const pathMalformed=p=>{const v=cleanPath(p);return !v||v.includes('\0')};
const normalizeSeparators=p=>cleanPath(p).replace(/\\/g,'/');
const isAbsOrUrl=p=>/^[A-Za-z][A-Za-z0-9+.-]*:\/\//.test(p)||p.startsWith('/')||/^[A-Za-z]:\//.test(p);
const dirname=p=>{const i=p.lastIndexOf('/');return i<0?'':p.slice(0,i)};
const basename=p=>{const n=normalizeSeparators(p);const i=n.lastIndexOf('/');return i<0?n:n.slice(i+1)};
const collapsePath=v=>{const parts=[];for(const bit of String(v).split('/')){if(!bit||bit==='.')continue;if(bit==='..'){if(!parts.length)return null;parts.pop();continue}parts.push(bit)}return parts.join('/')};
const classifyPackagePath=p=>{if(pathMalformed(p))return{error:'invalid'};const n=normalizeSeparators(p);if(isAbsOrUrl(n))return{error:'unsafe'};const collapsed=collapsePath(n);if(collapsed===null||collapsed==='')return{error:'unsafe'};return{path:collapsed}};
const resolvePath=(base,rel)=>{if(pathMalformed(rel))return{error:'invalid'};const r=normalizeSeparators(rel);if(isAbsOrUrl(r))return{error:'unsafe'};const baseClass=classifyPackagePath(base);const b=baseClass.path||'';const d=dirname(b);const joined=d?`${d}/${r}`:r;const collapsed=collapsePath(joined);if(collapsed===null||collapsed===''||isAbsOrUrl(collapsed))return{error:'unsafe'};return{path:collapsed}};
const utf8Bytes=s=>{try{return new TextEncoder().encode(String(s??'')).length;}catch{let n=0;const t=String(s??'');for(let i=0;i<t.length;i++){const cp=t.charCodeAt(i);n+=cp<=0x7f?1:cp<=0x7ff?2:cp>=0xd800&&cp<=0xdbff?4:3;if(cp>=0xd800&&cp<=0xdbff)i++;}return n;}};
// Only a real INCLUDE directive at the beginning of a logical line is an
// include.  The old cross-line regex also matched words in comments or quoted
// prose (for example `-- do not INCLUDE "..."`), which could manufacture an
// invalid path even when the simulator-facing deck had no INCLUDE directive.
const extractIncludes=text=>{
  const out=[],source=String(text??'');
  const wordChar=c=>!!c&&/[A-Za-z0-9_]/.test(c);
  let i=0,lineStart=true;
  while(i<source.length){
    const c=source[i];
    if(c==='\r'||c==='\n'){
      if(c==='\r'&&source[i+1]==='\n')i++;
      i++;lineStart=true;continue;
    }
    if(c===' '||c==='\t'||c==='\f'||c==='\uFEFF'){i++;continue;}
    if(c==='-'&&source[i+1]==='-'){
      const nl=source.indexOf('\n',i+2);
      i=nl<0?source.length:nl;
      continue;
    }
    if(lineStart&&source.slice(i,i+7).toUpperCase()==='INCLUDE'&&!wordChar(source[i+7])){
      i+=7;
      let j=i,quote='';
      while(j<source.length){
        while(j<source.length&&/[ \t\r\n\f]/.test(source[j]))j++;
        if(source[j]==='-'&&source[j+1]==='-'){
          const nl=source.indexOf('\n',j+2);
          j=nl<0?source.length:nl;
          continue;
        }
        if(source[j]==='\''||source[j]==='"'){quote=source[j];break;}
        break;
      }
      if(!quote){out.push('');i=j;lineStart=false;continue;}
      const start=j+1;let end=start,closed=false;
      while(end<source.length){
        if(source[end]===quote){closed=true;break;}
        if(source[end]==='\r'||source[end]==='\n')break;
        end++;
      }
      out.push(closed?source.slice(start,end):'');
      i=closed?end+1:source.length;
      lineStart=false;continue;
    }
    lineStart=false;i++;
  }
  return out;
};
const hasIncludeKeyword=text=>/^\s*INCLUDE\b/mi.test(String(text??''));
function materializeSchedulePackage(input){
  const uploads=Array.isArray(input?.uploads)?input.uploads:[];
  const preferred=cleanPath(input?.preferred_root||input?.root_path||'');
  const errors=[],warnings=[];
  if(!uploads.length)return{ok:false,errors:['No SCHEDULE files uploaded.'],warnings,package:null};
  if(uploads.length>MAX_SCHEDULE_FILES)return{ok:false,errors:[`Too many SCHEDULE files (max ${MAX_SCHEDULE_FILES}).`],warnings,package:null};
  const normalized=[];
  for(const raw of uploads){
    const text=typeof raw?.text==='string'?raw.text:null;
    const fileName=cleanPath(raw?.fileName||raw?.filename||'');
    const relativePath=cleanPath(raw?.relativePath||raw?.relative_path||'');
    if(text===null){errors.push(`Invalid upload payload for ${fileName||'file'}.`);continue;}
    const bytes=utf8Bytes(text);
    if(bytes>MAX_SCHEDULE_FILE_BYTES){errors.push(`${fileName||'file'} exceeds 2 MiB.`);continue;}
    const candidatePath=relativePath||fileName;
    if(!candidatePath){errors.push('Upload missing fileName.');continue;}
    if(!SCHEDULE_EXT.test(candidatePath)){errors.push(`${candidatePath} must be .data/.inc/.sch/.txt/.grdecl.`);continue;}
    let pathHint=null;
    if(relativePath){
      const c=classifyPackagePath(relativePath);
      if(c.error){errors.push(`Unsafe relative path for ${fileName}: ${relativePath}`);continue;}
      pathHint=c.path;
    }else if(fileName.includes('/')||fileName.includes('\\')){
      const c=classifyPackagePath(fileName);
      if(c.error){errors.push(`Unsafe fileName path: ${fileName}`);continue;}
      pathHint=c.path;
    }
    normalized.push({fileName:basename(fileName||candidatePath),text,bytes,pathHint,base:basename(fileName||candidatePath).toLowerCase()});
  }
  if(errors.length)return{ok:false,errors,warnings,package:null};
  const total=normalized.reduce((n,f)=>n+f.bytes,0);
  if(total>MAX_SCHEDULE_TOTAL_BYTES)return{ok:false,errors:[`Unpacked SCHEDULE package exceeds ${MAX_SCHEDULE_TOTAL_BYTES} bytes.`],warnings,package:null};

  const byBase=new Map();
  for(const f of normalized){
    const list=byBase.get(f.base)||[];
    list.push(f);
    byBase.set(f.base,list);
  }
  for(const [base,list] of byBase){
    if(list.length>1){
      const hints=list.map(x=>x.pathHint).filter(Boolean);
      if(hints.length!==list.length||new Set(hints).size!==list.length){
        errors.push(`Duplicate basename among uploads: ${base}`);
      }
    }
  }
  if(errors.length)return{ok:false,errors,warnings,package:null};

  const pickUpload=baseLower=>{
    const list=byBase.get(baseLower)||[];
    const free=list.filter(x=>!x.used);
    if(free.length===1)return free[0];
    return null;
  };

  let rootUpload=null,rootPath=null;
  if(preferred){
    const prefBase=basename(preferred).toLowerCase();
    if(preferred.includes('/')||preferred.includes('\\')){
      const prefClass=classifyPackagePath(preferred);
      if(prefClass.error)errors.push(`preferred root_path unsafe: ${preferred}`);
      else{
        rootPath=prefClass.path;
        rootUpload=normalized.find(f=>(f.pathHint&&f.pathHint.toLowerCase()===rootPath.toLowerCase())||f.base===prefBase)||null;
      }
    }else{
      rootUpload=pickUpload(prefBase);
      if(rootUpload)rootPath=rootUpload.pathHint||rootUpload.fileName;
    }
    if(!errors.length&&!rootUpload)errors.push(`preferred root not found among uploads: ${preferred}`);
  }else if(normalized.length===1){
    rootUpload=normalized[0];
    rootPath=rootUpload.pathHint||rootUpload.fileName;
  }else{
    const withInclude=normalized.filter(f=>hasIncludeKeyword(f.text));
    const candidates=withInclude.length?withInclude:normalized;
    if(candidates.length===1){
      rootUpload=candidates[0];
      rootPath=rootUpload.pathHint||rootUpload.fileName;
    }else{
      errors.push(`Ambiguous SCHEDULE root among uploads; set schedule_root. Candidates: ${candidates.map(f=>f.pathHint||f.fileName).join(', ')}`);
    }
  }
  if(errors.length||!rootUpload||!rootPath)return{ok:false,errors:errors.length?errors:['Could not select SCHEDULE root.'],warnings,package:null};

  const rootClass=classifyPackagePath(rootPath);
  if(rootClass.error||!rootClass.path)return{ok:false,errors:[`Root path invalid/unsafe: ${rootPath}`],warnings,package:null};
  rootPath=rootClass.path;
  rootUpload.used=true;

  // Flat basename roots need depth so Petrel `../../INCLUDE/...` collapses inside the package.
  if(!rootPath.includes('/')&&extractIncludes(rootUpload.text).some(p=>String(p).includes('..'))){
    rootPath=`SCHEDULE/FORECAST/${rootPath}`;
  }

  const assigned=new Map([[rootPath,{text:rootUpload.text,from:rootUpload.fileName}]]);
  const queue=[rootPath];
  while(queue.length){
    const current=queue.shift();
    const body=assigned.get(current)?.text||'';
    for(const rel of extractIncludes(body)){
      const resolved=resolvePath(current,rel);
      if(resolved.error==='invalid'){errors.push(`INCLUDE path invalid from ${current}: ${rel}`);continue;}
      if(resolved.error==='unsafe'||!resolved.path){errors.push(`INCLUDE path unsafe from ${current}: ${rel}`);continue;}
      const target=resolved.path;
      if(assigned.has(target))continue;
      const base=basename(target).toLowerCase();
      let upload=normalized.find(f=>!f.used&&f.pathHint&&f.pathHint.toLowerCase()===target.toLowerCase())||null;
      if(!upload){
        const list=(byBase.get(base)||[]).filter(f=>!f.used);
        if(list.length===1)upload=list[0];
        else if(list.length>1){errors.push(`Ambiguous upload for INCLUDE ${rel} (basename ${basename(target)})`);continue;}
      }
      // Missing INCLUDE bodies are warnings: root + present siblings still form a usable
      // package (commissioning revise / DATES work often only needs the root .INC).
      if(!upload){warnings.push(`Missing uploaded body for INCLUDE ${rel} → ${target}`);continue;}
      upload.used=true;
      assigned.set(target,{text:upload.text,from:upload.fileName});
      queue.push(target);
    }
  }
  if(errors.length)return{ok:false,errors,warnings,package:null};

  for(const f of normalized){
    if(f.used)continue;
    if(f.pathHint&&!assigned.has(f.pathHint)){
      warnings.push(`Unreferenced upload kept unreachable: ${f.pathHint}`);
      assigned.set(f.pathHint,{text:f.text,from:f.fileName});
      f.used=true;
    }else warnings.push(`Unreferenced upload ignored: ${f.fileName}`);
  }

  const include_files=[];
  for(const [path,meta] of assigned){
    if(path===rootPath)continue;
    include_files.push({path,text:meta.text});
  }
  include_files.sort((a,b)=>a.path.localeCompare(b.path));
  const byte_length=[...assigned.values()].reduce((n,m)=>n+utf8Bytes(m.text),0);
  const package_hash_seed=[rootPath,...include_files.map(f=>f.path)].join('|')+`|${byte_length}|${assigned.size}`;
  let h=2166136261;for(const ch of package_hash_seed){h^=ch.charCodeAt(0);h=Math.imul(h,16777619);}
  return{
    ok:true,
    errors:[],
    warnings,
    package:{
      kind:assigned.size>1?'multi':'single',
      root_path:rootPath,
      baseline_schedule_text:assigned.get(rootPath).text,
      baseline_filename:basename(rootPath),
      include_files,
      file_count:assigned.size,
      byte_length,
      package_hash:`fnv1a32:${(h>>>0).toString(16).padStart(8,'0')}`,
      warnings,
    }
  };
}
"""


def build_materialize_uploads_node_js() -> str:
    """n8n Code node: decode schedule_* binaries and materialize package onto json."""
    return (
        MATERIALIZE_CORE_JS
        + r"""
const item=$input.first();
const binary=item.binary&&typeof item.binary==='object'?item.binary:{};
const json=item.json&&typeof item.json==='object'?item.json:{};
// n8n webhook/form multi-file keys may be schedule_files, schedule_files0, schedule_files[0], etc.
const isScheduleKey=k=>/(?:^|_)schedule_files?(?:\[\d+\])?\d*$/i.test(String(k||'').replace(/\s+/g,''))||/^(schedule_file|schedule_files)\d*$/i.test(String(k||''));
const keys=Object.keys(binary).filter(isScheduleKey).sort((a,b)=>a.localeCompare(b,undefined,{numeric:true}));
const uploads=[];
for(const key of keys){
  const meta=binary[key]||{};
  const fileName=String(meta.fileName||meta.filename||'');
  let text='';
  try{
    const buf=await this.helpers.getBinaryDataBuffer(0,key);
    text=Buffer.from(buf).toString('utf8');
    if(text.charCodeAt(0)===0xfeff)text=text.slice(1);
  }catch(err){
    return[{json:{...json,schedule_materialize_ok:false,schedule_materialize_error:`Failed to read ${key}: ${String(err?.message||err)}`},binary}];
  }
  const directory=String(meta.directory||'').replace(/\\/g,'/').replace(/\/$/,'');
  uploads.push({fileName,text,relativePath:directory?`${directory}/${fileName}`:''});
}
if(!uploads.length&&Array.isArray(json.schedule_upload_texts)){
  for(const row of json.schedule_upload_texts){
    if(row&&typeof row.text==='string')uploads.push({fileName:String(row.fileName||row.filename||'schedule.inc'),text:row.text,relativePath:String(row.relativePath||row.relative_path||'')});
  }
}
if(!uploads.length&&typeof json.baseline_schedule_text==='string'&&json.baseline_schedule_text.length){
  uploads.push({fileName:String(json.baseline_filename||binary.schedule_file?.fileName||'schedule.inc'),text:json.baseline_schedule_text,relativePath:''});
}
if(!uploads.length){
  return[{json:{...json,schedule_materialize_ok:null,schedule_materialize_error:null},binary}];
}
const preferred=String(json.schedule_root||json.root_path||(json.body&&json.body.schedule_root)||'').trim();
const result=materializeSchedulePackage({uploads,preferred_root:preferred});
if(!result.ok){
  return[{json:{...json,schedule_materialize_ok:false,schedule_materialize_error:result.errors.join(' '),schedule_materialize_warnings:result.warnings},binary}];
}
const pkg=result.package;
return[{json:{
  ...json,
  schedule_materialize_ok:true,
  schedule_materialize_error:null,
  schedule_materialize_warnings:pkg.warnings||[],
  baseline_schedule_text:pkg.baseline_schedule_text,
  baseline_filename:pkg.baseline_filename,
  root_path:pkg.root_path,
  include_files:pkg.include_files,
  baseline_package:{kind:pkg.kind,file_count:pkg.file_count,byte_length:pkg.byte_length,package_hash:pkg.package_hash,root_path:pkg.root_path},
},binary}];
""".strip()
    )
