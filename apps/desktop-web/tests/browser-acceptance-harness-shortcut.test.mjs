import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('LAN desktop offers the DeepSeek Harness quick entry and opens the same-origin LAN path', async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const server = await createServer({
    root: appRoot,
    optimizeDeps: {include: ['react', 'react-dom/client', 'react/jsx-dev-runtime']},
    server: {host: '127.0.0.1', port: 0, watch: null},
  });
  await server.listen();
  const browser = await chromium.launch({executablePath, headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 1280, height: 900}});
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    // The shortcut must open the same-origin LAN path and must not navigate the outgoing window.
    await page.addInitScript(() => {
      window.__harnessOpen = null;
      window.open = (url, target, features) => {
        window.__harnessOpen = {url: String(url), target: String(target || ''), features: String(features || '')};
        return null;
      };
    });
    await page.goto(`${server.resolvedUrls.local[0]}tests/shell-fixture.html`);
    await page.locator('nav.dock').waitFor();
    await page.locator('button[title="LaunchPad"]').click();
    await page.locator('.launchpad').waitFor();
    await page.locator('.launchpad-search').fill('Harness');
    const option = page.getByRole('option').filter({hasText: 'DeepSeek Harness'}).first();
    await option.waitFor();
    await option.click();
    await page.locator('.window[data-app-id=harness]').waitFor();
    assert.match(await page.locator('.window[data-app-id=harness]').innerText(), /DeepSeek Harness/);
    await page.getByRole('button', {name: '打开 DeepSeek Harness', exact: true}).click();
    assert.deepEqual(await page.evaluate(() => window.__harnessOpen), {url: '/harness', target: '_blank', features: 'noopener'});
    assert.deepEqual(errors, []);
  } finally {
    await browser.close();
    await server.close();
  }
});
