import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import http from 'node:http';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import {spawn,execFileSync} from 'node:child_process';
import {createServer} from '../../../apps/desktop-web/node_modules/vite/dist/node/index.js';
import {chromium} from '../../../apps/desktop-web/node_modules/playwright-core/index.mjs';
const helper=path.dirname(new URL(import.meta.url).pathname),source=path.resolve(helper,'../../..'),appRoot=path.join(source,'apps/desktop-web');
const evidence=path.join(source,'runtime/verification/xl-snapshot-download');await fs.mkdir(evidence,{recursive:true});
const temp=await fs.mkdtemp(path.join(os.tmpdir(),'xeonolab-snapshot-'));
const tsx=path.join(appRoot,'tests/.round34-attachment-switch-fixture.tsx'),html=path.join(appRoot,'tests/.round34-attachment-switch-fixture.html');
await fs.copyFile(path.join(helper,'fixture.tsx'),tsx);await fs.copyFile(path.join(helper,'fixture.html'),html);
const port=async()=>{const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p;};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function waitFor(fn,label){const until=Date.now()+30000;while(Date.now()<until){try{if(await fn())return;}catch{}await sleep(80);}throw Error('Timed out: '+label);}
const apiPort=await port(),proxyPort=await port(),webPort=await port();
const identities={'snapshot-admin-token':{sub:'snapshot-human',scope:'xelibrary:read xelibrary:write xelibrary:attach xelibrary:admin',agent_kind:'human'},'snapshot-readonly-token':{sub:'snapshot-human',scope:'xelibrary:read',agent_kind:'human'}};
const backend=spawn('uv',['run','python','-m','uvicorn','xeonolab.library:create_app','--factory','--app-dir',source,'--host','127.0.0.1','--port',String(apiPort)],{cwd:source,env:{...process.env,PYTHONPATH:source,APP_MODE:'development',DATA_DIR:path.join(temp,'api-data'),RATE_LIMIT_ENABLED:'false',DEV_TOKENS_JSON:JSON.stringify(identities)},stdio:'ignore'});
const events=[],plans=[];function hold(fail=false){let release;const gate=new Promise(r=>release=r);const plan={release,gate,fail,ready:false,sent:false};plans.push(plan);return plan;}
let nextPlan=null,proxy,vite,browser;
try{
 await waitFor(async()=>{try{return(await fetch(`http://127.0.0.1:${apiPort}/health/live`)).ok}catch{return false}},'actual library backend');
 const base=`http://127.0.0.1:${apiPort}/api/v1`,headers={Authorization:'Bearer snapshot-admin-token','Content-Type':'application/json'};
 async function post(p,body){const r=await fetch(base+p,{method:'POST',headers,body:JSON.stringify(body)});const j=await r.json();assert.equal(r.status,201,JSON.stringify(j));return j.data;}
 const columns=[{name:'title',type:'string',nullable:false,default_value:null},{name:'n',type:'int64',nullable:false,default_value:0}];
 const a=await post('/tables',{path:'/Snapshot',name:'Snapshot A',columns}),b=await post('/tables',{path:'/Snapshot',name:'Snapshot B',columns});
 for(let n=0;n<101;n++)await post(`/tables/${a.table_id}/rows`,{values:{title:`Row${String(n).padStart(3,'0')}`,n}});
 await post(`/tables/${b.table_id}/rows`,{values:{title:'B row',n:900}});
 proxy=http.createServer((req,res)=>{const event={path:req.url,method:req.method};events.push(event);const plan=req.url.endsWith('/export')?nextPlan:null;if(plan)nextPlan=null;
  const up=http.request({host:'127.0.0.1',port:apiPort,path:req.url,method:req.method,headers:{...req.headers,host:`127.0.0.1:${apiPort}`}},response=>{const chunks=[];response.on('data',c=>chunks.push(c));response.on('end',async()=>{let body=Buffer.concat(chunks),status=response.statusCode;event.status=status;if(plan){plan.ready=true;await plan.gate;if(plan.fail){status=503;body=Buffer.from(JSON.stringify({error:{message:'Synthetic snapshot unavailable',code:'SERVICE_UNAVAILABLE',request_id:'snapshot-test-request'}}));}}
   const h={...response.headers};delete h.connection;delete h['transfer-encoding'];h['content-length']=String(body.length);if(plan?.fail)h['content-type']='application/json';res.writeHead(status,h);res.end(body);event.sent=true;if(plan)plan.sent=true;
  });});up.on('error',()=>{res.writeHead(502);res.end();});req.pipe(up);
 });await new Promise(r=>proxy.listen(proxyPort,'127.0.0.1',r));
 vite=await createServer({root:appRoot,configFile:false,server:{host:'127.0.0.1',port:webPort,strictPort:true,watch:null,proxy:{'/api/v1':`http://127.0.0.1:${proxyPort}`}}});await vite.listen();
 browser=await chromium.launch({headless:true,executablePath:'/usr/bin/google-chrome',args:['--no-sandbox','--disable-dev-shm-usage']});
 const page=await browser.newPage({viewport:{width:1440,height:950}}),pageErrors=[],downloads=[];page.on('pageerror',e=>pageErrors.push(e.message));page.on('download',d=>downloads.push(d));
 const url=`http://127.0.0.1:${webPort}/tests/.round34-attachment-switch-fixture.html`;
 async function open(p,name){await p.locator('.knowledge-sidebar .library-tree-table').filter({hasText:name}).click();await p.locator('.library-table-head h2').filter({hasText:name}).waitFor();}
 await page.goto(url);await page.locator('button[aria-label="刷新知识表目录"]').click();await open(page,'Snapshot A');await waitFor(async()=> (await page.locator('.library-table-toolbar').innerText()).includes('100'),'initial page100');
 await page.getByPlaceholder('筛选已加载记录（服务端过滤请使用查询控制台）').fill('Row000');
 const row=page.locator('.table-scroll tbody tr').filter({hasText:'Row000'});await row.locator('input[type=checkbox]').check();
 const control=page.getByRole('region',{name:'知识表快照'}); // section has no heading: explicit aria label gives region
 assert.ok((await control.innerText()).includes('不含附件文件'));
 const plan=hold();nextPlan=plan;const exportsBefore=events.filter(e=>e.path.endsWith('/export')).length;
 await control.getByRole('button',{name:'下载 .xl 快照',exact:true}).click();await waitFor(()=>plan.ready&&!plan.sent,'actual export held');
 assert.equal(await control.getByRole('button').first().isDisabled(),true);assert.equal(downloads.length,0);assert.equal(await control.getByRole('status').count(),0);await page.screenshot({path:path.join(evidence,'download-pending.png'),fullPage:true});
 await control.getByRole('button').first().evaluate(el=>{el.click();el.click();});assert.equal(events.filter(e=>e.path.endsWith('/export')).length,exportsBefore+1);
 const downloadPromise=page.waitForEvent('download');plan.release();const first=await downloadPromise;assert.equal(first.suggestedFilename(),`${a.table_id}.xl`);const saved=path.join(evidence,first.suggestedFilename());await first.saveAs(saved);
 const snapshot=JSON.parse(execFileSync('uv',['run','python','-c',"import sqlite3,json,sys;db=sqlite3.connect('file:'+sys.argv[1]+'?mode=ro',uri=True);print(json.dumps({'schema':[json.loads(r[0]) for r in db.execute('select definition from __xl_schema order by position')],'rows':[json.loads(r[0]) for r in db.execute('select values_json from data')]}))",saved],{cwd:source,encoding:'utf8'}));
 assert.equal(snapshot.rows.length,101);assert.deepEqual(snapshot.rows.map(r=>r.n).sort((x,y)=>x-y),Array.from({length:101},(_,n)=>n));assert.ok(JSON.stringify(snapshot.schema).includes('title'));assert.ok(JSON.stringify(snapshot.schema).includes('int64'));
 await control.getByRole('status').waitFor();await page.screenshot({path:path.join(evidence,'download-filtered-page.png'),fullPage:true});
 const failure=hold(true);nextPlan=failure;await control.getByRole('button',{name:'下载 .xl 快照',exact:true}).click();await waitFor(()=>failure.ready,'failure response held');failure.release();await control.getByRole('alert').waitFor();assert.ok((await control.getByRole('alert').innerText()).includes('SERVICE_UNAVAILABLE'));assert.equal(await control.getByRole('status').count(),0);assert.equal(downloads.length,1);await page.screenshot({path:path.join(evidence,'download-error.png'),fullPage:true});
 const retryDownload=page.waitForEvent('download');await control.getByRole('button',{name:'重试',exact:true}).click();await retryDownload;assert.equal(downloads.length,2);
 for(const fail of [false,true]){const old=hold(fail);nextPlan=old;await control.getByRole('button',{name:'下载 .xl 快照',exact:true}).click();await waitFor(()=>old.ready&&!old.sent,'old A export held');await open(page,'Snapshot B');const rawBefore=await page.evaluate(()=>window.snapshotAudit.rawCalls.length);old.release();await waitFor(()=>old.sent,'old A response delivered');await waitFor(async()=>await page.evaluate(n=>window.snapshotAudit.rawCalls.length===n+1,rawBefore),'old raw response body settled');await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));assert.equal(await control.getByRole('status').count(),0);assert.equal(await control.getByRole('alert').count(),0);assert.equal(downloads.length,2);await open(page,'Snapshot A');}
 const ro=await browser.newPage();ro.on('pageerror',e=>pageErrors.push(e.message));await ro.goto(url+'?readonly=1');await ro.locator('button[aria-label="刷新知识表目录"]').click();await open(ro,'Snapshot A');assert.equal(await ro.getByRole('button',{name:'下载 .xl 快照',exact:true}).count(),0);assert.equal(await ro.getByRole('region',{name:'知识表快照'}).count(),0);await ro.close();
 assert.ok(events.filter(e=>/\/rows\?/.test(e.path)).every(e=>!new URL(e.path,'http://127.0.0.1').searchParams.get('cursor')),'snapshot never requests unloaded row pages');
 assert.deepEqual(pageErrors,[]);
 const report={baseline:'03fb8985bb18d7c3ae9a4a965c34f164907226a8',sourceRoot:source,checks:['actual admin browser download+server filename+SQLite schema101rows despite100page/filter/selection','pending no false success,duplicate click oneGET','failure no download and retry actualGET','old success after table switch discarded','old error after table switch discarded','nonadmin no entry'],pageErrors,snapshot,events,scope:'actual LibraryApp and local XeLibrary FastAPI temporary SQLite;explicit synthetic admin and same-subject scoped read-only identity;fixture users/me matchesdevtoken scopes; only benign synthetic503 failures, real exportedbytes'};
 await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
}finally{for(const p of plans)p.release();if(proxy)proxy.closeAllConnections();if(browser)await browser.close();if(vite)await vite.close();if(proxy)await new Promise(r=>proxy.close(r));backend.kill('SIGTERM');await new Promise(r=>{if(backend.exitCode!==null)return r();backend.once('exit',r);setTimeout(r,3000).unref();});await fs.rm(temp,{recursive:true,force:true});await Promise.all([tsx,html].map(f=>fs.rm(f,{force:true})));}
