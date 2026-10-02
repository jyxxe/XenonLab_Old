import React,{useMemo,useState} from 'react';import {createRoot} from 'react-dom/client';
import {ToastProvider} from 'SOURCE/src/design-system/primitives';import {DesktopShell} from 'SOURCE/src/shell/DesktopShell';import {defaultPreferences} from 'SOURCE/src/DesktopPreferences';
import 'SOURCE/src/design-system/tokens.css';import 'SOURCE/src/design-system/primitives.css';import 'SOURCE/src/shell/shell.css';import 'SOURCE/src/style.css';
const values=new Map<string,any>();const assets=new Map<string,Uint8Array>();
const stored=(o:string)=>values.get(o)||{...defaultPreferences,theme:'dark',wallpaper:'custom',wallpaper_mode:'gradient',widgets:[]};
const control={hold:false,requests:[] as any[],calls:[] as any[],holdTimers:false,timers:new Map<number,()=>void>(),remount:()=>{},switchOwner:()=>{}};
const nativeSet=window.setTimeout.bind(window),nativeClear=window.clearTimeout.bind(window);
window.setTimeout=((fn:TimerHandler,delay?:number,...args:any[])=>{let handle=0;handle=nativeSet(()=>{if(control.holdTimers&&delay===350)control.timers.set(handle,()=>{if(typeof fn==='function')fn(...args)});else if(typeof fn==='function')fn(...args);},delay);return handle;}) as typeof window.setTimeout;
window.clearTimeout=(id?:number)=>{control.timers.delete(id!);nativeClear(id)};
async function api(owner:string,path:string,init:RequestInit={}){
 const method=init.method||'GET',body=typeof init.body==='string'?JSON.parse(init.body):undefined;control.calls.push({owner,path,method,body});
 if(path==='/users/me')return {subject:owner,kind:'human',scopes:['desktop:read','desktop:admin']};if(path==='/apps')return [];
 if(path==='/users/me/preferences'){
  if(method==='PATCH'){
   const record={owner,body,phase:'pending',release:(_ok:boolean)=>{}};control.requests.push(record);
   if(control.hold)await new Promise<void>((resolve,reject)=>{record.release=(ok:boolean)=>{record.phase=ok?'resolved':'rejected';ok?resolve():reject(Error('synthetic save failed'));}});
   if(body.wallpaper==='custom'&&body.wallpaper_mode==='image'&&!assets.has(owner))throw Error('synthetic WALLPAPER_NOT_FOUND');
   values.set(owner,{...stored(owner),...body});record.phase='settled';
  }
  return {...stored(owner),custom_wallpaper_available:assets.has(owner)};
 }
 if(path==='/users/me/layout')return {dock:['settings'],dock_initialized:true,windows:[]};if(path==='/embed/models')return [];if(path.startsWith('/work'))return [];
 throw Error('Unexpected synthetic route '+path);
}
async function raw(owner:string,path:string,init:RequestInit={}){
 if(path==='/users/me/wallpaper'){
  const method=init.method||'GET';control.calls.push({owner,path,method});
  if(method==='PUT'){assets.set(owner,Uint8Array.from(atob('UklGRjYAAABXRUJQVlA4ICoAAACQAQCdASoMAAkAAUAmJaACdLoAA5gA/vfEX/8bx4FeS7K/+SUKH/2wAAA='),c=>c.charCodeAt(0)));return new Response(JSON.stringify({data:{wallpaper:'custom',media_type:'image/webp',size:assets.get(owner)!.length}}));}
  if(method==='DELETE'){assets.delete(owner);values.set(owner,{...stored(owner),wallpaper:'aurora'});return new Response(JSON.stringify({data:{wallpaper:'aurora'}}));}
  if(!assets.has(owner))throw Error('synthetic WALLPAPER_NOT_FOUND');return new Response(assets.get(owner),{headers:{'Content-Type':'image/webp','Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'}});
 }
 return new Response(JSON.stringify({data:await api(owner,path,init)}));
}
function Fixture(){const [owner,setOwner]=useState('synthetic-a'),[version,setVersion]=useState(0);control.remount=()=>setVersion(v=>v+1);control.switchOwner=()=>setOwner(o=>o==='synthetic-a'?'synthetic-b':'synthetic-a');const bound=useMemo(()=>((p:string,i?:RequestInit)=>api(owner,p,i)),[owner]),boundRaw=useMemo(()=>((p:string,i?:RequestInit)=>raw(owner,p,i)),[owner]);return <ToastProvider><DesktopShell key={version} api={bound} apiPage={async(p,i)=>({data:await bound(p,i),meta:{}})} apiRaw={boundRaw} onLogout={async()=>setVersion(v=>v+1)}/></ToastProvider>}
const originalFetch=window.fetch.bind(window);window.fetch=(i,o)=>String(i)==='/health/ready'?Promise.resolve(new Response('{}')):originalFetch(i,o);
(window as any).combinationFixture=control;(window as any).readSyntheticPreferences=(o:string)=>({...stored(o),custom_wallpaper_available:assets.has(o)});createRoot(document.getElementById('root')!).render(<Fixture/>);
