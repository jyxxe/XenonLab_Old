import React from 'react';
import {createRoot} from 'react-dom/client';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/shell/shell.css';
import '../src/style.css';
import {ToastProvider} from '../src/design-system/primitives';
import {IFrameApp} from '../src/shell/IFrameApp';
import {DesktopShell} from '../src/shell/DesktopShell';
import {apiErrorFromResponse} from '../src/api-error.js';

(globalThis as any).__XEONOLAB_IFRAME_TIMEOUT__ = 400;
const token='round31-local-synthetic-token';
const apiRaw=async(path:string,init:RequestInit={})=>{
 const headers=new Headers(init.headers);headers.set('Authorization',`Bearer ${token}`);
 if(init.body&&typeof init.body==='string'&&!headers.has('Content-Type'))headers.set('Content-Type','application/json');
 return fetch(`/api/v1${path}`,{...init,headers});
};
const apiPage=async(path:string,init:RequestInit={})=>{
 const response=await apiRaw(path,init);let payload:any=null;
 try{payload=await response.json()}catch{}
 if(!response.ok)throw apiErrorFromResponse(response,payload);
 return payload;
};
const api=async(path:string,init:RequestInit={})=>(await apiPage(path,init)).data;
function Probe() {
 const params=new URLSearchParams(location.search);
 const [origin,setOrigin]=React.useState(params.get('a')!);
 const [entry,setEntry]=React.useState('/ready');
 const [theme,setTheme]=React.useState('light');
 const app={id:'probe',title:'Probe',icon:'x',kind:'iframe' as const,origin,entry,defaultSize:[800,600] as [number,number],minSize:[200,200] as [number,number],singleton:true,sandbox:{allow_scripts:true,allow_forms:true,allow_downloads:false}};
 return <><button onClick={()=>{setOrigin(params.get('b')!);setEntry('/')}}>Navigate B</button><button onClick={()=>setEntry('/hang')}>Hang</button><button onClick={()=>setEntry('/ready')}>Ready</button><button onClick={()=>setTheme('dark')}>Theme</button><IFrameApp app={app} windowId="probe" theme={theme} active={true} intent={{id:'draft-1',action:'askAI',prompt:'Unsent local test draft'}} /></>;
}
createRoot(document.getElementById('root')!).render(<ToastProvider>{new URLSearchParams(location.search).has('probe')?<Probe/>:<DesktopShell api={api as any} apiPage={apiPage as any} apiRaw={apiRaw as any} onLogout={async()=>{}} />}</ToastProvider>);
