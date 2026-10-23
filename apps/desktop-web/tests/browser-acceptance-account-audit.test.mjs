import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('account audit explicitly appends, retries, finishes, and fences stale identity/API responses', async t => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: 0, watch: null}});
  await server.listen();
  const browser = await chromium.launch({executablePath, headless: true});
  try {
    for (const mode of ['full', 'retry', 'short', 'deferred', 'api-change', 'unmount']) {
      await t.test(mode, async () => {
        const page = await browser.newPage();
        const errors = [], external = [];
        page.on('pageerror', error => errors.push(String(error)));
        page.on('request', request => {if (!request.url().startsWith(server.resolvedUrls.local[0])) external.push(request.url())});
        try {
          const fixtureMode = ['api-change', 'unmount'].includes(mode) ? 'deferred' : mode;
          await page.goto(`${server.resolvedUrls.local[0]}tests/account-audit-fixture.html?mode=${fixtureMode}`);
          await page.locator('.account-audit summary').click();
          assert.equal(await page.evaluate(() => window.__auditCalls.length), 0);
          const audit = page.locator('.account-audit');
          await audit.getByRole('button', {name: '读取最近记录'}).click();
          await audit.locator('li').nth(mode === 'short' ? 2 : 199).waitFor();
          assert.equal(await page.evaluate(() => window.__auditCalls.length), 1);
          assert.ok((await audit.locator('li').first().innerText()).includes('Root root（root-login） · root'));
          assert.ok((await audit.locator('li').first().innerText()).includes('Child（child-login） · child'));
          if (mode === 'short') {
            assert.equal(await audit.getByRole('button', {name: '加载更早记录'}).count(), 0);
            await audit.getByRole('status').filter({hasText: '历史已读完'}).waitFor();
          } else {
            // Move within the list. Appending must preserve this scroll position.
            const position = await page.locator('#root').evaluate(element => {element.scrollTop = 1500; return element.scrollTop});
            // Invoke the visible control through the DOM to avoid Playwright's own scroll-into-view.
            await audit.getByRole('button', {name: '加载更早记录'}).evaluate(button => button.click());
            if (mode === 'retry') {
              await audit.getByRole('alert').filter({hasText: '合成读取故障'}).waitFor();
              assert.equal(await audit.locator('li').count(), 200);
              await audit.getByRole('button', {name: '重试读取'}).evaluate(button => button.click());
            }
            if (['deferred', 'api-change', 'unmount'].includes(mode)) {
              await audit.getByRole('status').filter({hasText: '正在读取'}).waitFor();
              assert.equal(await audit.getByRole('button', {name: '加载更早记录'}).isEnabled(), false);
              if (mode === 'deferred') await page.evaluate(() => window.__changeIdentity());
              if (mode === 'api-change') await page.evaluate(() => window.__changeAPI());
              if (mode === 'unmount') await page.evaluate(() => window.__unmountAudit());
              await page.waitForFunction(() => !document.querySelector('.account-audit li'));
              await page.evaluate(() => window.__resolveAudit());
              await page.waitForTimeout(50);
              assert.equal(await audit.locator('li').count(), 0);
              if (mode !== 'unmount') {
                await audit.getByRole('button', {name: '读取最近记录'}).click();
                await audit.locator('li').first().waitFor();
                const expected = mode === 'deferred' ? 'other-0' : 'replacement-0';
                assert.equal(await audit.locator('li').first().getAttribute('data-event-id'), expected);
              }
            } else {
              await audit.locator('li').nth(206).waitFor();
              assert.equal(await audit.locator('li').count(), 207);
              assert.equal(await page.locator('#root').evaluate(element => element.scrollTop), position);
              const ids = await audit.locator('li').evaluateAll(rows => rows.map(row => row.dataset.eventId));
              assert.equal(new Set(ids).size, 207);
              assert.deepEqual(ids, Array.from({length: 207}, (_, index) => `root-${index}`));
              await audit.getByRole('status').filter({hasText: '历史已读完'}).waitFor();
              assert.equal(await audit.getByRole('button', {name: '加载更早记录'}).count(), 0);
              const calls = await page.evaluate(() => window.__auditCalls);
              assert.equal(calls.length, mode === 'retry' ? 3 : 2);
              assert.equal(calls[1].path, '/auth/accounts/audit/events?cursor=older');
              if (mode === 'retry') assert.equal(calls[2].path, calls[1].path);
            }
          }
          assert.deepEqual(errors, []);
          assert.deepEqual(external, []);
        } finally {await page.close()}
      });
    }
  } finally {await browser.close(); await server.close()}
});
