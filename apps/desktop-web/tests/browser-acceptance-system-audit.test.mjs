import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium, expect} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const evidence = path.resolve(appRoot,'../../runtime/verification/issue212');

test('System audit filters only loaded records, keeps pagination/order and distinguishes read states', async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const server = await createServer({root:appRoot, server:{host:'127.0.0.1',port:0,watch:null},cacheDir:path.join(evidence,'vite-cache')});
  let browser;
  const calls=[], external=[], errors=[];
  let mode='normal';
  const cursor='audit/older?next=2';
  const first=Array.from({length:100},(_,i)=>({id:`audit-${i}`,at:1790990000-i,event:i===0?'Key.Created':'table.updated',actor:i===0?'Alice':'Operator',resource:i===0?'key-alpha':`table-${i}`}));
  const older=[{...first[99]},
    {id:'audit-100',at:'2026-09-30T10:00:00Z',event:'KEY.revoked',actor:'ALICE',target:'key-beta'},
    {id:'audit-101',at:1790800000,event:'table.created',actor:'Bob',resource:'table-older'},
    {id:'audit-102',at:1790700000,event:'table.deleted',actor:'Operator',resource:'table-oldest'}];
  try {
    await fs.mkdir(evidence,{recursive:true});
    await server.listen();
    const origin=new URL(server.resolvedUrls.local[0]).origin;
    browser=await chromium.launch({executablePath,headless:true});
    const context=await browser.newContext({viewport:{width:1000,height:800},locale:'en-US',timezoneId:'Asia/Shanghai'});
    await context.route('**/*', async route => {
      const url=new URL(route.request().url());
      if(url.origin!==origin){external.push(url.href);return route.abort();}
      if(!url.pathname.startsWith('/fixture-api')) return route.continue();
      const apiPath=url.pathname.slice('/fixture-api'.length)+url.search;
      calls.push(apiPath);
      let data=[],meta={next_cursor:null};
      if(url.pathname.endsWith('/users/me')) data={subject:'synthetic-audit-admin',scopes:mode==='denied'?['xelibrary:read']:['xelibrary:admin']};
      else if(url.pathname.endsWith('/audit')) {
        if(mode==='error'||(mode==='page-error'&&url.searchParams.has('cursor'))){
          return route.fulfill({status:503,json:{error:{code:'FIXTURE_UNAVAILABLE',message:'Synthetic audit read unavailable'}}});
        }
        if(mode!=='empty') {
          data=url.searchParams.has('cursor')?older:first;
          meta.next_cursor=url.searchParams.has('cursor')?null:cursor;
        }
      }
      return route.fulfill({json:{data,meta}});
    });
    const page=await context.newPage();
    page.on('pageerror',e=>errors.push(e.message));
    async function openAudit(nextMode='normal'){
      mode=nextMode;
      await page.goto(`${origin}/tests/system-audit-fixture.html`);
      await page.getByRole('button',{name:'审计',exact:true}).click();
    }
    const input=page.getByRole('searchbox',{name:'筛选已加载审计记录'});
    const status=page.getByRole('status').filter({hasText:'已加载'});
    const dataRows=page.locator('.system-content tbody tr');
    await openAudit();
    await expect(status).toContainText('匹配 100 条 / 已加载 100 条');
    const beforeTyping=calls.length;
    await input.fill('  aLIce  ');
    await expect(status).toContainText('匹配 1 条 / 已加载 100 条');
    await expect(dataRows).toHaveCount(1);
    await expect(dataRows.first()).toContainText('key-alpha');
    assert.equal(calls.length,beforeTyping,'typing only filters local rows');
    // A cursor page duplicates one existing ID and adds three older records.
    await page.getByRole('button',{name:'加载更多',exact:true}).click();
    await expect(status).toContainText('匹配 2 条 / 已加载 103 条');
    await expect(input).toHaveValue('  aLIce  ');
    await expect(dataRows).toHaveCount(2);
    assert.deepEqual(await dataRows.locator('td:last-child').allTextContents(),['key-alpha','key-beta']);
    assert.ok(calls.includes(`/audit?limit=100&cursor=${encodeURIComponent(cursor)}`));
    await expect(page.getByRole('button',{name:'加载更多',exact:true})).toHaveCount(0);
    await page.screenshot({path:path.join(evidence,'filtered-after-pagination.png'),fullPage:true});

    const afterPaging=calls.length;
    await input.fill('kEy.ReVoKeD');
    await expect(dataRows).toHaveCount(1);
    await expect(dataRows.first()).toContainText('key-beta');
    await input.fill('KEY-ALPHA');
    await expect(dataRows).toHaveCount(1);
    const visibleTime=await dataRows.first().locator('td').first().innerText();
    await input.fill(visibleTime);
    await expect(dataRows).toHaveCount(1);
    await expect(dataRows.first()).toContainText('key-alpha');
    await input.fill('not-a-loaded-event');
    await expect(page.getByText('没有匹配的审计记录',{exact:true})).toBeVisible();
    await expect(status).toContainText('匹配 0 条 / 已加载 103 条');
    await expect(page.getByText('暂无记录',{exact:true})).toHaveCount(0);
    // Clear is reached by native Tab and Enter; focus returns to the input.
    await input.press('Tab');
    await expect(page.getByRole('button',{name:'清除筛选',exact:true})).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(input).toHaveValue('');
    await expect(input).toBeFocused();
    await expect(status).toContainText('匹配 103 条 / 已加载 103 条');
    assert.deepEqual(await dataRows.locator('td:last-child').allTextContents(),[...first.map(r=>r.resource),'key-beta','table-older','table-oldest']);
    assert.equal(calls.length,afterPaging,'event/object/time/empty/clear do not call API');
    await page.setViewportSize({width:390,height:844});
    await input.fill('Alice');
    await expect(dataRows).toHaveCount(2);
    const bounds=await page.getByRole('button',{name:'清除筛选',exact:true}).boundingBox();
    assert.ok(bounds.x>=0&&bounds.x+bounds.width<=390,'clear is fully reachable at 390px');
    await input.press('Tab');
    await expect(page.getByRole('button',{name:'清除筛选',exact:true})).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(input).toBeFocused();
    await expect(status).toContainText('匹配 103 条 / 已加载 103 条');
    await input.fill('Alice');
    await expect(dataRows).toHaveCount(2);
    assert.equal(calls.length,afterPaging,'narrow-screen keyboard clear stays local');
    await page.screenshot({path:path.join(evidence,'audit-filter-390.png'),fullPage:true});

    await openAudit('empty');
    await expect(page.getByText('暂无记录',{exact:true})).toBeVisible();
    await expect(status).toContainText('匹配 0 条 / 已加载 0 条');
    await expect(page.getByText('没有匹配的审计记录',{exact:true})).toHaveCount(0);
    await openAudit('error');
    await expect(page.getByRole('alert')).toContainText('Synthetic audit read unavailable');
    await expect(page.getByText('暂无记录',{exact:true})).toHaveCount(0);
    mode='normal'; // Operator retry follows explicit fixture service recovery.
    await page.getByRole('button',{name:'重试',exact:true}).click();
    await expect(status).toContainText('匹配 100 条 / 已加载 100 条');
    await openAudit('page-error');
    await expect(status).toContainText('已加载 100 条');
    await input.fill('alice');
    await expect(status).toContainText('匹配 1 条');
    await page.getByRole('button',{name:'加载更多',exact:true}).click();
    await expect(page.getByRole('alert')).toContainText('Synthetic audit read unavailable');
    await expect(page.getByText('没有匹配的审计记录',{exact:true})).toHaveCount(0);
    const deniedStart=calls.length;
    await openAudit('denied');
    await expect(page.getByRole('alert')).toContainText('xelibrary:admin');
    assert.equal(calls.slice(deniedStart).filter(p=>p.startsWith('/audit')).length,0,'scope check prevents audit fetch');
    assert.deepEqual(errors,[]);
    assert.deepEqual(external,[]);
    await fs.writeFile(path.join(evidence,'browser-report.json'),JSON.stringify({outcome:'PASS',source:'Real Chromium renders repository System component. All HTTP API envelopes, identity/scopes and 103 unique audit records are synthetic responses intercepted at physical loopback Vite; no production identity, records, authorization service or server-side search is verified.',checks:['case insensitive event/actor/object','visible locale time','103 ordered deduplicated loaded rows','cursor keeps filter','typing and clear no API requests','native Tab/Enter clear','390px controls','no records vs no match vs initial/page error','retry','admin scope blocks audit fetch'],calls,external,pageErrors:errors},null,2));
  } catch(error) {
    await fs.writeFile(path.join(evidence,'browser-failure.json'),JSON.stringify({mode,calls,external,errors,error:String(error)},null,2));
    throw error;
  } finally {
    try{await browser?.close();}finally{await server.close();}
  }
});
