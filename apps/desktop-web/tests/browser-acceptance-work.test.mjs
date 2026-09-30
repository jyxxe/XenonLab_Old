import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';

const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const appRoot=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
async function port(){
  const server=net.createServer();
  await new Promise((resolve,reject)=>server.listen(0,'127.0.0.1',resolve).once('error',reject));
  const value=server.address().port;
  await new Promise(resolve=>server.close(resolve));
  return value;
}
async function waitFor(fn,label){
  const until=Date.now()+15000;
  while(Date.now()<until){try{const result=await fn();if(result)return result}catch{}await sleep(100)}
  throw new Error(`Timed out waiting for ${label}`);
}
function waitForExit(child,timeoutMs){
  if(child.exitCode!==null||child.signalCode!==null)return Promise.resolve(true);
  return new Promise(resolve=>{
    const onExit=()=>{clearTimeout(timer);resolve(true)};
    const timer=setTimeout(()=>{child.off('exit',onExit);resolve(false)},timeoutMs);
    child.once('exit',onExit);
  });
}
async function stopProcess(child){
  if(child.exitCode!==null||child.signalCode!==null)return;
  child.kill('SIGTERM');
  if(await waitForExit(child,3000))return;
  child.kill('SIGKILL');
  if(!await waitForExit(child,3000))throw new Error('Chrome did not exit after SIGKILL');
}
async function removeTree(directory){
  for(let attempt=0;;attempt++){
    try{await fs.rm(directory,{recursive:true,force:true});return}
    catch(error){if(!['ENOTEMPTY','EBUSY'].includes(error.code)||attempt>=5)throw error;await sleep(100*(attempt+1))}
  }
}

class CDP{
  constructor(socket){this.socket=socket;this.id=0;this.pending=new Map();socket.addEventListener('message',event=>{
    const message=JSON.parse(event.data);if(!message.id)return;
    const pending=this.pending.get(message.id);if(!pending)return;this.pending.delete(message.id);
    if(message.error)pending.reject(new Error(message.error.message));else pending.resolve(message.result);
  })}
  static async connect(url){
    const socket=new WebSocket(url);
    await new Promise((resolve,reject)=>{socket.addEventListener('open',resolve,{once:true});socket.addEventListener('error',reject,{once:true})});
    return new CDP(socket);
  }
  call(method,params={}){
    const id=++this.id;this.socket.send(JSON.stringify({id,method,params}));
    return new Promise((resolve,reject)=>this.pending.set(id,{resolve,reject}));
  }
  async evaluate(expression){
    const response=await this.call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});
    if(response.exceptionDetails)throw new Error(response.exceptionDetails.text||'Browser evaluation failed');
    return response.result?.value;
  }
}

