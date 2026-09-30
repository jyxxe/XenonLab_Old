import React, {useEffect, useState} from 'react';
type API=(path:string,init?:RequestInit)=>Promise<any>;
export function Permissions({api}:{api:API}){
 const [identity,setIdentity]=useState<any>(null),[message,setMessage]=useState(''),[audit,setAudit]=useState<any[]>([]);
 useEffect(()=>{api('/users/me').then(setIdentity).catch(e=>setMessage(String(e)))},[]);
 return <section className="query"><h2>身份与权限</h2><p>当前身份：{identity?.subject||'读取中'} · Harness：{identity?.kind||'—'}</p>
 <details><summary>查看当前 scopes</summary><pre>{identity?.scopes?.join('\n')}</pre></details>
 {identity?.scopes?.includes('xelibrary:admin')&&<><p>知识库资源 ACL 的查看、显式授权和恢复继承请使用“知识库 ACL”标签。该页面通过 revision / If-Match 防止覆盖其他管理员的修改。</p><button onClick={()=>api('/audit').then(setAudit).catch(e=>setMessage(String(e)))}>查看最近审计</button>{audit.length>0&&<pre>{JSON.stringify(audit,null,2)}</pre>}</>}
 <p role="status">{message}</p></section>
}
