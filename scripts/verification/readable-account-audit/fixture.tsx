import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {Accounts} from './src/Accounts';
import './src/design-system/tokens.css';
import './src/design-system/primitives.css';
import './src/style.css';
const scopes=['desktop:read','accounts:manage'];
const account=(id:string,username:string,name:string,parent:string|null)=>({id,username,name,parent,enabled:true,inherit_all:false,grants:scopes,effective_scopes:scopes,kind:'human'});
const state={calls:[] as {path:string;method:string}[],renamed:false,failAudit:false};
// B is an authorized human manager of A; their same names exercise exact-ID lookup.
// Unknown IDs below are read-response boundary fixtures, not successful mutation evidence.
const events=[
 {id:'00000000-0000-4000-8000-000000000001',actor:'synthetic-root',target:'synthetic-child-a',event:'account.updated',at:1790960039},
 {id:'00000000-0000-4000-8000-000000000002',actor:'synthetic-child-b',target:'synthetic-child-a',event:'api_key.revoked:synthetic-key',at:1790960029},
 {id:'00000000-0000-4000-8000-000000000003',actor:'synthetic-outside',target:'synthetic-child-a',event:'account.updated',at:1790960019},
 {id:'00000000-0000-4000-8000-000000000004',actor:'synthetic-root',target:'synthetic-missing',event:'account.updated',at:1790960009},
];
async function api(path:string,init?:RequestInit){
 const method=init?.method||'GET';state.calls.push({path,method});if(method!=='GET')throw Error('This acceptance only permits local synthetic GET');
 if(path==='/users/me')return {subject:'synthetic-root',kind:'human',scopes};
 if(path==='/auth/accounts/root-scope-candidates')return {candidate_scopes:scopes};
 if(path.startsWith('/auth/accounts?'))return [account('synthetic-root','root.login','根账号',null),account('synthetic-child-a','child.a',state.renamed?'更新后的账号':'同名账号','synthetic-child-b'),account('synthetic-child-b','child.b','同名账号','synthetic-root')];
 if(path.startsWith('/auth/api-keys?'))return [];
 if(path==='/auth/accounts/audit/events'){if(state.failAudit)throw Error('synthetic audit unavailable');const visible=new Set(['synthetic-root','synthetic-child-a','synthetic-child-b']);return structuredClone(events.filter(event=>visible.has(event.actor)||visible.has(event.target)))}
 throw Error('Unexpected local route '+path);
}
function Fixture(){const [version,setVersion]=useState(0);return <><button onClick={()=>{state.renamed=true;setVersion(v=>v+1)}}>测试：重新载入当前账号树</button><Accounts key={version} api={api}/></>}
(window as any).auditFixture=state;
createRoot(document.getElementById('root')!).render(<Fixture/>);
