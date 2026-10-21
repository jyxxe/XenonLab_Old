import React from 'react';
import {createRoot} from 'react-dom/client';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/shell/shell.css';
import '../src/style.css';
import {APIRequestError} from '../src/api-error.js';
import {LibraryApp} from '../src/library/LibraryApp';
const readonly=new URLSearchParams(location.search).has('readonly');
const token=readonly?'tree-filter-readonly-token':'tree-filter-local-human';
const calls:any[]=[];
async function apiPage(path:string,init:RequestInit={}){
 const method=(init.method||'GET').toUpperCase();
 const headers=new Headers(init.headers||{});headers.set('Authorization',`Bearer ${token}`);
 if(init.body&&!(init.body instanceof FormData)&&!headers.has('Content-Type'))headers.set('Content-Type','application/json');
 if(path==='/users/me')return {data:{subject:'tree-filter-local-human',kind:'human',scopes:readonly?['xelibrary:read']:['xelibrary:read','xelibrary:write','xelibrary:admin']},meta:{}};
 const response=await fetch('/api/v1'+path,{...init,headers});const payload=await response.json().catch(()=>({}));
 const call={method,path,status:response.status,response:payload.data,error:payload.error};calls.push({...call, requestHeaders:Object.fromEntries(headers.entries()), body:init.body});
 if(!response.ok){const e=payload.error||{};throw new APIRequestError(e.message||response.statusText,response.status,e.code||'HTTP_ERROR',e.details||{});}
 return payload;
}
const api=async(path:string,init?:RequestInit)=>(await apiPage(path,init)).data;
const apiRaw=async(path:string,init:RequestInit={})=>{const headers=new Headers(init.headers||{});headers.set('Authorization',`Bearer ${token}`);return fetch('/api/v1'+path,{...init,headers});};
(window as any).treeAudit={calls};
createRoot(document.getElementById('root')!).render(<LibraryApp api={api} apiPage={apiPage} apiRaw={apiRaw}/>);
