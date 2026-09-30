import React, {useEffect, useRef, useState} from 'react';
type Item={id:string;repository:string;issue_id:number;title:string;status:string;holder:string|null;expires:number;epoch:number;claim_active:boolean};
type API=(path:string,init?:RequestInit)=>Promise<any>;
type APIPage=(path:string,init?:RequestInit)=>Promise<{data:Item[];meta:{next_cursor?:string|null;[key:string]:any}}>;

function issueUrl(item:Item){
 const [owner,repo,...rest]=item.repository.split('/');
 if(rest.length||!owner||!repo||owner==='.'||owner==='..'||repo==='.'||repo==='..'||!/^[-A-Za-z0-9_.]+$/.test(owner)||!/^[-A-Za-z0-9_.]+$/.test(repo))return null;
 return `https://xenon.jyxxe.com/dev/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/issues/${item.issue_id}`;
}

export function Work({api,apiPage}:{api:API;apiPage:APIPage}){
 const [items,setItems]=useState<Item[]>([]),[message,setMessage]=useState(''),[subject,setSubject]=useState('');
 const [filter,setFilter]=useState(''),[cursor,setCursor]=useState<string|null>(null),[loading,setLoading]=useState(false),[loadingMore,setLoadingMore]=useState(false),[actionId,setActionId]=useState('');
 const generation=useRef(0),loadingMoreRef=useRef(false),cursorRef=useRef<string|null>(null);

 async function refresh(){
   const current=++generation.current;
   loadingMoreRef.current=false;cursorRef.current=null;setCursor(null);setLoading(true);setLoadingMore(false);setMessage('');
   try{
     const page=await apiPage('/work?limit=100');
     if(current!==generation.current)return;
     setItems(page.data);const next=page.meta.next_cursor||null;cursorRef.current=next;setCursor(next);
   }catch(e){if(current===generation.current)setMessage(String(e))}
   finally{if(current===generation.current)setLoading(false)}
 }

 async function loadMore(){
   const current= generation.current, requestedCursor=cursorRef.current;
   if(!requestedCursor||loadingMoreRef.current)return;
   loadingMoreRef.current=true;setLoadingMore(true);setMessage('');
   try{
     const page=await apiPage(`/work?limit=100&cursor=${encodeURIComponent(requestedCursor)}`);
     if(current!==generation.current)return;
     setItems(old=>{const seen=new Set(old.map(item=>item.id)),added:Item[]=[];for(const item of page.data){if(!seen.has(item.id)){seen.add(item.id);added.push(item)}}return [...old,...added]});
     const next=page.meta.next_cursor||null;
     if(next===requestedCursor){cursorRef.current=null;setCursor(null);setMessage('任务列表分页游标未前进，请刷新后重试')}else{cursorRef.current=next;setCursor(next)}
   }catch(e){if(current===generation.current)setMessage(String(e))}
   finally{if(current===generation.current){loadingMoreRef.current=false;setLoadingMore(false)}}
 }

 useEffect(()=>{void refresh();api('/users/me').then(v=>setSubject(v.subject)).catch(e=>setMessage(String(e)))},[]);
 async function action(item:Item,operation:string,completed=false){
   setActionId(item.id);setMessage('');
   try{await api(`/work/${item.id}/${operation}`,{method:'POST',body:JSON.stringify(operation==='claim'?{ttl_seconds:900}:{epoch:item.epoch,completed})});await refresh()}
   catch(e){setMessage(String(e))}
   finally{setActionId('')}
 }
 const needle=filter.trim().toLocaleLowerCase();
 const visible=items.filter(item=>!needle||`${item.issue_id} ${item.title} ${item.repository} ${item.status} ${item.holder||''}`.toLocaleLowerCase().includes(needle));
 return <section className="query"><h2>Harness 任务队列</h2><p>领取有效期 15 分钟。过期后允许重新领取；旧领取者不能完成后续任务。正式提交仍通过分支与 PR。</p>
   <button onClick={()=>void refresh()} disabled={loading}>刷新队列</button>
   <label>筛选已加载任务<input aria-label="筛选已加载任务" value={filter} onChange={e=>setFilter(e.target.value)} placeholder="Issue 编号、标题、状态或身份"/></label>
   <small>筛选范围：当前已加载的 {items.length} 项任务</small><p role="status">{loading?'正在读取任务…':message}</p>
   <table><thead><tr><th>任务</th><th>状态</th><th>领取身份</th><th>操作</th></tr></thead><tbody>{visible.map(item=>{
     const href=issueUrl(item);
     return <tr key={item.id}><td>{href?<a href={href} target="_blank" rel="noopener noreferrer">#{item.issue_id} {item.title}</a>:<>#{item.issue_id} {item.title}</>}</td><td>{item.status}{item.claim_active?' · 领取有效':''}</td><td>{item.holder||'—'}</td>
       <td>{item.status!=='completed'&&!item.claim_active&&<button disabled={actionId===item.id} onClick={()=>void action(item,'claim')}>领取</button>}
       {item.claim_active&&item.holder===subject&&<><button disabled={actionId===item.id} onClick={()=>void action(item,'release')}>释放</button><button disabled={actionId===item.id} onClick={()=>void action(item,'release',true)}>标记完成</button></>}</td>
     </tr>;
   })}</tbody></table>
   {!loading&&!visible.length&&<p>{items.length?'没有匹配的已加载任务。':'队列暂无任务。'}</p>}
   {cursor&&<button onClick={()=>void loadMore()} disabled={loading||loadingMore}>{loadingMore?'正在加载…':'加载更多任务'}</button>}
 </section>
}
