import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync, mkdirSync, mkdtempSync, rmSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium, expect} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const evidence = path.resolve(appRoot, '../../runtime/verification/issue227');

test('Calendar browses civil months by pointer and keyboard with timezone-aware today', async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const cacheDir = mkdtempSync(path.join(os.tmpdir(), 'xeonolab-calendar-'));
  const server = await createServer({root: appRoot, cacheDir, optimizeDeps: {include:['react','react-dom/client','react/jsx-runtime','react/jsx-dev-runtime']}, server: {host:'127.0.0.1',port:0,watch:null}});
  await server.listen();
  const browser = await chromium.launch({executablePath, headless:true});
  const page = await browser.newPage({viewport:{width:390,height:620}, timezoneId:'UTC'});
  const errors = [], externalRequests = [], apiRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    const url = new URL(request.url());
    if (!['127.0.0.1','localhost'].includes(url.hostname)) externalRequests.push(request.url());
    if (url.pathname.startsWith('/api/') || url.pathname === '/health/ready') apiRequests.push(request.url());
  });
  const calendar = page.locator('.widget-calendar');
  const previous = calendar.getByRole('button', {name:'上个月',exact:true});
  const next = calendar.getByRole('button', {name:'下个月',exact:true});
  const today = calendar.getByRole('button', {name:'回到今天',exact:true});
  const month = calendar.getByRole('status');
  const currentDay = calendar.locator('[aria-current="date"]');
  const zone = page.getByRole('combobox', {name:'合成配置时区'});
  try {
    // At this instant the UTC browser is still in December 2023, but the
    // configured +14 timezone has reached January 2024. All assets are local.
    await page.clock.install({time:new Date('2023-12-31T12:30:00Z')});
    await page.goto(`${server.resolvedUrls.local[0]}tests/calendar-fixture.html?timezone=Pacific%2FKiritimati`);
    await expect(month).toHaveText('2024年1月');
    await expect(currentDay).toHaveText('1');
    await expect(calendar.getByRole('grid')).toHaveAttribute('aria-label','2024年1月日期');
    await expect(month).toHaveAttribute('aria-live','polite');
    assert.equal(await calendar.locator('[role="gridcell"]').count(),42);

    await page.keyboard.press('Tab');
    await expect(previous).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(month).toHaveText('2023年12月');
    await expect(currentDay).toHaveCount(0);
    await page.keyboard.press('Tab');
    await expect(today).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(month).toHaveText('2024年1月');
    await expect(currentDay).toHaveText('1');
    await page.keyboard.press('Tab');
    await expect(next).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(month).toHaveText('2024年2月');
    await expect(calendar.getByRole('gridcell',{name:'2024年2月29日',exact:true})).toBeVisible();
    await expect(calendar.getByRole('gridcell',{name:'2024年2月30日',exact:true})).toHaveCount(0);
    await expect(currentDay).toHaveCount(0);
    mkdirSync(evidence,{recursive:true});
    await page.screenshot({path:path.join(evidence,'calendar-leap-keyboard.png')});

    await next.click();
    await expect(month).toHaveText('2024年3月');
    await previous.click();
    await expect(month).toHaveText('2024年2月');
    await today.click();
    await expect(month).toHaveText('2024年1月');
    await previous.click();
    await expect(month).toHaveText('2023年12月');
    await next.click();
    await expect(month).toHaveText('2024年1月');
    await next.click();
    await page.reload();
    await expect(month).toHaveText('2024年1月');
    await expect(currentDay).toHaveText('1');

    // Configuration changes reset browsing. Invalid zones share the existing
    // browser fallback for both the date and month heading instead of throwing.
    await next.click();
    await zone.selectOption('UTC');
    await expect(month).toHaveText('2023年12月');
    await expect(currentDay).toHaveText('31');
    await zone.selectOption('not/a-timezone');
    await expect(month).toHaveText('2023年12月');
    await expect(currentDay).toHaveText('31');
    await zone.selectOption('Pacific/Kiritimati');
    await expect(month).toHaveText('2024年1月');
    await expect(currentDay).toHaveText('1');
    await page.screenshot({path:path.join(evidence,'calendar-timezone-today.png')});

    // A timer update follows today's civil month only while not browsing. The
    // selected February stays put when local today rolls into January 2.
    await next.click();
    await page.clock.setSystemTime(new Date('2024-01-01T12:30:00Z'));
    await page.clock.runFor(60_000);
    await expect(month).toHaveText('2024年2月');
    await expect(currentDay).toHaveCount(0);
    await today.click();
    await expect(month).toHaveText('2024年1月');
    await expect(currentDay).toHaveText('2');
    await page.clock.setSystemTime(new Date('2024-01-31T10:01:00Z'));
    await page.clock.runFor(60_000);
    await expect(month).toHaveText('2024年2月');
    await expect(currentDay).toHaveText('1');
    const bounds = await calendar.evaluate(element=>({scroll:element.scrollWidth,client:element.clientWidth}));
    assert.ok(bounds.client<=224,'fixture uses the real shell base styles and widget width');
    assert.ok(bounds.scroll<=bounds.client,'controls fit the existing 224px widget');
    assert.deepEqual(errors,[]);
    assert.deepEqual(externalRequests,[]);
    assert.deepEqual(apiRequests,[]);
  } finally {
    await browser.close();
    await server.close();
    rmSync(cacheDir,{recursive:true,force:true});
  }
});
