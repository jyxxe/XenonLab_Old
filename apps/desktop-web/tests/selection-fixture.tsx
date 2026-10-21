import React from 'react';
import {createRoot} from 'react-dom/client';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/style.css';
import {LibraryApp} from '../src/library/LibraryApp';
import {parseAPIJSON} from '../src/library/json.js';
import {APIRequestError} from '../src/api-error.js';

// Identity is synthetic; rows/schema/ACL requests use the real temporary
// loopback XeLibrary API. No desktop auth bridge or production account is used.
const token='issue236-synthetic-token';
const request=(path:string,init:RequestInit={})=>fetch(`/api/v1${path}`,{
  ...init, headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json',...Object.fromEntries(new Headers(init.headers))},
});
async function apiPage(path:string,init?:RequestInit){
  const response=await request(path,init);
  const body=parseAPIJSON(path,await response.text());
  if(!response.ok)throw new APIRequestError(body?.error?.message || `HTTP ${response.status}`,response.status,body?.error?.code || 'ERROR');
  return {data:body.data,meta:body.meta||{}};
}
async function api(path:string,init?:RequestInit){
  if(path==='/users/me')return {subject:'issue236-synthetic-user',scopes:['xelibrary:read','xelibrary:write','xelibrary:query','xelibrary:attach']};
  return (await apiPage(path,init)).data;
}
createRoot(document.getElementById('root')!).render(<LibraryApp api={api} apiPage={apiPage} apiRaw={request} apps={[]} openApp={()=>{}}/>);
