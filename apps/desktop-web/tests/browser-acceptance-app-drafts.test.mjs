import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium, expect} from '@playwright/test';

const appRoot=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const root=path.resolve(appRoot,'../..');
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
async function freePort(){
  const server=net.createServer();
  await new Promise((resolve,reject)=>server.listen(0,'127.0.0.1',resolve).once('error',reject));
  const port=server.address().port;
  await new Promise(resolve=>server.close(resolve));return port;
}

test('AppRegistry protects dirty existing/new drafts and keeps actual revision failures editable', {timeout:120000}, async () => {
  const executablePath=process.env.CHROME_BIN||chromium.executablePath();
  assert.ok(existsSync(executablePath),`Chromium is required: ${executablePath}`);
  const temp=await fs.mkdtemp(path.join(os.tmpdir(),'xeonolab-app-drafts-'));
  const evidence=path.join(root,'runtime/verification/issue244');
  await fs.mkdir(evidence,{recursive:true});
  const apiOrigin=`http://127.0.0.1:${await freePort()}`;
  const child=spawn('uv',['run','python','apps/desktop-web/tests/app-drafts-local-api.py','--data-dir',path.join(temp,'data'),'--port',new URL(apiOrigin).port],{cwd:root,stdio:['ignore','pipe','pipe']});
  let startupError,stderr='',server,browser,page,releaseSave,heldContinuation;
  child.once('error',error=>{startupError=error});child.stderr.on('data',data=>{stderr+=data.toString()});
  const errors=[],external=[],mutations=[],responses=[],requests=[];
  async function api(path,init={}){
    const response=await fetch(`${apiOrigin}/api/v1${path}`,{...init,headers:{Authorization:'Bearer issue244-synthetic-token','Content-Type':'application/json',...init.headers}});
    assert.ok(response.ok,await response.clone().text());
    return (await response.json()).data;
  }
  try {
    let ready=false;
    for(let attempt=0;attempt<200;attempt++){
      if(startupError)throw new Error(`Local API start failed: ${startupError.message}; install pinned uv/Python test runtime.`);
      if(child.exitCode!==null)throw new Error(`Local API exited: ${stderr}`);
      try{if((await fetch(`${apiOrigin}/health/live`)).ok){ready=true;break}}catch{}
      await sleep(100);
    }
    assert.ok(ready,`Local API unavailable: ${stderr}`);
    const originalAlpha=await api('/apps/synthetic-alpha');
    const originalBeta=await api('/apps/synthetic-beta');
    server=await createServer({root:appRoot,cacheDir:path.join(temp,'vite-cache'),optimizeDeps:{noDiscovery:true,include:['react','react-dom','react-dom/client','react/jsx-runtime','react/jsx-dev-runtime']},server:{host:'127.0.0.1',port:0,watch:null,hmr:false,proxy:{'/api/v1':apiOrigin}}});
    await server.listen();
    const origin=new URL(server.resolvedUrls.local[0]).origin;
    browser=await chromium.launch({executablePath,headless:true});
    const context=await browser.newContext({viewport:{width:1100,height:1100}});
    await context.route('**/*',route=>{
      if(new URL(route.request().url()).origin!==origin){external.push(route.request().url());return route.abort()}
      return route.continue();
    });
    page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
    page.on('request',request=>{if(request.url().includes('/api/v1/'))requests.push(new URL(request.url()).pathname);if(['POST','PATCH','DELETE'].includes(request.method()))mutations.push({method:request.method(),path:new URL(request.url()).pathname,ifMatch:request.headers()['if-match'],body:JSON.parse(request.postData()||'null')})});
    page.on('response',response=>{if(response.url().includes('/api/v1/apps'))responses.push({path:new URL(response.url()).pathname,status:response.status()})});
    await page.goto(`${origin}/tests/app-drafts-fixture.html`);
    const alpha=page.locator('.registry-columns > div > button').filter({hasText:'synthetic-alpha'});
    const beta=page.locator('.registry-columns > div > button').filter({hasText:'synthetic-beta'});
    const newApp=page.getByRole('button',{name:'新增应用',exact:true});
    const title=page.getByLabel('名称',{exact:true});
    const dialog=page.getByRole('dialog',{name:'放弃未保存的应用配置？'});
    await alpha.click();
    await expect(title).toHaveValue('Synthetic Alpha');
    // Every editable field family is independently dirty; reverting it is clean.
    const cases=[
      ['名称','Alpha unsaved','Synthetic Alpha'],['图标','☆','◇'],
      ['应用来源','https://edited.example.test','https://alpha.example.test'],['入口路径','/changed','/'],
      ['默认宽度','1200','1000'],['默认高度','850','700'],
      ['只打开一个窗口',false,true],['运行脚本',false,true],['提交表单',false,true],['下载文件',true,false],
      ['desktop:read',false,true],['desktop:admin',false,true],
    ];
    for(const [label,edited,original] of cases){
      const input=page.getByLabel(label,{exact:true});
      if(typeof edited==='boolean')await input.setChecked(edited);else await input.fill(edited);
      await beta.click();await expect(dialog).toBeVisible();
      await expect(dialog).toContainText('Synthetic Beta');
      await expect(page.locator('.registry-columns > div > button.active')).toContainText('synthetic-alpha');
      await dialog.getByRole('button',{name:'继续编辑',exact:true}).click();
      await expect(dialog).toHaveCount(0);
      if(typeof edited==='boolean'){assert.equal(await input.isChecked(),edited);await input.setChecked(original)}
      else{await expect(input).toHaveValue(edited);await input.fill(original)}
      await beta.click();await expect(title).toHaveValue('Synthetic Beta');await expect(dialog).toHaveCount(0);
      await alpha.click();await expect(title).toHaveValue('Synthetic Alpha');
    }
    assert.equal(mutations.length,0,'edits, confirmations, cancellation, reverted/clean navigation do not write');
    assert.deepEqual(await api('/apps/synthetic-alpha'),originalAlpha);
    assert.deepEqual(await api('/apps/synthetic-beta'),originalBeta);
    // The initial API has two permissions. Removing then restoring the first
    // checkbox appends it in UI array order, but the permission set is unchanged.
    const readScope=page.getByLabel('desktop:read',{exact:true});
    const adminScope=page.getByLabel('desktop:admin',{exact:true});
    await readScope.uncheck();await beta.click();await expect(dialog).toBeVisible();
    await dialog.getByRole('button',{name:'继续编辑'}).click();
    await readScope.check();await expect(adminScope).toBeChecked();
    await beta.click();await expect(title).toHaveValue('Synthetic Beta');await expect(dialog).toHaveCount(0);
    await alpha.click();await expect(readScope).toBeChecked();await expect(adminScope).toBeChecked();
    assert.equal(mutations.length,0,'permission remove/restore is clean and never writes');
    assert.deepEqual((await api('/apps/synthetic-alpha')).permissions,['desktop:read','desktop:admin']);

    await title.fill('Alpha keyboard draft');await beta.click();await expect(dialog).toBeVisible();
    await page.screenshot({path:path.join(evidence,'dirty-confirmation.png'),fullPage:true,animations:'disabled'});
    await page.keyboard.press('Escape');await expect(dialog).toHaveCount(0);
    await expect(title).toHaveValue('Alpha keyboard draft');await expect(beta).toBeFocused();
    // Native keyboard reaches the non-destructive action inside the focus trap.
    await page.keyboard.press('Enter');await expect(dialog).toBeVisible();
    await expect(dialog.getByRole('button',{name:'关闭对话框'})).toBeFocused();
    await page.keyboard.press('Tab');await expect(dialog.getByRole('button',{name:'继续编辑'})).toBeFocused();
    await page.keyboard.press('Enter');await expect(dialog).toHaveCount(0);await expect(title).toHaveValue('Alpha keyboard draft');
    await beta.click();await dialog.getByRole('button',{name:'放弃修改',exact:true}).click();
    await expect(title).toHaveValue('Synthetic Beta');
    await title.fill('Beta unsaved for new');await newApp.click();await expect(dialog).toBeVisible();
    await dialog.getByRole('button',{name:'继续编辑'}).click();await expect(title).toHaveValue('Beta unsaved for new');
    await newApp.click();await dialog.getByRole('button',{name:'放弃修改'}).click();
    await expect(title).toHaveValue('');await expect(page.getByLabel('应用标识',{exact:true})).toBeEnabled();
    await page.getByLabel('应用标识',{exact:true}).fill('synthetic-new-draft');
    await alpha.click();await expect(dialog).toBeVisible();await dialog.getByRole('button',{name:'继续编辑'}).click();
    await expect(page.getByLabel('应用标识',{exact:true})).toHaveValue('synthetic-new-draft');
    await alpha.click();await dialog.getByRole('button',{name:'放弃修改'}).click();await expect(title).toHaveValue('Synthetic Alpha');
    assert.equal(mutations.length,0,'discard existing/new drafts never autosaves');

    await title.fill('Alpha saved normally');
    // Hold only dispatch of this owned local request; the response and write are
    // still produced by the real API. No fake success/error envelope is used.
    const saveGate=new Promise(resolve=>{releaseSave=resolve});
    let signalHeld;
    const held=new Promise(resolve=>{signalHeld=resolve});
    await page.route(`${origin}/api/v1/apps/synthetic-alpha`,route=>{
      signalHeld();heldContinuation=saveGate.then(()=>route.continue());return heldContinuation;
    });
    await page.getByRole('button',{name:'保存应用',exact:true}).click();
    await held;
    await expect(page.getByRole('status')).toContainText('正在读取或保存应用');
    await expect(beta).toBeDisabled();await expect(newApp).toBeDisabled();await expect(title).toBeDisabled();
    await page.getByText('应用目录审计',{exact:true}).click();
    const auditRead=page.getByRole('button',{name:'读取记录',exact:true});
    await expect(auditRead).toBeDisabled();
    const auditBox=await auditRead.boundingBox();
    await page.mouse.click(auditBox.x+auditBox.width/2,auditBox.y+auditBox.height/2);
    await expect(beta).toBeDisabled();await expect(title).toBeDisabled();
    await expect(page.getByRole('status')).toContainText('正在读取或保存应用');
    assert.equal(requests.filter(p=>p.endsWith('/users/me/app-audit')).length,0,'disabled audit cannot unlock a held save');
    releaseSave();
    await expect(page.getByRole('status')).toHaveText('应用目录已更新');
    await heldContinuation;await page.unroute(`${origin}/api/v1/apps/synthetic-alpha`);
    assert.equal(mutations.length,1);assert.equal(mutations[0].method,'PATCH');
    assert.equal(mutations[0].ifMatch,'"revision:1"');
    assert.equal((await api('/apps/synthetic-alpha')).title,'Alpha saved normally');
    await beta.click();await expect(title).toHaveValue('Synthetic Beta');await expect(dialog).toHaveCount(0);
    // A real second local client increments revision. The component's stale
    // If-Match must really fail in FastAPI, retaining the unsaved form.
    await api('/apps/synthetic-beta',{method:'PATCH',headers:{'If-Match':'"revision:1"'},body:JSON.stringify({title:'Beta changed by second synthetic client'})});
    await title.fill('Beta draft after actual conflict');
    const failedResponse=page.waitForResponse(r=>r.request().method()==='PATCH'&&r.url().endsWith('/apps/synthetic-beta'));
    await page.getByRole('button',{name:'保存应用',exact:true}).click();
    assert.equal((await failedResponse).status(),412);
    await expect(page.getByRole('status')).toContainText('If-Match must equal the current ETag');
    await expect(title).toHaveValue('Beta draft after actual conflict');
    await alpha.click();await expect(dialog).toBeVisible();
    await dialog.getByRole('button',{name:'继续编辑'}).click();await expect(title).toHaveValue('Beta draft after actual conflict');
    await expect(page.getByRole('status')).toContainText('If-Match must equal the current ETag');
    assert.equal(mutations.length,2);assert.equal(mutations[1].ifMatch,'"revision:1"');
    assert.equal((await api('/apps/synthetic-beta')).title,'Beta changed by second synthetic client');
    await page.screenshot({path:path.join(evidence,'failed-save-retained-draft.png'),fullPage:true,animations:'disabled'});
    await alpha.click();await dialog.getByRole('button',{name:'放弃修改'}).click();
    await expect(title).toHaveValue('Alpha saved normally');
    assert.equal(mutations.length,2,'confirmed discard after failure sends no further mutation');
    assert.deepEqual(errors,[]);assert.deepEqual(external,[]);
    await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify({outcome:'PASS',source:'Actual AppRegistry and product tokens/primitives/shell styles in real Chromium; physical owned loopback Desktop FastAPI and fresh temporary SQLite with synthetic human/two seeded apps. Actual stale If-Match returns412 after a controlled second local API update; no error envelope is fabricated. External origins are metadata only and never loaded. No production OIDC, admin account, registry or deployment verified.',fieldCases:cases.map(c=>c[0]),permissionSetReversion:'two initial permissions, real removal prompts; restore appended first permission produces clean switch with no mutation or server change',mutations,responses,requests,pageErrors:errors,external,checks:['each editable field independently protects dirty draft and reversion becomes clean','continue/cancel retains selection and values','Escape/Tab/Enter/focus return','explicit discard and dirty new ID','no autosave','successful PATCH updates baseline; held save disables audit and prevents competing act' ,'actual412 retains draft and still prompts']},null,2));
  }catch(error){
    await fs.writeFile(path.join(evidence,'browser-failure.json'),JSON.stringify({error:String(error),stderr,mutations,responses,errors,external},null,2));
    await page?.screenshot({path:path.join(evidence,'browser-failure.png'),fullPage:true}).catch(()=>{});throw error;
  }finally{
    releaseSave?.();
    try{await heldContinuation}finally{try{await browser?.close()}finally{try{await server?.close()}finally{try{
      if(child.pid&&child.exitCode===null&&child.signalCode===null){const closed=new Promise(resolve=>child.once('close',resolve));child.kill('SIGTERM');await closed}
    }finally{await fs.rm(temp,{recursive:true,force:true})}}}}
  }
});
