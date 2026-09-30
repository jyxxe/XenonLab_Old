import React, {useEffect,useState} from 'react';
type Item={id:string;repository:string;issue_id:number;title:string;status:string;holder:string|null;expires:number;epoch:number;claim_active:boolean};
type API=(path:string,init?:RequestInit)=>Promise<any>;
export function Work({api}:{api:API}){
 const [items,setItems]=useState<Item[]>([]),[message,setMessage]=useState(''),[subject,setSubject]=useState('');
 async function refresh(){try{setItems(await api('/work?limit=100'));setMessage('')}catch(e){setMessage(String(e))}}
 useEffect(()=>{refresh();api('/users/me').then(v=>setSubject(v.subject)).catch(e=>setMessage(String(e)))},[]);
 async function action(item:Item,operation:string,completed=false){try{await api(`/work/${item.id}/${operation}`,{method:'POST',body:JSON.stringify(operation==='claim'?{ttl_seconds:900}:{epoch:item.epoch,completed})});await refresh()}catch(e){setMessage(String(e))}}
 return <section className="query"><h2>Harness 任务队列</h2><p>领取有效期 15 分钟。过期后允许重新领取；旧领取者不能完成后续任务。正式提交仍通过分支与 PR。</p>
 <button onClick={refresh}>刷新队列</button><p role="status">{message}</p>
 <table><thead><tr><th>任务</th><th>状态</th><th>领取身份</th><th>操作</th></tr></thead><tbody>{items.map(item=><tr key={item.id}>
 <td>#{item.issue_id} {item.title}</td><td>{item.status}{item.claim_active?' · 领取有效':''}</td><td>{item.holder||'—'}</td>
 <td>{item.status!=='completed'&&!item.claim_active&&<button onClick={()=>action(item,'claim')}>领取</button>}
 {item.claim_active&&item.holder===subject&&<><button onClick={()=>action(item,'release')}>释放</button><button onClick={()=>action(item,'release',true)}>标记完成</button></>}</td>
 </tr>)}</tbody></table>{!items.length&&!message&&<p>队列暂无任务。</p>}</section>
}
