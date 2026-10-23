import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const evidence = path.resolve(appRoot, '../../runtime/verification/issue199-forgejo-origins');
const desktops = [
  {name:'public',origin:'https://xenon.jyxxe.com',forgejo:'https://xenon.jyxxe.com/dev'},
  {name:'lan-ip',origin:'https://192.168.0.101:9443',forgejo:'https://192.168.0.101:9445'},
];

test('Work, Dashboard and WorkWidget use the current desktop Forgejo origin on actual clicks', async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const server = await createServer({root:appRoot,server:{host:'127.0.0.1',port:0,watch:null,hmr:false}});
  let browser;
  try {
    await server.listen();
    const local = `http://127.0.0.1:${server.httpServer.address().port}`;
    await fs.mkdir(evidence,{recursive:true});
    browser = await chromium.launch({executablePath,headless:true});
    const checks=[];
    for (const desktop of desktops) {
      const context = await browser.newContext({viewport:{width:1440,height:1000}});
      const navigations=[], blocked=[], pageErrors=[];
      // The browser has real public/LAN location.hostnames, but every resource
      // comes from loopback Vite. No desktop or Forgejo server is contacted.
      await context.routeWebSocket('**',socket=>socket.close());
      await context.route('**/*', async route => {
        const request=route.request(), url=new URL(request.url());
        if (url.origin === desktop.origin && !url.pathname.startsWith('/dev/')) {
          const response=await route.fetch({url:local+url.pathname+url.search});
          await route.fulfill({response});
        } else if (url.origin === new URL(desktop.forgejo).origin && /\/issues\/\d+$/.test(url.pathname)) {
          navigations.push(request.url());
          await route.fulfill({status:200,contentType:'text/html',body:'<!doctype html><title>Intercepted synthetic Issue navigation</title>'});
        } else {
          blocked.push(request.url());
          await route.abort();
        }
      });
      const page=await context.newPage();
      page.on('pageerror',error=>pageErrors.push(error.message));
      for (const view of ['work','dashboard','widget']) {
        await page.goto(`${desktop.origin}/tests/forgejo-origins-fixture.html?view=${view}`);
        assert.equal(await page.evaluate(()=>location.hostname),new URL(desktop.origin).hostname);
        const link=page.getByRole('link',{name:/Origin regression source Issue/});
        await link.waitFor();
        const expected=`${desktop.forgejo}/xenon-admin/XeonoLab/issues/51`;
        assert.equal(await link.getAttribute('href'),expected);
        assert.equal(await link.getAttribute('target'),'_blank');
        assert.equal(await link.getAttribute('rel'),'noopener noreferrer');
        for (const title of ['Malformed repository','Invalid issue number']) {
          assert.equal(await page.getByRole('link',{name:new RegExp(title)}).count(),0,'invalid metadata stays plain text');
          await page.getByText(title,{exact:false}).waitFor();
        }
        const popup=context.waitForEvent('page');
        await link.click(); // Ordinary pointer action; clipped links must fail.
        const tab=await popup;
        await tab.waitForLoadState('domcontentloaded');
        assert.equal(tab.url(),expected);
        assert.equal(await tab.evaluate(()=>window.opener),null);
        await tab.close();
        await page.screenshot({path:path.join(evidence,`${desktop.name}-${view}.png`)});
        checks.push({desktop:desktop.origin,view,href:expected,clickNavigation:navigations.at(-1)});
      }
      assert.equal(navigations.length,3);
      assert.deepEqual(blocked,[]);
      assert.deepEqual(pageErrors,[]);
      await context.close();
    }
    const report={source:'Real Chromium renders repository Work/Dashboard/WorkWidget. All API data and identity are synthetic. Public and LAN document origins and all Issue popups are intercepted locally; every asset is served by loopback Vite. No real desktop/Forgejo request, login, DNS/TLS reachability or production service is verified.',checks,outcome:'PASS'};
    await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify(report,null,2));
    console.log(JSON.stringify(report));
  } finally {
    await browser?.close();
    await server.close();
  }
});
