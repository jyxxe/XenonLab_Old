import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import net from 'node:net';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium,expect} from '@playwright/test';
const appRoot=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),root=path.resolve(appRoot,'../..');
const evidence=path.join(root,'runtime/verification/issue241');
const dialog=page=>page.getByRole('dialog',{name:'选择同表已有附件',exact:true});
const list=page=>dialog(page).locator('.existing-attachment-list li');
const choose=page=>dialog(page).getByRole('button',{name:/^选择 .*（来源：/});
const calls=page=>page.evaluate(()=>window.__picker.calls);
const metadataCalls=async page=>(await calls(page)).filter(call=>/^\/tables\/[^/]+\/attachments\?/.test(call.path));
async function freePort(){const server=net.createServer();await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));const port=server.address().port;await new Promise(resolve=>server.close(resolve));return port}
async function waitFor(fn){for(let i=0;i<200;i++){try{if(await fn())return}catch{}await new Promise(resolve=>setTimeout(resolve,50))}throw Error('local API did not become ready')}

test('existing attachment picker actual component discovery, fencing and real local API',{timeout:120000},async t=>{
 const chrome=process.env.CHROME_BIN||chromium.executablePath();assert.ok(existsSync(chrome),`Chrome required: ${chrome}`);await fs.mkdir(evidence,{recursive:true});const temp=await fs.mkdtemp(path.join(os.tmpdir(),'xeonolab-picker-')),apiPort=await freePort(),manifestPath=path.join(temp,'manifest.json');
 const backend=spawn('uv',['run','python','apps/desktop-web/tests/attachment-picker-local-api.py','--data-dir',path.join(temp,'data'),'--manifest',manifestPath,'--port',String(apiPort)],{cwd:root,stdio:['ignore','pipe','pipe']});let server,browser;const report={checks:[],pageErrors:[],externalRequests:[],backendOutput:''};backend.stdout.on('data',data=>report.backendOutput+=data);backend.stderr.on('data',data=>report.backendOutput+=data);
 try{
  await waitFor(async()=>existsSync(manifestPath)&&(await fetch(`http://127.0.0.1:${apiPort}/health/live`)).ok);
  const manifest=JSON.parse(await fs.readFile(manifestPath,'utf8'));server=await createServer({root:appRoot,server:{host:'127.0.0.1',port:0,watch:null,proxy:{'/api/v1':`http://127.0.0.1:${apiPort}`}},plugins:[{name:'picker-manifest',configureServer(vite){vite.middlewares.use('/attachment-picker-manifest.json',async(_request,response)=>{response.setHeader('Content-Type','application/json');response.end(await fs.readFile(manifestPath,'utf8'))})}}]});await server.listen();const origin=server.resolvedUrls.local[0];browser=await chromium.launch({executablePath:chrome,headless:true});
  const open=async(query='',width=1050)=>{const page=await browser.newPage({viewport:{width,height:850}});page.on('pageerror',error=>report.pageErrors.push(String(error)));await page.route('**/*',route=>{if(route.request().url().startsWith(origin))return route.continue();report.externalRequests.push(route.request().url());return route.abort()});await page.goto(origin+'tests/attachment-picker-fixture.html?'+query);return page};
  const openPicker=async page=>{await page.getByRole('button',{name:'选择已有附件',exact:true}).click();await expect(dialog(page)).toBeVisible()};
  await t.test('explicit pages, filename/source search, error retry and bind only on selection',async()=>{
   const page=await open();try{
    await expect(page.getByText('当前记录没有附件。',{exact:true})).toBeVisible();assert.equal((await metadataCalls(page)).length,0);await openPicker(page);await expect(list(page)).toHaveCount(25);await expect(dialog(page).getByRole('status').first()).toContainText('已加载 25 项，还有候选');assert.equal((await metadataCalls(page)).length,1);await expect(list(page).first()).toContainText('application/pdf · 1,234 字节');await expect(list(page).first()).toContainText('来源记录：Readable source');
    await page.evaluate(()=>window.__picker.failPage=true);await dialog(page).getByRole('button',{name:'加载更多已有附件'}).click();await expect(dialog(page).getByRole('alert')).toContainText('synthetic metadata read failed');await expect(list(page)).toHaveCount(25);await dialog(page).getByRole('button',{name:'重试读取已有附件'}).click();await expect(list(page)).toHaveCount(27);await expect(dialog(page).getByRole('button',{name:'加载更多已有附件'})).toHaveCount(0);assert.equal((await metadataCalls(page)).length,3);assert.equal((await metadataCalls(page))[1].path,(await metadataCalls(page))[2].path);
    await dialog(page).getByLabel('按文件名或来源记录搜索').fill('FINAL SOURCE');await dialog(page).getByRole('button',{name:'搜索已有附件'}).click();await expect(list(page)).toHaveCount(1);await expect(list(page)).toContainText('Final 图解.png');await expect(list(page)).toContainText('image/png');await dialog(page).getByLabel('按文件名或来源记录搜索').fill('absent');await dialog(page).getByRole('button',{name:'搜索已有附件'}).click();await expect(dialog(page).getByText('没有匹配的可读附件。')).toBeVisible();await dialog(page).getByRole('button',{name:'清除搜索'}).click();await expect(list(page)).toHaveCount(25);assert.ok((await calls(page)).filter(call=>!call.path.includes('/users/me/preferences')).every(call=>call.method==='GET'),'discovery and Inspector stay read-only; only the account preference sync may write');
    await page.evaluate(()=>window.__picker.failBind=true);await choose(page).first().click();await expect(dialog(page).getByRole('alert')).toContainText('synthetic binding denied');await expect(list(page)).toHaveCount(25);await choose(page).first().click();await expect(dialog(page)).toHaveCount(0);await expect(page.locator('.attachment-panel>ul')).toContainText('proof-01.pdf');assert.equal((await calls(page)).filter(call=>call.method==='POST').length,2);assert.ok((await calls(page)).every(call=>!call.path.includes('/content')&&!call.path.includes('/preview')));assert.equal(await page.evaluate(()=>window.__picker.changed),1);
    report.checks.push('Explicit25+2/retained error page/exact retry/no auto-more; case filename/source/noresult/clear; denied bind retry; metadata-only preloading');
   }finally{await page.close()}
  });
  await t.test('search restart rejects old successful and failed page responses',async()=>{
   for(const reject of [false,true]){const page=await open();try{
    await page.evaluate(()=>window.__picker.hold=true);await openPicker(page);await expect.poll(()=>page.evaluate(()=>window.__picker.pending.length)).toBe(1);await page.evaluate(()=>window.__picker.hold=false);await dialog(page).getByLabel('按文件名或来源记录搜索').fill('Final');await dialog(page).getByRole('button',{name:'搜索已有附件'}).click();await expect(list(page)).toHaveCount(1);await page.evaluate(reject=>{const old=window.__picker.pending.shift();if(reject)old.reject(Error('old identity metadata rejected'));else old.resolve()},reject);await expect(list(page)).toHaveCount(1);await expect(dialog(page).getByRole('alert')).toHaveCount(0);await expect(list(page)).toContainText('Final 图解.png');
   }finally{await page.close()}}
   report.checks.push('Query generation fences late success and late error without clearing new results');
  });
  await t.test('row/table/identity changes dismiss old picker and ignore stale pages',async()=>{
   for(const patch of [{row:'another-target'},{table:'t2'},{epoch:1}]){const page=await open();try{
    await page.evaluate(()=>window.__picker.hold=true);await openPicker(page);await expect.poll(()=>page.evaluate(()=>window.__picker.pending.length)).toBe(1);await page.evaluate(patch=>{window.__picker.hold=false;window.__picker.switch(patch)},patch);await expect(dialog(page)).toHaveCount(0);await openPicker(page);await expect(list(page)).toHaveCount(25);await page.evaluate(()=>window.__picker.pending.shift().reject(Error('stale context rejected')));await expect(list(page)).toHaveCount(25);await expect(dialog(page).getByRole('alert')).toHaveCount(0);assert.ok((await calls(page)).filter(call=>!call.path.includes('/users/me/preferences')).every(call=>call.method==='GET'),'discovery and Inspector stay read-only; only the account preference sync may write');
   }finally{await page.close()}}
   report.checks.push('Target row/table/auth epoch remount discards old modal/query; late rejected old requests cannot change new candidates');
  });
  await t.test('completed old bind cannot refresh or write a newly selected target',async()=>{
   const page=await open();try{
    await openPicker(page);await expect(list(page)).toHaveCount(25);await page.evaluate(()=>window.__picker.holdBind=true);await choose(page).first().click();await expect.poll(()=>page.evaluate(()=>window.__picker.pendingBind.length)).toBe(1);await page.evaluate(()=>{window.__picker.holdBind=false;window.__picker.switch({row:'replacement-target'})});await expect(dialog(page)).toHaveCount(0);await page.evaluate(()=>window.__picker.pendingBind.shift().resolve());await expect(page.getByText('当前记录没有附件。',{exact:true})).toBeVisible();assert.equal((await calls(page)).filter(call=>call.method==='POST').length,1);assert.ok((await calls(page)).filter(call=>call.method==='POST')[0].path.includes('/rows/target/'));assert.equal(await page.evaluate(()=>window.__picker.changed),0);assert.ok(!(await calls(page)).some(call=>call.method==='POST'&&call.path.includes('replacement-target')));
    report.checks.push('Late original bind remains scoped to explicit original target; no new-target write/refresh/message after remount');
   }finally{await page.close()}
  });
  await t.test('LibraryApp Inspector uses auth epoch fencing with unchanged API helper',async()=>{
   const page=await open('library=1');try{
    await page.locator('button.library-tree-table').click();await page.locator('tr[data-row-id="target"]').click();await page.evaluate(()=>window.__picker.hold=true);await openPicker(page);await expect.poll(()=>page.evaluate(()=>window.__picker.pending.length)).toBe(1);await page.evaluate(()=>{window.__picker.hold=false;window.__picker.switch({epoch:1})});await expect(dialog(page)).toHaveCount(0);await page.locator('button.library-tree-table').click();await page.locator('tr[data-row-id="target"]').click();await openPicker(page);await expect(list(page)).toHaveCount(25);await page.evaluate(()=>window.__picker.pending.shift().resolve());await expect(list(page)).toHaveCount(25);assert.ok((await calls(page)).filter(call=>!call.path.includes('/users/me/preferences')).every(call=>call.method==='GET'),'discovery and Inspector stay read-only; only the account preference sync may write');
    report.checks.push('Real LibraryApp→Inspector→Attachments same-helper authEpoch invalidates pending discovery');
   }finally{await page.close()}
  });
  await t.test('keyboard and narrow long metadata layout remain operable',async()=>{
   const page=await open('long=1',380);try{
    const trigger=page.getByRole('button',{name:'选择已有附件',exact:true});await trigger.focus();await page.keyboard.press('Enter');await expect(list(page)).toHaveCount(25);const close=dialog(page).getByRole('button',{name:'关闭对话框'});await expect(close).toBeFocused();await page.keyboard.press('Escape');await expect(dialog(page)).toHaveCount(0);await expect(trigger).toBeFocused();await page.keyboard.press('Space');await expect(list(page)).toHaveCount(25);await dialog(page).evaluate(element=>Promise.all(element.getAnimations().map(animation=>animation.finished)));await page.screenshot({path:path.join(evidence,'picker-narrow.png'),fullPage:true});assert.equal(await dialog(page).evaluate(element=>element.scrollWidth<=element.clientWidth+1),true);await choose(page).first().focus();await page.keyboard.press('Enter');await expect(dialog(page)).toHaveCount(0);await expect(page.locator('.attachment-panel>ul')).toContainText('证据');
    report.checks.push('Named modal/choice; native Enter/Space, Escape focus restoration,380px no horizontal overflow');
   }finally{await page.close()}
  });
  await t.test('real API paging, readable metadata, target/source ACL and preserved manual/upload/download/unbind',async()=>{
   const page=await open('live=1');try{
    await openPicker(page);await expect(list(page)).toHaveCount(25);await expect(dialog(page).locator('.existing-attachment-list')).not.toContainText('hidden-private.pdf');await dialog(page).getByRole('button',{name:'加载更多已有附件'}).click();await expect(list(page)).toHaveCount(27);await expect(dialog(page).getByRole('status').first()).toContainText('当前查询读取结束');await dialog(page).getByLabel('按文件名或来源记录搜索').fill('source second');await dialog(page).getByRole('button',{name:'搜索已有附件'}).click();await expect(list(page)).toHaveCount(1);await expect(list(page)).toContainText('proof-27.pdf');await expect(list(page)).toContainText('application/pdf');await expect(list(page)).toContainText('字节');await page.screenshot({path:path.join(evidence,'picker-live.png'),fullPage:true});await choose(page).first().click();await expect(dialog(page)).toHaveCount(0);await expect(page.locator('.attachment-panel>ul')).toContainText('proof-27.pdf');
    const binds=(await calls(page)).filter(call=>call.method==='POST');assert.equal(binds.length,1);assert.ok(binds[0].path.includes('/rows/'+manifest.target+'/'));assert.ok((await calls(page)).every(call=>!call.path.includes('/content')&&!call.path.includes('/preview')));
    await page.getByRole('button',{name:'鉴权下载',exact:true}).click();await expect.poll(async()=> (await calls(page)).filter(call=>call.path.includes('/content')).length).toBe(1);await page.getByRole('button',{name:'解除关联',exact:true}).click();await expect(page.getByText('当前记录没有附件。',{exact:true})).toBeVisible();await page.getByLabel('关联此表中已有附件 ID').fill(manifest.source_attachment);await page.getByRole('button',{name:'关联附件',exact:true}).click();await expect(page.locator('.attachment-panel>ul')).toContainText('proof-01.pdf');await page.getByLabel('上传附件（大于 16 MiB 时分片，最大 2 GiB）').setInputFiles({name:'extra-proof.pdf',mimeType:'application/pdf',buffer:Buffer.from('%PDF-upload-synthetic')});await expect(page.locator('.attachment-panel>ul')).toContainText('extra-proof.pdf');
    // Real source remains; objects reused by binds; denial re-evaluated by existing write endpoint.
    const check=async(path,token=manifest.token,init={})=>fetch(`http://127.0.0.1:${apiPort}/api/v1${path}`,{...init,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'}});
    const source=await(await check(`/tables/${manifest.table_id}/rows/${manifest.source_second}/attachments`)).json();assert.equal(source.data.length,1);assert.equal(source.data[0].filename,'proof-27.pdf');assert.equal((await check(`/tables/${manifest.table_id}/attachments`,'picker-outsider')).status,403);assert.equal((await check(`/tables/${manifest.table_id}/rows/${manifest.target}/attachments`, 'picker-reader',{method:'POST',body:JSON.stringify({attachment_id:manifest.source_attachment})})).status,403);assert.equal((await check(`/tables/${manifest.table_id}/rows/${manifest.target}/attachments`,manifest.token,{method:'POST',body:JSON.stringify({attachment_id:manifest.hidden_attachment})})).status,403);
    await page.evaluate(config=>window.__picker.switch({row:config.denied_target}),manifest);await openPicker(page);await expect(list(page)).toHaveCount(25);await choose(page).first().click();await expect(dialog(page).getByRole('alert')).toContainText('HTTP 403');assert.equal(await dialog(page).count(),1);
    report.checks.push('Real loopback candidate API25+2/search metadata/hidden exclusion/explicit bind/source kept; source/target/scope/outsider403; manual bind/upload/download/unbind remain');
   }finally{await page.close()}
  });
  assert.deepEqual(report.pageErrors,[]);assert.deepEqual(report.externalRequests,[]);
 }finally{
  if(browser)await browser.close();if(server)await server.close();backend.kill('SIGTERM');if(backend.exitCode===null)await new Promise(resolve=>{backend.once('exit',resolve);setTimeout(()=>{backend.kill('SIGKILL');resolve()},3000).unref()});await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify(report,null,2)+'\n');await fs.rm(temp,{recursive:true,force:true});
 }
});
