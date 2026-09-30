import React, {useEffect, useState} from 'react';
type API=(path:string,init?:RequestInit)=>Promise<any>;
export function Permissions({api}:{api:API}){
 const [identity,setIdentity]=useState<any>(null),[kind,setKind]=useState('table'),[resource,setResource]=useState('');
 const [grants,setGrants]=useState('{}'),[message,setMessage]=useState(''),[audit,setAudit]=useState<any[]>([]);
 const [loaded,setLoaded]=useState(false);
 useEffect(()=>{api('/users/me').then(setIdentity).catch(e=>setMessage(String(e)))},[]);
 const endpoint=()=>`/acl/${kind}/${encodeURIComponent(resource)}`;
 async function load(){try{const data=await api(endpoint());setGrants(JSON.stringify(data.grants,null,2));setLoaded(true);setMessage(data.explicit?'已读取显式权限':'未设置显式权限：继承上级或 owner 权限')}catch(e){setMessage(String(e))}}
 async function save(){try{const parsed=JSON.parse(grants);if(!parsed||Array.isArray(parsed)||typeof parsed!=='object')throw Error('授权必须是对象');await api(endpoint(),{method:'PUT',body:JSON.stringify({grants:parsed})});setMessage('权限已保存并写入审计')}catch(e){setMessage(String(e))}}
 return <section className="query"><h2>身份与权限</h2><p>当前身份：{identity?.subject||'读取中'} · Harness：{identity?.kind||'—'}</p>
 <details><summary>查看当前 scopes</summary><pre>{identity?.scopes?.join('\n')}</pre></details>
 {identity?.scopes?.includes('xelibrary:admin')&&<><h3>XeLibrary 资源授权</h3><p>授权格式为用户 subject 到 read / write / attach 列表。显式 ACL 覆盖继承规则；空对象会拒绝非管理员访问。* 表示所有已登录用户。</p>
 <select aria-label="资源类型" value={kind} onChange={e=>{setKind(e.target.value);setLoaded(false)}}><option value="table">表</option><option value="row">行</option><option value="path">路径</option></select>
 <input aria-label="资源标识" value={resource} onChange={e=>{setResource(e.target.value);setLoaded(false)}} placeholder="table UUID / table UUID:row UUID / 绝对路径"/>
 <button disabled={!resource} onClick={load}>读取权限</button><textarea aria-label="授权内容" value={grants} onChange={e=>setGrants(e.target.value)}/>
 <button disabled={!loaded} onClick={save}>保存授权</button><button onClick={()=>api('/audit').then(setAudit).catch(e=>setMessage(String(e)))}>查看最近审计</button>
 {audit.length>0&&<pre>{JSON.stringify(audit,null,2)}</pre>}</>}
 <p role="status">{message}</p></section>
}
