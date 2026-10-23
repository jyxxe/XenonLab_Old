import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createServer} from '../../../apps/desktop-web/node_modules/vite/dist/node/index.js';
import {chromium} from '../../../apps/desktop-web/node_modules/playwright-core/index.mjs';
const helper=path.dirname(new URL(import.meta.url).pathname);
const source=path.resolve(helper,'../../..');
const evidence=path.join(source,'runtime/verification/attachment-loading');await fs.mkdir(evidence,{recursive:true});
const appRoot=path.join(source,'apps/desktop-web');
const fixtureTsx=path.join(appRoot,'tests/.round34-attachment-switch-fixture.tsx');
const fixtureHtml=path.join(appRoot,'tests/.round34-attachment-switch-fixture.html');
const temp=await fs.mkdtemp(path.join(os.tmpdir(),'xeonolab-round34-attachment-'));
const port=async()=>{const s=net.createServer();await new Promise((ok,no)=>s.listen(0,'127.0.0.1',ok).once('error',no));const n=s.address().port;await new Promise(ok=>s.close(ok));return n};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function waitFor(fn,label,timeout=30000){const until=Date.now()+timeout;while(Date.now()<until){try{const x=await fn();if(x)return x}catch{}await sleep(80)}throw Error(`Timed out waiting for ${label}`)}
async function terminate(child){if(!child||child.exitCode!==null||child.signalCode!==null)return;const exited=new Promise(r=>child.once('exit',r));child.kill('SIGTERM');await Promise.race([exited,sleep(3000)]);if(child.exitCode===null&&child.signalCode===null)child.kill('SIGKILL')}
await fs.copyFile(path.join(helper,'fixture.tsx'),fixtureTsx);await fs.copyFile(path.join(helper,'fixture.html'),fixtureHtml);
const apiPort=await port(),proxyPort=await port(),vitePort=await port();
const token='round34-local-human',headers={Authorization:`Bearer ${token}`,'Content-Type':'application/json'};
const backend=spawn('uv',['run','python','-m','uvicorn','xeonolab.library:create_app','--factory','--app-dir',source,'--host','127.0.0.1','--port',String(apiPort)],{cwd:source,env:{...process.env,PYTHONPATH:source,APP_MODE:'development',DATA_DIR:path.join(temp,'db'),RATE_LIMIT_ENABLED:'false',DEV_TOKENS_JSON:JSON.stringify({[token]:{sub:'round34-local-human',scope:'xelibrary:read xelibrary:write xelibrary:attach',agent_kind:'human'},'round34-readonly-token':{sub:'round34-local-human',scope:'xelibrary:read',agent_kind:'human'}})},stdio:'ignore'});
let proxy,proxyListening=false,vite,browser;const proxyEvents=[];const plans=new Map(),allPlans=[];
 function hold(row,fail=false){let release;const gate=new Promise(r=>release=r);const plan={gate,release,fail,ready:false,sent:false};allPlans.push(plan);const key=`/api/v1/tables/${table.table_id}/rows/${row._row_id}/attachments`;const queue=plans.get(key)||[];queue.push(plan);plans.set(key,queue);return plan}
 let table;
