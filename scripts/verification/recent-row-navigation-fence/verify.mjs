import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createHash} from 'node:crypto';
import {createServer} from '../../../apps/desktop-web/node_modules/vite/dist/node/index.js';
import {chromium} from '../../../apps/desktop-web/node_modules/playwright-core/index.mjs';
const helper=path.dirname(new URL(import.meta.url).pathname),source=path.resolve(helper,'../../..'),appRoot=path.join(source,'apps/desktop-web');
const compat=process.env.LIBRARY_COMPAT_SOURCE?await fs.readFile(process.env.LIBRARY_COMPAT_SOURCE,'utf8'):null;
const effectiveLibrarySource=compat||await fs.readFile(path.join(appRoot,'src/library/LibraryApp.tsx'),'utf8');
const expectsKeyboardRecent=effectiveLibrarySource.includes('rememberLoadedRow(rowId);');
const evidence=path.join(source,'runtime/verification/recent-row-keyboard-race',process.env.EVIDENCE_CASE || (compat?'compat191':'current'));await fs.mkdir(evidence,{recursive:true});
const temp=await fs.mkdtemp(path.join(os.tmpdir(),'xeonolab-navigation-fence-'));
async function port(){const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p}
const apiPort=await port(),proxyPort=await port(),webPort=await port(),token='round44-local-human';
const backend=spawn('uv',['run','python','-m','uvicorn','xeonolab.library:create_app','--factory','--app-dir',source,'--host','127.0.0.1','--port',String(apiPort)],{cwd:source,env:{...process.env,PYTHONPATH:source,APP_MODE:'development',DATA_DIR:temp,RATE_LIMIT_ENABLED:'false',DEV_TOKENS_JSON:JSON.stringify({[token]:{sub:token,scope:'xelibrary:read xelibrary:write',agent_kind:'human'}})},stdio:'ignore'});
const tsx=path.join(appRoot,'tests/.recent-row-navigation-fence-fixture.tsx'),html=path.join(appRoot,'tests/.recent-row-navigation-fence-fixture.html');let browser,vite,proxy;const plans=[],queued=[];
async function waitFor(fn,label){for(let i=0;i<400;i++){try{const v=await fn();if(v)return v}catch{}await new Promise(r=>setTimeout(r,60))}throw Error('Timeout: '+label)}
async function api(route,method='GET',body){const headers={Authorization:`Bearer ${token}`,'Content-Type':'application/json'};const r=await fetch(`http://127.0.0.1:${apiPort}/api/v1${route}`,{method,headers,body:body===undefined?undefined:JSON.stringify(body)});const p=await r.json();assert.ok(r.ok,JSON.stringify(p));return p.data}
function hold(fail=false){let release;const gate=new Promise(r=>release=r);const plan={gate,release,fail,ready:false,sent:false};plans.push(plan);queued.push(plan);return plan}
try{
 await waitFor(async()=>{try{return(await fetch(`http://127.0.0.1:${apiPort}/health/live`)).ok}catch{return false}},'backend');
 const table=await api('/tables','POST',{path:'/R195/navigation',name:'navigation-fence',columns:[{name:'title',type:'string',nullable:false}]});
 const a=await api(`/tables/${table.table_id}/rows`,'POST',{values:{title:'Recent target A'}}),b=await api(`/tables/${table.table_id}/rows`,'POST',{values:{title:'Current target B'}});
 const other=await api('/tables','POST',{path:'/R195/navigation',name:'other-table',columns:[{name:'title',type:'string',nullable:false}]});
 const route=`/tables/${table.table_id}/rows/${a._row_id}`,heldPath='/api/v1'+route;
 proxy=http.createServer((req,res)=>{const plan=req.method==='GET'&&req.url===heldPath?queued.shift():null;const upstream=http.request({host:'127.0.0.1',port:apiPort,path:req.url,method:req.method,headers:{...req.headers,host:`127.0.0.1:${apiPort}`}},response=>{const chunks=[];response.on('data',c=>chunks.push(c));response.on('end',async()=>{if(plan){plan.ready=true;await plan.gate;}if(plan?.fail){res.writeHead(503,{'Content-Type':'application/json'});res.end(JSON.stringify({error:{code:'SERVICE_UNAVAILABLE',message:'Synthetic local read failure'}}));}else{res.writeHead(response.statusCode,response.headers);res.end(Buffer.concat(chunks));}if(plan)plan.sent=true;});});upstream.on('error',error=>{res.writeHead(502);res.end(String(error))});req.pipe(upstream);});await new Promise(r=>proxy.listen(proxyPort,'127.0.0.1',r));
 await fs.copyFile(path.join(helper,'fixture.tsx'),tsx);await fs.copyFile(path.join(helper,'fixture.html'),html);
 const libraryPath=path.join(appRoot,'src/library/LibraryApp.tsx');
 vite=await createServer({root:appRoot,configFile:false,plugins:compat?[{name:'test-public-library-combination',enforce:'pre',load(id){if(id.split('?')[0]===libraryPath)return compat}}]:[],server:{host:'127.0.0.1',port:webPort,strictPort:true,watch:null,proxy:{'/api/v1':`http://127.0.0.1:${proxyPort}`}}});await vite.listen();
 browser=await chromium.launch({headless:true,executablePath:'/usr/bin/google-chrome',args:['--no-sandbox','--disable-dev-shm-usage']});const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(`http://127.0.0.1:${webPort}/tests/.recent-row-navigation-fence-fixture.html`);
 const tableButton=name=>page.locator('.library-tree-table').filter({hasText:name});await tableButton(table.name).click();const grid=page.locator('.table-scroll');const row=id=>page.locator(`.table-scroll tbody tr[data-row-id="${id}"]`);await row(a._row_id).waitFor();await row(b._row_id).waitFor();
 const recent=page.locator('.library-sidebar-section').filter({hasText:'最近'}),recentA=recent.locator('.library-mini-list button').filter({hasText:a._row_id.slice(0,8)});await row(a._row_id).click();await recentA.waitFor();
 const active=async()=>page.locator('.table-scroll tbody tr.is-active-row').getAttribute('data-row-id');
 const selected=async()=>page.locator('.table-scroll tbody tr[aria-selected="true"]').evaluateAll(els=>els.map(e=>e.getAttribute('data-row-id')));
 const frames=async()=>page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
 const count=async()=>(await page.evaluate(route=>window.r44Audit.calls.filter(call=>call.path===route).length,route));
 const start=async(fail=false,initial='b')=>{await row(initial==='a'?a._row_id:b._row_id).click();const before=await count(),plan=hold(fail);await recentA.click();await waitFor(()=>plan.ready&&!plan.sent,'genuine A body held before browser consumes it');await row(b._row_id).waitFor();return {plan,before};};
 const finish=async({plan,before})=>{plan.release();await waitFor(()=>plan.sent,'held response delivered');await waitFor(async()=>await count()===before+1,'actual A response body consumed by browser fixture');await frames();};
 const assertB=async()=>{assert.equal(await active(),b._row_id);assert.ok((await page.locator('.inspector-head small').innerText()).startsWith(b._row_id.slice(0,8)));};
 const checks=[];
 for(const mode of ['Arrow','ShiftArrow','Enter','doubleclick','menu','mouse']){
  const pending=await start(false,mode.includes('Arrow')?'a':'b');
  if(mode.includes('Arrow')){const ids=await page.locator('.table-scroll tbody tr[data-row-id]').evaluateAll(e=>e.map(row=>row.getAttribute('data-row-id')));const direction=ids.indexOf(b._row_id)>ids.indexOf(a._row_id)?'ArrowDown':'ArrowUp';await grid.focus();await grid.press((mode==='ShiftArrow'?'Shift+':'')+direction);}
  else if(mode==='Enter'){await grid.focus();await grid.press('Enter');}
  else if(mode==='doubleclick')await row(b._row_id).dblclick();
  else if(mode==='menu'){await row(b._row_id).click({button:'right'});await page.getByRole('menuitem',{name:'在 Inspector 中打开',exact:true}).click();}
  else await row(b._row_id).click();
  await assertB();const expectedSelection=await selected();if(mode==='ShiftArrow')assert.equal(expectedSelection.length,2);await finish(pending);await assertB();assert.deepEqual(await selected(),expectedSelection);if(expectsKeyboardRecent&&mode!=='mouse')assert.ok((await recent.locator('.library-mini-list button').allInnerTexts()).some(text=>text.includes(b._row_id.slice(0,8))));
  if(mode==='ShiftArrow')await page.screenshot({path:path.join(evidence,'late-A-keeps-keyboard-B.png'),fullPage:true});checks.push(mode+' newer loaded-row activation fences old consumed Recent success and preserves selection/Inspector');
 }
 const failed=await start(true);await grid.focus();await grid.press('Enter');await finish(failed);await assertB();assert.equal(await page.getByText('记录不存在，或当前身份没有读取权限。',{exact:true}).count(),0);checks.push('late benign synthetic failed A read after Enter does not post stale navigation failure');
 const normal=await start();await finish(normal);assert.equal(await active(),a._row_id);assert.ok((await page.locator('.inspector-head small').innerText()).startsWith(a._row_id.slice(0,8)));checks.push('ordinary Recent navigation without newer activation still opens genuine A response');
 const selectionOnly=await start();await grid.focus();await grid.press('Space');await grid.press('Control+a');assert.equal((await selected()).length,2);await grid.press('Escape');assert.deepEqual(await selected(),[]);assert.equal(await active(),b._row_id);await finish(selectionOnly);assert.equal(await active(),a._row_id);checks.push('Space/Ctrl+A/Escape remain selection-only and do not cancel ordinary pending row navigation');
 const switched=await start();await tableButton(other.name).click();await page.getByRole('heading',{name:`${other.path}/${other.name}`,exact:true}).waitFor();await finish(switched);assert.equal(await page.getByRole('heading',{name:`${other.path}/${other.name}`,exact:true}).count(),1);assert.equal(await page.locator('.table-scroll tbody tr.is-active-row').count(),0);await page.screenshot({path:path.join(evidence,'late-A-keeps-other-table.png'),fullPage:true});checks.push('ordinary table switch existing fence still rejects consumed old A response');
 assert.deepEqual(errors,[]);const librarySource=compat||await fs.readFile(libraryPath,'utf8');const result={baseline:'865a72277008a5773236b50b993bacdecc78d6ab',source,librarySourceHash:createHash('sha256').update(librarySource).digest('hex'),compatSource:process.env.LIBRARY_COMPAT_SOURCE||null,expectsKeyboardRecent,checks,pageErrors:errors,table,a,b,heldResponses:plans.map(({ready,sent,fail})=>({ready,sent,fail})),browserCalls:await page.evaluate(()=>window.r44Audit.calls),scope:'Actual LibraryApp/TableView/Inspector/Recent; actual local XeLibrary temporary SQLite; lawful synthetic read/write human. Success body/status/headers genuinely held by Promise barrier, explicit benign503 only for failure case. Client body count +2RAF awaited. No production/new API or preference synchronization.'};await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
}finally{for(const plan of plans)plan.release();if(browser)await browser.close();if(vite)await vite.close();if(proxy){proxy.closeAllConnections();await new Promise(r=>proxy.close(r));}backend.kill('SIGTERM');await new Promise(r=>{if(backend.exitCode!==null)return r();backend.once('exit',r);setTimeout(r,3000).unref()});await fs.rm(temp,{recursive:true,force:true});await Promise.all([tsx,html].map(p=>fs.rm(p,{force:true})));}
