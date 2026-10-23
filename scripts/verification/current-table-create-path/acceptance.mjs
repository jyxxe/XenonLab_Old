import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import {spawn,spawnSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {createServer} from '../../../apps/desktop-web/node_modules/vite/dist/node/index.js';
import {chromium} from '../../../apps/desktop-web/node_modules/playwright-core/index.mjs';
const helper=path.dirname(new URL(import.meta.url).pathname),source=path.resolve(helper,'../../..'),app=path.join(source,'apps/desktop-web'),mode=process.env.SOURCE_CASE||'current',evidence=path.join(source,'runtime/verification/current-table-create-path',mode);
await fs.mkdir(evidence,{recursive:true});
const temp=await fs.mkdtemp(path.join(os.tmpdir(),'xelibrary-create-path-'));
const port=async()=>{const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p;};
async function wait(fn,label){const end=Date.now()+30000;while(Date.now()<end){try{if(await fn())return;}catch{}await new Promise(r=>setTimeout(r,50));}throw Error('Timed out: '+label);}
async function stop(p){if(!p||p.exitCode!==null)return;const end=new Promise(r=>p.once('exit',r));p.kill('SIGTERM');await Promise.race([end,new Promise(r=>setTimeout(r,2000))]);if(p.exitCode===null)p.kill('SIGKILL');}
const apiPort=await port(),desktopPort=await port(),vitePort=await port(),env={PATH:process.env.PATH,LANG:'C.UTF-8',PYTHONPATH:source,RATE_LIMIT_ENABLED:'false'};
const lib=spawn('uv',['run','python',path.join(helper,'local_xelibrary_api.py')],{cwd:source,env:{...env,CREATE_PATH_DATA_DIR:path.join(temp,'library'),CREATE_PATH_API_PORT:String(apiPort)},stdio:'ignore'}),desktop=spawn('uv',['run','python',path.join(helper,'local_desktop_api.py')],{cwd:source,env:{...env,CREATE_PATH_DESKTOP_DATA_DIR:path.join(temp,'desktop'),CREATE_PATH_DESKTOP_API_PORT:String(desktopPort)},stdio:'ignore'});
const tsx=path.join(app,'tests/__agent_create_path_fixture.tsx'),html=path.join(app,'tests/__agent_create_path_fixture.html');
let browser,vite;const checks=[],errors=[];
try{
 await wait(async()=>(await fetch(`http://127.0.0.1:${apiPort}/health/live`)).ok,'Library API');await wait(async()=>(await fetch(`http://127.0.0.1:${desktopPort}/health/live`)).ok,'Desktop API');
 const req=async(route,method='GET',body)=>{const r=await fetch(`http://127.0.0.1:${apiPort}/api/v1${route}`,{method,headers:{Authorization:'Bearer create-path-local-admin','Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});const p=await r.json();assert.ok(r.ok,JSON.stringify(p));return p.data;};

 const columns=[{name:'title',type:'string',nullable:false}];
 const nested=await req('/tables','POST',{path:'/数学/分析',name:'nested-source',columns});
 const deep=await req('/tables','POST',{path:'/数学/分析/实分析/级数',name:'deep-source',columns});
 const root=await req('/tables','POST',{path:'/',name:'root-source',columns});
 await fs.copyFile(path.join(helper,'fixture.tsx'),tsx);await fs.copyFile(path.join(helper,'fixture.html'),html);
 const originals={};
 const plugins=mode==='before'?[{name:'baseline',enforce:'pre',load(id){for(const f of ['LibraryApp.tsx','NewTableDialog.tsx'])if(id===path.join(app,'src/library',f)){const r=spawnSync('git',['show',`865a72277008a5773236b50b993bacdecc78d6ab:apps/desktop-web/src/library/${f}`],{cwd:source,encoding:'utf8'});assert.equal(r.status,0);originals[f]=r.stdout;return r.stdout;}}}]:[];
 vite=await createServer({root:app,configFile:false,plugins,server:{host:'127.0.0.1',port:vitePort,strictPort:true,watch:null,proxy:{'/api':`http://127.0.0.1:${apiPort}`,'/desktop-api':{target:`http://127.0.0.1:${desktopPort}`,rewrite:p=>p.replace(/^\/desktop-api/,'')}}},optimizeDeps:{include:['react','react-dom/client']}});await vite.listen();
 browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true,args:['--no-sandbox']});const page=await browser.newPage({viewport:{width:1550,height:1000}});page.on('pageerror',e=>errors.push(String(e)));
 await page.goto(`http://127.0.0.1:${vitePort}/tests/__agent_create_path_fixture.html`);await page.getByPlaceholder('搜索路径或表名').waitFor();
 const newButton=page.getByTitle('新建知识表',{exact:true});const dialog=page.getByRole('dialog',{name:'新建知识表',exact:true});const input=dialog.getByLabel(/^逻辑路径/);const cancel=async()=>{await dialog.getByRole('button',{name:'取消',exact:true}).click();await dialog.waitFor({state:'hidden'});};
 const openDialog=async()=>{await newButton.click();await dialog.waitFor();};
 const open=async(name)=>{await page.getByPlaceholder('搜索路径或表名').fill(name);await page.locator('.library-tree-table').filter({hasText:name}).click();await page.locator('.library-table-head h2').filter({hasText:name}).waitFor();await page.getByPlaceholder('搜索路径或表名').fill('');};
 const postCalls=async()=>page.evaluate(()=>window.createPathAudit.calls.filter(c=>c.method==='POST'&&c.path==='/tables'));
 await open('nested-source');await openDialog();
 if(mode==='before'){
  assert.equal(await input.inputValue(),'/知识');const before={mode,baseline:'865a722',table:nested,displayedPath:await page.locator('.library-table-head h2').textContent(),dialogPath:await input.inputValue(),errors,sourceHashes:{}};for(const f of Object.keys(originals))before.sourceHashes[f]=createHash('sha256').update(originals[f]).digest('hex');await page.screenshot({path:path.join(evidence,'unfixed-nested-default.png'),fullPage:true});await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify(before,null,2));await cancel();assert.equal((await postCalls()).length,0);console.log(JSON.stringify(before,null,2));
 }else{
  assert.equal(await input.inputValue(),nested.path);checks.push('visible nested table prepopulates its exact current path');
  await dialog.getByLabel('表名',{exact:true}).fill('kept-name-draft');await dialog.getByLabel('字段名',{exact:true}).first().fill('kept_schema_field');await input.fill('/用户/草稿路径');await cancel();assert.equal((await postCalls()).length,0);assert.ok((await page.locator('.library-table-head h2').textContent()).includes(nested.path));
  await openDialog();assert.equal(await input.inputValue(),nested.path);assert.equal(await dialog.getByLabel('表名',{exact:true}).inputValue(),'kept-name-draft');assert.equal(await dialog.getByLabel('字段名',{exact:true}).first().inputValue(),'kept_schema_field');await cancel();checks.push('cancel sends no POST and reopening refreshes only path while retaining existing name/schema drafts');
  await open('deep-source');await openDialog();assert.equal(await input.inputValue(),deep.path);checks.push('switching current table then reopening uses full new deep path');await page.screenshot({path:path.join(evidence,'deep-path-default.png'),fullPage:true});
  await input.fill('/用户/明确选择');await dialog.getByLabel('表名',{exact:true}).fill('ui-created-edited-path');await dialog.getByRole('button',{name:'创建知识表',exact:true}).click();await dialog.waitFor({state:'hidden'});await page.locator('.library-table-head h2').filter({hasText:'ui-created-edited-path'}).waitFor();const posts=await postCalls();assert.equal(posts.length,1);assert.equal(posts[0].status,201);assert.equal(posts[0].body.path,'/用户/明确选择');assert.equal(posts[0].body.columns[0].name,'kept_schema_field');const createdId=posts[0].responsePayload.data.table_id,createdSchema=await req(`/tables/${createdId}/schema`);assert.equal(createdSchema.path,'/用户/明确选择');assert.equal(createdSchema.name,'ui-created-edited-path');assert.equal(createdSchema.columns[0].name,'kept_schema_field');checks.push('edited final path and retained schema go through real existing POST201 with exact schema readback');await page.screenshot({path:path.join(evidence,'created-edited-path.png'),fullPage:true});
  await open('root-source');await openDialog();assert.equal(await input.inputValue(),'/');await cancel();checks.push('visible root path remains root rather than knowledge fallback');
  for(const viewName of ['搜索','查询控制台','定理']){await open('nested-source');await page.getByRole('button',{name:viewName,exact:true}).click();assert.equal(await page.locator('.library-table-head').count(),0);await openDialog();assert.equal(await input.inputValue(),'/知识');await cancel();checks.push(`${viewName} view uses knowledge default despite retained session table`);}
  await page.reload();await page.getByPlaceholder('搜索路径或表名').waitFor();assert.equal(await page.locator('.library-table-head').count(),0);await openDialog();assert.equal(await input.inputValue(),'/知识');const submit=dialog.getByRole('button',{name:'创建知识表',exact:true});assert.equal(await submit.isDisabled(),true);await dialog.getByLabel('表名',{exact:true}).fill('validation-control');await input.fill('relative');assert.equal(await submit.isDisabled(),true);await input.fill('/知识');await dialog.getByLabel('字段名',{exact:true}).first().fill('');assert.equal(await submit.isDisabled(),true);await dialog.getByLabel('字段名',{exact:true}).first().fill('title');assert.equal(await submit.isEnabled(),true);await cancel();assert.equal((await postCalls()).length,0);checks.push('welcome default and existing name/absolute-path/schema validation remain, cancellation has no creation');
  assert.deepEqual(errors,[]);const report={mode,baseline:'865a72277008a5773236b50b993bacdecc78d6ab',checks,errors,posts,createdSchema,identity:'Explicit synthetic create-path-local-admin development identity read through actual local Desktop users/me. Not OIDC/production.',services:'Actual isolated local XeLibrary and Desktop APIs; API-created nested/deep/root synthetic tables; actual LibraryApp/NewTableDialog/SchemaEditor and styles.',limits:'Only editable initial path preference; no assertion of real production ACL or user creation preferences.',sourceHashes:{}};for(const f of ['LibraryApp.tsx','NewTableDialog.tsx'])report.sourceHashes[f]=createHash('sha256').update(await fs.readFile(path.join(app,'src/library',f))).digest('hex');await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }
}catch(e){await fs.writeFile(path.join(evidence,'failure.json'),JSON.stringify({error:String(e),errors},null,2));throw e;}
finally{await browser?.close();await vite?.close();await stop(lib);await stop(desktop);await fs.rm(tsx,{force:true});await fs.rm(html,{force:true});await fs.rm(temp,{recursive:true,force:true});}
