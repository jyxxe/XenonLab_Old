import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium, expect} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = path.resolve(appRoot, '../..');
const artifactDir = path.resolve(process.env.CI_BROWSER_ARTIFACT_DIR || path.join(repoRoot, 'runtime/ci-browser-smoke'));

test('headless browser smoke: synthetic login, Knowledge and Work paging, recoverable API failures', async t => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required; expected executable at ${executablePath}`);

  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: 0, watch: null}});
  await server.listen();
  const base = server.resolvedUrls.local[0].replace(/\/$/, '');
  const browser = await chromium.launch({executablePath, headless: true});
  const context = await browser.newContext({viewport: {width: 1440, height: 900}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  const diagnostics = {console: [], pageErrors: [], failedRequests: [], httpErrors: []};
  const state = {mode: 'anonymous', exchangeCount: 0, workRequests: [], workPage: 0};
  await context.tracing.start({screenshots: true, snapshots: true, sources: true});
  page.on('console', message => diagnostics.console.push({type: message.type(), text: message.text()}));
  page.on('pageerror', error => diagnostics.pageErrors.push(error.message));
  page.on('requestfailed', request => diagnostics.failedRequests.push({url: new URL(request.url()).pathname, error: request.failure()?.errorText}));
  page.on('response', response => {
    if (response.status() >= 400) diagnostics.httpErrors.push({url: new URL(response.url()).pathname, status: response.status()});
  });

  try {
    await page.route('https://synthetic-identity.example/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith('/.well-known/openid-configuration')) {
        await route.fulfill({json: {
          issuer: 'https://synthetic-identity.example/realm',
          authorization_endpoint: 'https://synthetic-identity.example/realm/authorize',
          token_endpoint: 'https://synthetic-identity.example/realm/token',
          jwks_uri: 'https://synthetic-identity.example/realm/jwks',
        }});
        return;
      }
      assert.equal(url.searchParams.get('code_challenge_method'), 'S256');
      assert.ok(url.searchParams.get('code_challenge')?.length >= 43);
      await route.fulfill({status: 302, headers: {location: `${base}/?code=synthetic-code&state=${encodeURIComponent(url.searchParams.get('state') || '')}`}, body: ''});
    });

    await page.route(`${base}/api/v1/**`, async route => {
      const request = route.request();
      const url = new URL(request.url());
      const apiPath = url.pathname.slice('/api/v1'.length);
      if (apiPath === '/auth/config') return route.fulfill({json: {issuer: 'https://synthetic-identity.example/realm', client_id: 'ci-browser-smoke'}});
      if (apiPath === '/auth/session/exchange') {
        const body = request.postDataJSON();
        assert.equal(body.code, 'synthetic-code');
        assert.ok(body.verifier?.length >= 43);
        state.exchangeCount += 1;
        state.mode = 'ok';
        return route.fulfill({json: {token: 'synthetic-session-token', expires: 9999999999}});
      }
      if (apiPath === '/auth/session/token') {
        if (state.mode === 'anonymous') return route.fulfill({status: 401, json: {error: {code: 'NO_SESSION'}}});
        return route.fulfill({json: {token: 'synthetic-session-token', expires: 9999999999}});
      }
      if (apiPath === '/auth/session/logout') return route.fulfill({json: {}});
      if (apiPath === '/users/me') return route.fulfill({json: {data: {subject: 'ci-smoke-user', kind: 'human', scopes: ['desktop:read', 'xelibrary:read', 'dev:read']}, meta: {request_id: 'ci-smoke'}}});
      if (apiPath === '/users/me/preferences') return route.fulfill({json: {data: {theme: 'dark', accent: 'blue', wallpaper: 'silver', timezone: 'UTC', widgets: [], dock_size: 'medium', dock_autohide: false, restore_windows: true, animations: false, reduced_motion: true}, meta: {request_id: 'ci-smoke'}}});
      if (apiPath === '/users/me/layout') return route.fulfill({json: {data: {dock: [], windows: []}, meta: {request_id: 'ci-smoke'}}});
      if (apiPath === '/apps') return route.fulfill({json: {data: [], meta: {request_id: 'ci-smoke'}}});
      if (apiPath === '/tables') return route.fulfill({json: {data: [{id: 'smoke-table', path: '/CI', name: 'Smoke Knowledge'}], meta: {request_id: 'ci-smoke', next_cursor: null}}});
      if (apiPath === '/tables/smoke-table/schema' || apiPath === '/tables/smoke-table') return route.fulfill({json: {data: {id: 'smoke-table', path: '/CI', name: 'Smoke Knowledge', columns: [{name: 'title', type: 'text', nullable: false, default_value: null}], table_revision: 1, schema_revision: 1}, meta: {request_id: 'ci-smoke'}}});
      if (apiPath === '/tables/smoke-table/rows') return route.fulfill({json: {data: [], meta: {request_id: 'ci-smoke', next_cursor: null}}});
      if (apiPath === '/work' && request.method() === 'GET') {
        state.workRequests.push(url.searchParams.get('cursor'));
        if (state.mode === 'timeout') return route.abort('timedout');
        if (state.mode === 'network') return route.abort('failed');
        if (state.mode !== 'ok') {
          const status = Number(state.mode);
          const errorCode = status === 401 ? 'DESKTOP_ACCESS_REVOKED' : status === 429 ? 'AUTHORIZATION_RATE_LIMITED' : 'UPSTREAM_UNAVAILABLE';
          return route.fulfill({status, json: {error: {code: errorCode, message: `Synthetic ${state.mode} response`}}});
        }
        if (url.searchParams.get('cursor')) {
          return route.fulfill({json: {data: [{id: 'smoke-work-page-2', repository: 'xenon-admin/XeonoLab', issue_id: 902, title: 'CI smoke second page', status: 'queued', holder: null, expires: 0, epoch: 1, claim_active: false}], meta: {request_id: 'ci-smoke', next_cursor: null}}});
        }
        state.workPage += 1;
        return route.fulfill({json: {data: [{id: 'smoke-work-page-1', repository: 'xenon-admin/XeonoLab', issue_id: 901, title: 'CI smoke first page', status: 'queued', holder: null, expires: 0, epoch: 1, claim_active: false}], meta: {request_id: 'ci-smoke', next_cursor: 'smoke-page-2'}}});
      }
      return route.fulfill({json: {data: [], meta: {request_id: 'ci-smoke', next_cursor: null}}});
    });

    await page.goto(`${base}/`);
    await page.getByRole('button', {name: '登录 XeonoLab', exact: true}).click();
    await expect(page.locator('nav.dock')).toBeVisible();
    assert.equal(state.exchangeCount, 1, 'the browser completed synthetic OIDC/PKCE login');

    await page.locator('nav.dock button[title="知识库"]').click();
    const knowledge = page.locator('.window[data-app-id=knowledge]');
    await expect(knowledge).toBeVisible();
    await expect(knowledge.locator('.knowledge')).toBeVisible();
    await expect(knowledge).toContainText('Smoke Knowledge');

    await page.locator('nav.dock button[title="任务"]').click();
    const work = page.locator('.window[data-app-id=issues]');
    await expect(work).toBeVisible();
    await expect(work).toContainText('CI smoke first page');
    await work.getByRole('button', {name: '加载更多', exact: true}).click();
    await expect(work).toContainText('CI smoke second page');
    assert.deepEqual(state.workRequests.slice(-2), [null, 'smoke-page-2']);

    const assertWorkspaceRetained = async label => {
      await expect(page.locator('nav.dock')).toBeVisible();
      await expect(knowledge).toHaveCount(1);
      await expect(work).toHaveCount(1);
      assert.equal(await knowledge.isVisible(), true, `${label}: unrelated Knowledge window remains visible in the workspace`);
      assert.equal(await work.isVisible(), true, `${label}: active Work window remains open`);
    };
    for (const mode of ['429', '500', '503', 'timeout', 'network']) {
      state.mode = mode;
      const previousRequestCount = state.workRequests.length;
      await work.getByRole('button', {name: '刷新', exact: true}).click();
      await expect.poll(() => state.workRequests.length).toBe(previousRequestCount + 1);
      await expect(page.locator('[data-testid=session-status-banner]')).toBeVisible();
      if (/^\d+$/.test(mode)) {
        await expect.poll(() => diagnostics.httpErrors.some(entry => entry.status === Number(mode) && entry.url === '/api/v1/work')).toBe(true);
      } else {
        await expect.poll(() => diagnostics.failedRequests.some(entry => entry.url === '/api/v1/work')).toBe(true);
      }
      await assertWorkspaceRetained(mode);
      state.mode = 'ok';
      const retry = page.locator('[data-testid=session-retry]');
      await expect(retry).toBeVisible();
      await retry.click();
      await expect(page.locator('[data-testid=session-status-banner]')).toHaveCount(0, {timeout: 10000});
      await expect(work).toContainText('CI smoke first page');
    }

    state.mode = '401';
    const beforeUnauthorized = state.workRequests.length;
    await work.getByRole('button', {name: '刷新', exact: true}).click();
    await expect.poll(() => state.workRequests.length).toBe(beforeUnauthorized + 1);
    await expect(page.locator('[data-testid=session-status-banner]')).toBeVisible();
    await expect.poll(() => diagnostics.httpErrors.some(entry => entry.status === 401 && entry.url === '/api/v1/work')).toBe(true);
    await assertWorkspaceRetained('401');
    assert.deepEqual(diagnostics.pageErrors, [], 'no uncaught browser exceptions');
  } catch (error) {
    await fs.mkdir(artifactDir, {recursive: true});
    await page.screenshot({path: path.join(artifactDir, 'browser-smoke-failure.png'), fullPage: true}).catch(() => {});
    diagnostics.failure = error instanceof Error ? `${error.name}: ${error.message}\n${error.stack}` : String(error);
    diagnostics.currentUrl = page.url();
    diagnostics.fixtureState = state;
    await fs.writeFile(path.join(artifactDir, 'browser-smoke-failure.json'), JSON.stringify(diagnostics, null, 2), {mode: 0o600});
    await context.tracing.stop({path: path.join(artifactDir, 'browser-smoke-trace.zip')}).catch(() => {});
    throw error;
  } finally {
    await context.tracing.stop().catch(() => {});
    await context.close();
    await browser.close();
    await server.close();
  }
});
