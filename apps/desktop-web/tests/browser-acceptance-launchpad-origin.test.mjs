import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('LaunchPad distinguishes homonymous origins, launches original IDs and retains search/Palette', async t => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const server = await createServer({root: appRoot, optimizeDeps: {include: ['react', 'react-dom/client', 'react/jsx-runtime', 'react/jsx-dev-runtime']}, server: {host: '127.0.0.1', port: 0, watch: null}});
  await server.listen();
  const browser = await chromium.launch({executablePath, headless: true});
  try {
    for (const [mode, width] of [['normal', 1080], ['normal', 360], ['long', 360]]) {
      await t.test(`${mode}-${width}`, async () => {
        const page = await browser.newPage({viewport: {width, height: 800}});
        const errors = [], external = [];
        page.on('pageerror', error => errors.push(String(error)));
        page.on('request', request => {if (!request.url().startsWith(server.resolvedUrls.local[0])) external.push(request.url())});
        try {
          await page.goto(`${server.resolvedUrls.local[0]}tests/launchpad-origin-fixture.html?mode=${mode}`);
          await page.getByRole('combobox', {name: '搜索应用'}).waitFor();
          const apps = await page.evaluate(() => window.__launchpadFixture.apps.map(({id, title, origin, description, kind}) => ({id, title, origin, description, kind})));
          assert.equal(apps.some(app => app.id === 'insights-locked'), false);
          const prod = apps.find(app => app.id === 'insights-prod');
          const staging = apps.find(app => app.id === 'insights-staging');
          const search = page.getByRole('combobox', {name: '搜索应用'});
          await search.waitFor();
          assert.ok((await page.locator('#launchpad-app-insights-prod').innerText()).includes('最近使用'));
          await search.fill('Insights');
          assert.equal(await page.getByRole('option').count(), 2);
          for (const app of [prod, staging]) {
            const host = new URL(app.origin).host;
            const option = page.getByRole('option', {name: `${app.title} · ${host} · 外部应用`, exact: true});
            assert.equal(await option.count(), 1);
            assert.equal(await option.locator('.launchpad-source').innerText(), host);
            assert.equal(await option.getAttribute('aria-description'), `来源：${app.origin}`);
            assert.equal(await option.getAttribute('title'), `来源：${app.origin}`);
            assert.equal((await option.innerText()).includes('private-view'), false);
            assert.equal(await option.evaluate(element => element.scrollWidth <= element.clientWidth), true);
            await option.hover();
            await option.click();
          }
          assert.deepEqual(await page.evaluate(() => window.__launchpadFixture.calls), ['insights-prod', 'insights-staging']);
          // Query changes reset keyboard selection; ArrowDown maps to the second original ID.
          await search.fill('insights-prod');
          assert.equal(await page.getByRole('option').count(), 1);
          await search.fill('Insights');
          await search.press('ArrowDown');
          await search.press('Enter');
          assert.deepEqual(await page.evaluate(() => window.__launchpadFixture.calls), ['insights-prod', 'insights-staging', 'insights-staging']);
          await search.fill(new URL(staging.origin).host);
          assert.equal(await page.getByRole('option').count(), 1);
          assert.equal(await page.getByRole('option').getAttribute('id'), 'launchpad-app-insights-staging');
          await search.fill('Reports');
          assert.equal(await page.getByRole('option').count(), 1);
          assert.equal(await page.getByRole('option').locator('.launchpad-source').innerText(), 'reports.example.test');
          await search.fill('系统设置');
          assert.equal(await page.getByRole('option', {name: '系统设置 桌面应用'}).count(), 1);
          assert.equal(await page.locator('.launchpad-source').count(), 0);
          assert.equal(await page.getByRole('option').getAttribute('aria-description'), null);
          assert.equal(await page.locator('.launchpad-grid').evaluate(element => element.scrollWidth <= element.clientWidth), true);
          await page.evaluate(() => window.__showPalette());
          const paletteSearch = page.getByRole('combobox', {name: '搜索命令与应用'});
          await paletteSearch.fill('Insights');
          const palette = page.getByRole('dialog', {name: '命令面板'});
          await palette.getByRole('option').nth(1).waitFor();
          assert.equal(await palette.locator('#command-app-insights-prod small').innerText(), prod.description);
          assert.equal(await palette.locator('#command-app-insights-staging small').innerText(), staging.description);
          await paletteSearch.press('Enter');
          await page.waitForFunction(() => window.__launchpadFixture.calls.length === 4);
          assert.equal(await page.evaluate(() => window.__launchpadFixture.calls[3]), 'insights-prod');
          assert.deepEqual(errors, []);
          assert.deepEqual(external, []);
        } finally {await page.close()}
      });
    }
  } finally {await browser.close(); await server.close()}
});
