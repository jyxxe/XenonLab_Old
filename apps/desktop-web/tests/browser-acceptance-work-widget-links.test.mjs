import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const evidence = path.resolve(appRoot, '../../runtime/verification/issue162-work-widget');
test('Work widget links open exact Issues by pointer and keyboard while retaining queue summary', async t => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  if (!existsSync(executablePath)) return t.skip(`Chromium missing: ${executablePath}`);
  const server = await createServer({root: appRoot, plugins: [{name: 'work-widget-link-data', enforce: 'pre', transform(code,id) {
    if (!id.split('?')[0].endsWith('/tests/shell-fixture.tsx')) return null;
    const anchor = 'async function api(path: string';
    assert.ok(code.includes(anchor));
    const cases = `const widgetCase = new URLSearchParams(location.search).get('workCase');
      if (widgetCase === 'many') {
        work.splice(0, work.length, ...Array.from({length:8},(_,i)=>({...work[0],id:'many-'+i,issue_id:80+i,title:'Many synthetic '+i,status:'queued',claim_active:false})));
      }
      if (widgetCase === 'invalid') {
        const template=work[0];
        work.splice(0,work.length,...[
          {...template,id:'missing-repo',repository:undefined,issue_id:71,title:'Missing repository',status:'queued'},
          {...template,id:'bad-repo',repository:'owner name/repo',issue_id:71,title:'Malformed repository',status:'queued'},
          {...template,id:'missing-id',issue_id:undefined,title:'Missing issue',status:'queued'},
          {...template,id:'zero-id',issue_id:0,title:'Nonpositive issue',status:'queued'},
          {...template,id:'valid',issue_id:72,title:'Valid issue',status:'queued'},
        ] as any[]);
      }
    `;
    return code.replace(anchor,cases+anchor);
  }}], server:{host:'127.0.0.1',port:0,watch:null}});
  let browser;
  try {
    await fs.mkdir(evidence,{recursive:true}); await server.listen();
    browser = await chromium.launch({headless:true,executablePath,args:['--no-sandbox','--disable-dev-shm-usage']});
    const context = await browser.newContext({viewport:{width:1440,height:900}});
    const intercepted=[];
    // Verify actual new-tab navigation while avoiding all real Forgejo requests/content.
    await context.route('https://192.168.0.101:9445/**', async route=>{
      if (route.request().isNavigationRequest()) intercepted.push(route.request().url());
      await route.fulfill({status:200,contentType:'text/html',body:'<!doctype html><title>Local intercepted Issue navigation</title><p>Test navigation response only.</p>'});
    });
    const page = await context.newPage(); const errors=[];
    page.on('pageerror',error=>errors.push(String(error)));
    const url=`http://127.0.0.1:${server.httpServer.address().port}/tests/shell-fixture.html`;
    const enable = async () => {
      await page.locator('.desktop').waitFor();
      await page.locator('nav.dock button[title="系统设置"]').click();
      await page.locator('.window[data-app-id="settings"] .settings-panel').waitFor();
      const checkbox=page.locator('.widget-options label').filter({hasText:'任务队列'}).locator('input');
      await checkbox.check();
      await page.locator('.window[data-app-id="settings"] .bar button.close').click();
      await page.locator('.widget-work li').first().waitFor();
    };
    await page.goto(url); await enable();
    const widget=page.locator('.widget-work');
    assert.equal(await widget.locator('li').count(),2,'active status/claim filtering is unchanged');
    assert.deepEqual(await widget.locator('li em').allTextContents(),['queued','claimed']);
    const link=widget.locator('li a').first();
    const href=await link.getAttribute('href');
    assert.equal(href,'https://192.168.0.101:9445/xenon-admin/XeonoLab/issues/71');
    assert.equal(await link.getAttribute('target'),'_blank');
    assert.equal(await link.getAttribute('rel'),'noopener noreferrer');
    const open = async keyboard => {
      const popup=context.waitForEvent('page');
      if (keyboard) {await link.focus(); await page.keyboard.press('Shift+Tab'); await page.keyboard.press('Tab'); assert.equal(await link.evaluate(el=>el===document.activeElement),true,'native Tab navigation reaches the link'); await page.keyboard.press('Enter');} else await link.click();
      const tab=await popup; await tab.waitForLoadState('domcontentloaded');
      assert.equal(tab.url(),href); assert.equal(await tab.evaluate(()=>window.opener),null);
      await tab.close();
    };
    await open(false); await open(true); assert.deepEqual(intercepted,[href,href]);
    await page.screenshot({path:path.join(evidence,'work-widget-links.png')});
    await widget.getByRole('button',{name:'打开任务',exact:true}).click();
    const issues=page.locator('.window[data-app-id="issues"] .issues-app'); await issues.waitFor();
    await issues.getByRole('searchbox',{name:'搜索任务'}).fill('71');
    assert.equal(await issues.locator('.issues-table tbody tr').filter({hasText:'#71'}).locator('a').getAttribute('href'),href,'task center uses same base and URL helper');
    await page.goto(url+'?workCase=many'); await enable();
    assert.equal(await widget.locator('li').count(),5); assert.equal(await widget.locator('li a').count(),5);
    assert.ok((await widget.innerText()).includes('8 项进行中'),'more than five items keep full active count');
    await page.goto(url+'?workCase=invalid'); await enable();
    assert.equal(await widget.locator('li').count(),5); assert.equal(await widget.locator('li a').count(),1);
    for(const title of ['Missing repository','Malformed repository','Missing issue','Nonpositive issue']) {
      const row=widget.locator('li').filter({hasText:title}); assert.equal(await row.locator('a').count(),0); assert.equal(await row.locator('span.work-title').count(),1);
    }
    assert.equal(errors.length,0);
    const report={baseline:'2aabf62',source:'Real headless Chrome renders DesktopShell/WorkWidget/Issues using synthetic shell-fixture Work API and preferences. Test-only Vite data cases cover >5 items and benign missing/malformed metadata. Exact new-tab URLs are intercepted and fulfilled locally; no real Forgejo page or login was accessed.',href,intercepted,checks:['pointer and keyboard open accurate URL','noopener opener null','Open Tasks enters existing task center with same issue URL','status filtering and five-item summary unchanged','invalid metadata remains plain text'],pageErrors:errors,outcome:'PASS'};
    await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify(report,null,2)); console.log(JSON.stringify(report));
  } finally {await browser?.close(); await server.close();}
});
