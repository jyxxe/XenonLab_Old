import React from 'react';
import {createRoot} from 'react-dom/client';
import {Work} from '../src/Work';
import {Dashboard} from '../src/Dashboard';
import {WorkWidget} from '../src/Widgets';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/shell/shell.css';
import '../src/style.css';

// All component APIs are synthetic. Browser navigation to Forgejo is fulfilled
// locally by the test, so no account or production service is accessed.
const base = {repository:'xenon-admin/XeonoLab', status:'queued', holder:null, expires:0, epoch:1, claim_active:false};
const work = [
  {...base,id:'valid',issue_id:51,title:'Origin regression source Issue'},
  {...base,id:'invalid-repository',repository:'xenon-admin/../XeonoLab',issue_id:52,title:'Malformed repository'},
  {...base,id:'invalid-number',issue_id:0,title:'Invalid issue number'},
  {...base,id:'completed',issue_id:53,title:'Completed fixture item',status:'completed'},
];
const apiPage = async (path:string) => {
  if (path.startsWith('/work?')) return {data:work,meta:{next_cursor:null}};
  if (path.startsWith('/jobs?')) return {data:[],meta:{counts:{}}};
  if (path.startsWith('/tables?')) return {data:[],meta:{next_cursor:null}};
  throw new Error(`Unexpected synthetic API request: ${path}`);
};
const api = async (path:string) => {
  if (path === '/users/me') return {subject:'synthetic-link-reader'};
  throw new Error(`Unexpected synthetic API request: ${path}`);
};
window.fetch = () => Promise.resolve(new Response(JSON.stringify({service:'synthetic-desktop-health'}),{status:200,headers:{'Content-Type':'application/json'}}));
const view = new URLSearchParams(location.search).get('view');
createRoot(document.getElementById('root')!).render(
  view === 'widget' ? <div style={{width:360,margin:24}}><WorkWidget apiPage={apiPage} allowed onOpen={()=>{document.body.dataset.openedApp='work'}}/></div> :
  <div className="window" style={{left:24,top:24,width:1180,height:850}}><div className="window-body">
    {view === 'dashboard' ? <Dashboard apiPage={apiPage} openApp={id=>{document.body.dataset.openedApp=id}}/> : <Work api={api} apiPage={apiPage}/>}
  </div></div>,
);
