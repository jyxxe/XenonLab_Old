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

// Issue #85: at 390px the status tab strip must not widen the Issues app or clip
// filter options; the table keeps its own internal horizontal scrolling.
test('Issues toolbar stays inside a 390px viewport',async t=>{
  const chromePath=process.env.CHROME_BIN||(['/usr/bin/google-chrome','/usr/bin/chromium','/usr/bin/chromium-browser'].find(file=>existsSync(file)));
  if(!chromePath){t.skip('Chrome/Chromium unavailable; run with CHROME_BIN=/path/to/chrome');return}
  const portNumber=await port();
  const server=await createServer({root:appRoot,server:{host:'127.0.0.1',port:portNumber,strictPort:true,watch:null}});
  await server.listen();
  const tempDir=await fs.mkdtemp(path.join(os.tmpdir(),'xeonolab-issues-narrow-'));
  const chrome=spawn(chromePath,['--headless=new','--no-first-run','--no-default-browser-check',`--user-data-dir=${path.join(tempDir,'profile')}`,'--remote-debugging-port=0','--remote-debugging-address=127.0.0.1','--remote-allow-origins=*','about:blank'],{stdio:'ignore'});
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
    await cdp.call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false});
    const shotDir=process.env.UI_SCREENSHOT_DIR;
    if(shotDir)await fs.mkdir(shotDir,{recursive:true});
    const capture=async name=>{
      if(!shotDir)return;
      const result=await cdp.call('Page.captureScreenshot',{format:'png'});
      await fs.writeFile(path.join(shotDir,`${name}.png`),Buffer.from(result.data,'base64'));
    };
    await cdp.call('Page.navigate',{url:`http://127.0.0.1:${portNumber}/tests/issues-fixture.html`});
    await waitFor(()=>cdp.evaluate("document.querySelectorAll('.issues-app tbody tr').length===2"),'fixture rows');
    await sleep(200);
    const metrics=await cdp.evaluate(`(() => {
      const app=document.querySelector('.issues-app');
      const toolbar=document.querySelector('.issues-toolbar');
      const tabs=document.querySelector('.issues-toolbar .ds-tabs');
      const tabRects=[...document.querySelectorAll('.issues-toolbar .ds-tab')].map(button=>{const r=button.getBoundingClientRect();return {left:r.left,right:r.right,label:button.innerText}});
      return {
        app:{scroll:app.scrollWidth,client:app.clientWidth},
        toolbar:{scroll:toolbar.scrollWidth,client:toolbar.clientWidth},
        tabs:{scroll:tabs.scrollWidth,client:tabs.clientWidth},
        tabRects,
        viewport:window.innerWidth,
      };
    })()`);
    assert.ok(metrics.app.scroll<=metrics.app.client,`app must not overflow horizontally: ${JSON.stringify(metrics.app)}`);
    assert.ok(metrics.toolbar.scroll<=metrics.toolbar.client,`toolbar must not overflow: ${JSON.stringify(metrics.toolbar)}`);
    for(const rect of metrics.tabRects){
      assert.ok(rect.left>=0&&rect.right<=metrics.viewport,`filter option fully reachable: ${JSON.stringify(rect)}`);
    }
    await capture('issue-85-issues-toolbar-390x844');
    assert.ok(version.Browser,'connected to a local Chrome DevTools browser');
  }finally{
    cdp?.socket.close();
    try{await stopProcess(chrome)}finally{
      try{await server.close()}finally{await removeTree(tempDir)}
    }
  }
});
