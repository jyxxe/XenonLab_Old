import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/shell/shell.css';
import '../src/style.css';
import {APIRequestError} from '../src/api-error.js';
import {LibraryApp} from '../src/library/LibraryApp';
const readonly=new URLSearchParams(location.search).has('readonly');
const state={token:'resume-local-human',epoch:0};
const identity={subject:'resume-local-human',kind:'human',scopes:['xelibrary:read','xelibrary:write','xelibrary:attach']};
const calls:any[]=[];
async function apiPage(path:string,init:RequestInit={}){
 const method=(init.method||'GET').toUpperCase();
 const headers=new Headers(init.headers||{});headers.set('Authorization',`Bearer ${state.token}`);
 if(init.body&&!(init.body instanceof FormData)&&!headers.has('Content-Type'))headers.set('Content-Type','application/json');
 if(path==='/users/me')return {data:{...identity,subject:state.token},meta:{}};
 const response=await fetch('/api/v1'+path,{...init,headers});const payload=await response.json().catch(()=>({}));
 const call={method,path,status:response.status,response:payload.data,error:payload.error};calls.push(call);
 if(!response.ok){const e=payload.error||{};throw new APIRequestError(e.message||response.statusText,response.status,e.code||'HTTP_ERROR',e.details||{});}
 return payload;
}
const api=async(path:string,init?:RequestInit)=>(await apiPage(path,init)).data;
const apiRaw=async(path:string,init:RequestInit={})=>{const headers=new Headers(init.headers||{});headers.set('Authorization',`Bearer ${state.token}`);const response=await fetch('/api/v1'+path,{...init,headers});calls.push({method:init.method||'GET',path,status:response.status});if(!response.ok){const payload=await response.json();throw new APIRequestError(payload.error?.message||'request failed',response.status,payload.error?.code||'HTTP_ERROR');}return response;};
(window as any).resumeAudit={calls,state};
function Fixture(){const [epoch,setEpoch]=useState(0);return <><button onClick={()=>{state.token='resume-other-human';state.epoch++;setEpoch(state.epoch)}}>本地切换合成身份</button><LibraryApp api={api} apiPage={apiPage} apiRaw={apiRaw} authEpoch={epoch}/></>}
createRoot(document.getElementById('root')!).render(<Fixture/>);
