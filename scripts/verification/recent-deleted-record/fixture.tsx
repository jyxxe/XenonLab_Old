import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/shell/shell.css';
import '../src/style.css';
import {APIRequestError} from '../src/api-error.js';
import {LibraryApp} from '../src/library/LibraryApp';

const token='round51-local-admin';
const calls:any[]=[];
async function page(path:string,init:RequestInit={}){
 const method=(init.method||'GET').toUpperCase();const headers=new Headers(init.headers||{});headers.set('Authorization','Bearer '+token);
 if(init.body&&!headers.has('Content-Type'))headers.set('Content-Type','application/json');calls.push({method,path,headers:Object.fromEntries(headers.entries()),body:typeof init.body==='string'?JSON.parse(init.body):undefined});
 const trace=calls[calls.length-1];
 const response=await fetch(path==='/users/me'?'/desktop-api/api/v1/users/me':'/api/v1'+path,{...init,headers});const payload=await response.json().catch(()=>({}));
 if(trace){trace.status=response.status;trace.responsePayload=payload;}
 if(!response.ok){const err=payload.error||{};throw new APIRequestError(err.message||response.statusText,response.status,err.code||'HTTP_ERROR',err.details||{});}return payload;
}
const api=async(path:string,init?:RequestInit)=>(await page(path,init)).data;
const apiPage=page;
const apiRaw=async(path:string,init:RequestInit={})=>{const headers=new Headers(init.headers||{});headers.set('Authorization','Bearer '+token);return fetch(path==='/users/me'?'/desktop-api/api/v1/users/me':'/api/v1'+path,{...init,headers});};
(window as any).r51Audit={calls};
function Harness(){
 const [authEpoch,setAuthEpoch]=useState(0);
 (window as any).r51Audit.refreshAuth=()=>setAuthEpoch(value=>value+1);
 return <LibraryApp api={api} apiPage={apiPage} apiRaw={apiRaw} authEpoch={authEpoch}/>;
}
createRoot(document.getElementById('root')!).render(<Harness/>);