test('Work browser acceptance uses synthetic cursor pages and discards stale results',async t=>{
  const chromePath=process.env.CHROME_BIN||(['/usr/bin/google-chrome','/usr/bin/chromium','/usr/bin/chromium-browser'].find(file=>existsSync(file)));
  if(!chromePath){t.skip('Chrome/Chromium unavailable; run with CHROME_BIN=/path/to/chrome');return}
  const portNumber=await port();
  const server=await createServer({root:appRoot,server:{host:'127.0.0.1',port:portNumber,strictPort:true,watch:null}});
  await server.listen();
  const tempDir=await fs.mkdtemp(path.join(os.tmpdir(),'xelonolab-work-acceptance-'));
  const chrome=spawn(chromePath,['--headless=new','--no-first-run','--no-default-browser-check',`--user-data-dir=${path.join(tempDir,'profile')}`,'--remote-debugging-port=0','--remote-debugging-address=127.0.0.1','--remote-allow-origins=*','about:blank'],{stdio:'ignore'});
  let cdp;
  try{
    const endpoint=await waitFor(async()=>{
      // Chrome's ephemeral debugging port is recorded in DevToolsActivePort.
      const raw=await fs.readFile(path.join(tempDir,'profile','DevToolsActivePort'),'utf8').catch(()=>null);
      return raw?{port:Number(raw.split(/\r?\n/)[0])}:null;
    },'Chrome DevTools endpoint');
    const version=await (await fetch(`http://127.0.0.1:${endpoint.port}/json/version`)).json();
    const target=await (await fetch(`http://127.0.0.1:${endpoint.port}/json/new?about%3Ablank`,{method:'PUT'})).json();
    cdp=await CDP.connect(target.webSocketDebuggerUrl);
    await cdp.call('Page.enable');await cdp.call('Runtime.enable');
    await cdp.call('Page.navigate',{url:`http://127.0.0.1:${portNumber}/tests/work-fixture.html`});
    await waitFor(()=>cdp.evaluate("document.querySelectorAll('tbody tr').length===100"),'first synthetic page');
    const initial=await cdp.evaluate(`(() => ({rows:document.querySelectorAll('tbody tr').length,loaded:document.querySelector('small')?.textContent,href:document.querySelector('tbody a')?.href,claim:[...document.querySelectorAll('tbody tr')][0]?.innerText.includes('领取'),malformedLink:!![...document.querySelectorAll('tbody tr')][98]?.querySelector('a')}))()`);
    assert.equal(initial.rows,100);assert.match(initial.loaded,/当前已加载的 100 项任务/);
    assert.equal(initial.href,'https://xenon.jyxxe.com/dev/xenon-admin/XeonoLab/issues/51');
    assert.equal(initial.claim,true);assert.equal(initial.malformedLink,false);
    const owned=await cdp.evaluate("[...document.querySelectorAll('tbody tr')][1].innerText");
    assert.match(owned,/释放/);assert.match(owned,/标记完成/);
    const foreignActions=await cdp.evaluate("[...document.querySelectorAll('tbody tr')][2].cells[3].querySelectorAll('button').length");
    assert.equal(foreignActions,0);
    const completedActions=await cdp.evaluate("[...document.querySelectorAll('tbody tr')][3].cells[3].querySelectorAll('button').length");
    assert.equal(completedActions,0);
    await cdp.evaluate("[...document.querySelectorAll('button')].find(button=>button.textContent.includes('加载更多'))?.click()");
    await waitFor(()=>cdp.evaluate("document.querySelectorAll('tbody tr').length===194"),'second page and duplicate filtering');
    const twoPages=await cdp.evaluate("({rows:document.querySelectorAll('tbody tr').length,ids:[...document.querySelectorAll('tbody tr')].map(row=>row.dataset.workId),loaded:document.querySelector('small')?.textContent})");
    assert.equal(twoPages.rows,194,'page 2 adds only unique IDs despite six repeated IDs');
    assert.equal(new Set(twoPages.ids).size,twoPages.rows,'every rendered Work item ID is unique');
    assert.match(twoPages.loaded,/当前已加载的 194 项任务/);
    await cdp.evaluate("[...document.querySelectorAll('button')].find(button=>button.textContent.includes('加载更多'))?.click()");
    await waitFor(()=>cdp.evaluate('window.workFixture.delayedPageRequests===1'),'delayed page request');
    await cdp.evaluate("[...document.querySelectorAll('button')].find(button=>button.textContent==='刷新队列')?.click()");
    await waitFor(()=>cdp.evaluate("window.workFixture.refreshCount===2 && document.querySelectorAll('tbody tr').length===100"),'refresh to replace list');
    await cdp.evaluate('window.workFixture.resolveDelayedPage()');
    await sleep(150);
    const afterRace=await cdp.evaluate("({rows:document.querySelectorAll('tbody tr').length,stale:document.body.innerText.includes('STALE PAGE RESPONSE'),refreshed:document.body.innerText.includes('Synthetic Work item 1 refreshed'),loaded:document.querySelector('small')?.textContent})");
    assert.equal(afterRace.rows,100);assert.equal(afterRace.stale,false);assert.equal(afterRace.refreshed,true);
    assert.match(afterRace.loaded,/当前已加载的 100 项任务/);
    await cdp.evaluate("(() => {const input=document.querySelector('input[aria-label=\"筛选已加载任务\"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Synthetic Work item 150');input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}))})()");
    await sleep(80);
    const filtered=await cdp.evaluate("({matches:document.querySelectorAll('tbody tr').length,empty:document.body.innerText.includes('没有匹配的已加载任务'),loaded:document.querySelector('small')?.textContent})");
    assert.equal(filtered.matches,0);assert.equal(filtered.empty,true);assert.match(filtered.loaded,/当前已加载的 100 项任务/);
    await cdp.evaluate("[...document.querySelectorAll('button')].find(button=>button.textContent.includes('加载更多'))?.click()");
    await waitFor(()=>cdp.evaluate('window.workFixture.delayedPageRequests===2'),'final page request');
    await cdp.evaluate('window.workFixture.resolveDelayedPage([])');
    await waitFor(()=>cdp.evaluate("document.body.innerText.includes('已到任务列表末尾')"),'explicit cursor end');
    const ended=await cdp.evaluate("({more:[...document.querySelectorAll('button')].some(button=>button.textContent.includes('加载更多')),end:document.body.innerText.includes('已到任务列表末尾')})");
    assert.equal(ended.more,false);assert.equal(ended.end,true);
    await cdp.evaluate("(() => {const input=document.querySelector('input[aria-label=\"筛选已加载任务\"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'');input.dispatchEvent(new Event('input',{bubbles:true}));input.dispatchEvent(new Event('change',{bubbles:true}));window.workFixture.cursorMode='loop';[...document.querySelectorAll('button')].find(button=>button.textContent==='刷新队列')?.click()})()");
    await waitFor(()=>cdp.evaluate('window.workFixture.refreshCount===3 && document.querySelectorAll("tbody tr").length===100 && [...document.querySelectorAll("button")].some(button=>button.textContent.includes("加载更多任务"))'),'loop fixture refresh');
    await cdp.evaluate("[...document.querySelectorAll('button')].find(button=>button.textContent.includes('加载更多'))?.click()");
    await waitFor(()=>cdp.evaluate("document.body.innerText.includes('游标重复')"),'repeated cursor guard');
    const loop=await cdp.evaluate("({more:[...document.querySelectorAll('button')].some(button=>button.textContent.includes('加载更多')),message:document.body.innerText.includes('游标重复')})");
    assert.equal(loop.more,false);assert.equal(loop.message,true);
    assert.ok(version.Browser,'connected to a local Chrome DevTools browser');
  }finally{
    cdp?.socket.close();
    try{await stopProcess(chrome)}finally{
      try{await server.close()}finally{await removeTree(tempDir)}
    }
  }
});
