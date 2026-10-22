import React from 'react';
import {createRoot} from 'react-dom/client';
import '../../../apps/desktop-web/src/design-system/tokens.css';
import '../../../apps/desktop-web/src/design-system/primitives.css';
import '../../../apps/desktop-web/src/style.css';
import {Accounts} from '../../../apps/desktop-web/src/Accounts';
const scopes=['accounts:manage','desktop:admin','desktop:read','xelibrary:read','xelibrary:query'];
const rows:any[]=[{id:'synthetic-parent',parent:null,username:'operator',name:'Synthetic parent',enabled:true,inherit_all:false,grants:[...scopes],effective_scopes:[...scopes],kind:'human'}];
const keys:any[]=[{id:'synthetic-parent-key',owner:'synthetic-parent',name:'parent-only-key',scopes:['desktop:read'],revoked:false}];
const control={failCreate:false,failRefresh:false,holdParentKeys:false,parentKeysStarted:0,parentKeysSettled:0,pending:[] as Array<()=>void>,actorScopes:[...scopes],shrinkActorAfterCreate:true,createValidatedScopes:[] as string[],calls:[] as any[]};
(window as any).accountContextFixture={rows,control};
async function api(path:string,init:RequestInit={}){
 const method=init.method||'GET',body=init.body?JSON.parse(String(init.body)):null;
 // Do not retain password or token fields in test call evidence.
 control.calls.push({path,method,body:body?{...body,password:undefined}:null});
 if(path==='/users/me')return{subject:'synthetic-parent',kind:'human',scopes:control.actorScopes};
 if(path==='/auth/accounts/root-scope-candidates')return{candidate_scopes:[]};
 if(path.startsWith('/auth/accounts?')){if(control.failRefresh)throw Error('synthetic account refresh failed');return rows.map(r=>({...r}));}
 if(path.startsWith('/auth/api-keys?owner=')){
  const owner=decodeURIComponent(path.split('owner=')[1]);const result=keys.filter(k=>k.owner===owner).map(k=>({...k}));
  if(owner==='synthetic-parent'&&control.holdParentKeys){control.parentKeysStarted++;await new Promise<void>(r=>control.pending.push(r));control.parentKeysSettled++;}
  return result;
 }
 if(path==='/auth/accounts'&&method==='POST'){
  const parent=rows.find(r=>r.id===body.parent_id);
  const grantable=parent.effective_scopes.filter((s:string)=>control.actorScopes.includes(s));
  if(body.grants.some((s:string)=>!grantable.includes(s)))throw Error('synthetic create rejected outside actor/parent intersection');
  if(control.failCreate)throw Error('synthetic create rejected');
  control.createValidatedScopes=[...body.grants];
  const account={id:`synthetic-child-${rows.length}`,parent:body.parent_id,username:body.username,name:body.name,enabled:true,inherit_all:body.inherit_all,grants:[...body.grants],effective_scopes:body.inherit_all?[...rows.find(r=>r.id===body.parent_id).effective_scopes]:[...body.grants],kind:'human'};rows.push(account);
  // Explicit identity-refresh scenario: creation used the full actor scopes;
  // the subsequent /users/me reload observes reduced request scopes.
  if(control.shrinkActorAfterCreate){control.actorScopes=control.actorScopes.filter(s=>s!=='xelibrary:query');control.shrinkActorAfterCreate=false;}
  return{...account};
 }
 if(path.startsWith('/auth/accounts/')&&method==='PATCH'){const row=rows.find(r=>r.id===path.split('/').at(-1));Object.assign(row,body);return{...row};}
 if(path==='/auth/api-keys'&&method==='POST'){const key={id:'synthetic-issued-key',owner:body.owner,name:body.name,scopes:body.scopes,revoked:false};keys.push(key);return{...key,token:'LOCAL-TEST-PLACEHOLDER'};}
 throw Error('unexpected synthetic route '+path);
}
createRoot(document.getElementById('root')!).render(<Accounts api={api}/>);
