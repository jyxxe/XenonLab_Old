import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';
import {login,callback} from './auth';
import {Permissions} from './Permissions';
import {Work} from './Work';
type App = {id:string; title:string; origin:string; entry:string; icon:string; default_size:[number,number]; singleton:boolean};
type Window = {id:string; app_id:string; title:string; x:number; y:number; width:number; height:number; minimized:boolean};
const libraryView=location.hostname==='library.xenon.lan';
const allowedMessages = new Set(['app.ready','app.request_resize','app.set_title','app.open_app','app.notify']);
function Desktop(){
 useEffect(()=>{if(libraryView){const handler=(event:MessageEvent)=>{if(event.source!==parent||!['https://xenon.lan','https://192.168.0.101:9443'].includes(event.origin)||event.data?.type!=='auth.session_changed'||typeof event.data.access_token!=='string')return;setToken(event.data.access_token)};addEventListener('message',handler);return()=>removeEventListener('message',handler)}},[]);
 const [apps,setApps]=useState<App[]>([]), [windows,setWindows]=useState<Window[]>([]);
 const [token,setToken]=useState(''), [error,setError]=useState(''), [notice,setNotice]=useState('');
 const [query,setQuery]=useState('FROM "/**" LIMIT 20'), [results,setResults]=useState<unknown[]>([]);
 const [connected,setConnected]=useState(false);
 useEffect(()=>{callback().then(result=>{if(result){setToken(result.token);setTimeout(()=>{setToken('');setConnected(false);setNotice('登录已过期，请重新登录')},result.expires*1000)}}).catch(e=>setError(String(e)))},[]);
 useEffect(()=>{if(token)connect()},[token]);
 async function api(path:string, init:RequestInit={}){
   const response=await fetch(`/api/v1${path}`, {...init,headers:{'Content-Type':'application/json',Authorization:`Bearer ${token}`,...init.headers}});
   const result=await response.json();
   if(!response.ok) throw Error(result.error?.message || `HTTP ${response.status}`);
   return result.data;
 }
 function open(app:App){
   setWindows(old=>{
     const existing=old.find(w=>w.app_id===app.id);
     if(app.singleton && existing) return [...old.filter(w=>w.id!==existing.id),{...existing,minimized:false}];
     return [...old,{id:crypto.randomUUID(),app_id:app.id,title:app.title,x:60+old.length*24,y:70+old.length*24,width:Math.min(app.default_size[0],innerWidth-80),height:Math.min(app.default_size[1],innerHeight-200),minimized:false}];
   });
 }
 async function connect(){
   try {const data=await api('/apps'); setApps(data);const layout=await api('/users/me/layout');setWindows(layout.windows || []);setConnected(true);setError('');}
   catch(e){setError(String(e));}
 }
 useEffect(()=>{
   const handler=(event:MessageEvent)=>{
     const win=windows.find(w=>{const app=apps.find(a=>a.id===w.app_id);const frame=document.getElementById(`frame-${w.id}`) as HTMLIFrameElement|null;return app?.origin===event.origin && frame?.contentWindow===event.source});
     if(!win || !event.data || typeof event.data!=='object' || !allowedMessages.has(event.data.type)) return;
     if(event.data.type==='app.set_title' && typeof event.data.title==='string') setWindows(ws=>ws.map(w=>w.id===win.id?{...w,title:event.data.title.slice(0,80)}:w));
     if(event.data.type==='app.request_resize') setWindows(ws=>ws.map(w=>w.id===win.id?{...w,width:Math.max(320,Math.min(Number(event.data.width)||w.width,innerWidth-40)),height:Math.max(200,Math.min(Number(event.data.height)||w.height,innerHeight-140))}:w));
     if(event.data.type==='app.notify' && typeof event.data.message==='string')setNotice(event.data.message.slice(0,200));
     if(event.data.type==='app.open_app'){const app=apps.find(a=>a.id===event.data.app_id);if(app)open(app);}
   };
   addEventListener('message',handler);return()=>removeEventListener('message',handler);
 },[windows,apps]);
 const update=(id:string,patch:Partial<Window>)=>setWindows(ws=>ws.map(w=>w.id===id?{...w,...patch}:w));
 function drag(e:React.PointerEvent,id:string){
   if((e.target as HTMLElement).closest('button'))return;
   const w=windows.find(w=>w.id===id)!;const x=e.clientX,y=e.clientY;
   e.currentTarget.setPointerCapture(e.pointerId);
   const move=(event:PointerEvent)=>update(id,{x:Math.max(0,Math.min(innerWidth-100,w.x+event.clientX-x)),y:Math.max(35,w.y+event.clientY-y)});
   const end=()=>{removeEventListener('pointermove',move);removeEventListener('pointerup',end)};
   addEventListener('pointermove',move);addEventListener('pointerup',end);
 }
 async function search(){try{const data=await api('/query',{method:'POST',body:JSON.stringify({expression:query})});setResults(data);setError('')}catch(e){setError(String(e))}}
 return <main>
   <header><strong>✦ XeonoLab</strong><span>知识 · 模型 · 工作空间</span><a href="https://xenon.jyxxe.com/dev/xenon-admin/XeonoLab/issues" target="_blank" rel="noopener noreferrer">Issues / 协作</a><time>{new Date().toLocaleDateString('zh-CN')}</time></header>
   <section className="intro"><small>YOUR KNOWLEDGE WORKSPACE</small><h1>让知识，有处可循。</h1><p>统一桌面 · 可追溯数据 · 受控工具</p></section>
   {!connected?<section className="login"><h2>连接工作空间</h2><p>通过统一身份服务登录。</p><button onClick={()=>login().catch(e=>setError(String(e)))}>登录 XeonoLab</button><details><summary>API 访问令牌</summary><input type="password" aria-label="访问令牌" value={token} onChange={e=>setToken(e.target.value)} placeholder="OIDC access token"/><button onClick={connect}>连接</button></details><p className="error">{error}</p></section>:
   <><Work api={api}/><Permissions api={api}/><section className="query"><h2>XeLibrary 查询</h2><textarea aria-label="查询表达式" value={query} onChange={e=>setQuery(e.target.value)}/><button onClick={search}>执行查询</button><button onClick={async()=>{try{await api('/users/me/layout',{method:'PUT',body:JSON.stringify({dock:apps.map(a=>a.id),windows})});setNotice('桌面布局已保存')}catch(e){setError(String(e))}}}>保存桌面</button><p className="error">{error}</p>{results.length>0&&<pre>{JSON.stringify(results,null,2)}</pre>}</section>
   {windows.filter(w=>!w.minimized).map(w=>{const app=apps.find(a=>a.id===w.app_id);if(!app)return null;return <section className="window" key={w.id} style={{left:w.x,top:w.y,width:w.width,height:w.height}}><div className="bar" onPointerDown={e=>drag(e,w.id)}><button className="close" aria-label="关闭" onClick={()=>setWindows(ws=>ws.filter(x=>x.id!==w.id))}/><button className="min" aria-label="最小化" onClick={()=>update(w.id,{minimized:true})}/><button className="max" aria-label="最大化" onClick={()=>update(w.id,{x:20,y:45,width:innerWidth-40,height:innerHeight-140})}/><span>{w.title}</span></div><iframe onLoad={event=>{if(app.id==='xelibrary')(event.currentTarget as HTMLIFrameElement).contentWindow?.postMessage({type:'auth.session_changed',access_token:token},app.origin)}} id={`frame-${w.id}`} title={w.title} src={new URL(app.entry,app.origin).href} sandbox="allow-scripts allow-forms allow-same-origin" referrerPolicy="no-referrer"/></section>})}
   {!libraryView&&<nav className="dock" aria-label="应用程序">{apps.map(app=><button key={app.id} onClick={()=>open(app)} title={app.title}><b>{app.icon}</b><span>{app.title}</span></button>)}{windows.filter(w=>w.minimized).map(w=><button key={w.id} onClick={()=>update(w.id,{minimized:false})}>{w.title} ↗</button>)}</nav>}</>}
   {notice&&<aside onClick={()=>setNotice('')}>{notice}</aside>}
 </main>
}
createRoot(document.getElementById('root')!).render(<Desktop/>);
