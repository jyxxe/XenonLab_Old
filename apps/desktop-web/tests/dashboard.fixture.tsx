// Isolated component fixture: synthetic API data, no production account or services.
import React from 'react';
import {createRoot} from 'react-dom/client';
import {Dashboard} from '../src/Dashboard';
import '../src/style.css';

let jobCount=0;
const apiPage=async(path:string)=>{
  await new Promise(resolve=>setTimeout(resolve,80));
  if(path.startsWith('/jobs'))return {
    data:Array.from({length:jobCount},(_,i)=>({job_id:`job-${i}`,table_id:'table-1',status:'queued',created_at:new Date().toISOString()})),
    meta:{counts:{queued:jobCount,running:0,completed:0,failed:0}},
  };
  if(path.startsWith('/work'))return {
    data:Array.from({length:50},(_,i)=>({id:`work-${i}`,repository:'xenon/XeonoLab',issue_id:i+1,title:`Harness fixture task ${i+1}`,status:i<27?'queued':i<32?'claimed':i<49?'completed':'blocked',holder:null,claim_active:false})),
    meta:{},
  };
  return {data:[{id:'table-1',name:'示例',path:'/示例'}],meta:{}};
};
const originalFetch=window.fetch;
window.fetch=(input,init)=>input==='/health/ready'
  ? Promise.resolve(new Response(JSON.stringify({service:'desktop-web fixture'}),{headers:{'Content-Type':'application/json'}}))
  : originalFetch(input,init);

window.addEventListener('dashboard-fixture',(event)=>{
  jobCount=(event as CustomEvent<{jobs:number}>).detail.jobs;
  document.querySelector<HTMLElement>('.dashboard-actions button')!.click();
});
createRoot(document.getElementById('root')!).render(
  <div className="window" id="dashboard-fixture" style={{left:8,top:8,width:1080,height:800,resize:'none'}}>
    <div className="bar"><button className="close"/><button className="min"/><button className="max"/><span>系统面板 · 本地候选组件验收</span></div>
    <div className="window-body"><Dashboard apiPage={apiPage} openApp={(id)=>{document.body.dataset.openedApp=id}}/></div>
  </div>,
);
