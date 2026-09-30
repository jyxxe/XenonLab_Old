import React,{useEffect,useState} from 'react';
type API=(path:string,init?:RequestInit)=>Promise<any>;
type ResourceType='path'|'table'|'row';
type ACL={resource_type:ResourceType;resource_id:string;explicit:boolean;grants:Record<string,string[]>;revision:number};
const actions=['read','write','attach'];

function endpoint(type:ResourceType,id:string){return `/acl/${type}/${encodeURIComponent(id)}`}

export function ACLManager({api}:{api:API}){
 const [identity,setIdentity]=useState<any>(null),[resources,setResources]=useState<ACL[]>([]);
 const [kind,setKind]=useState<ResourceType>('path'),[resource,setResource]=useState(''),[loaded,setLoaded]=useState<ACL|null>(null);
 const [draft,setDraft]=useState('{}'),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 const isAdmin=identity?.scopes?.includes('xelibrary:admin')===true;

 async function loadResources(){
   try{setResources(await api('/acl?limit=1000'))}catch(e){setMessage(String(e))}
 }
 useEffect(()=>{api('/users/me').then(setIdentity).catch(e=>setMessage(String(e)))},[]);
 useEffect(()=>{if(isAdmin)void loadResources()},[isAdmin]);

 async function readACL(type=kind,id=resource){
   if(!id.trim()){setMessage('请输入路径、表 ID 或 table_id:row_id');return}
   setBusy(true);setMessage('');setLoaded(null);
   try{const acl:ACL=await api(endpoint(type,id.trim()));setLoaded(acl);setDraft(JSON.stringify(acl.grants,null,2));setKind(type);setResource(id.trim());setMessage(acl.explicit?'已读取显式 ACL；它覆盖此资源从父级继承的授权。':'此资源没有显式 ACL；权限按表/路径层级与账号树继承规则计算。')}
   catch(e){setMessage(String(e))}finally{setBusy(false)}
 }

 async function save(){
   if(!loaded||loaded.resource_type!==kind||loaded.resource_id!==resource.trim())return;
   let grants:Record<string,string[]>;
   try{grants=JSON.parse(draft);if(!grants||Array.isArray(grants)||typeof grants!=='object')throw Error('授权必须是对象');for(const [principal,values] of Object.entries(grants)){if(!principal||!Array.isArray(values)||values.some(value=>!actions.includes(value)))throw Error('每个主体必须对应 read、write、attach 权限数组')}}
   catch(e){setMessage(String(e));return}
   setBusy(true);setMessage('');
   try{const acl:ACL=await api(endpoint(kind,loaded.resource_id),{method:'PUT',headers:{'If-Match':`"revision:${loaded.revision}"`},body:JSON.stringify({grants})});setLoaded(acl);setDraft(JSON.stringify(acl.grants,null,2));setMessage(`ACL 已保存（revision ${acl.revision}）`);await loadResources()}
   catch(e){setMessage(`${String(e)}。若其他管理员已更新，请重新读取后再编辑。`)}finally{setBusy(false)}
 }

 async function inherit(){
   if(!loaded||!loaded.explicit||loaded.resource_type!==kind||loaded.resource_id!==resource.trim())return;
   setBusy(true);setMessage('');
   try{const acl:ACL=await api(endpoint(kind,loaded.resource_id),{method:'DELETE',headers:{'If-Match':`"revision:${loaded.revision}"`}});setLoaded(acl);setDraft('{}');setMessage(`显式 ACL 已清除（revision ${acl.revision}）；权限将从上级继承。`);await loadResources()}
   catch(e){setMessage(`${String(e)}。若其他管理员已更新，请重新读取后再操作。`)}finally{setBusy(false)}
 }

 return <section className="query acl-manager"><h2>知识库 ACL</h2>
   <p>ACL 授权对象是账号 subject，权限为 read、write、attach。优先应用行 ACL，再应用表 ACL 和路径 ACL；未设置显式 ACL 时继续按父级资源及账号树继承。</p>
   <p>当前身份：{identity?.subject||'读取中'} · {isAdmin?'可管理 ACL':'只读身份'}</p>
   {!isAdmin?<p role="status">只有具备 xelibrary:admin 的账号可读取和编辑 ACL。</p>:<>
     <h3>已有显式 ACL</h3><button disabled={busy} onClick={()=>void loadResources()}>刷新列表</button>
     <div className="acl-resources">{resources.map(item=><button key={`${item.resource_type}:${item.resource_id}`} disabled={busy} onClick={()=>void readACL(item.resource_type,item.resource_id)}>{item.resource_type} · {item.resource_id}<small>revision {item.revision}</small></button>)}{!resources.length&&<small>目前没有显式 ACL。</small>}</div>
     <h3>读取或设置资源权限</h3>
     <label>资源类型<select value={kind} disabled={busy} onChange={e=>{setKind(e.target.value as ResourceType);setLoaded(null)}}><option value="path">路径</option><option value="table">表</option><option value="row">记录</option></select></label>
     <label>资源标识<input value={resource} disabled={busy} onChange={e=>{setResource(e.target.value);setLoaded(null)}} placeholder={kind==='path'?'/数学/分析':kind==='table'?'table UUID':'table UUID:row UUID'}/></label>
     <button disabled={busy||!resource.trim()} onClick={()=>void readACL()}>读取 ACL</button>
     {loaded&&loaded.resource_type===kind&&loaded.resource_id===resource.trim()&&<>
       <p role="status">{loaded.explicit?`正在编辑显式覆盖（revision ${loaded.revision}）。`:`当前继承上级授权（revision ${loaded.revision}）。`}保存后会建立或更新显式覆盖。</p>
       <label>主体授权 JSON<textarea aria-label="主体授权 JSON" spellCheck={false} value={draft} disabled={busy} onChange={e=>setDraft(e.target.value)}/></label>
       <small>例如：{`{"account-subject":["read"],"*":["read"]}`}。空对象表示显式拒绝所有非管理员访问；要恢复继承请使用“清除显式 ACL”。</small>
       <div><button disabled={busy} onClick={()=>void save()}>保存 ACL</button><button disabled={busy||!loaded.explicit} onClick={()=>void inherit()}>清除显式 ACL并继承</button></div>
     </>}
   </>}
   <p role="status">{message}</p>
 </section>
}
