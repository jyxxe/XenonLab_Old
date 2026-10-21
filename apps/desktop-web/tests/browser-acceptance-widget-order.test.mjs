import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
const app=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const evidence=path.resolve(app,'../../runtime/verification/issue150-widget-order');
test('saved widget order drives desktop and mobile, with keyboard reordering and re-enable append',async()=>{
 const vite=await createServer({root:app,server:{host:'127.0.0.1',port:0,watch:null}});await vite.listen();
 const browser=await chromium.launch({executablePath:process.env.CHROME_BIN||'/usr/bin/google-chrome',headless:true,args:['--no-sandbox']});
 try{
 const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 const url=`http://127.0.0.1:${vite.httpServer.address().port}/tests/shell-fixture.html`;
 const order=()=>page.locator('.widgets > .widget').evaluateAll(els=>els.map(e=>[...e.classList].find(c=>c.startsWith('widget-')).slice(7)));
 await page.goto(url);await page.locator('.widgets > .widget').first().waitFor();assert.deepEqual(await order(),['clock','status']);
 await page.evaluate(()=>{const prefs={...window.shellFixture.preferences};delete prefs.widgets;sessionStorage.setItem('shell-fixture:preferences',JSON.stringify(prefs));});await page.reload();await page.locator('.widgets > .widget').first().waitFor();assert.deepEqual(await order(),['clock','status']);
 await page.evaluate(()=>sessionStorage.setItem('shell-fixture:preferences',JSON.stringify({...window.shellFixture.preferences,widgets:['clock','status','notes','search','work']})));
 await page.reload();await page.locator('.widget-work').waitFor();assert.deepEqual(await order(),['clock','status','notes','search','work']);
 await page.locator('nav.dock button[title="系统设置"]').click();const up=page.getByRole('button',{name:'上移知识库搜索',exact:true});await up.waitFor();
 assert.equal(await page.getByRole('button',{name:'上移时钟与日期',exact:true}).isDisabled(),true);assert.equal(await page.getByRole('button',{name:'下移任务队列',exact:true}).isDisabled(),true);
 await up.focus();await page.keyboard.press('Enter');await page.getByText('已应用并保存',{exact:true}).waitFor();assert.deepEqual(await order(),['clock','status','search','notes','work']);
 const saved=await page.evaluate(()=>window.shellFixture.calls.filter(c=>c.path==='/users/me/preferences'&&c.method==='PATCH').at(-1));assert.deepEqual(saved.body.widgets,['clock','status','search','notes','work']);
 await page.getByRole('button',{name:'下移知识库搜索',exact:true}).click();await page.getByText('已应用并保存',{exact:true}).waitFor();assert.deepEqual(await order(),['clock','status','notes','search','work']);
 await page.getByRole('button',{name:'上移知识库搜索',exact:true}).click();await page.getByText('已应用并保存',{exact:true}).waitFor();
 await page.getByRole('checkbox',{name:'快速便签',exact:true}).uncheck();await page.getByText('已应用并保存',{exact:true}).waitFor();assert.deepEqual(await order(),['clock','status','search','work']);
 await page.getByRole('checkbox',{name:'快速便签',exact:true}).check();await page.getByText('已应用并保存',{exact:true}).waitFor();assert.deepEqual(await order(),['clock','status','search','work','notes']);
 await page.reload();await page.locator('.widget-notes').waitFor();assert.deepEqual(await order(),['clock','status','search','work','notes']);
 await page.locator('nav.dock button[title="系统设置"]').click();for(let i=0;i<2;i++){await page.getByRole('button',{name:'上移知识库搜索',exact:true}).click();await page.getByText('已应用并保存',{exact:true}).waitFor();}
 assert.deepEqual(await order(),['search','clock','status','work','notes']);
 await fs.mkdir(evidence,{recursive:true});await page.screenshot({path:path.join(evidence,'widget-order-desktop.png')});
 await page.locator('.window[data-app-id="settings"] .bar .close').click();await page.setViewportSize({width:390,height:844});assert.deepEqual(await order(),['search','clock','status','work','notes']);
 const geometry=await page.locator('.widget-search').evaluate(e=>{const r=e.getBoundingClientRect();return{x:r.x,width:r.width}});assert.ok(geometry.x>=0&&geometry.x<390);await page.screenshot({path:path.join(evidence,'widget-order-mobile.png')});
 const other=await browser.newPage();await other.goto(url);await other.locator('.widgets > .widget').first().waitFor();assert.deepEqual(await other.locator('.widgets > .widget').evaluateAll(els=>els.map(e=>[...e.classList].find(c=>c.startsWith('widget-')).slice(7))),['clock','status']);await other.close();assert.deepEqual(errors,[]);
 const report={baseline:'2aabf62',source:'Actual DesktopShell/Settings in local Chrome, synthetic shell fixture preference API/sessionStorage; no production account or usage-frequency verification.',checks:['missing preference uses default order','existing explicit order','keyboard move and boundary buttons','ordered preference PATCH','down/up move','disable and append on re-enable','reload persisted order','390px order and first search visible','independent default session'],mobile:geometry,pageErrors:errors,outcome:'PASS'};await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 }finally{await browser.close();await vite.close();}
});
