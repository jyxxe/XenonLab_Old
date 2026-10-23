import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const subject = 'fixture-user';
const storageKey = `xeonolab.library.${encodeURIComponent(subject)}.recent`;

test('sidebar Recent expands saved entries beyond six with an accessible control', async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: 0, watch: null}});
  await server.listen();
  const browser = await chromium.launch({executablePath, headless: true});
  const origin = server.resolvedUrls.local[0];
  try {
    const page = await browser.newPage({viewport: {width: 1440, height: 950}});
    const pageErrors = [], external = [];
    page.on('pageerror', error => pageErrors.push(String(error)));
    page.on('request', request => {if (!request.url().startsWith(origin)) external.push(request.url())});
    await page.goto(`${origin}tests/library-fixture.html`);
    const seeded = Array.from({length: 8}, (_, index) => ({
      kind: 'row', id: `t1:row-${String(index + 1).padStart(3, '0')}`, title: `Synthetic row ${index + 1}`, at: 1_700_000_000 - index,
    }));
    await page.evaluate(([key, value]) => localStorage.setItem(key, value), [storageKey, JSON.stringify(seeded)]);
    await page.reload();
    const section = page.locator('.library-sidebar-section').filter({hasText: '最近'});
    await section.locator('.library-mini-list li').first().waitFor();
    assert.equal(await section.locator('small').innerText(), '8', 'the count shows every saved recent entry');
    assert.equal(await section.locator('.library-mini-list li').count(), 6, 'the default list stays capped at six');
    const toggle = section.locator('.library-recent-toggle');
    assert.equal(await toggle.innerText(), '显示全部 8 项');
    assert.equal(await toggle.getAttribute('aria-expanded'), 'false');

    await toggle.click();
    await page.waitForFunction(() => document.querySelectorAll('.library-sidebar-section .library-mini-list li').length === 8);
    assert.equal(await toggle.innerText(), '收起到 6 项');
    assert.equal(await toggle.getAttribute('aria-expanded'), 'true');

    await toggle.click();
    await page.waitForFunction(() => document.querySelectorAll('.library-sidebar-section .library-mini-list li').length === 6);
    assert.equal(await toggle.innerText(), '显示全部 8 项');
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(external, []);
    assert.deepEqual(await page.evaluate(() => window.libraryFixture.calls.filter(call => call.method !== 'GET').map(call => call.path)), [], 'expanding is read-only');
    await page.close();
  } finally {
    await browser.close();
    await server.close();
  }
});
