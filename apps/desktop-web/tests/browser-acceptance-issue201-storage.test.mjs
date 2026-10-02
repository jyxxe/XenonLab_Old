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

test('System storage tab shows the verified HF-cache pool watermark or honest unavailable states',async t=>{
  const chromePath=process.env.CHROME_BIN||(['/usr/bin/google-chrome','/usr/bin/chromium','/usr/bin/chromium-browser'].find(file=>existsSync(file)));
  if(!chromePath){t.skip('Chrome/Chromium unavailable; run with CHROME_BIN=/path/to/chrome');return}
  const evidence=path.resolve(appRoot,'../../runtime/verification/issue201-storage');
  const portNumber=await port();
  const server=await createServer({root:appRoot,server:{host:'127.0.0.1',port:portNumber,strictPort:true,watch:null}});
  await server.listen();
  const tempDir=await fs.mkdtemp(path.join(os.tmpdir(),'xelonolab-issue201-acceptance-'));
  const chrome=spawn(chromePath,[
    '--headless=new','--disable-dev-shm-usage',...(process.getuid?.()===0?['--no-sandbox']:[]),'--no-first-run','--no-default-browser-check',
    `--user-data-dir=${path.join(tempDir,'profile')}`,'--remote-debugging-port=0','--remote-debugging-address=127.0.0.1',
    '--remote-allow-origins=*','about:blank',
  ],{stdio:'ignore'});
  let cdp;
  const openStorage=async query=>{
    await cdp.call('Page.navigate',{url:`http://127.0.0.1:${portNumber}/tests/system-fixture.html${query}`});
    await waitFor(()=>cdp.evaluate("!!document.querySelector('.system-sidebar')"),'system sidebar');
    await cdp.evaluate("[...document.querySelectorAll('.system-sidebar button')].find(button=>button.textContent==='存储')?.click()");
  };
  try{
    const endpoint=await waitFor(async()=>{
      const raw=await fs.readFile(path.join(tempDir,'profile','DevToolsActivePort'),'utf8').catch(()=>null);
      return raw?{port:Number(raw.split(/\r?\n/)[0])}:null;
    },'Chrome DevTools endpoint');
    await (await fetch(`http://127.0.0.1:${endpoint.port}/json/version`)).json();
    const target=await (await fetch(`http://127.0.0.1:${endpoint.port}/json/new?about%3Ablank`,{method:'PUT'})).json();
    cdp=await CDP.connect(target.webSocketDebuggerUrl);
    await cdp.call('Page.enable');await cdp.call('Runtime.enable');
    await fs.mkdir(evidence,{recursive:true});
    const errors=[];
    cdp.socket.addEventListener('message',event=>{
      const message=JSON.parse(event.data);
      if(message.method==='Runtime.exceptionThrown')errors.push(String(message.params?.exceptionDetails?.text));
    });

    // Success: pool and guest usage, watermark, cached bytes and the verified sampling time.
    await openStorage('?storageCase=ok');
    await waitFor(()=>cdp.evaluate("!!document.querySelector('.dev-meta')"),'storage water level');
    const ok=await cdp.evaluate(`(() => ({text:document.querySelector('.system-content').innerText,sampled:document.querySelector('.dev-meta')?.innerText.includes('采样时间'),path:document.body.innerText.includes('/srv/hf-cache')}))()`);
    assert.match(ok.text,/aux-10t/);
    assert.match(ok.text,/池用量 72\.8%/);
    assert.match(ok.text,/正常/);
    assert.match(ok.text,/TiB/);
    assert.equal(ok.sampled,true);
    assert.doesNotMatch(ok.text,/—\n采样时间/,'sampling time renders as a real timestamp');
    assert.equal(ok.path,false,'server-local cache paths never reach the desktop');
    assert.match(ok.text,/不能相加/);
    await cdp.evaluate("document.querySelector('.system-content').scrollIntoView()");
    await sleep(150);

    // Missing scope: permission explanation, no numbers.
    await openStorage('?storageCase=forbidden');
    await waitFor(()=>cdp.evaluate("document.body.innerText.includes('hf-cache:read')"),'permission notice');
    const forbidden=await cdp.evaluate("document.querySelector('.system-content').innerText");
    assert.doesNotMatch(forbidden,/TiB/);

    // Upstream telemetry unavailable: explicit state without cached numbers.
    await openStorage('?storageCase=unavailable');
    await waitFor(()=>cdp.evaluate("document.body.innerText.includes('缓存池水位暂不可用')"),'unavailable notice');
    const unavailable=await cdp.evaluate("document.querySelector('.system-content').innerText");
    assert.doesNotMatch(unavailable,/TiB/);
    assert.match(unavailable,/不展示任何缓存数字/);
    await cdp.evaluate("document.querySelector('.system-content').scrollIntoView()");
    await sleep(120);
    await cdp.call('Page.captureScreenshot',{format:'png'}).then(async shot=>{
      await fs.writeFile(path.join(evidence,'system-storage-watermark.png'),Buffer.from(shot.data,'base64'));
    });
    assert.equal(errors.length,0);
    const report={source:'Real headless Chrome renders the System storage tab via a local synthetic fixture (no production account or services). Success, missing-scope and telemetry-unavailable states are asserted; no real HF-cache API was accessed.',cases:['ok','forbidden','unavailable'],checks:['pool name and watermark badge render','pool vs guest filesystem usage are distinguished','cached bytes and verified sampled_at render','server-local paths never appear','missing scope shows permission notice without numbers','unavailable telemetry shows explicit empty state without numbers'],pageErrors:errors,outcome:'PASS'};
    await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify(report,null,2));
  }finally{
    cdp?.socket.close();
    try{await stopProcess(chrome)}finally{
      try{await server.close()}finally{await removeTree(tempDir)}
    }
  }
});
