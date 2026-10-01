import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium, expect} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('Chrome: real API boundary renders body/header request IDs and separate error codes', async t => {
  const executablePath = process.env.CHROME_BIN || ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'].find(existsSync);
  if (!executablePath) {t.skip('Chrome/Chromium unavailable; set CHROME_BIN');return;}
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: 0, watch: null}});
  await server.listen();
  const base = server.resolvedUrls.local[0].replace(/\/$/, '');
  let browser;
  try {
    browser = await chromium.launch({executablePath, headless: true});
    const page = await browser.newPage({viewport: {width: 1440, height: 900}});
    const exceptions = [];
    page.on('pageerror', error => exceptions.push(error.message));
    let mode = 'body', logouts = 0;
    await page.route(base + '/api/v1/**', async route => {
      const request = route.request(), path = new URL(request.url()).pathname.slice('/api/v1'.length);
      if (path === '/auth/session/token') return route.fulfill({json: {token: 'synthetic-token', expires: 9999999999}});
      if (path === '/auth/session/logout') {logouts++;return route.fulfill({json: {}});}
      if (path === '/tables') {
        if (mode === 'header') return route.fulfill({status: 403, contentType: 'text/html', headers: {'X-Request-ID': 'header-correlation'}, body: '<h1>Gateway denied</h1>'});
        return route.fulfill({status: 403, headers: mode === 'body' ? {'X-Request-ID': 'header-fallback'} : {}, json: {error: {code: 'XL_FORBIDDEN', message: 'Access denied', ...(mode === 'body' ? {request_id: 'body-correlation'} : {})}}});
      }
      let data = [], meta = {};
      if (path === '/users/me') data = {subject: 'synthetic-user', kind: 'human', scopes: ['desktop:read', 'xelibrary:read']};
      else if (path === '/users/me/preferences') data = {theme: 'dark', accent: 'blue', wallpaper: 'silver', timezone: 'UTC', widgets: [], dock_size: 'medium', dock_autohide: false, restore_windows: true, animations: false, reduced_motion: true};
      else if (path === '/users/me/layout') data = {dock: [], windows: []};
      await route.fulfill({json: {data, meta}});
    });
    await page.goto(base + '/');
    await expect(page.locator('nav.dock')).toBeVisible();
    await page.locator('nav.dock button[title="知识库"]').click();
    const error = page.locator('.knowledge .ds-error');
    await expect(error).toContainText('权限不足');
    await expect(error.locator('.ds-error-code')).toHaveText('错误代码 XL_FORBIDDEN');
    await expect(error.locator('.ds-request-id')).toHaveText('请求编号 body-correlation');
    await expect(error).not.toContainText('header-fallback');
    if (process.env.UI_SCREENSHOT_DIR) {
      await fs.mkdir(process.env.UI_SCREENSHOT_DIR, {recursive: true});
      await page.screenshot({path: path.join(process.env.UI_SCREENSHOT_DIR, 'error-dark-1440x900.png')});
      await page.evaluate(() => document.documentElement.dataset.theme = 'light');
      await page.screenshot({path: path.join(process.env.UI_SCREENSHOT_DIR, 'error-light-1440x900.png')});
      await page.setViewportSize({width: 390, height: 844});
      await expect(error).toBeVisible();
      assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));
      await page.screenshot({path: path.join(process.env.UI_SCREENSHOT_DIR, 'error-light-390x844.png')});
      await page.setViewportSize({width: 1440, height: 900});
    }


    mode = 'header';
    await error.getByRole('button', {name: '重试', exact: true}).click();
    await expect(error.locator('.ds-request-id')).toHaveText('请求编号 header-correlation');
    await expect(error.locator('.ds-error-code')).toHaveCount(0);
    await expect(error).not.toContainText('Gateway denied');

    mode = 'missing';
    await error.getByRole('button', {name: '重试', exact: true}).click();
    await expect(error.locator('.ds-error-code')).toHaveText('错误代码 XL_FORBIDDEN');
    await expect(error.locator('.ds-request-id')).toHaveCount(0);
    assert.equal(logouts, 0, '403 error presentation never logs the session out');
    assert.deepEqual(exceptions, []);
  } finally {
    try {await browser?.close();} finally {await server.close();}
  }
});
