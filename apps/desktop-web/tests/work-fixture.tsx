import React from 'react';
import {createRoot} from 'react-dom/client';
import {Work} from '../src/Work';

type FixtureItem = {
  id:string; repository:string; issue_id:number; title:string; status:string;
  holder:string|null; expires:number; epoch:number; claim_active:boolean;
};

const makeItem = (n:number):FixtureItem => ({
  id:`synthetic-${n}`, repository:'xenon-admin/XeonoLab', issue_id:50+n,
  title:`Synthetic Work item ${n}`, status:n===4?'completed':'open',
  holder:n===2?'fixture-user':n===3?'other-agent':null, expires:0, epoch:1,
  claim_active:n===2||n===3,
});

const firstPage = Array.from({length:100},(_,i)=>makeItem(i+1));
firstPage[98] = {...makeItem(99),repository:'xenon-admin/../XeonoLab'};
let refreshCount = 0;
let resolveDelayedPage:(value:{data:FixtureItem[];meta:{next_cursor:string|null}})=>void = ()=>{};
let delayedPageStarted = false;

const apiPage = async (path:string) => {
  const url = new URL(path,'http://fixture.local');
  const cursor = url.searchParams.get('cursor');
  if (!cursor) {
    refreshCount += 1;
    const data = firstPage.map(item=>({...item}));
    if(refreshCount>1)data[0].title='Synthetic Work item 1 refreshed';
    return {data,meta:{next_cursor:`page-${refreshCount}-next`}};
  }
  if(cursor==='page-1-next') {
    return {data:Array.from({length:100},(_,i)=>makeItem(i+95)),meta:{next_cursor:'page-2-next'}};
  }
  if(cursor==='page-2-next') {
    delayedPageStarted=true;
    return new Promise(resolve=>{resolveDelayedPage=resolve});
  }
  return {data:[],meta:{next_cursor:null}};
};

(window as any).workFixture={
  get refreshCount(){return refreshCount},
  get delayedPageStarted(){return delayedPageStarted},
  resolveDelayedPage(){resolveDelayedPage({data:[{...makeItem(999),id:'stale-page-row',title:'STALE PAGE RESPONSE'}],meta:{next_cursor:null}})},
};

const api=async()=>({subject:'fixture-user'});
createRoot(document.getElementById('root')!).render(<Work api={api} apiPage={apiPage}/>);
