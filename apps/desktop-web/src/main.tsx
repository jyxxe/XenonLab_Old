import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';
import {login,callback,sessionToken,logout} from './auth';
import {Settings} from './Settings';
import {Work} from './Work';
import {Knowledge} from './Knowledge';
type App = {id:string; title:string; origin:string; entry:string; icon:string; default_size:[number,number]; singleton:boolean};
type Window = {id:string; app_id:string; title:string; x:number; y:number; width:number; height:number; minimized:boolean};
const builtins:App[]=[{id:'knowledge',title:'知识库',icon:'▤',origin:location.origin,entry:'/',default_size:[1100,740],singleton:true},{id:'settings',title:'系统设置',icon:'⚙',origin:location.origin,entry:'/',default_size:[850,680],singleton:true},{id:'work',title:'任务中心',icon:'✓',origin:location.origin,entry:'/',default_size:[1000,700],singleton:true}];
const libraryView=location.hostname==='library.xenon.lan';
const allowedMessages = new Set(['app.ready','app.request_resize','app.set_title','app.open_app','app.notify']);
function Desktop(){
 useEffect(()=>{if(libraryView){const handler=(event:MessageEvent)=>{if(event.source!==parent||!['https://xenon.lan','https://192.168.0.101:9443'].includes(event.origin)||event.data?.type!=='auth.session_changed'||typeof event.data.access_token!=='string')return;setToken(event.data.access_token)};addEventListener('message',handler);return()=>removeEventListener('message',handler)}},[]);
 const [apps,setApps]=useState<App[]>([]), [windows,setWindows]=useState<Window[]>([]);
 const [token,setToken]=useState(''), [error,setError]=useState(''), [notice,setNotice]=useState('');
 const [query,setQuery]=useState('FROM "/**" LIMIT 20'), [results,setResults]=useState<unknown[]>([]);
 const [connected,setConnected]=useState(false),[launchpad,setLaunchpad]=useState(false),[persistent,setPersistent]=useState(false);
 useEffect(()=>{callback().then(async result=>result||await sessionToken()).then(result=>{if(result){setPersistent(true);setToken(result.token)}}).catch(e=>setError(String(e)))},[]);
 useEffect(()=>{if(!persistent)return;const renew=()=>sessionToken().then(result=>{if(result)setToken(result.token);else{setToken('');setConnected(false);setPersistent(false);setNotice('登录已撤销，请重新登录')}}).catch(e=>setNotice(String(e)));const timer=setInterval(renew,60000);const visible=()=>{if(document.visibilityState==='visible')renew()};document.addEventListener('visibilitychange',visible);return()=>{clearInterval(timer);document.removeEventListener('visibilitychange',visible)}},[persistent]);
 useEffect(()=>{if(token&&!connected)connect()},[token]);
 async function api(path:string, init:RequestInit={}){
   const current=persistent?await sessionToken():{token};if(!current)throw Error('登录已撤销，请重新登录');
   const response=await fetch(`/api/v1${path}`, {...init,headers:{'Content-Type':'application/json',Authorization:`Bearer ${current.token}`,...init.headers}});
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
   try {const data=await api('/apps'); setApps([...builtins,...data.filter((a:App)=>!builtins.some(b=>b.id===a.id))]);const layout=await api('/users/me/layout');setWindows(layout.windows || []);setConnected(true);setError('');}
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
 if(libraryView&&connected)return <Knowledge api={api}/>;
 return <main>
   <header><strong>✦ XeonoLab</strong><span>知识 · 模型 · 工作空间</span><a href="https://xenon.jyxxe.com/dev/xenon-admin/XeonoLab/issues" target="_blank" rel="noopener noreferrer">Issues / 协作</a><time>{new Date().toLocaleDateString('zh-CN')}</time>{connected&&<button onClick={async()=>{try{if(persistent)await logout();setPersistent(false);setToken('');setConnected(false);setWindows([])}catch(e){setError(String(e))}}}>退出登录</button>}</header>
   {!connected&&<section className="intro"><small>YOUR KNOWLEDGE WORKSPACE</small><h1>让知识，有处可循。</h1><p>统一桌面 · 可追溯数据 · 受控工具</p></section>}
   {!connected?<section className="login"><h2>连接工作空间</h2><p>通过统一身份服务登录。</p><button onClick={()=>login().catch(e=>setError(String(e)))}>登录 XeonoLab</button><details><summary>API 访问令牌</summary><input type="password" aria-label="访问令牌" value={token} onChange={e=>setToken(e.target.value)} placeholder="OIDC access token"/><button onClick={connect}>连接</button></details><p className="error">{error}</p></section>:
   <>{launchpad&&<section className="launchpad" aria-label="LaunchPad"><button className="launchpad-close" onClick={()=>setLaunchpad(false)}>关闭 LaunchPad</button><h2>LaunchPad</h2><div>{apps.map(app=><button key={app.id} onClick={()=>{open(app);setLaunchpad(false)}}><b>{app.icon}</b><span>{app.title}</span></button>)}</div></section>}
   {windows.filter(w=>!w.minimized).map(w=>{const app=apps.find(a=>a.id===w.app_id);if(!app)return null;return <section className="window" key={w.id} style={{left:w.x,top:w.y,width:w.width,height:w.height}}><div className="bar" onPointerDown={e=>drag(e,w.id)}><button className="close" aria-label="关闭" onClick={()=>setWindows(ws=>ws.filter(x=>x.id!==w.id))}/><button className="min" aria-label="最小化" onClick={()=>update(w.id,{minimized:true})}/><button className="max" aria-label="最大化" onClick={()=>update(w.id,{x:20,y:45,width:innerWidth-40,height:innerHeight-140})}/><span>{w.title}</span></div>{builtins.some(b=>b.id===app.id)?<div className="window-body">{app.id==='knowledge'?<Knowledge api={api}/>:app.id==='work'?<Work api={api}/>:<Settings api={api} saveLayout={async()=>{await api('/users/me/layout',{method:'PUT',body:JSON.stringify({dock:apps.map(a=>a.id),windows})})}}/>}</div>:<iframe onLoad={event=>{if(app.id==='xelibrary')(event.currentTarget as HTMLIFrameElement).contentWindow?.postMessage({type:'auth.session_changed',access_token:token},app.origin)}} id={`frame-${w.id}`} title={w.title} src={new URL(app.entry,app.origin).href} sandbox="allow-scripts allow-forms allow-same-origin" referrerPolicy="no-referrer"/>}</section>})}
   {!libraryView&&<nav className="dock" aria-label="应用程序"><button onClick={()=>setLaunchpad(!launchpad)} title="LaunchPad"><b>▦</b><span>LaunchPad</span></button>{apps.map(app=><button key={app.id} onClick={()=>open(app)} title={app.title}><b>{app.icon}</b><span>{app.title}</span></button>)}{windows.filter(w=>w.minimized).map(w=><button key={w.id} onClick={()=>update(w.id,{minimized:false})}>{w.title} ↗</button>)}</nav>}</>}
   {notice&&<aside onClick={()=>setNotice('')}>{notice}</aside>}
 </main>
}
createRoot(document.getElementById('root')!).render(<Desktop/>);
