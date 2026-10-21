import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import {spawn} from 'node:child_process';
import {createServer} from '../../../apps/desktop-web/node_modules/vite/dist/node/index.js';
import {chromium} from '../../../apps/desktop-web/node_modules/playwright-core/index.mjs';
const root=process.cwd(),source=path.join(root,'scripts/verification/theorem-review-filter'),app=path.join(root,'apps/desktop-web'),evidence=path.join(root,'runtime/verification/issue171-theorem-review-filter');
const tsx=path.join(app,'__agent_theorem_filter_fixture.tsx'),html=path.join(app,'tests/__agent_theorem_filter_fixture.html');
await fs.mkdir(evidence,{recursive:true});await fs.copyFile(path.join(source,'fixture.tsx'),tsx);await fs.copyFile(path.join(source,'fixture.html'),html);
async function port(){const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p;}
const temp=await fs.mkdtemp(path.join(os.tmpdir(),'xeonolab-issue171-')),ap=await port();
const apiProc=spawn('uv',['run','python',path.join(source,'local_api.py')],{cwd:root,env:{...process.env,ROUND6_XELIBRARY_DATA:temp,ROUND6_XELIBRARY_PORT:String(ap)},stdio:'ignore'});
let vite,browser;
try{
for(let i=0;i<150;i++){try{if((await fetch(`http://127.0.0.1:${ap}/health/live`)).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
const base=`http://127.0.0.1:${ap}/api/v1`,headers={Authorization:'Bearer round6-local-test-token','Content-Type':'application/json'};
async function request(p,init={}){const r=await fetch(base+p,{...init,headers});assert.ok(r.ok,`${p}: ${r.status}`);return (await r.json()).data;}
const statuses=['draft','proposed','promoted','rejected'];
for(const status of statuses)for(let n=0;n<15;n++){
 const theorem=await request('/theorems',{method:'POST',body:JSON.stringify({path:'/theorems/issue171',title:`一致收敛 ${status} ${n}`,statement_natural:'Synthetic topic assertion only',review_status:status==='draft'?'draft':'proposed',formal_status:'none',provenance:'local-test'})});
 if(['promoted','rejected'].includes(status)){const r=await fetch(`${base}/theorems/${theorem.theorem_id}/${status==='promoted'?'promote':'reject'}`,{method:'POST',headers:{...headers,'If-Match':'"revision:1"'}});assert.equal(r.status,200);}
}
await request('/theorems',{method:'POST',body:JSON.stringify({path:'/theorems/issue171',title:'unrelated topic',statement_natural:'Nonmatching synthetic assertion',review_status:'proposed',provenance:'local-test'})});
vite=await createServer({root:app,server:{host:'127.0.0.1',port:await port(),strictPort:true,watch:null,proxy:{'/api/v1':{target:`http://127.0.0.1:${ap}`}}}});await vite.listen();
browser=await chromium.launch({executablePath:process.env.CHROME_BIN||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox']});const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto(`http://127.0.0.1:${vite.config.server.port}/tests/__agent_theorem_filter_fixture.html`);await page.getByRole('button',{name:/定理/}).first().click();await page.locator('.library-theorem-list li').nth(49).waitFor();
const filter=page.getByLabel('定理审查状态'),query=page.getByLabel('搜索定理'),list=page.locator('.library-theorem-list li'),partial=page.getByText('仅显示部分匹配结果；这不是完整审阅队列。请缩小关键词范围或调整审查状态。',{exact:true});assert.equal(await filter.isDisabled(),true);
await query.fill('一致收敛');await query.press('Enter');await partial.waitFor();assert.equal(await list.count(),50);
for(const status of statuses){await filter.selectOption(status);await page.waitForFunction(s=>{const nodes=[...document.querySelectorAll('.library-theorem-list li')];return nodes.length===15&&nodes.every(n=>n.textContent.includes(s));},status);assert.equal(await partial.count(),0);const body=await page.evaluate(()=>window.theoremFilterFixture.calls.filter(c=>c.path==='/theorems/search').at(-1).body);assert.equal(body.review_status,status);assert.equal(body.query,'一致收敛');assert.ok((await list.first().innerText()).includes('formal: none'));}
await filter.selectOption('');await partial.waitFor();await page.screenshot({path:path.join(evidence,'theorem-partial-results.png'),fullPage:true});
await page.evaluate(()=>window.theoremFilterFixture.holdStatus='draft');await filter.selectOption('draft');await page.waitForFunction(()=>window.theoremFilterFixture.started===1);assert.equal(await page.evaluate(()=>window.theoremFilterFixture.settled),0);await filter.selectOption('promoted');await page.waitForFunction(()=>[...document.querySelectorAll('.library-theorem-list li')].every(n=>n.textContent.includes('promoted'))&&document.querySelectorAll('.library-theorem-list li').length===15);await page.evaluate(()=>window.theoremFilterFixture.pending.splice(0).forEach(r=>r()));await page.waitForFunction(()=>window.theoremFilterFixture.settled===1);assert.ok((await list.first().innerText()).includes('promoted'));
await filter.selectOption('draft');await page.waitForFunction(()=>window.theoremFilterFixture.started===2);await page.getByRole('button',{name:'清除搜索',exact:true}).click();assert.equal(await query.inputValue(),'');assert.equal(await filter.inputValue(),'');assert.equal(await filter.isDisabled(),true);assert.equal(await list.count(),50);await page.evaluate(()=>window.theoremFilterFixture.pending.splice(0).forEach(r=>r()));await page.waitForFunction(()=>window.theoremFilterFixture.settled===2);assert.equal(await list.count(),50);assert.equal(await partial.count(),0);
await query.fill('一致收敛');await page.evaluate(()=>{window.theoremFilterFixture.holdStatus='';window.theoremFilterFixture.failStatus='proposed';});await filter.selectOption('proposed');await page.getByText('synthetic search unavailable',{exact:true}).waitFor();await page.evaluate(()=>window.theoremFilterFixture.failStatus='');await page.getByRole('button',{name:'重试',exact:true}).click();await page.waitForFunction(()=>document.querySelectorAll('.library-theorem-list li').length===15);assert.ok((await list.first().innerText()).includes('proposed'));assert.equal(await filter.inputValue(),'proposed');
await page.screenshot({path:path.join(evidence,'theorem-proposed-filter.png'),fullPage:true});await page.setViewportSize({width:390,height:844});await filter.scrollIntoViewIfNeeded();await page.screenshot({path:path.join(evidence,'theorem-filter-mobile.png'),fullPage:true});assert.deepEqual(errors,[]);
const report={baseline:'2aabf62',source:'Real Chrome/LibraryApp/TheoremView/useTheorems with temporary local XeLibrary API, 61 synthetic records and development identity. Promote/reject are local fixture setup only; no production data or worker verification.',checks:['nonempty keyword only','all four review_status values and matching results','review/formal badges distinct','real meta.partial warning','status switch searches current keyword','late old response cannot overwrite newer status','clear restores defaults and invalidates pending results','error retry retains query/status','390px UI'],records:61,pageErrors:errors,outcome:'PASS'};await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser?.close();await vite?.close();apiProc.kill('SIGTERM');await fs.rm(temp,{recursive:true,force:true});await fs.rm(tsx,{force:true});await fs.rm(html,{force:true});}
