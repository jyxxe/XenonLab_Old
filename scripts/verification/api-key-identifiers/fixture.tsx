import React,{useState} from 'react';import {createRoot} from 'react-dom/client';import {Accounts} from './src/Accounts';
import './src/design-system/tokens.css';import './src/design-system/primitives.css';import './src/style.css';
const owner='synthetic-owner',scopes=['desktop:read'];
const keys=[
 {id:'00000000-0000-4000-8000-00000000a111',owner,name:'轮换凭据',scopes,created:1790960000,revoked:0},
 {id:'00000000-0000-4000-8000-00000000b222',owner,name:'轮换凭据',scopes,created:1790961000,revoked:0},
 {id:'00000000-0000-4000-8000-00000000c333',owner,name:'轮换凭据',scopes,created:1790950000,revoked:1},
];
const state={calls:[] as any[],failRevoke:false};
async function api(path:string,init:RequestInit={}){
 const method=init.method||'GET';state.calls.push({path,method});
 if(path==='/users/me')return {subject:owner,kind:'human',scopes};
 if(path.startsWith('/auth/accounts?'))return [{id:owner,parent:null,username:'synthetic.login',name:'合成账号',enabled:true,inherit_all:false,grants:scopes,effective_scopes:scopes,kind:'human'}];
 if(path==='/auth/api-keys?owner='+owner)return structuredClone(keys);
 if(method==='DELETE'&&path.startsWith('/auth/api-keys/')){const key=keys.find(k=>'/auth/api-keys/'+k.id===path);if(!key||key.owner!==owner)throw Error('invalid synthetic owner');if(state.failRevoke)throw Error('synthetic revoke failed');key.revoked=1;return {revoked:true};}
 throw Error('Unexpected synthetic route '+path);
}
function Fixture(){const [version,setVersion]=useState(0);return <><button onClick={()=>setVersion(v=>v+1)}>测试：重新读取列表</button><Accounts key={version} api={api}/></>}
(window as any).keyFixture=state;createRoot(document.getElementById('root')!).render(<Fixture/>);
