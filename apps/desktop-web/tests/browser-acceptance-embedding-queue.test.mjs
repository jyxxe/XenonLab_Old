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

test('Embedding queue acceptance renders synthetic queue, history pages and refresh',async t=>{
  const chromePath=process.env.CHROME_BIN||(['/usr/bin/google-chrome','/usr/bin/chromium','/usr/bin/chromium-browser'].find(file=>existsSync(file)));
  if(!chromePath){t.skip('Chrome/Chromium unavailable; run with CHROME_BIN=/path/to/chrome');return}
  const portNumber=await port();
  const server=await createServer({root:appRoot,server:{host:'127.0.0.1',port:portNumber,strictPort:true,watch:null}});
  await server.listen();
  const tempDir=await fs.mkdtemp(path.join(os.tmpdir(),'xeonolab-queue-acceptance-'));
  // CI job containers run as root, where Chrome needs --no-sandbox to start; local
  // operator workstations keep the sandbox. --disable-dev-shm-usage covers small /dev/shm.
  const chrome=spawn(chromePath,['--headless=new','--disable-dev-shm-usage',...(process.getuid?.()===0?['--no-sandbox']:[]),'--no-first-run','--no-default-browser-check',`--user-data-dir=${path.join(tempDir,'profile')}`,'--remote-debugging-port=0','--remote-debugging-address=127.0.0.1','--remote-allow-origins=*','about:blank'],{stdio:'ignore'});
  let cdp;
  try{
    const endpoint=await waitFor(async()=>{
      const raw=await fs.readFile(path.join(tempDir,'profile','DevToolsActivePort'),'utf8').catch(()=>null);
      return raw?{port:Number(raw.split(/\r?\n/)[0])}:null;
    },'Chrome DevTools endpoint');
    const version=await (await fetch(`http://127.0.0.1:${endpoint.port}/json/version`)).json();
    const target=await (await fetch(`http://127.0.0.1:${endpoint.port}/json/new?about%3Ablank`,{method:'PUT'})).json();
    cdp=await CDP.connect(target.webSocketDebuggerUrl);
    await cdp.call('Page.enable');await cdp.call('Runtime.enable');
    await cdp.call('Emulation.setDeviceMetricsOverride',{width:1440,height:900,deviceScaleFactor:1,mobile:false});
    const shotDir=process.env.UI_SCREENSHOT_DIR;
    if(shotDir)await fs.mkdir(shotDir,{recursive:true});
    const capture=async name=>{
      if(!shotDir)return;
      const result=await cdp.call('Page.captureScreenshot',{format:'png'});
      await fs.writeFile(path.join(shotDir,`${name}.png`),Buffer.from(result.data,'base64'));
    };
    await cdp.call('Page.navigate',{url:`http://127.0.0.1:${portNumber}/tests/embedding-queue-fixture.html`});
    await waitFor(()=>cdp.evaluate("document.querySelectorAll('[data-testid=queue-history-row]').length===100"),'first synthetic history page');
    const initial=await cdp.evaluate(`(() => ({
      running:document.querySelector('[data-testid=queue-stat-running] strong')?.textContent,
      completed:document.querySelector('[data-testid=queue-stat-completed] strong')?.textContent,
      failed:document.querySelector('[data-testid=queue-stat-failed] strong')?.textContent,
      worker:document.querySelector('[data-testid=queue-stat-worker] strong')?.textContent,
      active:document.querySelector('[data-testid=queue-active-row]')?.textContent,
      rows:document.querySelectorAll('[data-testid=queue-history-row]').length,
      failedRow:document.body.innerText.includes('TOKEN_LIMIT'),
      moreButton:[...document.querySelectorAll('button')].some(button=>button.textContent.includes('加载更多')),
    }))()`);
    assert.equal(initial.running,'1');
    assert.equal(initial.completed,'118');
    assert.equal(initial.failed,'7');
    assert.equal(initial.worker,'忙碌');
    assert.match(initial.active,/队列 #301/);
    assert.match(initial.active,/6 条文本/);
    assert.equal(initial.rows,100);
    assert.equal(initial.failedRow,true,'failed rows show their error code');
    assert.equal(initial.moreButton,true);
    await capture('issue-74-embedding-queue-1440x900');
    await cdp.evaluate("[...document.querySelectorAll('button')].find(button=>button.textContent.includes('加载更多'))?.click()");
    await waitFor(()=>cdp.evaluate("document.querySelectorAll('[data-testid=queue-history-row]').length===150"),'second history page appends unique rows');
    const paged=await cdp.evaluate(`(() => ({
      rows:document.querySelectorAll('[data-testid=queue-history-row]').length,
      ids:[...document.querySelectorAll('[data-testid=queue-history-row]')].length,
      moreButton:[...document.querySelectorAll('button')].some(button=>button.textContent.includes('加载更多')),
    }))()`);
    assert.equal(paged.rows,150);
    assert.equal(paged.moreButton,false,'cursor end hides the load-more button');
    await cdp.evaluate("[...document.querySelectorAll('button')].find(button=>button.textContent==='刷新')?.click()");
    await waitFor(()=>cdp.evaluate('window.queueFixture.refreshCount===2'),'refresh issues a new history request');
    await waitFor(()=>cdp.evaluate("document.querySelectorAll('[data-testid=queue-history-row]').length===100"),'refresh replaces the loaded history');
    const afterRefresh=await cdp.evaluate("({rows:document.querySelectorAll('[data-testid=queue-history-row]').length,moreButton:[...document.querySelectorAll('button')].some(button=>button.textContent.includes('加载更多'))})");
    assert.equal(afterRefresh.rows,100);
    assert.equal(afterRefresh.moreButton,true,'refresh restores the first-page cursor');
    assert.ok(version.Browser,'connected to a local Chrome DevTools browser');
  }finally{
    cdp?.socket.close();
    try{await stopProcess(chrome)}finally{
      try{await server.close()}finally{await removeTree(tempDir)}
    }
  }
});
