import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('定理视图按 xelibrary:theorem:read 门控：无权限时诚实空态且不发注定失败的请求', async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: 0, watch: null}});
  await server.listen();
  const browser = await chromium.launch({executablePath, headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  const calls = () => page.evaluate(() => window.libraryFixture.calls);
  const theoremsNav = page.locator('.library-nav button', {hasText: '定理'}).first();
  try {
    await page.goto(`${server.resolvedUrls.local[0]}tests/library-fixture.html?noTheoremRead=1`);
    await theoremsNav.waitFor();
    await page.evaluate(() => window.libraryFixture.resetCalls());
    await theoremsNav.click();
    await page.getByText('需要定理读取权限').waitFor();
    assert.match(await page.locator('.library-theorems').innerText(), /xelibrary:theorem:read/);
    assert.equal(await page.locator('input[aria-label="搜索定理"]').count(), 0, 'the doomed search bar is not offered without permission');
    assert.equal((await calls()).some(call => call.path.startsWith('/theorems')), false, 'no theorem request is fired without the scope');

    await page.goto(`${server.resolvedUrls.local[0]}tests/library-fixture.html`);
    await theoremsNav.waitFor();
    await theoremsNav.click();
    await page.locator('.library-theorem-list li').first().waitFor();
    assert.ok((await calls()).some(call => call.path === '/theorems?limit=50'), 'an authorized identity still loads the list');
    assert.ok(await page.locator('.library-theorem-list li').count() >= 2, 'fixture theorems render');
  } finally {
    await browser.close();
    await server.close();
  }
  assert.deepEqual(pageErrors, []);
});
