import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium, expect} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('one-time API key blocks account switching until acknowledged', async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: 0, watch: null}});
  await server.listen();
  const browser = await chromium.launch({executablePath, headless: true});
  const page = await browser.newPage({viewport: {width: 1280, height: 900}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    await page.goto(`${server.resolvedUrls.local[0]}tests/account-api-key-fixture.html`);
    await page.getByRole('button', {name: /^Child Account/}).waitFor();
    await page.getByLabel('新 key 名称').fill('acceptance-key');
    await page.getByRole('button', {name: '建立 API key'}).click();
    const token = page.getByLabel('新 API key（仅显示一次）');
    await expect(token).toHaveValue('xek-ONLY-ONCE-SECRET');
    // Switching account while the token is unsaved must not hide it.
    await page.getByRole('button', {name: /^Child Account/}).click();
    await expect(token).toHaveValue('xek-ONLY-ONCE-SECRET');
    await expect(page.getByRole('status').first()).toContainText('尚未保存');
    await page.getByRole('button', {name: '已保存，关闭'}).click();
    await page.getByRole('button', {name: /^Child Account/}).click();
    await expect(page.getByRole('heading', {name: 'Child Account', exact: true})).toBeVisible();
  } finally {
    await browser.close();
    await server.close();
  }
  assert.deepEqual(errors, []);
});
