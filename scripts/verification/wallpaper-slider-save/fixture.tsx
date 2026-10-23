import React, {useMemo,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {ToastProvider} from './src/design-system/primitives';
import {DesktopShell} from './src/shell/DesktopShell';
import {defaultPreferences} from './src/DesktopPreferences';
import './src/design-system/tokens.css';import './src/design-system/primitives.css';import './src/shell/shell.css';import './src/style.css';
const control={fail:false,failNext:false,hold:false,pending:[] as (()=>void)[],started:0,settled:0,calls:[] as any[],switchOwner:()=>{},remount:()=>{}};
const stored=(owner:string)=>{const s=sessionStorage.getItem('slider:'+owner);return s?JSON.parse(s):{...defaultPreferences,theme:'dark',wallpaper:'custom',wallpaper_mode:'gradient',widgets:[]}};
async function api(owner:string,path:string,init:RequestInit={}){
 const method=init.method||'GET',body=typeof init.body==='string'?JSON.parse(init.body):undefined;control.calls.push({owner,path,method,body,keepalive:init.keepalive===true});
 if(path==='/users/me')return {subject:owner,kind:'human',scopes:['desktop:read','desktop:admin']};
 if(path==='/apps')return [];
 if(path==='/users/me/preferences'){
  if(method==='PATCH'){control.started++;if(control.hold)await new Promise<void>(r=>control.pending.push(r));control.settled++;if(control.fail||control.failNext){control.failNext=false;throw Error('synthetic save failed');}const next={...stored(owner),...body};sessionStorage.setItem('slider:'+owner,JSON.stringify(next));return next;}
  return stored(owner);
 }
 if(path==='/users/me/layout')return {dock:['settings'],dock_initialized:true,windows:[]};
 if(path==='/embed/models')return [];
 if(path.startsWith('/work'))return [];
 throw Error('Unexpected synthetic route '+path);
}
function Fixture(){const [owner,setOwner]=useState('synthetic-a'),[version,setVersion]=useState(0);control.switchOwner=()=>setOwner(o=>o==='synthetic-a'?'synthetic-b':'synthetic-a');control.remount=()=>setVersion(v=>v+1);
 const bound=useMemo(()=>((p:string,i?:RequestInit)=>api(owner,p,i)),[owner]);const raw=useMemo(()=>async(p:string,i?:RequestInit)=>new Response(JSON.stringify({data:await bound(p,i)}),{headers:{'Content-Type':'application/json'}}),[bound]);
 return <ToastProvider><DesktopShell key={version} api={bound} apiPage={async(p,i)=>({data:await bound(p,i),meta:{}})} apiRaw={raw} onLogout={async()=>setVersion(v=>v+1)}/></ToastProvider>;
}
const fetchOriginal=window.fetch.bind(window);window.fetch=(input,init)=>String(input)==='/health/ready'?Promise.resolve(new Response('{}')):fetchOriginal(input,init);
(window as any).sliderFixture=control;createRoot(document.getElementById('root')!).render(<Fixture/>);
