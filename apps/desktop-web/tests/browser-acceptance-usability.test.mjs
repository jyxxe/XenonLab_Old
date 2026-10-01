import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium, expect} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('Playwright: real login UI, keyboard navigation, persistent settings and retained sessions', async t => {
  const executablePath = process.env.CHROME_BIN || ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'].find(existsSync);
  if (!executablePath) {t.skip('Chrome/Chromium unavailable; set CHROME_BIN');return;}
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: 0, watch: null}});
  await server.listen();
  const base = server.resolvedUrls.local[0].replace(/\/$/, '');
  let browser;
  try {
    browser = await chromium.launch({executablePath, headless: true});
    const page = await browser.newPage({viewport: {width: 1440, height: 900}, reducedMotion: 'reduce'});
    const exceptions = [];
    page.on('pageerror', error => exceptions.push(error.message));
    const preferences = {theme: 'dark', accent: 'blue', wallpaper: 'silver', timezone: 'UTC', widgets: [], dock_size: 'medium', dock_autohide: false, restore_windows: true, animations: true, reduced_motion: false};
    const state = {mode: 'anonymous', renewals: 0, logouts: 0, exchanges: 0, layout: {dock: [], windows: []}, scopes: ['desktop:read', 'xelibrary:read', 'dev:read']};
    await page.route('https://synthetic-identity.example/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith('/.well-known/openid-configuration')) {
        await route.fulfill({json: {issuer: 'https://synthetic-identity.example/realm', authorization_endpoint: 'https://synthetic-identity.example/realm/authorize', token_endpoint: 'https://synthetic-identity.example/realm/token', jwks_uri: 'https://synthetic-identity.example/realm/jwks'}});
      } else {
        assert.equal(url.searchParams.get('code_challenge_method'), 'S256');
        assert.ok(url.searchParams.get('code_challenge').length >= 43);
        assert.equal(url.searchParams.get('redirect_uri'), base + '/');
        await route.fulfill({status: 302, headers: {location: base + '/?code=synthetic-code&state=' + encodeURIComponent(url.searchParams.get('state'))}, body: ''});
      }
    });
    await page.route(base + '/api/v1/**', async route => {
      const request = route.request(), url = new URL(request.url());
      const path = url.pathname.slice('/api/v1'.length);
      if (path === '/auth/config') return route.fulfill({json: {issuer: 'https://synthetic-identity.example/realm', client_id: 'synthetic-desktop'}});
      if (path === '/auth/session/exchange') {
        const body = request.postDataJSON();
        assert.equal(body.code, 'synthetic-code');
        assert.equal(body.redirect, base + '/');
        assert.ok(body.verifier.length >= 43);
        state.exchanges++;state.mode = 'good';
        return route.fulfill({json: {token: 'synthetic-token-0', expires: 9999999999}});
      }
      if (path === '/auth/session/token') {
        state.renewals++;
        if (state.mode === 'anonymous') return route.fulfill({status: 401, json: {error: {code: 'NO_SESSION'}}});
        if (state.mode === 'temporary') return route.fulfill({status: 503, json: {error: {code: 'PROVIDER_UNAVAILABLE', message: 'Synthetic provider timeout'}}});
        if (state.mode === 'revoked') return route.fulfill({status: 401, json: {error: {code: 'SESSION_REVOKED', message: 'Synthetic session requires authorization'}}});
        return route.fulfill({json: {token: 'synthetic-token-' + state.renewals, expires: 9999999999}});
      }
      if (path === '/auth/session/logout') {state.logouts++;return route.fulfill({json: {}});}
      let data = [], meta = {};
      if (path === '/users/me') data = {subject: 'synthetic-user', kind: 'human', scopes: state.scopes};
      else if (path === '/users/me/preferences') {if (request.method() === 'PATCH') Object.assign(preferences, request.postDataJSON());data = {...preferences};}
      else if (path === '/users/me/layout') {if (request.method() === 'PUT') state.layout = request.postDataJSON();data = state.layout;}
      else if (path === '/jobs') meta = {counts: {queued: 0, running: 0, completed: 0, failed: 0}};
      await route.fulfill({json: {data, meta}});
    });
    await page.route(base + '/health/ready', route => route.fulfill({json: {service: 'desktop-web'}}));
    await page.goto(base + '/');
    await page.getByRole('button', {name: '登录 XeonoLab', exact: true}).click();
    await expect(page.locator('nav.dock')).toBeVisible();
    assert.equal(state.exchanges, 1, 'real callback exchanged the synthetic PKCE code');

    await page.keyboard.press('Control+Space');
    await expect(page.getByRole('dialog', {name: 'LaunchPad', exact: true})).toBeVisible();
    await page.getByRole('combobox', {name: '搜索应用', exact: true}).fill('知识');
    await page.keyboard.press('Enter');
    await expect(page.locator('.window[data-app-id=knowledge]')).toBeVisible();
    await expect(page.locator('.menubar').getByRole('button', {name: '知识库', exact: true})).toBeVisible();

    await page.keyboard.press('Control+k');
    const palette = page.getByRole('dialog', {name: '命令面板', exact: true});
    await expect(palette).toBeVisible();
    await page.keyboard.press('Shift+Tab');
    assert.equal(await page.evaluate(() => Boolean(document.activeElement?.closest('.command-palette'))), true, 'palette traps backward Tab');
    await page.keyboard.press('Tab');
    await page.keyboard.press('Escape');
    await expect(palette).toHaveCount(0);

    await page.locator('nav.dock button[title="系统设置"]').click();
    const settings = page.locator('.window[data-app-id=settings]');
    await expect(settings).toBeVisible();
    await settings.locator('.preference-form select').first().selectOption('light');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await expect(settings.locator('.preference-form [role=status]')).toContainText('已应用并保存');
    await page.evaluate(() => {window.preservedSettings = document.querySelector('.window[data-app-id=settings]');});
    const draft = settings.locator('input[list=timezones]');
    await draft.fill('unsaved-local-draft');
    const renewalsBefore = state.renewals;
    await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
    await expect.poll(() => state.renewals).toBe(renewalsBefore);
    await expect(draft).toHaveValue('unsaved-local-draft');
    assert.equal(await page.evaluate(() => document.querySelector('.window[data-app-id=settings]') === window.preservedSettings), true);
    assert.equal(state.logouts, 0, 'visibility does not log out a valid cached session');
    // Restore the draft before blurring so the preference remains a valid timezone.
    await draft.fill('UTC');
    await settings.getByRole('button', {name: '立即保存当前布局', exact: true}).click();
    await expect.poll(() => state.layout.windows.length).toBe(2);
    await page.reload();
    await expect(page.locator('.window[data-app-id=settings]')).toBeVisible();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');

    await page.locator('nav.dock button[title="AI 对话"]').click();
    await expect(page.locator('.window[data-app-id=chat]')).toContainText('AI 对话尚未配置');
    await page.setViewportSize({width: 390, height: 844});
    await page.locator('.menubar-mobile-switcher').getByRole('button', {name: '切换应用', exact: true}).click();
    await page.locator('.menubar-mobile-switcher').getByRole('menuitemcheckbox', {name: '系统设置', exact: true}).click();
    await expect(page.locator('.window[data-app-id=settings]')).toBeVisible();
    assert.equal(await page.evaluate(() => [...document.querySelectorAll('.window')].filter(element => getComputedStyle(element).visibility === 'visible').length), 1);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 2));

    // After a fresh connection, revoked scopes filter launcher/menu/palette apps.
    state.scopes = ['desktop:read'];
    await page.reload();
    await expect(page.locator('nav.dock button[title="知识库"]')).toHaveCount(0);
    await expect(page.locator('.window[data-app-id=knowledge]')).toHaveCount(0);
    await page.locator('nav.dock button[title="LaunchPad"]').click();
    await expect(page.locator('.launchpad')).toBeVisible();
    await expect(page.locator('.launchpad').getByRole('option', {name: /知识库|任务/})).toHaveCount(0);
    assert.deepEqual(exceptions, [], 'no uncaught browser exceptions');
  } finally {
    try {await browser?.close();} finally {await server.close();}
  }
});
