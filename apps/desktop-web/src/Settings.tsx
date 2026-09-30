import React,{useState} from 'react';
import {Accounts} from './Accounts';
import {Permissions} from './Permissions';
import {AppRegistry} from './AppRegistry';
import {DesktopPreferences,Preferences} from './DesktopPreferences';
type API=(path:string,init?:RequestInit)=>Promise<any>;
export function Settings({api,saveLayout,preferences,applyPreferences,refreshApps}:{api:API;saveLayout:()=>Promise<void>;preferences:Preferences;applyPreferences:(p:Preferences)=>void;refreshApps:()=>Promise<void>}){
 const [tab,setTab]=useState('accounts');
 return <div className="settings-panel"><nav className="settings-tabs" aria-label="设置分类">{[['accounts','账号与 API key'],['permissions','知识库权限'],['apps','应用管理'],['desktop','外观与桌面']].map(([key,title])=><button key={key} aria-pressed={tab===key} onClick={()=>setTab(key)}>{title}</button>)}</nav>{tab==='accounts'?<Accounts api={api}/>:tab==='permissions'?<Permissions api={api}/>:tab==='apps'?<AppRegistry api={api} changed={refreshApps}/>:<DesktopPreferences api={api} preferences={preferences} apply={applyPreferences} saveLayout={saveLayout}/>}</div>
}
