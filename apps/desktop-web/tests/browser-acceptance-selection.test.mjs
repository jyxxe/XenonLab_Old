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
import {chromium, expect} from '@playwright/test';

const appRoot=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const repositoryRoot=path.resolve(appRoot,'../..');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function freePort(){
  const server=net.createServer();
  await new Promise((resolve,reject)=>server.listen(0,'127.0.0.1',resolve).once('error',reject));
  const port=server.address().port;
  await new Promise(resolve=>server.close(resolve));
  return port;
}
async function stop(child){
  if(!child?.pid||child.exitCode!==null)return;
  const exited=new Promise(resolve=>child.once('exit',resolve));
  child.kill('SIGTERM');
  let timer;
  await Promise.race([exited,new Promise(resolve=>{timer=setTimeout(resolve,3000)})]);
  clearTimeout(timer);
  if(child.exitCode===null){child.kill('SIGKILL');await exited;}
}

test('Shift-click selects the loaded sorted filtered view and excludes hidden rows from AI preview', {timeout:120000}, async()=>{
  const executablePath=process.env.CHROME_BIN||chromium.executablePath();
  assert.ok(existsSync(executablePath),`Chromium is required: ${executablePath}`);
  const temp=await fs.mkdtemp(path.join(os.tmpdir(),'xel-selection-'));
  const evidence=path.join(repositoryRoot,'runtime/verification/issue236');
  await fs.mkdir(evidence,{recursive:true});
  const apiOrigin=`http://127.0.0.1:${await freePort()}`;
  const child=spawn('uv',['run','python','apps/desktop-web/tests/selection-local-api.py','--data-dir',path.join(temp,'data'),'--port',new URL(apiOrigin).port],{cwd:repositoryRoot,stdio:['ignore','pipe','pipe']});
  let startupError,stderr='';
  child.once('error',error=>{startupError=error});
  child.stderr.on('data',data=>{stderr+=data.toString()});
  child.stdout.on('data',()=>{});
  let server,browser;
  const seedCalls=[],browserCalls=[],pageErrors=[],external=[];
  const headers={Authorization:'Bearer issue236-synthetic-token','Content-Type':'application/json'};
  const call=async(method,route,body,extra={})=>{
    const response=await fetch(`${apiOrigin}/api/v1${route}`,{method,headers:{...headers,...extra},...(body===undefined?{}:{body:JSON.stringify(body)})});
    const value=await response.json();
    seedCalls.push({method,route,status:response.status});
    assert.ok(response.ok,`${method} ${route}: ${JSON.stringify(value)}`);
    return value.data;
  };
  async function seed(name,count,interleaved){
    const table=await call('POST','/tables',{path:'/selection',name,columns:[{name:'title',type:'text',nullable:false,default_value:null}]});
    for(let i=0;i<count;i++)await call('POST',`/tables/${table.table_id}/rows`,{values:{title:`seed-${i}`}});
    const rows=await call('GET',`/tables/${table.table_id}/rows?limit=1000`);
    for(let i=0;i<rows.length;i++){
      const title=interleaved?(i%2===0?`keep-${String(count/2-1-i/2).padStart(2,'0')}`:`hide-${String(i).padStart(2,'0')}`):`plain-${i}`;
      await call('PATCH',`/tables/${table.table_id}/rows/${rows[i]._row_id}`,{values:{title}},{'If-Match':'"revision:1"'});
    }
    return {id:table.table_id,rows:await call('GET',`/tables/${table.table_id}/rows?limit=1000`)};
  }
  try{
    let ready=false;
    for(let i=0;i<200;i++){
      if(startupError)throw new Error(`Local API could not start: ${startupError.message}; install the pinned uv/Python runtime.`);
      if(child.exitCode!==null)throw new Error(`Local API exited: ${stderr}`);
      try{if((await fetch(`${apiOrigin}/health/live`)).ok){ready=true;break}}catch{}
      await sleep(100);
    }
    assert.ok(ready,`Local API readiness failed: ${stderr}`);
    const control=await seed('control',4,false);
    const target=await seed('sorted and filtered',12,true);
    const virtual=await seed('virtual loaded rows',120,true);
    server=await createServer({root:appRoot,cacheDir:path.join(temp,'vite-cache'),optimizeDeps:{noDiscovery:true,include:['react','react-dom/client','react/jsx-runtime','react/jsx-dev-runtime']},server:{host:'127.0.0.1',port:0,watch:null,hmr:false,proxy:{'/api/v1':apiOrigin}}});
    await server.listen();
    browser=await chromium.launch({executablePath,headless:true});
    const page=await browser.newPage({viewport:{width:1440,height:1000}});
    page.on('pageerror',error=>pageErrors.push(error.message));
    page.on('request',request=>{
      const url=new URL(request.url());
      if(!['127.0.0.1','localhost'].includes(url.hostname))external.push(request.url());
      if(url.pathname.startsWith('/api/v1/'))browserCalls.push({method:request.method(),path:url.pathname+url.search});
    });
    await page.goto(`${server.resolvedUrls.local[0]}tests/selection-fixture.html`);
    const row=id=>page.locator(`tr[data-row-id="${id}"]`);
    const rendered=()=>page.locator('tr[data-row-id]').evaluateAll(rows=>rows.map(row=>row.getAttribute('data-row-id')));
    const selected=()=>page.locator('tr[data-row-id][aria-selected="true"]').evaluateAll(rows=>rows.map(row=>row.getAttribute('data-row-id')));
    const filter=page.getByRole('searchbox',{name:'筛选已加载记录'});
    const titleSort=page.locator('.library-sort').filter({hasText:'title'});
    const count=n=>page.getByText(`已选 ${n} 行`,{exact:true});
    const open=async(name,firstId)=>{
      await page.locator('.library-tree-table').filter({hasText:name}).click();
      await expect(row(firstId)).toBeVisible();
    };
    const range=async(ids,reverse=false)=>{
      await row(reverse?ids.at(-1):ids[0]).click();
      await row(reverse?ids[0]:ids.at(-1)).click({modifiers:['Shift']});
      await expect(count(ids.length)).toBeVisible();
      assert.deepEqual(await selected(),ids);
    };
    await open('control',control.rows[0]._row_id);
    await range(control.rows.map(row=>row._row_id));
    await open('sorted and filtered',target.rows[0]._row_id);
    await titleSort.click();
    await filter.fill('keep-');
    const asc=target.rows.filter(row=>row.values.title.startsWith('keep-')).sort((a,b)=>a.values.title.localeCompare(b.values.title)).map(row=>row._row_id);
    await expect.poll(rendered).toEqual(asc);
    await range(asc);
    await page.screenshot({path:path.join(evidence,'filtered-visible-range.png')});
    await page.getByRole('button',{name:'Ask AI about selection',exact:true}).click();
    const draft=page.getByRole('textbox',{name:'AI 草稿',exact:true});
    await expect(draft).toHaveValue(/选中记录：6 行/);
    const prompt=await draft.inputValue();
    assert.equal(prompt.includes('hide-'),false,'preview never implicitly shares filtered rows');
    for(const id of asc)assert.ok(prompt.includes(id));
    await expect(page.locator('.library-ask-meta')).toContainText('6 行');
    await page.screenshot({path:path.join(evidence,'ai-preview-visible-only.png')});
    await page.getByRole('button',{name:'取消',exact:true}).click();
    await range(asc,true);
    await titleSort.click();
    const desc=[...asc].reverse();
    await expect.poll(rendered).toEqual(desc);
    await range(desc.slice(1,4));
    await range(desc.slice(1,4),true);

    // An active row removed by filtering is not a range anchor in the new view.
    await filter.fill('');
    const hidden=target.rows.find(row=>row.values.title.startsWith('hide-'))._row_id;
    await row(hidden).click();
    await filter.fill('keep-');
    await row(desc[2]).click({modifiers:['Shift']});
    await expect(count(1)).toBeVisible();
    assert.deepEqual(await selected(),[desc[2]]);

    // A selected row hidden by the current filter is never shared with AI.
    await filter.fill('');
    await row(hidden).click();
    await row(desc[2]).click({modifiers:['Control']});
    await expect(count(2)).toBeVisible();
    await filter.fill('keep-');
    await page.getByRole('button',{name:'Ask AI about selection',exact:true}).click();
    await expect(draft).toHaveValue(/选中记录：1 行/);
    const filteredDraft=await draft.inputValue();
    assert.equal(filteredDraft.includes('hide-'),false,'filtered-out selection is excluded from the AI draft');
    assert.equal(filteredDraft.includes('keep-'),true,'visible selection remains in the AI draft');
    await expect(page.locator('.library-ask-meta')).toContainText('1 行');
    await page.getByRole('button',{name:'取消',exact:true}).click();
    await expect(count(1)).toBeVisible();

    // Existing modifiers, checkbox toggles and keyboard selection keep their
    // established behavior; this fix changes only pointer range ordering.
    await row(desc[0]).click();
    await row(desc[1]).click({modifiers:['Control']});
    await expect(count(2)).toBeVisible();
    await row(desc[0]).click({modifiers:['Meta']});
    await expect(count(1)).toBeVisible();
    await page.getByRole('checkbox',{name:`选择记录 ${desc[2]}`,exact:true}).check();
    await expect(count(2)).toBeVisible();
    await page.getByRole('checkbox',{name:`选择记录 ${desc[1]}`,exact:true}).uncheck();
    await expect(count(1)).toBeVisible();
    await row(desc[0]).click();
    const grid=page.locator('.table-scroll');
    await grid.focus();
    await page.keyboard.press('ArrowDown');
    await expect.poll(selected).toEqual([desc[1]]);
    await page.keyboard.press('Shift+ArrowDown');
    await expect.poll(selected).toEqual(desc.slice(1,3));
    await page.keyboard.press('Space');
    await expect.poll(selected).toEqual([desc[1]]);
    await page.keyboard.press('Control+a');
    await expect(count(6)).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByText(/^已选 \d+ 行$/)).toHaveCount(0);

    // Load the second real API page before selecting a range across virtual
    // rows. Only mounted rows are clicked; the hidden middle is still selected.
    await open('virtual loaded rows',virtual.rows[0]._row_id);
    // Table-wide sorting resets the loaded page, so request the sort first and
    // only then load the second sorted page before selecting across both.
    await titleSort.click();
    await expect(page.locator('.library-table-count')).toContainText('100');
    await page.getByRole('button',{name:'加载更多记录',exact:true}).click();
    await expect(page.locator('.library-table-count')).toContainText('120');
    await filter.fill('keep-');
    const virtualIds=virtual.rows.filter(row=>row.values.title.startsWith('keep-')).sort((a,b)=>a.values.title.localeCompare(b.values.title)).map(row=>row._row_id);
    await expect(row(virtualIds[0])).toBeVisible();
    assert.ok((await rendered()).length<virtualIds.length,'virtual view does not mount the entire result');
    await row(virtualIds[0]).click();
    await grid.evaluate(element=>element.scrollTo({top:element.scrollHeight}));
    await row(virtualIds.at(-1)).click({modifiers:['Shift']});
    await expect(count(60)).toBeVisible();
    await page.screenshot({path:path.join(evidence,'virtual-loaded-range.png')});
    await page.getByRole('button',{name:'Ask AI about selection',exact:true}).click();
    await expect(draft).toHaveValue(/选中记录：60 行/);
    assert.equal((await draft.inputValue()).includes('hide-'),false);
    await expect(page.locator('.library-ask-meta')).toContainText('60 行');
    await page.getByRole('button',{name:'取消',exact:true}).click();
    assert.deepEqual(pageErrors,[]);
    assert.deepEqual(external,[]);
    assert.equal(browserCalls.some(call=>call.method!=='GET'),false,'selection and preview never write records or send AI messages');
    await fs.writeFile(path.join(evidence,'result.json'),JSON.stringify({source:'Real loopback XeLibrary API, temporary synthetic owner/data and actual LibraryApp; no production auth bridge or AI sending',asc,virtualSelectedCount:60,seedCalls,browserCalls,pageErrors,external},null,2));
  }finally{
    try{await browser?.close()}
    finally{
      try{await server?.close()}
      finally{
        try{await stop(child)}
        finally{
          await fs.rm(temp,{recursive:true,force:true});
          await fs.writeFile(path.join(evidence,'cleanup.json'),JSON.stringify({browserClosed:true,localAPIStopped:true,temporaryDataRemoved:true}));
        }
      }
    }
  }
});
