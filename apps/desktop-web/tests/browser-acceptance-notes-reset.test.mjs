import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium, expect} from '@playwright/test';

const appRoot=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const repositoryRoot=path.resolve(appRoot,'../..');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const headers={Authorization:'Bearer issue242-synthetic-token','Content-Type':'application/json'};
async function freePort(){
  const server=net.createServer();
  await new Promise((resolve,reject)=>server.listen(0,'127.0.0.1',resolve).once('error',reject));
  const port=server.address().port;
  await new Promise(resolve=>server.close(resolve));
  return port;
}

test('restoring default appearance preserves saved and newer server notes through the real local Desktop API', {timeout:120000}, async () => {
  const executablePath=process.env.CHROME_BIN||chromium.executablePath();
  assert.ok(existsSync(executablePath),`Chromium is required: ${executablePath}`);
  const temp=await fs.mkdtemp(path.join(os.tmpdir(),'xeonolab-notes-reset-'));
  const evidence=path.join(repositoryRoot,'runtime/verification/issue242');
  await fs.mkdir(evidence,{recursive:true});
  const apiOrigin=`http://127.0.0.1:${await freePort()}`;
  const apiProcess=spawn('uv',['run','python','apps/desktop-web/tests/notes-reset-local-api.py','--data-dir',path.join(temp,'data'),'--port',new URL(apiOrigin).port],{cwd:repositoryRoot,stdio:['ignore','pipe','pipe']});
  let stderr='',startupError,server,browser,page;
  apiProcess.once('error',error=>{startupError=error});
  apiProcess.stderr.on('data',data=>{stderr+=data.toString()});
  const pageErrors=[],external=[],patches=[];
  async function preferences(body){
    const response=await fetch(`${apiOrigin}/api/v1/users/me/preferences`,{headers,...(body?{method:'PATCH',body:JSON.stringify(body)}:{})});
    assert.equal(response.status,200,await response.clone().text());
    return (await response.json()).data;
  }
  try {
    let ready=false;
    for(let attempt=0;attempt<200;attempt++){
      if(startupError) throw new Error(`Local API could not start: ${startupError.message}; install the pinned uv/Python test runtime.`);
      if(apiProcess.exitCode!==null) throw new Error(`Local API exited: ${stderr}`);
      try{if((await fetch(`${apiOrigin}/health/live`)).ok){ready=true;break}}catch{}
      await sleep(100);
    }
    assert.ok(ready,`Local Desktop API did not start: ${stderr}`);
    server=await createServer({root:appRoot,cacheDir:path.join(temp,'vite-cache'),optimizeDeps:{noDiscovery:true,include:['react','react-dom','react-dom/client','react/jsx-runtime','react/jsx-dev-runtime']},server:{host:'127.0.0.1',port:0,watch:null,hmr:false,proxy:{'/api/v1':apiOrigin}}});
    await server.listen();
    const origin=new URL(server.resolvedUrls.local[0]).origin;
    browser=await chromium.launch({executablePath,headless:true});
    const context=await browser.newContext({viewport:{width:1440,height:1000}});
    // Actual native fetch through the loopback Vite proxy; no asynchronous route.fetch callbacks.
    await context.route('**/*',route=>{
      if(new URL(route.request().url()).origin!==origin){external.push(route.request().url());return route.abort()}
      return route.continue();
    });
    page=await context.newPage();
    page.on('pageerror',error=>pageErrors.push(error.message));
    page.on('request',request=>{
      if(request.method()==='PATCH'&&request.url().endsWith('/api/v1/users/me/preferences')) patches.push(JSON.parse(request.postData()));
    });
    await page.goto(`${origin}/tests/notes-reset-fixture.html`);
    await page.locator('nav.dock button[title="系统设置"]').click();
    let settings=page.locator('.window[data-app-id=settings]');
    const toggle=()=>settings.locator('.widget-options label').filter({hasText:'快速便签'}).locator('input');
    await toggle().check();
    const note='Issue242 synthetic saved note\n非空便签：reset 后保留 📝';
    const noteBox=page.getByRole('textbox',{name:'便签内容',exact:true});
    await noteBox.fill(note);
    await expect(page.locator('.widget-notes [role=status]')).toHaveText('已保存');
    const before=await preferences();
    assert.equal(before.notes,note);
    assert.ok(before.widgets.includes('notes'));
    await page.screenshot({path:path.join(evidence,'saved-note-before-reset.png'),fullPage:true});

    // A normal appearance edit still saves, then the actual reset restores defaults.
    await settings.locator('.preference-form select').first().selectOption('light');
    await expect.poll(async()=> (await preferences()).theme).toBe('light');
    async function reset(){
      const requestPromise=page.waitForRequest(request=>request.method()==='PATCH'&&request.url().endsWith('/api/v1/users/me/preferences')&&JSON.parse(request.postData()).theme==='system');
      await settings.getByRole('button',{name:'恢复默认外观',exact:true}).click();
      const body=JSON.parse((await requestPromise).postData());
      assert.equal(Object.hasOwn(body,'notes'),false,'appearance reset omits note content entirely');
      assert.equal(body.theme,'system');
      assert.equal(body.wallpaper,'aurora');
      assert.equal(body.wallpaper_gradient_angle,160);
      assert.deepEqual(body.widgets,['clock','status']);
      await expect(settings.getByRole('status').filter({hasText:'已应用并保存'})).toBeVisible();
      await expect(page.locator('.widget-notes')).toHaveCount(0);
      return body;
    }
    const resetPatch=await reset();
    const after=await preferences();
    assert.equal(after.notes,note,'SQLite API retains content while appearance changes');
    assert.equal(after.theme,'system');
    assert.deepEqual(after.widgets,['clock','status']);
    await page.reload();
    await page.locator('nav.dock').waitFor();
    const reloaded=await preferences();
    assert.equal(reloaded.notes,note);
    await expect(page.locator('.widget-notes')).toHaveCount(0);
    settings=page.locator('.window[data-app-id=settings]');
    if(!await settings.isVisible()) await page.locator('nav.dock button[title="系统设置"]').click();
    await toggle().check();
    await expect(noteBox).toHaveValue(note);
    await page.screenshot({path:path.join(evidence,'saved-note-after-reset-reload.png'),fullPage:true});

    // Simulate another local client updating the same synthetic account after the
    // shell has read its snapshot. Reset must not copy that old snapshot back.
    const newerNote='Issue242 newer synthetic server note, from a controlled second local API client';
    await preferences({notes:newerNote});
    await expect(noteBox).toHaveValue(note);
    const newerResetPatch=await reset();
    assert.equal((await preferences()).notes,newerNote);
    await page.reload();
    await page.locator('nav.dock').waitFor();
    settings=page.locator('.window[data-app-id=settings]');
    if(!await settings.isVisible()) await page.locator('nav.dock button[title="系统设置"]').click();
    await toggle().check();
    await expect(noteBox).toHaveValue(newerNote);
    await page.screenshot({path:path.join(evidence,'newer-server-note-after-reset.png'),fullPage:true});
    assert.deepEqual(pageErrors,[]);
    assert.deepEqual(external,[]);
    await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify({outcome:'PASS',source:'Real Chromium runs repository DesktopShell, Settings, DesktopPreferences and NotesWidget with actual owned loopback Desktop FastAPI and fresh temporary SQLite. Fixed synthetic development human/token only; note data synthetic. No production OIDC, records, external origin or deployment is verified. Second-client case is an explicitly controlled local API write, not measured production concurrency.',before,after,reloaded,resetPatch,newerResetPatch,newerNote,patches,pageErrors,external,checks:['actual Notes UI saved/real API readback','reset omits notes, restores appearance defaults and hides widget','API/reload/re-enable preserves saved content','newer server note survives reset against old UI snapshot']},null,2));
  } catch(error) {
    await fs.writeFile(path.join(evidence,'browser-failure.json'),JSON.stringify({error:String(error),stderr,patches,pageErrors,external},null,2));
    await page?.screenshot({path:path.join(evidence,'browser-failure.png'),fullPage:true}).catch(()=>{});
    throw error;
  } finally {
    try{await browser?.close()}finally{
      try{await server?.close()}finally{
        try{
          if(apiProcess.pid&&apiProcess.exitCode===null&&apiProcess.signalCode===null){
            const closed=new Promise(resolve=>apiProcess.once('close',resolve));
            apiProcess.kill('SIGTERM');await closed;
          }
        }finally{await fs.rm(temp,{recursive:true,force:true})}
      }
    }
  }
});
