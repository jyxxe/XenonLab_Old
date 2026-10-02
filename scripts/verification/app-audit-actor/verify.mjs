import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import net from 'node:net';
import {existsSync, mkdirSync, writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
const helper = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(helper, '../../../');
const output = path.join(repoRoot, 'runtime/verification/app-audit-actor');
mkdirSync(output, {recursive: true});
const appRoot = path.join(repoRoot, 'apps/desktop-web');
const require = createRequire(path.join(appRoot, 'package.json'));
const {createServer} = require('vite');
const {chromium} = require('@playwright/test');
const dataDir = await fs.mkdtemp(path.join(os.tmpdir(), 'xeonolab-round35-desktop-'));
const fixturePath = path.join(appRoot, 'tests/r35-appregistry-fixture.tsx');
const htmlPath = path.join(appRoot, 'r35-appregistry-fixture.html');
async function freePort() {
  const server = net.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}
const apiPort = await freePort();
const webPort = await freePort();
const serverPath = path.join(helper, 'local_api.py');
const child = spawn('uv', ['run', 'python', serverPath], {
  cwd: repoRoot,
  env: {...process.env, APP_AUDIT_DATA_DIR: dataDir, APP_AUDIT_API_PORT: String(apiPort)},
  stdio: ['ignore', 'pipe', 'pipe'],
});
let backendSource='';
child.stdout.on('data', bytes=>{backendSource+=bytes.toString();});
let stderr = '';
child.stderr.on('data', bytes => { stderr += bytes.toString(); });
const fixture = `import React from 'react';
import {createRoot} from 'react-dom/client';
import {AppRegistry} from '../src/AppRegistry';
import '../src/design-system/tokens.css'; import '../src/design-system/primitives.css'; import '../src/style.css';
(window as any).__activeR35Token='r35-admin-a-token';
async function api(path:string,init:RequestInit={}) { const headers=new Headers(init.headers); headers.set('Authorization','Bearer '+(window as any).__activeR35Token); if(init.body&&!headers.has('Content-Type'))headers.set('Content-Type','application/json'); const response=await fetch('/api/v1'+path,{...init,headers}); const body=await response.json(); if(!response.ok)throw new Error(body.error?.message||'HTTP '+response.status); return body.data; }
(window as any).r35SetActor=(token:string)=>{(window as any).__activeR35Token=token};
createRoot(document.getElementById('root')!).render(<AppRegistry api={api} changed={async()=>{}}/>);`;
writeFileSync(fixturePath, fixture);
writeFileSync(htmlPath, '<!doctype html><html><head><meta charset="utf-8"></head><body><div id="root"></div><script type="module" src="/tests/r35-appregistry-fixture.tsx"></script></body></html>');
const vite = await createServer({root: appRoot, configFile: false, server: {host: '127.0.0.1', port: webPort, strictPort: true, watch: null, proxy: {'/api/v1': `http://127.0.0.1:${apiPort}`}}});
let browser;
let apiLog = '';
async function waitForApi() {
  for (let i=0; i<100; i++) {
    try { const response=await fetch(`http://127.0.0.1:${apiPort}/health/ready`); if(response.ok)return; } catch {}
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw new Error('Local Desktop API did not start: '+apiLog+'\n'+stderr);
}
try {
  await waitForApi();
  await vite.listen();
  const executablePath = process.env.CHROME_BIN || ['/usr/bin/google-chrome','/usr/bin/chromium','/usr/bin/chromium-browser'].find(existsSync);
  assert.ok(executablePath, 'Chrome/Chromium is required');
  browser=await chromium.launch({executablePath,headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
  const page=await browser.newPage({locale:'en-US',viewport:{width:1440,height:950}});
  const requests=[]; const pageErrors=[]; const apiResponses=[];
  page.on('pageerror',error=>pageErrors.push(error.message));
  page.on('request',request=>{if(request.url().includes('/api/v1/'))requests.push({method:request.method(),path:new URL(request.url()).pathname+new URL(request.url()).search,body:request.postDataJSON()||null});});
  page.on('response',async response=>{if(response.url().includes('/api/v1/'))apiResponses.push({status:response.status(),url:response.url(),body:response.status()>=400?await response.text():undefined});});
  await page.goto(`http://127.0.0.1:${webPort}/r35-appregistry-fixture.html`);
  await page.getByRole('heading',{name:'应用管理'}).waitFor();
  const details=page.locator('.registry-panel details');
  await page.getByText('应用目录审计',{exact:true}).focus();await page.keyboard.press('Enter');
  await Promise.all([page.waitForResponse(r=>r.url().endsWith('/users/me/app-audit')),page.getByRole('button',{name:'读取记录'}).click()]);
  assert.equal(await details.locator('li').count(),0,'actual empty audit has no fabricated row');

  await page.getByRole('button',{name:'新增应用'}).click();
  await page.getByLabel('应用标识').fill('r35-audit-example');
  await page.getByLabel('名称').fill('R35 合成研究入口');
  await page.getByLabel('应用来源').fill(`http://127.0.0.1:${apiPort}`);
  await page.getByLabel('入口路径').fill('/');
  await page.getByRole('button',{name:'保存应用'}).click();
  await page.locator('.registry-panel [role=status]').waitFor();
  if(!((await page.locator('.registry-panel [role=status]').innerText()).includes('应用目录已更新')))throw new Error('Create status: '+await page.locator('.registry-panel [role=status]').innerText()+'; requests='+JSON.stringify(requests)+'; responses='+JSON.stringify(apiResponses));
  await page.evaluate(()=>window.r35SetActor('r35-admin-b-token'));
  await page.getByRole('button',{name:/R35 合成研究入口/}).click();
  await page.getByLabel('名称').fill('R35 合成研究入口 已审核');
  await page.getByRole('button',{name:'保存应用'}).click();
  await page.getByRole('status').filter({hasText:'应用目录已更新'}).waitFor();
  await page.getByRole('button',{name:'读取记录'}).click();
  await page.getByText(/app\.updated/).waitFor();
  await page.screenshot({path:path.join(output,'app-audit-two-admins.png'),fullPage:true});
  const uiAuditText=await page.locator('.registry-panel details').innerText();
  const apiResponse=await fetch(`http://127.0.0.1:${apiPort}/api/v1/users/me/app-audit`,{headers:{Authorization:'Bearer r35-admin-b-token'}});
  assert.equal(apiResponse.status,200);
  const apiBody=await apiResponse.json();
  const events=apiBody.data;
  assert.deepEqual(events.map(event=>event.event),['app.updated','app.created']);
  assert.deepEqual(events.map(event=>event.actor),['r35-admin-b','r35-admin-a']);
  assert.deepEqual(events.map(event=>event.target),['r35-audit-example','r35-audit-example']);
  assert.ok(uiAuditText.includes('app.updated')&&uiAuditText.includes('app.created'));
  const rows=await details.locator('li').allInnerTexts();
  for(let i=0;i<events.length;i++) {
    assert.equal(rows[i],`${new Date(events[i].at*1000).toLocaleString('en-US')} · ${events[i].event} · ${events[i].target} · 操作人 ID：${events[i].actor}`);
  }
  const readsStart=requests.length;
  await page.route('**/api/v1/users/me/app-audit',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{message:'Synthetic temporary audit read failure'}})}),{times:1});
  await page.getByRole('button',{name:'读取记录'}).click();
  await page.getByRole('status').filter({hasText:'Synthetic temporary audit read failure'}).waitFor();
  assert.deepEqual(await details.locator('li').allInnerTexts(),rows,'failed read retains previous actor rows');
  await Promise.all([page.waitForResponse(r=>r.url().endsWith('/users/me/app-audit')&&r.status()===200),page.getByRole('button',{name:'读取记录'}).click()]);
  assert.deepEqual(await details.locator('li').allInnerTexts(),rows,'retry recovers real API data');
  assert.ok(requests.slice(readsStart).every(r=>r.method==='GET'&&r.path==='/api/v1/users/me/app-audit'));
  // An ordinary third admin updates the app; this ID has no directory/name mapping.
  await page.evaluate(()=>window.r35SetActor('r35-admin-c-token'));
  await page.getByRole('button',{name:/R35 合成研究入口/}).click();
  await page.getByLabel('名称').fill('R35 合成研究入口 final');
  const updateResponse=page.waitForResponse(r=>r.request().method()==='PATCH'&&r.url().includes('/apps/'));
  await page.getByRole('button',{name:'保存应用'}).click();assert.equal((await updateResponse).status(),200);
  await Promise.all([page.waitForResponse(r=>r.url().endsWith('/users/me/app-audit')),page.getByRole('button',{name:'读取记录'}).click()]);
  await details.locator('li').filter({hasText:'9a03d4f1-3357-4d72-afdb-a906070eea10'}).waitFor();
  const narrowText=await details.locator('li').allInnerTexts();
  await page.setViewportSize({width:390,height:844});
  await details.scrollIntoViewIfNeeded();
  assert.deepEqual(await details.locator('li').allInnerTexts(),narrowText);
  assert.equal(await details.locator('li').evaluateAll(rows=>rows.every(row=>row.scrollWidth<=row.clientWidth)),true,'full actor ID wraps at390px');
  await page.screenshot({path:path.join(output,'app-audit-390px.png'),fullPage:true});
  const allowed=['/api/v1/users/me','/api/v1/apps','/api/v1/apps?include_unavailable=true','/api/v1/apps/r35-audit-example','/api/v1/users/me/app-audit'];
  assert.ok(requests.every(r=>allowed.includes(r.path)), 'no directory/identity expansion');
  assert.ok(requests.filter(r=>r.method==='PATCH').every(r=>r.body&&r.path==='/api/v1/apps/r35-audit-example'));

  assert.deepEqual(pageErrors,[]);
  const backendModule=JSON.parse(backendSource.trim().split('\n')[0]);
  assert.equal(backendModule.module_path,path.join(repoRoot,'xeonolab/desktop.py'));
  writeFileSync(path.join(output,'browser-report.json'),JSON.stringify({
    baseline:'cfae14f2443af80da4647356b29995a32c1dc33d',
    sourceRoot:repoRoot,
    backendModule,
    browser:'Headless Chromium against current AppRegistry.tsx on loopback Vite',
    backend:'Actual xeonolab.desktop.create_app with temporary SQLite desktop.sqlite, development mode, loopback only',
    identity:{kind:'human',actors:[{subject:'r35-admin-a',scopes:['desktop:read','desktop:admin']},{subject:'r35-admin-b',scopes:['desktop:read','desktop:admin']},{subject:'9a03d4f1-3357-4d72-afdb-a906070eea10',scopes:['desktop:read','desktop:admin']}],tokens:'ephemeral local dev tokens; values are test-only'},
    workflow:['Admin A creates a valid app via UI → actual local POST /api/v1/apps','Admin B edits the same app via UI with actual If-Match revision → actual local PATCH /api/v1/apps/{id}','Admin B reads audit via AppRegistry UI → actual local GET /api/v1/users/me/app-audit','Independent local GET and exact UI rows agree on actor subjects B then A, event/time/target/order','Empty read, failed read retains rows, real retry, third legal admin UUID,390px wrapping and endpoint trace pass'],
    checks:['empty actual audit','two admins exact actor/event/target/time and order','no name mapping','failed read preserves loaded rows','successful retry','read actions GET only','390px long UUID wrapping','no directory endpoints'],
    auditEvents:events,
    finalRenderedRows:narrowText,
    renderedAuditText:uiAuditText,
    requests,
    pageErrors,
    cleanup:'Vite and API processes stopped; temporary SQLite/data directory removed; ephemeral harness files removed.',
    limits:'No production accounts/data, no external iframe load, no account-directory lookup. The UI harness switches between three legal local human principals by replacing the local Bearer token; this models sequential admin sessions and is not an application feature.'
  },null,2));
  console.log('Issue182 eight local API/Chrome checks passed.');
} finally {
  if(browser)await browser.close();
  await vite.close().catch(()=>{});
  child.kill('SIGTERM');
  await new Promise(resolve=>{if(child.exitCode!==null)return resolve();child.once('exit',resolve);setTimeout(resolve,4000).unref()});
  await fs.rm(dataDir,{recursive:true,force:true});
  await fs.rm(fixturePath,{force:true});
  await fs.rm(htmlPath,{force:true});
}
