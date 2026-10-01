import React from 'react';
import {createRoot} from 'react-dom/client';
import {Work} from '../src/Work';

type FixtureItem = {
  id:string; repository:string; issue_id:number; title:string; status:string;
  holder:string|null; expires:number; epoch:number; claim_active:boolean;
};

const makeItem = (n:number):FixtureItem => ({
  id:`synthetic-${n}`, repository:'xenon-admin/XeonoLab', issue_id:50+n,
  title:`Synthetic Work item ${n}`, status:n===1?'queued':n===2||n===3||n===5?'claimed':n===4?'completed':n===6?'blocked':'queued',
  holder:n===2?'fixture-user':n===3?'other-agent':n===5?'expired-agent':null, expires:0, epoch:1,
  claim_active:n===2||n===3,
});

const firstPage = Array.from({length:100},(_,i)=>makeItem(i+1));
firstPage[98] = {...makeItem(99),repository:'xenon-admin/../XeonoLab'};
let refreshCount = 0;
let cursorMode:'normal'|'loop' = 'normal';
const claimRequests:{path:string;body:any}[]=[];
let resolveDelayedPage:(value:{data:FixtureItem[];meta:{next_cursor:string|null}})=>void = ()=>{};
let delayedPageRequests = 0;

const apiPage = async (path:string) => {
  const url = new URL(path,'http://fixture.local');
  const cursor = url.searchParams.get('cursor');
  if (!cursor) {
    refreshCount += 1;
    const data = firstPage.map(item=>({...item}));
    if(refreshCount>1)data[0].title='Synthetic Work item 1 refreshed';
    return {data,meta:{next_cursor:cursorMode==='loop'?'loop-cursor':`page-${refreshCount}-next`}};
  }
  if(cursor==='loop-cursor')return {data:[makeItem(1000)],meta:{next_cursor:'loop-cursor'}};
  if(cursor==='page-1-next') {
    return {data:Array.from({length:100},(_,i)=>makeItem(i+95)),meta:{next_cursor:'page-2-next'}};
  }
  if(cursor==='page-2-next') {
    delayedPageRequests+=1;
    return new Promise(resolve=>{resolveDelayedPage=resolve});
  }
  return {data:[],meta:{next_cursor:null}};
};

(window as any).workFixture={
  get refreshCount(){return refreshCount},
  get delayedPageRequests(){return delayedPageRequests},
  claimRequests,
  set cursorMode(value:'normal'|'loop'){cursorMode=value},
  resolveDelayedPage(data:FixtureItem[]=[{...makeItem(999),id:'stale-page-row',title:'STALE PAGE RESPONSE'}]){resolveDelayedPage({data,meta:{next_cursor:null}})},
};

const api=async(path:string,init:RequestInit={})=>{
  if(path==='/users/me')return {subject:'fixture-user'};
  if(path.endsWith('/claim')){
    claimRequests.push({path,body:typeof init.body==='string'?JSON.parse(init.body):null});
    const id=path.split('/').at(-2);
    const item=firstPage.find(row=>row.id===id);
    if(item){item.holder='fixture-user';item.claim_active=true}
    return {};
  }
  return {};
};
createRoot(document.getElementById('root')!).render(<Work api={api} apiPage={apiPage}/>);
