import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './style.css';
import {login,callback,sessionToken,logout} from './auth';
import {Settings} from './Settings';
import {defaultPreferences,Preferences} from './DesktopPreferences';
import {Work} from './Work';
import {Knowledge} from './Knowledge';
import {Dashboard} from './Dashboard';
import {MenuClock,ClockWidget,StatusWidget} from './Widgets';
type App = {id:string; title:string; origin:string; entry:string; icon:string; default_size:[number,number]; singleton:boolean;permissions?:string[];sandbox?:{allow_scripts:boolean;allow_forms:boolean;allow_downloads:boolean}};
type Window = {id:string; app_id:string; title:string; x:number; y:number; width:number; height:number; minimized:boolean};
type APIPage<T=any>={data:T;meta:{request_id?:string;next_cursor?:string|null;[key:string]:any}};
const builtins:App[]=[{id:'dashboard',title:'系统面板',icon:'◎',origin:location.origin,entry:'/',default_size:[1080,720],singleton:true},{id:'knowledge',title:'知识库',permissions:['xelibrary:read'],icon:'▤',origin:location.origin,entry:'/',default_size:[1100,740],singleton:true},{id:'settings',title:'系统设置',icon:'⚙',origin:location.origin,entry:'/',default_size:[850,680],singleton:true},{id:'work',title:'任务中心',permissions:['dev:read'],icon:'✓',origin:location.origin,entry:'/',default_size:[1000,700],singleton:true}];
const libraryView=location.hostname==='library.xenon.lan';
const allowedMessages = new Set(['app.ready','app.request_resize','app.set_title','app.open_app','app.notify']);
function Desktop(){
 useEffect(()=>{if(libraryView){const handler=(event:MessageEvent)=>{if(event.source!==parent||!['https://xenon.lan','https://192.168.0.101:9443'].includes(event.origin)||event.data?.type!=='auth.session_changed'||typeof event.data.access_token!=='string')return;setToken(event.data.access_token)};addEventListener('message',handler);return()=>removeEventListener('message',handler)}},[]);
 const [apps,setApps]=useState<App[]>([]), [windows,setWindows]=useState<Window[]>([]);
 const [token,setToken]=useState(''), [error,setError]=useState(''), [notice,setNotice]=useState('');
 const [preferences,setPreferences]=useState<Preferences>(defaultPreferences),[dock,setDock]=useState<string[]>([]);
 const [connected,setConnected]=useState(false),[launchpad,setLaunchpad]=useState(false),[persistent,setPersistent]=useState(false),[launchFilter,setLaunchFilter]=useState('');
 useEffect(()=>{callback().then(async result=>result||await sessionToken()).then(result=>{if(result){setPersistent(true);setToken(result.token)}}).catch(e=>setError(String(e)))},[]);
 useEffect(()=>{if(!persistent)return;const renew=()=>sessionToken().then(result=>{if(result)setToken(result.token);else{setToken('');setConnected(false);setPersistent(false);setNotice('登录已撤销，请重新登录')}}).catch(e=>setNotice(String(e)));const timer=setInterval(renew,60000);const visible=()=>{if(document.visibilityState==='visible')renew()};document.addEventListener('visibilitychange',visible);return()=>{clearInterval(timer);document.removeEventListener('visibilitychange',visible)}},[persistent]);
 useEffect(()=>{if(token&&!connected)connect()},[token]);
 async function apiPage<T=any>(path:string, init:RequestInit={}):Promise<APIPage<T>>{
   const current=persistent?await sessionToken():{token};if(!current)throw Error('登录已撤销，请重新登录');
   const headers=new Headers(init.headers);if(!headers.has('Content-Type'))headers.set('Content-Type','application/json');headers.set('Authorization',`Bearer ${current.token}`);
   const response=await fetch(`/api/v1${path}`, {...init,headers});
   const result=await response.json();
   if(!response.ok) throw Error(result.error?.message || `HTTP ${response.status}`);
   return {data:result.data,meta:result.meta||{}};
 }
 async function api<T=any>(path:string, init:RequestInit={}):Promise<T>{return (await apiPage<T>(path,init)).data}
 async function apiRaw(path:string, init:RequestInit={}):Promise<Response>{
   const current=persistent?await sessionToken():{token};if(!current)throw Error('登录已撤销，请重新登录');
   const headers=new Headers(init.headers);
   if(typeof FormData!=='undefined'&&init.body instanceof FormData)headers.delete('Content-Type');
   else if(init.body&&!headers.has('Content-Type'))headers.set('Content-Type','application/json');
   headers.set('Authorization',`Bearer ${current.token}`);
   const response=await fetch(`/api/v1${path}`,{...init,headers});
   if(!response.ok){
     const result=await response.clone().json().catch(()=>null);
     throw Error(result?.error?.message||result?.detail?.[0]?.msg||`HTTP ${response.status}`);
   }
   return response;
 }
 function open(app:App){
   setWindows(old=>{
     const existing=old.find(w=>w.app_id===app.id);
     if(app.singleton && existing) return [...old.filter(w=>w.id!==existing.id),{...existing,minimized:false}];
     return [...old,{id:crypto.randomUUID(),app_id:app.id,title:app.title,x:60+old.length*24,y:70+old.length*24,width:Math.min(app.default_size[0],innerWidth-80),height:Math.min(app.default_size[1],innerHeight-200),minimized:false}];
   });
 }
 async function refreshApps(){
   const [data,me]=await Promise.all([api('/apps'),api('/users/me')]);
   const available=[...builtins.filter(a=>(a.permissions||[]).every(scope=>me.scopes.includes(scope))),...data.filter((a:App)=>!builtins.some(b=>b.id===a.id))];
   setApps(available);setWindows(old=>old.filter(w=>available.some(a=>a.id===w.app_id)));return available;
 }
 async function connect(){
   try {const available=await refreshApps();const [layout,prefs]=await Promise.all([api('/users/me/layout'),api('/users/me/preferences')]);setWindows((layout.windows||[]).filter((w:Window)=>available.some(a=>a.id===w.app_id)));setDock(layout.dock||[]);setPreferences(prefs);setConnected(true);setError('');}
   catch(e){setError(String(e));}
 }
 useEffect(()=>{
   const media=matchMedia('(prefers-color-scheme: dark)');
   const apply=()=>{document.documentElement.dataset.theme=preferences.theme==='system'?(media.matches?'dark':'light'):preferences.theme;document.documentElement.dataset.accent=preferences.accent;document.documentElement.dataset.wallpaper=preferences.wallpaper};
   apply();media.addEventListener('change',apply);return()=>media.removeEventListener('change',apply);
 },[preferences]);
 useEffect(()=>{
   const observer=new ResizeObserver(entries=>{setWindows(old=>old.map(win=>{const entry=entries.find(e=>(e.target as HTMLElement).dataset.windowId===win.id);if(!entry)return win;const rect=entry.target.getBoundingClientRect();return Math.abs(rect.width-win.width)>1||Math.abs(rect.height-win.height)>1?{...win,width:Math.round(rect.width),height:Math.round(rect.height)}:win}))});
   document.querySelectorAll('.window').forEach(element=>observer.observe(element));return()=>observer.disconnect();
 },[windows.map(w=>`${w.id}:${w.minimized}`).join(',')]);
 useEffect(()=>{windows.forEach(w=>{const app=apps.find(a=>a.id===w.app_id);const frame=document.getElementById('frame-'+w.id) as HTMLIFrameElement|null;if(!app||!frame)return;frame.contentWindow?.postMessage({type:'theme.changed',theme:preferences.theme,accent:preferences.accent},app.origin);if(app.id==='xelibrary'&&app.origin==='https://library.xenon.lan')frame.contentWindow?.postMessage({type:'auth.session_changed',access_token:token},app.origin)})},[token,preferences]);
 useEffect(()=>{
   const handler=(event:MessageEvent)=>{
     const win=windows.find(w=>{const app=apps.find(a=>a.id===w.app_id);const frame=document.getElementById(`frame-${w.id}`) as HTMLIFrameElement|null;return app?.origin===event.origin && frame?.contentWindow===event.source});
     if(!win || !event.data || typeof event.data!=='object' || !allowedMessages.has(event.data.type)) return;
     if(event.data.type==='app.ready'){const app=apps.find(a=>a.id===win.app_id);if(app)(event.source as globalThis.Window)?.postMessage({type:'desktop.ready',theme:preferences.theme,accent:preferences.accent},app.origin)};
     if(event.data.type==='app.set_title' && typeof event.data.title==='string') setWindows(ws=>ws.map(w=>w.id===win.id?{...w,title:event.data.title.slice(0,80)}:w));
     if(event.data.type==='app.request_resize') setWindows(ws=>ws.map(w=>w.id===win.id?{...w,width:Math.max(320,Math.min(Number(event.data.width)||w.width,innerWidth-40)),height:Math.max(200,Math.min(Number(event.data.height)||w.height,innerHeight-140))}:w));
     if(event.data.type==='app.notify' && typeof event.data.message==='string')setNotice(event.data.message.slice(0,200));
     if(event.data.type==='app.open_app'){const app=apps.find(a=>a.id===event.data.app_id);if(app)open(app);}
   };
   addEventListener('message',handler);return()=>removeEventListener('message',handler);
 },[windows,apps,preferences]);
 const update=(id:string,patch:Partial<Window>)=>setWindows(ws=>ws.map(w=>w.id===id?{...w,...patch}:w));
 function drag(e:React.PointerEvent,id:string){
   if((e.target as HTMLElement).closest('button'))return;
   const w=windows.find(w=>w.id===id)!;const x=e.clientX,y=e.clientY;
   e.currentTarget.setPointerCapture(e.pointerId);
   const move=(event:PointerEvent)=>update(id,{x:Math.max(0,Math.min(innerWidth-100,w.x+event.clientX-x)),y:Math.max(35,w.y+event.clientY-y)});
   const end=()=>{removeEventListener('pointermove',move);removeEventListener('pointerup',end)};
   addEventListener('pointermove',move);addEventListener('pointerup',end);
 }
 if(libraryView&&connected)return <Knowledge api={api} apiPage={apiPage} apiRaw={apiRaw}/>;
 return <main>
   <header><strong>✦ XeonoLab</strong><span>知识 · 模型 · 工作空间</span><a href="https://xenon.jyxxe.com/dev/xenon-admin/XeonoLab/issues" target="_blank" rel="noopener noreferrer">Issues / 协作</a><MenuClock timezone={preferences.timezone}/>{connected&&<button onClick={async()=>{try{if(persistent)await logout();setPersistent(false);setToken('');setConnected(false);setWindows([])}catch(e){setError(String(e))}}}>退出登录</button>}</header>
   {!connected&&<section className="intro"><small>YOUR KNOWLEDGE WORKSPACE</small><h1>让知识，有处可循。</h1><p>统一桌面 · 可追溯数据 · 受控工具</p></section>}
   {!connected?<section className="login"><h2>连接工作空间</h2><p>通过统一身份服务登录。</p><button onClick={()=>login().catch(e=>setError(String(e)))}>登录 XeonoLab</button><details><summary>API 访问令牌</summary><input type="password" aria-label="访问令牌" value={token} onChange={e=>setToken(e.target.value)} placeholder="OIDC access token"/><button onClick={connect}>连接</button></details><p className="error">{error}</p></section>:
   <>{launchpad&&<section className="launchpad" aria-label="LaunchPad"><button className="launchpad-close" onClick={()=>setLaunchpad(false)}>关闭 LaunchPad</button><h2>LaunchPad</h2><input className="launchpad-search" aria-label="搜索应用" placeholder="搜索应用" value={launchFilter} onChange={e=>setLaunchFilter(e.target.value)}/><div>{apps.filter(app=>(app.title+' '+app.id).toLowerCase().includes(launchFilter.toLowerCase())).map(app=><button key={app.id} onClick={()=>{open(app);setLaunchpad(false)}}><b>{app.icon}</b><span>{app.title}</span></button>)}</div>{!apps.some(app=>(app.title+' '+app.id).toLowerCase().includes(launchFilter.toLowerCase()))&&<p className="launchpad-empty">没有匹配的应用</p>}</section>}
   {windows.filter(w=>!w.minimized).map(w=>{
     const app=apps.find(a=>a.id===w.app_id);if(!app)return null;
     return <section className="window" key={w.id} data-window-id={w.id} onPointerDown={()=>setWindows(old=>[...old.filter(item=>item.id!==w.id),old.find(item=>item.id===w.id)!])} style={{left:w.x,top:w.y,width:w.width,height:w.height,zIndex:10+windows.findIndex(item=>item.id===w.id)}}>
       <div className="bar" onPointerDown={e=>drag(e,w.id)}><button className="close" aria-label="关闭" onClick={()=>setWindows(ws=>ws.filter(x=>x.id!==w.id))}/><button className="min" aria-label="最小化" onClick={()=>update(w.id,{minimized:true})}/><button className="max" aria-label="最大化" onClick={()=>update(w.id,{x:20,y:45,width:innerWidth-40,height:innerHeight-140})}/><span>{w.title}</span></div>
       {builtins.some(b=>b.id===app.id)?<div className="window-body">
         {app.id==='knowledge'?<Knowledge api={api} apiPage={apiPage} apiRaw={apiRaw}/>:app.id==='work'?<Work api={api} apiPage={apiPage}/>:app.id==='dashboard'?<Dashboard apiPage={apiPage} openApp={id=>{const target=apps.find(a=>a.id===id);if(target)open(target)}}/>:<Settings api={api} preferences={preferences} applyPreferences={setPreferences} refreshApps={async()=>{await refreshApps()}} saveLayout={async()=>{
           await api('/users/me/layout',{method:'PUT',body:JSON.stringify({dock:dock.length?dock.filter(id=>apps.some(a=>a.id===id)):apps.map(a=>a.id),windows})});
         }}/>}</div>:<iframe onLoad={event=>{if(app.id==='xelibrary'&&app.origin==='https://library.xenon.lan')(event.currentTarget as HTMLIFrameElement).contentWindow?.postMessage({type:'auth.session_changed',access_token:token},app.origin)}} id={`frame-${w.id}`} title={w.title} src={new URL(app.entry,app.origin).href} sandbox={['allow-same-origin',...(app.sandbox?.allow_scripts===false?[]:['allow-scripts']),...(app.sandbox?.allow_forms===false?[]:['allow-forms']),...(app.sandbox?.allow_downloads?['allow-downloads']:[])].join(' ')} referrerPolicy="no-referrer"/>}
     </section>;
   })}
   {(preferences.widgets||[]).length>0&&<div className="widgets">{(preferences.widgets||[]).includes('clock')&&<ClockWidget timezone={preferences.timezone}/>}{(preferences.widgets||[]).includes('status')&&<StatusWidget apiPage={apiPage} onOpen={()=>{const app=apps.find(a=>a.id==='dashboard');if(app)open(app)}}/>}</div>}
   {!libraryView&&<nav className="dock" aria-label="应用程序"><button onClick={()=>setLaunchpad(!launchpad)} title="LaunchPad"><b>▦</b><span>LaunchPad</span></button>{[...apps].sort((a,b)=>{const ai=dock.indexOf(a.id),bi=dock.indexOf(b.id);return (ai<0?999:ai)-(bi<0?999:bi)}).map(app=><button key={app.id} onClick={()=>open(app)} title={app.title}><b>{app.icon}</b><span>{app.title}</span></button>)}{windows.filter(w=>w.minimized).map(w=><button key={w.id} onClick={()=>update(w.id,{minimized:false})}>{w.title} ↗</button>)}</nav>}</>}
   {notice&&<aside onClick={()=>setNotice('')}>{notice}</aside>}
 </main>
}
createRoot(document.getElementById('root')!).render(<Desktop/>);
