import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import {spawn} from 'node:child_process';
import {createServer} from '../../../apps/desktop-web/node_modules/vite/dist/node/index.js';
import {chromium} from '../../../apps/desktop-web/node_modules/playwright-core/index.mjs';
const root=process.cwd(),source=path.join(root,'scripts/verification/history-compare'),app=path.join(root,'apps/desktop-web'),evidence=path.join(root,'runtime/verification/issue156-history-compare');
const tsx=path.join(app,'__agent_history_compare_fixture.tsx'),html=path.join(app,'tests/__agent_history_compare_fixture.html');
await fs.mkdir(evidence,{recursive:true});await fs.copyFile(path.join(source,'fixture.tsx'),tsx);await fs.copyFile(path.join(source,'fixture.html'),html);
async function port(){const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p;}
const temp=await fs.mkdtemp(path.join(os.tmpdir(),'xeonolab-issue156-')),ap=await port();
const apiProc=spawn('uv',['run','python',path.join(source,'local_api.py')],{cwd:root,env:{...process.env,ROUND6_XELIBRARY_DATA:temp,ROUND6_XELIBRARY_PORT:String(ap)},stdio:'ignore'});
let vite,browser;
try{
for(let i=0;i<150;i++){try{if((await fetch(`http://127.0.0.1:${ap}/health/live`)).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
const base=`http://127.0.0.1:${ap}/api/v1`,headers={Authorization:'Bearer round6-local-test-token','Content-Type':'application/json'};
async function request(p,init={}){const r=await fetch(base+p,{...init,headers});assert.ok(r.ok,`${p}: ${r.status}`);return (await r.json()).data;}
const table=await request('/tables',{method:'POST',body:JSON.stringify({path:'/issue156/history',name:'history-compare',columns:[{name:'title',type:'string',nullable:false},{name:'detail',type:'json',nullable:true}]})});
const rowPath=`/tables/${table.table_id}/rows`;
const row=await request(rowPath,{method:'POST',body:JSON.stringify({values:{title:'version-one',detail:{items:[1],flag:false}}})});
for(const [rev,values] of [[1,{title:'version-two',detail:{items:[1,2],flag:false}}],[2,{title:'version-three',detail:{items:[1,2],flag:true}}]]){const r=await fetch(`${base}${rowPath}/${row._row_id}`,{method:'PATCH',headers:{...headers,'If-Match':`"revision:${rev}"`},body:JSON.stringify({values})});assert.equal(r.status,200);}
const other=await request(rowPath,{method:'POST',body:JSON.stringify({values:{title:'other-row',detail:{}}})});
vite=await createServer({root:app,server:{host:'127.0.0.1',port:await port(),strictPort:true,watch:null,proxy:{'/api/v1':{target:`http://127.0.0.1:${ap}`}}}});await vite.listen();
browser=await chromium.launch({executablePath:process.env.CHROME_BIN||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox']});const page=await browser.newPage({viewport:{width:1440,height:1000}}),errors=[];page.on('pageerror',e=>errors.push(e.message));
const url=`http://127.0.0.1:${vite.config.server.port}/tests/__agent_history_compare_fixture.html`;
await page.goto(url);await page.getByText('history-compare',{exact:true}).first().click();await page.locator(`[data-row-id="${row._row_id}"]`).dblclick();await page.locator('.inspector-tabs').getByRole('tab',{name:'历史',exact:true}).click();
const revision=n=>page.locator('.history-timeline li').filter({has:page.getByText(`r${n}`,{exact:true})});
await revision(1).getByRole('button',{name:'查看',exact:true}).click();
const compare=page.locator('.history-compare'),heading=compare.locator('h4'),baseSelect=compare.getByLabel('对照版本',{exact:true});
await heading.waitFor();assert.equal(await heading.innerText(),'r1 与当前 r3 的差异');assert.ok((await compare.innerText()).includes('version-one'));assert.ok((await compare.innerText()).includes('version-three'));
const writes=[];page.on('request',r=>{if(['POST','PATCH','DELETE','PUT'].includes(r.method()))writes.push({headers:r.headers(),body:r.postData()});});
await baseSelect.focus();await page.keyboard.press('ArrowDown');await page.keyboard.press('ArrowDown');await page.keyboard.press('Enter');assert.equal(await baseSelect.inputValue(),'2');assert.equal(await heading.innerText(),'r1 与历史 r2 的差异');assert.ok((await compare.innerText()).includes('version-two'));
await compare.getByLabel('查看版本',{exact:true}).selectOption('2');assert.equal(await heading.innerText(),'r2 与历史 r2 的差异');assert.ok((await compare.innerText()).includes('两端字段内容相同'));await compare.getByLabel('查看版本',{exact:true}).selectOption('1');assert.deepEqual(writes,[]);assert.equal((await request(`${rowPath}/${row._row_id}`))._revision,3);
await page.keyboard.press('Escape');await page.screenshot({path:path.join(evidence,'history-compare-desktop.png'),fullPage:true});await page.setViewportSize({width:390,height:844});await compare.scrollIntoViewIfNeeded();const geometry=await compare.locator('.history-diff>div').first().evaluate(e=>{const r=e.getBoundingClientRect();return{x:r.x,width:r.width}});assert.ok(geometry.width>0&&geometry.x>=0&&geometry.x+geometry.width<=391);await page.screenshot({path:path.join(evidence,'history-compare-mobile.png'),fullPage:true});await page.setViewportSize({width:1440,height:1000});
await page.locator(`[data-row-id="${other._row_id}"]`).click();await revision(1).getByRole('button',{name:'查看',exact:true}).click();assert.equal(await heading.innerText(),'r1 与当前 r1 的差异');assert.equal(await baseSelect.inputValue(),'current');await page.locator(`[data-row-id="${row._row_id}"]`).click();await revision(1).getByRole('button',{name:'查看',exact:true}).click();assert.equal(await baseSelect.inputValue(),'current');
await revision(1).getByRole('button',{name:'恢复此版本',exact:true}).click();for(let i=0;i<100;i++){if((await request(`${rowPath}/${row._row_id}`))._revision===4)break;await new Promise(r=>setTimeout(r,50));}const restored=await request(`${rowPath}/${row._row_id}`);assert.equal(restored._revision,4);assert.deepEqual(restored.values,{title:'version-one',detail:{items:[1],flag:false}});assert.deepEqual((await request(`${rowPath}/${row._row_id}/revisions`)).map(r=>r._revision),[1,2,3,4]);assert.equal(writes.length,1);assert.equal(writes[0].headers['if-match'],'"revision:3"');assert.deepEqual(JSON.parse(writes[0].body),{revision:1});
await page.goto(url+'?readonly=1');await page.getByText('history-compare',{exact:true}).first().click();await page.locator(`[data-row-id="${row._row_id}"]`).dblclick();await page.locator('.inspector-tabs').getByRole('tab',{name:'历史',exact:true}).click();await revision(1).waitFor();for(const button of await page.getByRole('button',{name:'恢复此版本',exact:true}).all())assert.equal(await button.isDisabled(),true);await revision(1).getByRole('button',{name:'查看',exact:true}).click();await baseSelect.selectOption('2');assert.equal(await heading.innerText(),'r1 与历史 r2 的差异');
const denied=await fetch(`${base}${rowPath}/${row._row_id}/restore`,{method:'POST',headers:{...headers,Authorization:'Bearer history-readonly-test-token','If-Match':'"revision:4"'},body:JSON.stringify({revision:1})});assert.equal(denied.status,403);assert.deepEqual(errors,[]);
const report={baseline:'2aabf62',source:'Real Chrome/LibraryApp/Inspector with temporary local XeLibrary API, synthetic revisions and development identity. No production access.',checks:['default current comparison and values','keyboard historical baseline','both endpoints and same-version equality','comparison sends no writes/current revision unchanged','390px values fit','record switch resets baseline','real restore If-Match current3 creates4 and retains1-3','read-only comparison allowed and restore disabled/API403'],geometry,pageErrors:errors,outcome:'PASS'};await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
}finally{await browser?.close();await vite?.close();apiProc.kill('SIGTERM');await fs.rm(temp,{recursive:true,force:true});await fs.rm(tsx,{force:true});await fs.rm(html,{force:true});}
