import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import {spawn} from 'node:child_process';
import {createServer} from '../../../apps/desktop-web/node_modules/vite/dist/node/index.js';
import {chromium} from '../../../apps/desktop-web/node_modules/playwright-core/index.mjs';
const root=process.cwd(),source=path.join(root,'scripts/verification/new-record-tab'),app=path.join(root,'apps/desktop-web'),evidence=path.join(root,'runtime/verification/issue161-new-record-tab');
const tsx=path.join(app,'__agent_new_record_fixture.tsx'),html=path.join(app,'tests/__agent_new_record_fixture.html');
await fs.mkdir(evidence,{recursive:true});await fs.copyFile(path.join(source,'fixture.tsx'),tsx);await fs.copyFile(path.join(source,'fixture.html'),html);
async function port(){const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p;}
const temp=await fs.mkdtemp(path.join(os.tmpdir(),'xeonolab-issue161-')),ap=await port();
const apiProc=spawn('uv',['run','python',path.join(source,'local_api.py')],{cwd:root,env:{...process.env,ROUND6_XELIBRARY_DATA:temp,ROUND6_XELIBRARY_PORT:String(ap)},stdio:'ignore'});
let vite,browser;
try{
for(let i=0;i<150;i++){try{if((await fetch(`http://127.0.0.1:${ap}/health/live`)).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
const base=`http://127.0.0.1:${ap}/api/v1`,headers={Authorization:'Bearer round6-local-test-token','Content-Type':'application/json'};
async function request(p,init={}){const r=await fetch(base+p,{...init,headers});assert.ok(r.ok,`${p}: ${r.status}`);return (await r.json()).data;}
const table=await request('/tables',{method:'POST',body:JSON.stringify({path:'/issue161/new-record',name:'new-record-tabs',columns:[{name:'title',type:'string',nullable:false,default_value:''},{name:'count',type:'int64',nullable:false,default_value:17},{name:'enabled',type:'boolean',nullable:false,default_value:true}]})});
const existing=await request(`/tables/${table.table_id}/rows`,{method:'POST',body:JSON.stringify({values:{title:'existing',count:17,enabled:true}})});
vite=await createServer({root:app,server:{host:'127.0.0.1',port:await port(),strictPort:true,watch:null,proxy:{'/api/v1':{target:`http://127.0.0.1:${ap}`}}}});await vite.listen();
browser=await chromium.launch({executablePath:process.env.CHROME_BIN||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox']});const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto(`http://127.0.0.1:${vite.config.server.port}/tests/__agent_new_record_fixture.html`);
await page.getByText('new-record-tabs',{exact:true}).first().click();await page.locator('.library-table tbody tr[data-row-id]').first().waitFor();
const tabs=page.locator('.inspector-tabs'),newButton=page.getByRole('button',{name:'新增记录',exact:true});
async function fields(){await page.locator('.inspector-fields input').first().waitFor();await page.waitForFunction(()=>document.querySelector('.inspector-fields input')?.value==='');assert.equal(await tabs.getByRole('tab',{name:'字段',exact:true}).getAttribute('aria-selected'),'true');assert.ok((await page.locator('.inspector-head').innerText()).includes('新建记录'));}
const checks=[];
for(const tab of ['概览','附件','历史']){await tabs.getByRole('tab',{name:tab,exact:true}).click();await newButton.click();await fields();checks.push(`${tab} → 新增记录 → 字段`);}
await page.getByRole('button',{name:'关闭 Inspector',exact:true}).click();await newButton.click();await fields();checks.push('hidden Inspector → new form');
await page.locator(`[data-row-id="${existing._row_id}"]`).click();await tabs.getByRole('tab',{name:'历史',exact:true}).click();await page.getByText('r1',{exact:true}).first().waitFor();await newButton.click();await fields();checks.push('selected row/history → new form');
for(const title of ['created-one','created-two']){await page.locator('.inspector-fields input').first().fill(title);await page.getByRole('button',{name:'创建记录',exact:true}).click();await page.getByText(title,{exact:true}).first().waitFor();const rows=await request(`/tables/${table.table_id}/rows?limit=100`),row=rows.find(r=>r.values.title===title);assert.ok(row);assert.equal(row.values.count,17);assert.equal(row.values.enabled,true);assert.equal(typeof row.values.count,'number');assert.equal(typeof row.values.enabled,'boolean');await newButton.click();await fields();}
checks.push('consecutive creates persist typed defaults');
await page.locator(`[data-row-id="${existing._row_id}"]`).dblclick();assert.equal(await tabs.getByRole('tab',{name:'概览',exact:true}).getAttribute('aria-selected'),'true');await tabs.getByRole('tab',{name:'历史',exact:true}).click();await page.getByText('r1',{exact:true}).first().waitFor();checks.push('normal record overview/history navigation');
await newButton.click();await fields();await page.screenshot({path:path.join(evidence,'new-record-fields.png'),fullPage:true});assert.deepEqual(errors,[]);
const report={baseline:'2aabf62',source:'Real Chrome and LibraryApp/Inspector against temporary local XeLibrary API; synthetic user identity/dev token and local test records, no production access.',checks,pageErrors:errors,outcome:'PASS'};await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser?.close();await vite?.close();apiProc.kill('SIGTERM');await fs.rm(temp,{recursive:true,force:true});await fs.rm(tsx,{force:true});await fs.rm(html,{force:true});}
