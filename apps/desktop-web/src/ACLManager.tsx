import React,{useEffect,useState} from 'react';
import {mergeManagedGrants,unknownSubjectsForTree} from './acl-state.js';
type API=(path:string,init?:RequestInit)=>Promise<any>;
type ResourceType='path'|'table'|'row';
type ACL={resource_type:ResourceType;resource_id:string;explicit:boolean;grants:Record<string,string[]>;revision:number};
type Account={id:string;username:string;name:string;enabled:boolean};
const actions=['read','write','attach'] as const;
function displayError(error:unknown,fallback:string){return error instanceof Error&&error.message.trim()?error.message:fallback}

function endpoint(type:ResourceType,id:string){return `/acl/${type}/${encodeURIComponent(id)}`}

export function ACLManager({api}:{api:API}){
 const [identity,setIdentity]=useState<any>(null),[resources,setResources]=useState<ACL[]>([]);
 const [accounts,setAccounts]=useState<Account[]>([]),[treeMessage,setTreeMessage]=useState(''),[treeReady,setTreeReady]=useState(false);
 const [kind,setKind]=useState<ResourceType>('path'),[resource,setResource]=useState(''),[loaded,setLoaded]=useState<ACL|null>(null);
 const [draft,setDraft]=useState<Record<string,string[]>>({}),[message,setMessage]=useState(''),[busy,setBusy]=useState(false);
 const isAdmin=identity?.scopes?.includes('xelibrary:admin')===true;

 async function loadResources(){
   try{setResources(await api('/acl?limit=1000'))}catch(e){setMessage(displayError(e,'读取 ACL 失败，请重试'))}
 }
 async function loadAccounts(){
   setTreeReady(false);setTreeMessage('');
   try{
     const rows:Account[]=[];let cursor='';
     for(;;){
       const page:Account[]=await api(`/auth/accounts?limit=1000&cursor=${encodeURIComponent(cursor)}`);
       if(!Array.isArray(page))throw Error('账号树接口返回格式无效');
       rows.push(...page);
       if(page.length<1000)break;
       const next=page[page.length-1]?.id;
       if(!next||next===cursor)throw Error('账号树分页游标未前进');
       cursor=next;
     }
     setAccounts(rows);setTreeReady(true);
   }catch(error){
     setAccounts([]);
     setTreeMessage(`无法读取当前身份可管理的账号树（${displayError(error,'请求失败，请重试')}）。为避免向未核实的主体授权，授权编辑已停用；仍可查看 ACL 或清除显式 ACL。`);
   }
 }
 useEffect(()=>{api('/users/me').then(setIdentity).catch(e=>setMessage(displayError(e,'读取当前身份失败，请重试')))},[]);
 useEffect(()=>{if(isAdmin){void loadResources();void loadAccounts()}},[isAdmin]);

 async function readACL(type=kind,id=resource){
   if(!id.trim()){setMessage('请输入路径、表 ID 或 table_id:row_id');return}
   setBusy(true);setMessage('');setLoaded(null);
   try{const acl:ACL=await api(endpoint(type,id.trim()));setLoaded(acl);setDraft(acl.grants||{});setKind(type);setResource(id.trim());setMessage(acl.explicit?'已读取显式 ACL；它覆盖此资源从父级继承的授权。':'此资源没有显式 ACL；权限按表/路径层级与账号树继承规则计算。')}
   catch(e){setMessage(displayError(e,'读取 ACL 失败，请重试'))}finally{setBusy(false)}
 }

 function changeGrant(subject:string,action:string,checked:boolean){
   setDraft(current=>{
     const grants={...current};const values=new Set(grants[subject]||[]);
     if(checked)values.add(action);else values.delete(action);
     if(values.size)grants[subject]=[...values];else delete grants[subject];
     return grants;
   });
 }

 async function save(){
   if(!loaded||!treeReady||loaded.resource_type!==kind||loaded.resource_id!==resource.trim())return;
   const grants=mergeManagedGrants(loaded.grants,draft,accounts);
   setBusy(true);setMessage('');
   try{const acl:ACL=await api(endpoint(kind,loaded.resource_id),{method:'PUT',headers:{'If-Match':`"revision:${loaded.revision}"`},body:JSON.stringify({grants})});setLoaded(acl);setDraft(acl.grants);setMessage(`ACL 已保存（revision ${acl.revision}）`);await loadResources()}
   catch(e){setMessage(`${displayError(e,'保存 ACL 失败，请重试')}。若其他管理员已更新，请重新读取后再编辑。`)}finally{setBusy(false)}
 }

 async function inherit(){
   if(!loaded||!loaded.explicit||loaded.resource_type!==kind||loaded.resource_id!==resource.trim())return;
   setBusy(true);setMessage('');
   try{const acl:ACL=await api(endpoint(kind,loaded.resource_id),{method:'DELETE',headers:{'If-Match':`"revision:${loaded.revision}"`}});setLoaded(acl);setDraft({});setMessage(`显式 ACL 已清除（revision ${acl.revision}）；权限将从上级继承。`);await loadResources()}
   catch(e){setMessage(`${displayError(e,'清除 ACL 失败，请重试')}。若其他管理员已更新，请重新读取后再操作。`)}finally{setBusy(false)}
 }

 const unknownSubjects=unknownSubjectsForTree(loaded?.grants||{},accounts,treeReady);
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
       {treeMessage&&<p role="status">{treeMessage}</p>}
       {treeReady?<fieldset className="acl-subjects" disabled={busy}>
         <legend>账号树主体及权限</legend>
         <p>主体仅从当前身份可访问的账号树选择。停用账号已有授权仍可移除；不能在此创建或猜测账号 subject。</p>
         <div className="acl-subject"><strong>所有主体 (*)</strong>{actions.map(action=><label key={action}><input type="checkbox" checked={(draft['*']||[]).includes(action)} onChange={e=>changeGrant('*',action,e.target.checked)}/>{action}</label>)}</div>
         {accounts.map(account=>{const current=loaded.grants[account.id]||[];return <div className="acl-subject" key={account.id}>
           <strong>{account.name} <small>{account.username} · {account.id}{account.enabled?'':' · 停用'}</small></strong>
           {actions.map(action=><label key={action}><input type="checkbox" checked={(draft[account.id]||[]).includes(action)} disabled={!account.enabled&&!current.includes(action)} onChange={e=>changeGrant(account.id,action,e.target.checked)}/>{action}</label>)}
         </div>})}
         {!accounts.length&&<small>当前账号树没有可选主体。</small>}
       </fieldset>:<p>账号树状态不可用，主体授权只能查看；清除显式 ACL 仍可恢复继承。</p>}
       {unknownSubjects.length>0&&<details><summary>{unknownSubjects.length} 个现有主体不在当前账号树中，保存时会原样保留</summary><ul>{unknownSubjects.map(subject=><li key={subject}>{subject} · {(loaded.grants[subject]||[]).join(', ')}</li>)}</ul></details>}
       <div><button disabled={busy||!treeReady} onClick={()=>void save()}>保存 ACL</button><button disabled={busy||!loaded.explicit} onClick={()=>void inherit()}>清除显式 ACL并继承</button></div>
     </>}
   </>}
   <p role="status">{message}</p>
 </section>
}
