import React,{useState} from 'react';
import {Accounts} from './Accounts';
import {Permissions} from './Permissions';
type API=(path:string,init?:RequestInit)=>Promise<any>;
export function Settings({api,saveLayout}:{api:API;saveLayout:()=>Promise<void>}){
 const [tab,setTab]=useState('accounts'),[message,setMessage]=useState('');
 return <div className="settings-panel"><nav className="settings-tabs" aria-label="设置分类"><button aria-pressed={tab==='accounts'} onClick={()=>setTab('accounts')}>账号与 API key</button><button aria-pressed={tab==='permissions'} onClick={()=>setTab('permissions')}>知识库权限</button><button aria-pressed={tab==='desktop'} onClick={()=>setTab('desktop')}>桌面</button></nav>{tab==='accounts'?<Accounts api={api}/>:tab==='permissions'?<Permissions api={api}/>:<section className="query"><h2>桌面布局</h2><button onClick={async()=>{try{await saveLayout();setMessage('窗口布局已保存')}catch(e){setMessage(String(e))}}}>保存当前窗口布局</button><p role="status">{message}</p></section>}</div>
}
