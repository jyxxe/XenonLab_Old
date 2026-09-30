#!/usr/bin/env node
// Run against the local Vite fixture; no credentials or production requests.
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import {spawn} from 'node:child_process';
import assert from 'node:assert/strict';

const fixtureUrl=process.argv[2]||'http://127.0.0.1:5179/tests/dashboard.html';
const out=path.resolve(process.argv[3]||'runtime/dashboard-acceptance');
assert.equal(new URL(fixtureUrl).hostname,'127.0.0.1','Use the isolated localhost fixture');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function until(fn,label){
  const deadline=Date.now()+15000;
  while(Date.now()<deadline){try{if(await fn())return}catch{}await sleep(100)}
  throw new Error(`Timed out: ${label}`);
}
const report={source:'local candidate Dashboard component with synthetic API fixtures',state:'running',checks:[],consoleErrors:[],pageExceptions:[]};
await fs.mkdir(out,{recursive:true,mode:0o700});
const temp=await fs.mkdtemp(path.join(os.tmpdir(),'xeonolab-dashboard-'));
const server=net.createServer();
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
const port=server.address().port;
await new Promise(resolve=>server.close(resolve));
const chrome=spawn('/usr/bin/google-chrome',[
  '--headless=new','--no-first-run','--no-default-browser-check','--window-size=1440,1000',
  `--user-data-dir=${temp}`,`--remote-debugging-port=${port}`,'--remote-debugging-address=127.0.0.1','about:blank',
],{stdio:'ignore'});
let socket;
try{
  await until(async()=>{
    assert.equal(chrome.exitCode,null,'Chrome exited before CDP became available');
    return (await fetch(`http://127.0.0.1:${port}/json/version`)).ok;
  },'Chrome DevTools');
  const target=await (await fetch(`http://127.0.0.1:${port}/json/new?about%3Ablank`,{method:'PUT'})).json();
  socket=new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true})});
  let id=0;
  const pending=new Map();
  socket.addEventListener('message',event=>{
    const message=JSON.parse(event.data);
    if(message.id){const p=pending.get(message.id);if(p){clearTimeout(p.timer);pending.delete(message.id);message.error?p.reject(Error(message.error.message)):p.resolve(message.result)}}
    if(message.method==='Runtime.exceptionThrown')report.pageExceptions.push(message.params.exceptionDetails.text);
    if(message.method==='Runtime.consoleAPICalled'&&message.params.type==='error')report.consoleErrors.push(message.params.args.map(arg=>arg.value||arg.description).join(' '));
  });
  const call=(method,params={})=>new Promise((resolve,reject)=>{
    const requestId=++id;
    const timer=setTimeout(()=>{pending.delete(requestId);reject(Error(`CDP timeout: ${method}`))},15000);
    pending.set(requestId,{resolve,reject,timer});socket.send(JSON.stringify({id:requestId,method,params}));
  });
  const evaluate=async expression=>{
    const result=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});
    if(result.exceptionDetails)throw Error(result.exceptionDetails.text);
    return result.result.value;
  };
  await call('Page.enable');await call('Runtime.enable');
  await call('Page.navigate',{url:fixtureUrl});
  await until(()=>evaluate("document.querySelector('.mac-badge')?.textContent==='最近 0 条' && document.querySelector('.status-line') && document.querySelectorAll('.job-list a').length===4"),'fixture API results');
  const measure=()=>evaluate(`(()=>{
    const grid=document.querySelector('.dashboard-grid');
    return {gap:parseFloat(getComputedStyle(grid).getPropertyValue('--dashboard-card-gap')),overflow:grid.scrollWidth-grid.clientWidth,
      cards:[...grid.children].map(c=>{const r=c.getBoundingClientRect();return {title:c.querySelector('h3').textContent,x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom,right:r.right}})};
  })()`);
  async function verify(label,columns){
    await sleep(250);
    const geometry=await measure();
    const cards=geometry.cards;
    assert.equal(cards.length,4);
    assert.ok(geometry.overflow<=1,`${label}: horizontal overflow ${geometry.overflow}px`);
    const xs=[...new Set(cards.map(c=>Math.round(c.x)))];
    assert.equal(xs.length,columns,`${label}: column count`);
    for(let i=0;i<cards.length;i++)for(let j=i+1;j<cards.length;j++){
      const a=cards[i],b=cards[j];
      assert.ok(Math.min(a.right,b.right)-Math.max(a.x,b.x)<=1||Math.min(a.bottom,b.bottom)-Math.max(a.y,b.y)<=1,`${label}: overlapping cards`);
    }
    for(const x of xs){
      const column=cards.filter(c=>Math.round(c.x)===x).sort((a,b)=>a.y-b.y);
      for(let i=1;i<column.length;i++){
        const gap=column[i].y-column[i-1].bottom;
        assert.ok(gap>=geometry.gap-0.1&&gap<geometry.gap+1.1,`${label}: unexpected gap ${gap}px`);
      }
    }
    if(columns===3){
      const first=cards.slice(0,3),service=cards[3];
      assert.ok(Math.abs(service.y-Math.min(...first.map(c=>c.bottom))-geometry.gap)<1.1,`${label}: service card must follow shortest card`);
    }
    report.checks.push({name:label,passed:true,...geometry});
    const screenshot=await call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
    await fs.writeFile(path.join(out,`${label}.png`),Buffer.from(screenshot.data,'base64'),{mode:0o600});
  }
  await verify('desktop-empty-index',3);
  for(const [width,label,columns] of [[740,'two-columns',2],[420,'single-column',1],[1080,'desktop-restored',3]]){
    await evaluate(`document.querySelector('#dashboard-fixture').style.width='${width}px'`);
    await verify(label,columns);
  }
  await evaluate("window.dispatchEvent(new CustomEvent('dashboard-fixture',{detail:{jobs:6}}))");
  await until(()=>evaluate("document.querySelectorAll('.mac-card:first-child .job-list li').length===6"),'dynamic job growth');
  await verify('desktop-index-grown',3);
  await evaluate("window.dispatchEvent(new CustomEvent('dashboard-fixture',{detail:{jobs:0}}))");
  await until(()=>evaluate("!document.querySelector('.mac-card:first-child .job-list')"),'dynamic job shrink');
  await verify('desktop-index-shrunk',3);
  await evaluate("document.documentElement.dataset.theme='light'");
  await verify('desktop-light',3);
  await evaluate("[...document.querySelectorAll('.mac-card button')].find(b=>b.textContent==='打开知识库').click()");
  assert.equal(await evaluate('document.body.dataset.openedApp'),'knowledge');
  assert.deepEqual(report.consoleErrors,[],'Browser console errors');
  assert.deepEqual(report.pageExceptions,[],'Browser exceptions');
  report.state='passed';
}catch(error){report.state='failed';report.error=String(error);process.exitCode=1}
finally{
  await fs.writeFile(path.join(out,'report.json'),JSON.stringify(report,null,2),{mode:0o600});
  socket?.close();chrome.kill('SIGTERM');
  await new Promise(resolve=>{if(chrome.exitCode!==null)resolve();else chrome.once('exit',resolve)});
  await fs.rm(temp,{recursive:true,force:true});
}
console.log(JSON.stringify({state:report.state,checks:report.checks.length,error:report.error,out}));