try{
 await waitFor(async()=>{try{return(await fetch(`http://127.0.0.1:${apiPort}/health/live`)).ok}catch{return false}},'local XeLibrary API',60000);
 const base=`http://127.0.0.1:${apiPort}/api/v1`;
 const create=async(path,name,columns)=>{const r=await fetch(`${base}/tables`,{method:'POST',headers,body:JSON.stringify({path,name,columns})});const p=await r.json();assert.equal(r.status,201,JSON.stringify(p));return p.data};
 table=await create('/R34/attachments','attachment-loading-r34',[{name:'title',type:'string',nullable:false,default_value:null}]);
 const seed=async(title)=>{const r=await fetch(`${base}/tables/${table.table_id}/rows`,{method:'POST',headers,body:JSON.stringify({values:{title}})});const p=await r.json();assert.equal(r.status,201,JSON.stringify(p));return p.data};
 const rowA=await seed('Row A has a report'),rowB=await seed('Row B has a memo'),rowC=await seed('Row C is empty');
 async function upload(row,filename,text){const form=new FormData();form.append('upload',new Blob([text],{type:'text/plain'}),filename);const uploadHeaders={Authorization:`Bearer ${token}`};const r=await fetch(`${base}/attachments?table_id=${table.table_id}&row_id=${row._row_id}`,{method:'POST',headers:uploadHeaders,body:form});const p=await r.json();assert.equal(r.status,201,JSON.stringify(p));return p.data;}
 const uploadedA=await upload(rowA,'row-a-report.txt','synthetic attachment for row A');const uploadedB=await upload(rowB,'row-b-memo.txt','synthetic attachment for row B');
 const getAttachments=async(row)=>{const r=await fetch(`${base}/tables/${table.table_id}/rows/${row._row_id}/attachments`,{headers:{Authorization:`Bearer ${token}`}});const p=await r.json();assert.equal(r.status,200,JSON.stringify(p));return p.data};
 const metadataA=await getAttachments(rowA),metadataB=await getAttachments(rowB);assert.equal(metadataA[0].attachment_id,uploadedA.attachment_id);assert.equal(metadataB[0].attachment_id,uploadedB.attachment_id);
 proxy=http.createServer((req,res)=>{
  const event={path:req.url,method:req.method,phase:'received'};proxyEvents.push(event);
  const plan=req.method==='GET'?plans.get(req.url)?.shift():null;
  const upstream=http.request({host:'127.0.0.1',port:apiPort,path:req.url,method:req.method,headers:{...req.headers,host:`127.0.0.1:${apiPort}`}},up=>{
   const chunks=[];up.on('data',chunk=>chunks.push(chunk));up.on('end',async()=>{
    event.status=up.statusCode;let body=Buffer.concat(chunks);let status=up.statusCode;
    if(plan){plan.ready=true;await plan.gate;if(plan.fail){status=503;body=Buffer.from(JSON.stringify({error:{message:'Synthetic attachment read unavailable',code:'SERVICE_UNAVAILABLE'}}));}}
    const responseHeaders={...up.headers};delete responseHeaders.connection;delete responseHeaders['transfer-encoding'];responseHeaders['content-length']=String(body.length);
    res.writeHead(status||502,responseHeaders);res.end(body);event.phase='sent';if(plan)plan.sent=true;
   });
  });upstream.on('error',()=>{res.writeHead(502);res.end('local proxy error')});req.pipe(upstream);
 });
 await new Promise((ok,no)=>proxy.listen(proxyPort,'127.0.0.1',ok).once('error',no));proxyListening=true;
 vite=await createServer({root:appRoot,configFile:false,server:{host:'127.0.0.1',port:vitePort,strictPort:true,watch:null,proxy:{'/api/v1':{target:`http://127.0.0.1:${proxyPort}`,changeOrigin:false}}}});await vite.listen();
 browser=await chromium.launch({headless:true,executablePath:'/usr/bin/google-chrome',args:['--no-sandbox','--disable-dev-shm-usage']});const page=await browser.newPage({viewport:{width:1440,height:900}});const pageErrors=[];page.on('pageerror',e=>pageErrors.push(e.message));
 await page.goto(`http://127.0.0.1:${vitePort}/tests/.round34-attachment-switch-fixture.html`);await page.locator('.knowledge .knowledge-sidebar').waitFor({state:'visible'});
 await page.locator('button[aria-label="刷新知识表目录"]').click();await waitFor(async()=>page.locator('.knowledge-sidebar .library-tree-table').count().then(n=>n===1),'local API catalogue');await page.locator('.knowledge-sidebar .library-tree-table').filter({hasText:'attachment-loading-r34'}).click();
 await page.locator(`.table-scroll tbody tr[data-row-id="${rowA._row_id}"]`).waitFor({state:'visible'});await page.locator(`.table-scroll tbody tr[data-row-id="${rowB._row_id}"]`).waitFor({state:'visible'});
 await page.locator(`.table-scroll tbody tr[data-row-id="${rowA._row_id}"]`).click();await page.locator('.inspector-tabs .ds-tab').filter({hasText:'附件'}).click();
 await page.locator('.attachment-panel li').filter({hasText:'row-a-report.txt'}).waitFor({state:'visible'});
 const panel=page.locator('.attachment-panel');const checks=[];
 const select=async(row)=>page.locator(`.table-scroll tbody tr[data-row-id="${row._row_id}"]`).click();
 const loaded=async(name)=>panel.locator('li').filter({hasText:name}).waitFor();
 const pending=async(plan)=>{await waitFor(()=>plan.ready&&!plan.sent,'real upstream response held');assert.equal(await panel.getByText('当前记录没有附件。',{exact:true}).count(),0);assert.equal(await panel.getByRole('status').filter({hasText:'正在读取附件列表'}).count(),1)};
 const b=hold(rowB);await select(rowB);await pending(b);assert.equal(await panel.locator('li').count(),0);await page.screenshot({path:path.join(evidence,'nonempty-pending.png'),fullPage:true});b.release();await loaded('row-b-memo.txt');
 assert.ok((await panel.innerText()).includes(metadataB[0].attachment_id));checks.push('nonempty pending neutral then real metadata');
 const c=hold(rowC);await select(rowC);await pending(c);c.release();await panel.getByText('当前记录没有附件。',{exact:true}).waitFor();checks.push('empty pending neutral then confirmed empty');
 const error=hold(rowA,true);await select(rowA);await pending(error);error.release();await panel.getByRole('alert').waitFor();assert.equal(await panel.getByText('当前记录没有附件。',{exact:true}).count(),0);
 await panel.getByRole('button',{name:'重试读取附件'}).click();await loaded('row-a-report.txt');assert.equal(await panel.getByRole('alert').count(),0);checks.push('failed initial read is error, GET retry recovers');
 // A legal ordinary small-file upload causes a list refresh; inject only its read failure.
 const refresh=hold(rowA,true);await panel.locator('input[type=file]').setInputFiles({name:'extra-normal.txt',mimeType:'text/plain',buffer:Buffer.from('ordinary synthetic text')});await pending(refresh);
 assert.equal(await panel.locator('li strong').first().innerText(),'row-a-report.txt');refresh.release();await panel.getByRole('alert').waitFor();
 assert.equal(await panel.locator('li strong').first().innerText(),'row-a-report.txt');await page.screenshot({path:path.join(evidence,'failed-refresh-retains.png'),fullPage:true});
 const retry=hold(rowA);await panel.getByRole('button',{name:'重试读取附件'}).click();await pending(retry);retry.release();await loaded('extra-normal.txt');checks.push('refresh failure retains loaded items; retry GET shows real upload');
 for(const fail of [false,true]){
  await select(rowC);await panel.getByText('当前记录没有附件。',{exact:true}).waitFor();
  const old=hold(rowA,fail);await select(rowA);await pending(old);
  await select(rowB);await loaded('row-b-memo.txt');
  const current=hold(rowA);await select(rowA);await pending(current);
  const oldPath=`/tables/${table.table_id}/rows/${rowA._row_id}/attachments`;
  const completedBefore=await page.evaluate(path=>window.r34Audit.calls.filter(c=>c.path===path).length,oldPath);
  old.release();await waitFor(()=>old.sent,'obsolete response delivered');
  // Its browser-side Promise completion must settle before checking the new panel.
  await waitFor(async()=>await page.evaluate(({path,count})=>window.r34Audit.calls.filter(c=>c.path===path).length===count+1,{path:oldPath,count:completedBefore}),'response observed by fixture');
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await panel.getByRole('alert').count(),0);assert.equal(await panel.locator('li').count(),0);await pending(current);
  current.release();await loaded('row-a-report.txt');checks.push('A-B-A obsolete '+(fail?'error':'success')+' isolated');
 }
 console.log('checking ordinary unlink/link');
 const extra=panel.locator('li').filter({hasText:'extra-normal.txt'});
 await extra.getByRole('button',{name:'解除关联'}).click();await waitFor(async()=>await panel.locator('li').filter({hasText:'extra-normal.txt'}).count()===0,'ordinary unbind reflected');
 await panel.getByLabel('关联此表中已有附件 ID').fill(metadataB[0].attachment_id);await panel.getByRole('button',{name:'关联附件',exact:true}).click();await loaded('row-b-memo.txt');
 checks.push('ordinary upload/unbind/bind keep API and loaded metadata semantics');
 console.log('checking readonly');
 const readonlyPage=await browser.newPage({viewport:{width:1200,height:850}});readonlyPage.on('pageerror',e=>pageErrors.push(e.message));
 await readonlyPage.goto(`http://127.0.0.1:${vitePort}/tests/.round34-attachment-switch-fixture.html?readonly=1`);
 await readonlyPage.locator('button[aria-label="刷新知识表目录"]').click();await readonlyPage.locator('.knowledge-sidebar .library-tree-table').filter({hasText:table.name}).click();
 await readonlyPage.locator(`.table-scroll tbody tr[data-row-id="${rowA._row_id}"]`).click();await readonlyPage.locator('.inspector-tabs .ds-tab').filter({hasText:'附件'}).click();
 const ro=readonlyPage.locator('.attachment-panel');await ro.locator('li').filter({hasText:'row-a-report.txt'}).waitFor();assert.equal(await ro.locator('input[type=file]').count(),0);assert.equal(await ro.getByRole('button',{name:'解除关联'}).count(),0);
 const downloadEvent=readonlyPage.waitForEvent('download');await ro.locator('li').filter({hasText:'row-a-report.txt'}).getByRole('button',{name:'鉴权下载'}).click();const download=await downloadEvent;assert.equal(download.suggestedFilename(),'row-a-report.txt');assert.equal(await fs.readFile(await download.path(),'utf8'),'synthetic attachment for row A');
 const readCalls=await readonlyPage.evaluate(()=>window.r34Audit.calls);assert.ok(readCalls.every(c=>c.method==='GET'));checks.push('same owner scoped read-only identity lists and downloads without mutation controls');
 await readonlyPage.screenshot({path:path.join(evidence,'readonly-loaded.png'),fullPage:true});await readonlyPage.close();
 const calls=await page.evaluate(()=>window.r34Audit.calls);
 assert.deepEqual(pageErrors,[]);
 const result={baseline:'e74301b03fab2cde4e50e395c02bc041862cde85',sourceRoot:source,checks,pageErrors,metadataA,metadataB,proxyEvents,browserCalls:calls,scope:'actual LibraryApp/TableView/Inspector/Attachments and current local XeLibrary FastAPI temporary SQLite; lawful synthetic setup read/write/attach human, same-subject read-only dev token; ordinary text files; real success responses gated, explicit benign synthetic503 only for failure cases'};
 await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
}finally{for(const plan of allPlans)plan.release();if(proxyListening)proxy.closeAllConnections();if(browser)await browser.close();if(vite)await vite.close();if(proxyListening)await new Promise(r=>proxy.close(r));await terminate(backend);await fs.rm(temp,{recursive:true,force:true,maxRetries:5,retryDelay:100});await Promise.all([fixtureTsx,fixtureHtml].map(f=>fs.rm(f,{force:true})));}
