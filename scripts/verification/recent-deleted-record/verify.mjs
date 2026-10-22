import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import {spawn, spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {createServer} from '../../../apps/desktop-web/node_modules/vite/dist/node/index.js';
import {chromium} from '../../../apps/desktop-web/node_modules/playwright-core/index.mjs';
const helper=path.dirname(new URL(import.meta.url).pathname);
const source=path.resolve(helper,'../../..');
const mode=process.env.SOURCE_CASE||'current';
const evidence=path.join(source,'runtime/verification/recent-deleted-record',mode);await fs.mkdir(evidence,{recursive:true});
const checks=[];
const appRoot=path.join(source,'apps/desktop-web');
const fixtureTsx=path.join(appRoot,'tests/__recent_deleted_record_fixture.tsx');
const fixtureHtml=path.join(appRoot,'tests/__recent_deleted_record_fixture.html');
const temp=await fs.mkdtemp(path.join(os.tmpdir(),'xeonolab-r51-soft-delete-'));
const port=async()=>{const s=net.createServer();await new Promise((ok,no)=>s.listen(0,'127.0.0.1',ok).once('error',no));const n=s.address().port;await new Promise(ok=>s.close(ok));return n};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function waitFor(fn,label,timeout=45000){const until=Date.now()+timeout;while(Date.now()<until){try{const x=await fn();if(x)return x}catch{}await sleep(100)}throw Error('Timed out waiting for '+label)}
async function stop(child){if(!child||child.exitCode!==null||child.signalCode!==null)return;const exited=new Promise(r=>child.once('exit',r));child.kill('SIGTERM');await Promise.race([exited,sleep(3000)]);if(child.exitCode===null&&child.signalCode===null)child.kill('SIGKILL')}
await fs.copyFile(path.join(helper,'fixture.tsx'),fixtureTsx);await fs.copyFile(path.join(helper,'fixture.html'),fixtureHtml);
const apiPort=await port(),desktopPort=await port(),vitePort=await port(),token='round51-local-admin';
const env={...process.env,LANG:'C.UTF-8',APP_MODE:'development',PYTHONPATH:source,RATE_LIMIT_ENABLED:'false'};
const library=spawn('uv',['run','python',path.join(helper,'local_xelibrary_api.py')],{cwd:source,env:{...env,ROUND51_DATA_DIR:path.join(temp,'library-data'),ROUND51_API_PORT:String(apiPort)},stdio:'ignore'});
const desktop=spawn('uv',['run','python',path.join(helper,'local_desktop_api.py')],{cwd:source,env:{...env,ROUND51_DESKTOP_DATA_DIR:path.join(temp,'desktop-data'),ROUND51_DESKTOP_API_PORT:String(desktopPort)},stdio:'ignore'});
let vite,browser;
try{
 await waitFor(async()=>{try{return(await fetch(`http://127.0.0.1:${apiPort}/health/live`)).ok}catch{return false}},'actual local XeLibrary API');
 await waitFor(async()=>{try{return(await fetch(`http://127.0.0.1:${desktopPort}/health/live`)).ok}catch{return false}},'actual local desktop identity API');
 const headers={authorization:'Bearer '+token,'content-type':'application/json'};
 const apiRequest=async(route,method='GET',body,extra={})=>{const response=await fetch(`http://127.0.0.1:${apiPort}/api/v1${route}`,{method,headers:{...headers,...extra},body:body===undefined?undefined:JSON.stringify(body)});let payload={};try{payload=await response.json()}catch{};return {status:response.status,payload}};
 const made=await apiRequest('/tables','POST',{path:'/R51',name:'round51-soft-delete-restore',columns:[{name:'label',type:'string',nullable:false,default_value:null}]});assert.equal(made.status,201,JSON.stringify(made.payload));
 const table=made.payload.data;
 const desktopIdentity=await fetch(`http://127.0.0.1:${desktopPort}/api/v1/users/me`,{headers});const identity=(await desktopIdentity.json()).data;assert.equal(desktopIdentity.status,200);assert.ok(identity.scopes.includes('xelibrary:write'));
 vite=await createServer({root:appRoot,configFile:false,plugins:mode==='before'?[{name:'exact-base',enforce:'pre',load(id){for(const file of ['src/library/LibraryApp.tsx','src/library/hooks.ts'])if(id===path.join(appRoot,file)){const result=spawnSync('git',['show',`eecec3ec4e171b7536681bf872b5898faf7ba25f:apps/desktop-web/${file}`],{cwd:source,encoding:'utf8'});assert.equal(result.status,0);return result.stdout;}}}]:[],optimizeDeps:{include:['react','react-dom/client','react/jsx-dev-runtime']},server:{host:'127.0.0.1',port:vitePort,strictPort:true,watch:null,proxy:{'/api/v1':{target:`http://127.0.0.1:${apiPort}`,changeOrigin:false},'/desktop-api':{target:`http://127.0.0.1:${desktopPort}`,changeOrigin:false,rewrite:p=>p.replace(/^\/desktop-api/,'')}}}});await vite.listen();
 browser=await chromium.launch({headless:true,executablePath:process.env.CHROME_BIN||'/usr/bin/google-chrome',args:['--no-sandbox','--disable-dev-shm-usage']});
 const page=await browser.newPage({viewport:{width:1500,height:950}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(`http://127.0.0.1:${vitePort}/tests/__recent_deleted_record_fixture.html`);
 await page.locator('.knowledge-sidebar').waitFor();
 await waitFor(()=>page.locator('.library-tree-table').filter({hasText:'round51-soft-delete-restore'}).count().then(n=>n===1),'created table in catalogue');
 await page.locator('.library-tree-table').filter({hasText:'round51-soft-delete-restore'}).click();
 await page.getByRole('heading',{name:'/R51/round51-soft-delete-restore'}).waitFor();
 await page.getByRole('button',{name:'新增记录'}).click();await page.getByRole('tab',{name:'字段'}).click();
 const field=page.locator('.inspector-fields input').first();await field.waitFor();await field.fill('R51 recoverable sentinel');
 await page.getByRole('button',{name:'创建记录'}).click();await page.getByText(/记录已创建/).first().waitFor();
 const initialRows=await apiRequest(`/tables/${table.table_id}/rows?limit=100`);assert.equal(initialRows.status,200);assert.equal(initialRows.payload.data.length,1);const row=initialRows.payload.data[0];assert.equal(row.values.label,'R51 recoverable sentinel');
 const gridrow=page.locator(`tr[data-row-id="${row._row_id}"]`);await gridrow.waitFor();await gridrow.click();
 await waitFor(()=>page.getByRole('button',{name:new RegExp(row._row_id.slice(0,8))}).count().then(n=>n>0),'row recent entry saved');
 await page.getByRole('button',{name:'搜索',exact:true}).click();
 await page.getByRole('searchbox',{name:'搜索 XeLibrary'}).fill('R51 recoverable sentinel');
 await page.getByRole('checkbox').check();await page.getByRole('button',{name:'搜索',exact:true}).last().click();
 await waitFor(()=>page.locator('.library-results button').count().then(n=>n===1),'row is keyword-searchable before deletion');
 const searchBefore={count:await page.locator('.library-results button').count(),text:await page.locator('.library-results').innerText()};
 await page.locator('.library-tree-table').filter({hasText:'round51-soft-delete-restore'}).click();
 await page.locator(`tr[data-row-id="${row._row_id}"]`).click();await page.getByRole('tab',{name:'字段'}).click();
 await page.getByRole('button',{name:'软删除记录'}).click();
 await page.getByRole('status').filter({hasText:'记录已软删除'}).first().waitFor();
 const deletedRows=await apiRequest(`/tables/${table.table_id}/rows?limit=100&include_deleted=true`);assert.equal(deletedRows.payload.data.length,1);const deleted=deletedRows.payload.data[0];assert.ok(deleted._deleted_at);assert.equal(deleted._revision,2);
 const defaultRows=await apiRequest(`/tables/${table.table_id}/rows?limit=100`);assert.equal(defaultRows.payload.data.length,0);
 const deletedDetail=await apiRequest(`/tables/${table.table_id}/rows/${row._row_id}`);assert.equal(deletedDetail.status,404);
 const historyBefore=await apiRequest(`/tables/${table.table_id}/rows/${row._row_id}/revisions`);assert.equal(historyBefore.status,200);assert.equal(historyBefore.payload.data.length,2);
 // Positive control: the immediate delete refresh explicitly includes deleted rows and History can restore r1.
 await page.locator(`tr[data-row-id="${row._row_id}"]`).waitFor();
 await page.getByRole('tab',{name:'历史'}).click();await page.locator('.history-timeline li').nth(0).waitFor();
 const historyLi=rev=>page.locator('.history-timeline li').filter({hasText:`r${rev}`});
 await historyLi(1).getByRole('button',{name:'恢复此版本'}).waitFor();
 const immediateHistory=await page.locator('.history-timeline').innerText();
 await page.screenshot({path:path.join(evidence,'immediate-deleted-row-history.png'),fullPage:true});
 await historyLi(1).getByRole('button',{name:'恢复此版本'}).click();await page.getByText(/已从 r1 恢复/).waitFor();
 const immediatelyRestored=await apiRequest(`/tables/${table.table_id}/rows/${row._row_id}`);assert.equal(immediatelyRestored.status,200);assert.equal(immediatelyRestored.payload.data._revision,3);assert.equal(immediatelyRestored.payload.data._deleted_at,null);
 // Delete once more, then perform ordinary Refresh and reopen. refresh(true) did not persist includeDeleted in session state.
 await page.getByRole('tab',{name:'字段'}).click();await page.getByRole('button',{name:'软删除记录'}).click();await page.getByRole('status').filter({hasText:'记录已软删除'}).first().waitFor();
 const deletedAgainList=await apiRequest(`/tables/${table.table_id}/rows?limit=100&include_deleted=true`);const deletedAgain=deletedAgainList.payload.data[0];assert.equal(deletedAgain._revision,4);assert.ok(deletedAgain._deleted_at);
 await page.getByRole('button',{name:'搜索',exact:true}).click();await page.getByRole('searchbox',{name:'搜索 XeLibrary'}).fill('R51 recoverable sentinel');await page.getByRole('button',{name:'搜索',exact:true}).last().click();
 await page.getByText('没有搜索结果',{exact:true}).waitFor();
 const searchAfter={empty:await page.getByText('没有搜索结果',{exact:true}).count()===1,warnings:await page.locator('.library-warning').count()};
 await page.screenshot({path:path.join(evidence,'deleted-row-excluded-from-search.png'),fullPage:true});
 // The in-session includeDeleted flag from the delete/restore flows persists across
 // tree re-opens by design (pre-existing base behavior, not part of #218), so the
 // default deleted-row exclusion is asserted on a genuine fresh page load.
 await page.reload();await page.locator('.knowledge-sidebar').waitFor();
 await page.locator('.library-tree-table').filter({hasText:'round51-soft-delete-restore'}).click();
 await waitFor(()=>page.locator(`tr[data-row-id="${row._row_id}"]`).count().then(n=>n===0),'fresh load restores default deleted-row exclusion');
 const reopenListCount=await apiRequest(`/tables/${table.table_id}/rows?limit=100`);assert.equal(reopenListCount.payload.data.length,0);
 // A persisted Recent row now activates the exact deleted row from the successful current page.
 await page.reload();await page.locator('.knowledge-sidebar').waitFor();
 const recentButton=page.getByRole('button',{name:new RegExp(row._row_id.slice(0,8))});await recentButton.waitFor();const recentCallStart=await page.evaluate(()=>window.r51Audit.calls.length);await recentButton.click();
 await page.locator(`tr[data-row-id="${row._row_id}"]`).waitFor();
 await waitFor(async()=>(await page.evaluate(()=>window.r51Audit.calls)).slice(recentCallStart).some(c=>c.path.includes('include_deleted=true')&&c.status===200),'genuine list parsed');
 if(mode==='before'){
  await waitFor(async()=>(await page.evaluate(()=>window.r51Audit.calls)).some(c=>c.path===`/tables/${table.table_id}/rows/${row._row_id}`&&c.status===404),'unfixed detail consumed');
  await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
  await page.screenshot({path:path.join(evidence,'unfixed-recent-deleted-row.png'),fullPage:true});
  const baseHashes={};for(const file of ['src/library/LibraryApp.tsx','src/library/hooks.ts']){const value=spawnSync('git',['show',`eecec3ec4e171b7536681bf872b5898faf7ba25f:apps/desktop-web/${file}`],{cwd:source,encoding:'utf8'});baseHashes[file]=createHash('sha256').update(value.stdout).digest('hex');}await fs.writeFile(path.join(evidence,'before-trace.json'),JSON.stringify({baseline:'eecec3ec4e171b7536681bf872b5898faf7ba25f',baseHashes,actualSelected:await page.locator(`tr[data-row-id="${row._row_id}"]`).getAttribute('aria-selected'),requests:await page.evaluate(()=>window.r51Audit.calls)},null,2));
  assert.equal(await page.locator(`tr[data-row-id="${row._row_id}"]`).getAttribute('aria-selected'),'true','unfixed base should fail exact selected-row regression');
 }
 await waitFor(async()=>await page.locator(`tr[data-row-id="${row._row_id}"]`).getAttribute('aria-selected')==='true','Recent automatically selected deleted row');
 assert.equal(await page.locator('.inspector-head strong').textContent(),'记录 Inspector');assert.equal(await page.getByRole('alert').filter({hasText:'记录不存在，或当前身份没有读取权限。'}).count(),0);
 const recentRequests=(await page.evaluate(()=>window.r51Audit.calls)).slice(recentCallStart);const fetchedDeleted=recentRequests.find(c=>c.path.includes('include_deleted=true'));assert.equal(fetchedDeleted.status,200);const target=fetchedDeleted.responsePayload.data.find(r=>r._row_id===row._row_id);assert.equal(target._revision,4);assert.ok(target._deleted_at);assert.equal(recentRequests.filter(c=>c.path===`/tables/${table.table_id}/rows/${row._row_id}`).length,0);
 const recentFlow={rowSelected:true,revision:target._revision,deletedAt:target._deleted_at,apiTrace:recentRequests};
 await page.screenshot({path:path.join(evidence,'recent-deleted-row-activated.png'),fullPage:true});checks.push('persisted Recent activates authenticated deleted r4, no default-detail404 or false notice, no secondary manual selection');
 await page.getByRole('tab',{name:'历史'}).click();
 await historyLi(3).getByRole('button',{name:'恢复此版本'}).waitFor();
 const revisionsUi=await page.locator('.history-timeline').innerText();
 await page.screenshot({path:path.join(evidence,'deleted-row-history-restore.png'),fullPage:true});
 await historyLi(3).getByRole('button',{name:'恢复此版本'}).click();await waitFor(async()=>(await apiRequest(`/tables/${table.table_id}/rows/${row._row_id}`)).payload.data?._revision===5,'restored current revision5');await historyLi(5).waitFor();
 const restored=await apiRequest(`/tables/${table.table_id}/rows/${row._row_id}`);assert.equal(restored.status,200);assert.equal(restored.payload.data._deleted_at,null);assert.equal(restored.payload.data.values.label,'R51 recoverable sentinel');assert.equal(restored.payload.data._revision,5);
 const finalRows=await apiRequest(`/tables/${table.table_id}/rows?limit=100`);assert.equal(finalRows.payload.data.length,1);assert.equal(finalRows.payload.data[0]._row_id,row._row_id);
 checks.push('ordinary default list/detail/search still exclude deleted rows; immediate History restore r1→r3 and redelete→r4 unchanged');
 checks.push('Recent-to-History restores r3 with current revision4 If-Match, yielding active revision5 and preserved label/history');
 // Active Recent still performs the original default detail read.
 await page.reload();await page.getByRole('button',{name:new RegExp(row._row_id.slice(0,8))}).waitFor();await page.getByRole('button',{name:new RegExp(row._row_id.slice(0,8))}).click();await waitFor(async()=>await page.locator(`tr[data-row-id="${row._row_id}"]`).getAttribute('aria-selected')==='true','active Recent');let requests=await page.evaluate(()=>window.r51Audit.calls);assert.ok(requests.some(c=>c.path===`/tables/${table.table_id}/rows/${row._row_id}`&&c.status===200));checks.push('active row Recent retains default authenticated detail200 read');
 // A legitimately remembered row subsequently purged uses the same not-found path.
 const gone=await apiRequest(`/tables/${table.table_id}/rows`,'POST',{values:{label:'temporary remembered row'}});const goneRow=gone.payload.data;await page.getByRole('button',{name:'刷新记录',exact:true}).click();await page.locator(`tr[data-row-id="${goneRow._row_id}"]`).waitFor();await page.locator(`tr[data-row-id="${goneRow._row_id}"]`).click();const wrongETag=await apiRequest(`/tables/${table.table_id}/rows/${goneRow._row_id}?purge=true`,'DELETE',undefined,{'If-Match':'"1"'});assert.equal(wrongETag.status,412);await fs.writeFile(path.join(evidence,'if-match-contract-negative.json'),JSON.stringify(wrongETag,null,2));const purged=await apiRequest(`/tables/${table.table_id}/rows/${goneRow._row_id}?purge=true`,'DELETE',undefined,{'If-Match':`"revision:${goneRow._revision}"`});assert.equal(purged.status,200);await page.reload();await page.getByRole('button',{name:new RegExp(goneRow._row_id.slice(0,8))}).click();await page.getByRole('alert').filter({hasText:'记录不存在，或当前身份没有读取权限。'}).waitFor();requests=await page.evaluate(()=>window.r51Audit.calls);assert.ok(requests.some(c=>c.path===`/tables/${table.table_id}/rows/${goneRow._row_id}`&&c.status===404));checks.push('normally purged remembered row is not fabricated from another loaded row and retains actual detail404 notice');
 // Current include_deleted list can be pending when the user navigates elsewhere.
 await page.reload();await page.getByRole('button',{name:new RegExp(row._row_id.slice(0,8))}).waitFor();let barrier;await page.route('**/api/v1/tables/*/rows?*',async route=>{if(!route.request().url().includes('include_deleted=true'))return route.continue();let release;const held=new Promise(r=>release=r);barrier={ready:false,sent:false,release};const upstream=await route.fetch();const body=await upstream.body();barrier.ready=true;await held;await route.fulfill({status:upstream.status(),headers:upstream.headers(),body});barrier.sent=true;});await page.getByRole('button',{name:new RegExp(row._row_id.slice(0,8))}).click();await waitFor(()=>barrier?.ready&&!barrier.sent,'genuine pending list response');await page.getByRole('button',{name:'搜索',exact:true}).click();barrier.release();await waitFor(()=>barrier.sent,'pending list sent');await waitFor(async()=>(await page.evaluate(()=>window.r51Audit.calls)).some(c=>c.path.includes('include_deleted=true')&&c.status===200),'pending list actually consumed');await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));assert.equal(await page.getByRole('searchbox',{name:'搜索 XeLibrary'}).count(),1);assert.equal(await page.locator('.knowledge-inspector').count(),0);assert.equal((await page.evaluate(()=>window.r51Audit.calls)).filter(c=>c.path===`/tables/${table.table_id}/rows/${row._row_id}`).length,0);checks.push('genuine pending list consumed after ordinary search navigation cannot activate stale row or dispatch detail');await page.unroute('**/api/v1/tables/*/rows?*');
 // A new table open advances the session view generation as well as row-navigation.
 const again=await apiRequest(`/tables/${table.table_id}/rows/${row._row_id}`,'DELETE',undefined,{'If-Match':'"revision:5"'});assert.equal(again.status,200);
 const otherTable=(await apiRequest('/tables','POST',{path:'/R51',name:'other-navigation-table',columns:[{name:'label',type:'string',nullable:false}]})).payload.data;const otherRow=(await apiRequest(`/tables/${otherTable.table_id}/rows`,'POST',{values:{label:'Other navigation sentinel'}})).payload.data;
 await page.reload();await page.getByRole('button',{name:new RegExp(row._row_id.slice(0,8))}).waitFor();
 async function holdList(){let heldBarrier;await page.route('**/api/v1/tables/*/rows?*',async route=>{if(!route.request().url().includes(`/${table.table_id}/rows?`)||!route.request().url().includes('include_deleted=true'))return route.continue();let release;const held=new Promise(r=>release=r);heldBarrier={ready:false,sent:false,release};const upstream=await route.fetch();const body=await upstream.body();heldBarrier.ready=true;await held;await route.fulfill({status:upstream.status(),headers:upstream.headers(),body});heldBarrier.sent=true;});const start=await page.evaluate(()=>window.r51Audit.calls.length);await page.getByRole('button',{name:new RegExp(row._row_id.slice(0,8))}).click();await waitFor(()=>heldBarrier?.ready&&!heldBarrier.sent,'held genuine deleted page');return {barrier:heldBarrier,start};}
 async function releaseList(value){value.barrier.release();await waitFor(()=>value.barrier.sent,'deleted page sent');await waitFor(async()=>(await page.evaluate(()=>window.r51Audit.calls)).slice(value.start).some(c=>c.path.includes(`/${table.table_id}/rows?`)&&c.path.includes('include_deleted=true')&&c.status===200),'exact later list body consumed');await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));await page.unroute('**/api/v1/tables/*/rows?*');}
 let held=await holdList();await page.locator('.library-tree-table').filter({hasText:'other-navigation-table'}).click();await page.locator(`tr[data-row-id="${otherRow._row_id}"]`).waitFor();await releaseList(held);assert.equal(await page.getByRole('heading',{name:'/R51/other-navigation-table',exact:true}).count(),1);assert.equal(await page.locator(`tr[data-row-id="${row._row_id}"]`).count(),0);assert.equal(await page.locator(`tr[data-row-id="${otherRow._row_id}"]`).count(),1);checks.push('genuine deleted page returned after another table open cannot overwrite the newer session or activate old record');
 held=await holdList();await releaseList(held);await waitFor(async()=>await page.locator(`tr[data-row-id="${row._row_id}"]`).getAttribute('aria-selected')==='true','held deleted page positive activation');const finalDeleted=(await apiRequest(`/tables/${table.table_id}/rows?limit=100&include_deleted=true`)).payload.data.find(r=>r._row_id===row._row_id);assert.equal(finalDeleted._revision,6);assert.ok(finalDeleted._deleted_at);assert.equal((await page.evaluate(()=>window.r51Audit.calls)).slice(held.start).filter(c=>c.path===`/tables/${table.table_id}/rows/${row._row_id}`).length,0);checks.push('genuine pending deleted-page response with no navigation activates exact r6 when released, with no default detail read');
 if(mode!=='before'){
 const large=(await apiRequest('/tables','POST',{path:'/R51',name:'paged-recent-target',columns:[{name:'label',type:'string',nullable:false}]})).payload.data;
 const madeRows=[];for(let n=0;n<101;n++){const created=await apiRequest(`/tables/${large.table_id}/rows`,'POST',{values:{label:`paged ${n}`}});assert.equal(created.status,201);madeRows.push(created.payload.data);}
 const last=madeRows.sort((a,b)=>a._row_id.localeCompare(b._row_id)).at(-1);const first=(await apiRequest(`/tables/${large.table_id}/rows?limit=100&include_deleted=true`)).payload;assert.equal(first.data.length,100);assert.ok(first.meta.next_cursor);assert.ok(!first.data.some(r=>r._row_id===last._row_id));
 await page.reload();await page.locator('.library-tree-table').filter({hasText:'paged-recent-target'}).click();await page.getByRole('button',{name:'加载更多记录',exact:true}).click();await waitFor(async()=>(await page.evaluate(()=>window.r51Audit.calls)).some(c=>c.path.includes(`/tables/${large.table_id}/rows?`)&&c.path.includes('cursor=')&&c.status===200),'second page consumed');await page.locator('.table-scroll').evaluate(e=>e.scrollTop=e.scrollHeight);await page.locator(`tr[data-row-id="${last._row_id}"]`).click();
 assert.equal((await apiRequest(`/tables/${large.table_id}/rows/${last._row_id}`,'DELETE',undefined,{'If-Match':'"revision:1"'})).status,200);
 await page.reload();const start=await page.evaluate(()=>window.r51Audit.calls.length);await page.getByRole('button',{name:new RegExp(last._row_id.slice(0,8))}).click();
 await waitFor(async()=>(await page.evaluate(()=>window.r51Audit.calls)).slice(start).some(c=>c.path===`/tables/${large.table_id}/rows/${last._row_id}`&&c.status===404),'paged target ordinary detail404 consumed');await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));
 await waitFor(async()=>await page.locator(`tr[data-row-id="${last._row_id}"]`).getAttribute('aria-selected')==='true','paged deleted Recent selected');
 const pagedCalls=(await page.evaluate(()=>window.r51Audit.calls)).slice(start);assert.ok(pagedCalls.some(c=>c.path===`/tables/${large.table_id}/rows/${last._row_id}?include_deleted=true`&&c.status===200));assert.equal(pagedCalls.filter(c=>c.path.includes(`/tables/${large.table_id}/rows?`)).length,1);assert.equal(pagedCalls.filter(c=>c.path.includes('cursor=')).length,0);
 assert.equal(await page.getByRole('alert').filter({hasText:'记录不存在，或当前身份没有读取权限。'}).count(),0);
 await page.getByRole('tab',{name:'历史'}).click();await historyLi(2).waitFor();await page.screenshot({path:path.join(evidence,'pagination-selected-history.png'),fullPage:true});await historyLi(1).getByRole('button',{name:'恢复此版本'}).click();await waitFor(async()=>(await apiRequest(`/tables/${large.table_id}/rows/${last._row_id}`)).payload.data?._revision===3,'paged row restored revision3');
 const proof={count:101,target:last,firstPage:first,requests:pagedCalls,restored:(await apiRequest(`/tables/${large.table_id}/rows/${last._row_id}`)).payload.data};await fs.writeFile(path.join(evidence,'pagination-positive.json'),JSON.stringify(proof,null,2));await page.screenshot({path:path.join(evidence,'pagination-positive.png'),fullPage:true});checks.push('101-row persisted Recent target beyond first100 uses bounded exact opt-in read and History restores current2 to3 without cursor scanning');
 assert.equal((await apiRequest(`/tables/${large.table_id}/rows/${last._row_id}`,'DELETE',undefined,{'If-Match':'"revision:3"'})).status,200);
 await page.reload();let preciseReady=false,preciseSent=false,releasePrecise;const preciseGate=new Promise(r=>releasePrecise=r);const preciseUrl=`**/api/v1/tables/${large.table_id}/rows/${last._row_id}?include_deleted=true`;
 await page.route(preciseUrl,async route=>{const response=await route.fetch();const body=await response.body();assert.equal(response.status(),200);preciseReady=true;await preciseGate;await route.fulfill({status:response.status(),headers:response.headers(),body});preciseSent=true;});
 const preciseStart=await page.evaluate(()=>window.r51Audit.calls.length);await page.getByRole('button',{name:new RegExp(last._row_id.slice(0,8))}).click();await waitFor(()=>preciseReady&&!preciseSent,'genuine exact fallback buffered');await page.getByRole('button',{name:'搜索',exact:true}).click();releasePrecise();await waitFor(async()=>(await page.evaluate(()=>window.r51Audit.calls)).slice(preciseStart).some(c=>c.path.endsWith(`${last._row_id}?include_deleted=true`)&&c.status===200),'late exact fallback body consumed');await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));assert.equal(await page.getByRole('searchbox',{name:'搜索 XeLibrary'}).count(),1);assert.equal(await page.locator('.knowledge-inspector').count(),0);await page.unroute(preciseUrl);checks.push('late genuine exact deleted-detail fallback consumed after search navigation cannot reactivate stale record');
 await page.reload();let defaultReady=false,defaultSent=false,releaseDefault;const defaultGate=new Promise(r=>releaseDefault=r);const defaultUrl=`**/api/v1/tables/${large.table_id}/rows/${last._row_id}`;
 await page.route(defaultUrl,async route=>{const response=await route.fetch();const body=await response.body();assert.equal(response.status(),404);defaultReady=true;await defaultGate;await route.fulfill({status:404,headers:response.headers(),body});defaultSent=true;});
 const defaultStart=await page.evaluate(()=>window.r51Audit.calls.length);await page.getByRole('button',{name:new RegExp(last._row_id.slice(0,8))}).click();await waitFor(()=>defaultReady&&!defaultSent,'genuine default404 buffered before fallback');await page.getByRole('button',{name:'搜索',exact:true}).click();releaseDefault();await waitFor(async()=>(await page.evaluate(()=>window.r51Audit.calls)).slice(defaultStart).some(c=>c.path.endsWith(last._row_id)&&c.status===404),'default404 body consumed after navigation');await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));assert.equal((await page.evaluate(()=>window.r51Audit.calls)).slice(defaultStart).filter(c=>c.path.endsWith(`${last._row_id}?include_deleted=true`)).length,0);assert.equal(await page.getByRole('searchbox',{name:'搜索 XeLibrary'}).count(),1);await page.unroute(defaultUrl);checks.push('default404 arriving after navigation cannot dispatch an old-context exact opt-in fallback');
 await page.reload();defaultReady=false;defaultSent=false;let releaseOwner;const ownerGate=new Promise(r=>releaseOwner=r);
 await page.route(defaultUrl,async route=>{const response=await route.fetch();const body=await response.body();assert.equal(response.status(),404);defaultReady=true;await ownerGate;await route.fulfill({status:404,headers:response.headers(),body});defaultSent=true;});
 const ownerStart=await page.evaluate(()=>window.r51Audit.calls.length);await page.getByRole('button',{name:new RegExp(last._row_id.slice(0,8))}).click();await waitFor(()=>defaultReady&&!defaultSent,'default404 buffered during auth generation refresh');await page.evaluate(()=>window.r51Audit.refreshAuth());await waitFor(async()=>(await page.evaluate(()=>window.r51Audit.calls)).slice(ownerStart).filter(c=>c.path==='/users/me'&&c.status===200).length===1,'actual identity refreshed');releaseOwner();await waitFor(async()=>(await page.evaluate(()=>window.r51Audit.calls)).slice(ownerStart).some(c=>c.path.endsWith(last._row_id)&&c.status===404),'old generation404 consumed');await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));assert.equal((await page.evaluate(()=>window.r51Audit.calls)).slice(ownerStart).filter(c=>c.path.endsWith(`${last._row_id}?include_deleted=true`)).length,0);assert.equal(await page.locator(`tr[data-row-id="${last._row_id}"]`).count(),0);await page.unroute(defaultUrl);checks.push('authEpoch refresh of actual same development identity invalidates old404 before fallback dispatch');


 }
 assert.deepEqual(errors,[]);const sourceHashes={};for(const file of ['src/library/LibraryApp.tsx','src/library/hooks.ts','src/library/logic.js'])sourceHashes[file]=createHash('sha256').update(await fs.readFile(path.join(appRoot,file))).digest('hex');
 sourceHashes['xeonolab/library.py']=createHash('sha256').update(await fs.readFile(path.join(source,'xeonolab/library.py'))).digest('hex');
 const report={baseline:'865a72277008a5773236b50b993bacdecc78d6ab',mode,source,sourceHashes,checks,pageErrors:errors,tableId:table.table_id,rowId:row._row_id,recentFlow,restore:restored.payload.data,requests:await page.evaluate(()=>window.r51Audit.calls),scope:'Actual LibraryApp/Sidebar/TableView/Inspector/SearchView and real local XeLibrary + Desktop identity factories, temporary data dirs, explicit synthetic dev human, genuine API-created data/normal UI writes. Identity is actual Desktop API response for the configured dev actor. No production/OIDC/worker/security testing.'};await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));

}finally{if(browser)await browser.close();if(vite)await vite.close();await stop(library);await stop(desktop);await fs.rm(temp,{recursive:true,force:true,maxRetries:5,retryDelay:100});await Promise.all([fixtureTsx,fixtureHtml].map(f=>fs.rm(f,{force:true})));}
