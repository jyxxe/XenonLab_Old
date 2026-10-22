import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createServer} from '../../../apps/desktop-web/node_modules/vite/dist/node/index.js';

const repo=process.cwd();
const appRoot=path.join(repo,'apps/desktop-web');
const evidence=path.join(repo,'runtime/verification/issue149-query-pagination');
const fixtureTsx=path.join(appRoot,'__agent_query_fixture.tsx');
const fixtureHtml=path.join(appRoot,'tests/__agent_query_fixture.html');
const temp=await fs.mkdtemp(path.join(os.tmpdir(),'xeonolab-query-recent-'));
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function freePort(){const server=net.createServer();await new Promise((resolve,reject)=>server.listen(0,'127.0.0.1',resolve).once('error',reject));const p=server.address().port;await new Promise(resolve=>server.close(resolve));return p}
async function waitFor(fn,label,timeout=15000){const end=Date.now()+timeout;while(Date.now()<end){try{const v=await fn();if(v)return v}catch{}await sleep(100)}throw Error(`Timed out waiting for ${label}`)}
await fs.mkdir(evidence,{recursive:true});const sources=path.join(repo,'scripts/verification/query-pagination');await fs.copyFile(path.join(sources,'fixture.tsx'),fixtureTsx);await fs.copyFile(path.join(sources,'fixture.html'),fixtureHtml);
const apiPort=await freePort();const apiData=path.join(temp,'xelibrary-data');await fs.mkdir(apiData,{recursive:true});
const api=spawn('uv',['run','python',path.join(sources,'local_api.py')],{cwd:repo,env:{...process.env,ROUND4_XELIBRARY_DATA:apiData,ROUND4_XELIBRARY_PORT:String(apiPort)},stdio:['ignore','pipe','pipe']});
let apiLog='';api.stdout.on('data',b=>apiLog+=b);api.stderr.on('data',b=>apiLog+=b);
let server,chrome,ws;
try{
 await waitFor(async()=>{try{const r=await fetch(`http://127.0.0.1:${apiPort}/health/live`);return r.ok}catch{return false}},'local XeLibrary API',30000);
 const base=`http://127.0.0.1:${apiPort}/api/v1`,headers={'Authorization':'Bearer round4-local-test-token','Content-Type':'application/json'};
 const create=await fetch(`${base}/tables`,{method:'POST',headers,body:JSON.stringify({path:'/round4/query-pagination',name:'recent-pagination-fixture',columns:[{name:'n',type:'int64',nullable:false,default_value:null},{name:'label',type:'string',nullable:false,default_value:null}]})});assert.equal(create.status,201);const table=(await create.json()).data;
 for(let n=1;n<=8;n++){const response=await fetch(`${base}/tables/${table.table_id}/rows`,{method:'POST',headers,body:JSON.stringify({values:{n,label:`row-${n}`}})});assert.equal(response.status,201,await response.clone().text())}
 const expressionA='FROM "/round4/**" WHERE n >= 1 ORDER BY n ASC LIMIT 2';
 const expressionB='FROM "/round4/**" WHERE n >= 5 ORDER BY n ASC LIMIT 2';
 const control=await fetch(`${base}/query`,{method:'POST',headers,body:JSON.stringify({expression:`${expressionA} OFFSET 2`})});assert.equal(control.status,200);const expectedNext=(await control.json()).data.map(x=>x.values.n);
 const vitePort=await freePort();server=await createServer({root:appRoot,server:{host:'127.0.0.1',port:vitePort,strictPort:true,watch:null,proxy:{'/api/v1':{target:`http://127.0.0.1:${apiPort}`,changeOrigin:false}}}});await server.listen();
 const profile=path.join(temp,'chrome-profile');chrome=spawn('/usr/bin/google-chrome',['--headless=new','--no-sandbox','--disable-dev-shm-usage',`--user-data-dir=${profile}`,'--remote-debugging-port=0','--remote-debugging-address=127.0.0.1','--remote-allow-origins=*','about:blank'],{stdio:'ignore'});
 const devPort=await waitFor(async()=>{try{return Number((await fs.readFile(path.join(profile,'DevToolsActivePort'),'utf8')).split(/\r?\n/)[0])}catch{return null}},'Chrome DevTools');
 const target=await(await fetch(`http://127.0.0.1:${devPort}/json/new?about%3Ablank`,{method:'PUT'})).json();ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise((resolve,reject)=>{ws.onopen=resolve;ws.onerror=reject});let id=0;const pending=new Map();ws.onmessage=e=>{const m=JSON.parse(e.data);if(m.id&&pending.has(m.id)){const {resolve,reject}=pending.get(m.id);pending.delete(m.id);m.error?reject(Error(m.error.message)):resolve(m.result)}};const call=(method,params={})=>{const n=++id;ws.send(JSON.stringify({id:n,method,params}));return new Promise((resolve,reject)=>pending.set(n,{resolve,reject}))};const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.text);return r.result?.value};
 await call('Page.enable');await call('Runtime.enable');await call('Emulation.setDeviceMetricsOverride',{width:1440,height:1000,deviceScaleFactor:1,mobile:false});await call('Page.navigate',{url:`http://127.0.0.1:${vitePort}/tests/__agent_query_fixture.html`});await waitFor(()=>evaluate("Boolean(document.querySelector('.library-query-editor'))"),'Query Console');
 const click=async(selector)=>{const point=await evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return null;const r=e.getBoundingClientRect();return{x:r.x+r.width/2,y:r.y+r.height/2}})()`);assert.ok(point,`missing ${selector}`);await call('Input.dispatchMouseEvent',{type:'mousePressed',x:point.x,y:point.y,button:'left',buttons:1,clickCount:1});await sleep(35);await call('Input.dispatchMouseEvent',{type:'mouseReleased',x:point.x,y:point.y,button:'left',buttons:0,clickCount:1});await sleep(100)};
 const values=()=>evaluate("[...document.querySelectorAll('.library-query-results tbody tr')].map(r=>JSON.parse(r.querySelector('td:nth-child(5)').innerText).n)");
 const setExpression=async text=>{await evaluate(`(()=>{const e=document.querySelector('.library-query-editor');const set=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set;set.call(e,${JSON.stringify(text)});e.dispatchEvent(new Event('input',{bubbles:true}));return true})()`);await waitFor(()=>evaluate(`document.querySelector('.library-query-editor').value===${JSON.stringify(text)}`),'expression update')};
 const run=async()=>{await click('.library-query-toolbar button');await waitFor(async()=>{const r=await values();return r?.length>0},'query results')};
 const next=async()=>{await click('.library-query-pagination button:last-child');await waitFor(async()=>{const r=await evaluate("document.querySelector('.library-query-plan')?.innerText");return r},'query plan update')};
 await setExpression(expressionA);await run();const aFirst=await values();await next();assert.deepEqual(await values(),[3,4]);await run();assert.deepEqual(await values(),[1,2]);
 await setExpression(expressionB);await run();await next();const bSecond=await values();
 await click('.library-query-history summary');await waitFor(()=>evaluate("document.querySelector('.library-query-history ol li')?.innerText"),'recent queries');
 const loadA=await evaluate(`(()=>{const li=[...document.querySelectorAll('.library-query-history ol li')].find(x=>x.querySelector('code')?.innerText===${JSON.stringify(expressionA)});return Boolean(li)})()`);assert.ok(loadA,'query A should be in recent history');
 await evaluate(`(()=>{const li=[...document.querySelectorAll('.library-query-history ol li')].find(x=>x.querySelector('code')?.innerText===${JSON.stringify(expressionA)});li.querySelector('button').click();return true})()`);
 await waitFor(()=>evaluate(`document.querySelector('.library-query-editor').value===${JSON.stringify(expressionA)}`),'load recent A');
 await run();const aReloadedFirst=await values();await next();const aReloadedNext=await values();
 assert.equal(await evaluate("document.querySelector('.library-query-pagination span').innerText"),'OFFSET 2');await click('.library-query-pagination button:first-child');assert.deepEqual(await values(),[1,2]);await setExpression(expressionA+' OFFSET 4');await run();assert.deepEqual(await values(),[5,6]);assert.equal(await evaluate("document.querySelector('.library-query-pagination span').innerText"),'OFFSET 4');await next();assert.deepEqual(await values(),[7,8]);const planText=await evaluate("document.querySelector('.library-query-plan')?.innerText");
 const result={baseline:'2aabf623aa88f68c0050774ddb03f2773681abfe',browser:'Google Chrome headless with CDP pointer interaction; local Vite frontend proxying to a real local FastAPI XeLibrary service',identity:'synthetic development identity round4-synthetic-user with only xelibrary:read/write/query; no external credentials',data:'temporary local XeLibrary catalogue/.xl table with rows n=1..8, created through HTTP API calls; temporary directory deleted on exit',expressions:{A:expressionA,B:expressionB},observed:{Afirst:aFirst,Bsecond:bSecond,expectedANextFromAPI:expectedNext,AloadedFirst:aReloadedFirst,AloadedNext:aReloadedNext,queryPlanAfterNext:planText},outcome:'PASS: loaded A returns 1,2 then 3,4; page counter agrees with server plan. Same-expression rerun returns first page; previous and explicit OFFSET4 then next OFFSET6 are verified.'};
 await fs.writeFile(path.join(evidence,'recent-pagination-result.json'),JSON.stringify(result,null,2));await fs.writeFile(path.join(evidence,'query-recent-pagination.png'),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'));console.log(JSON.stringify(result,null,2));assert.deepEqual(aFirst,[1,2]);assert.deepEqual(bSecond,[7,8]);assert.deepEqual(expectedNext,[3,4]);assert.deepEqual(aReloadedFirst,[1,2]);assert.deepEqual(aReloadedNext,[3,4]);
}catch(error){if(apiLog)console.error('LOCAL API LOG',apiLog);throw error}finally{ws?.close();if(chrome){chrome.kill('SIGTERM');await new Promise(resolve=>chrome.once('exit',resolve))}if(server)await server.close();if(api){api.kill('SIGTERM');await new Promise(resolve=>api.once('exit',resolve))}await fs.rm(temp,{recursive:true,force:true,maxRetries:5,retryDelay:100});await Promise.all([fixtureTsx,fixtureHtml].map(file=>fs.rm(file,{force:true})))}
