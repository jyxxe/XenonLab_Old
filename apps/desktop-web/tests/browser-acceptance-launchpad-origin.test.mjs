import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import fs from 'node:fs/promises';
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

// The full catalogue matters: searching down to two cards hides the scrolling regression.
test('wrapped origins keep every full-catalogue card pointer reachable and the hint below the grid', async t => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chrome is required: ${executablePath}`);
  const evidence = path.resolve(appRoot, '../../runtime/verification/launchpad-layout-repair');
  await fs.mkdir(evidence, {recursive: true});
  const server = await createServer({root: appRoot, optimizeDeps: {include: ['react', 'react-dom/client', 'react/jsx-runtime', 'react/jsx-dev-runtime']}, server: {host: '127.0.0.1', port: 0, watch: null}});
  await server.listen();
  const browser = await chromium.launch({executablePath, headless: true});
  const report = {source: 'actual mergeApps/LaunchPad/styles; synthetic twelve-app catalogue', checks: [], errors: [], external: []};
  try {
    for (const [width, height] of [[360, 950], [320, 950], [280, 800], [360, 640]]) {
      await t.test(`${width}x${height} full catalogue`, async () => {
        const page = await browser.newPage({viewport: {width, height}});
        page.setDefaultTimeout(5000);
        page.on('pageerror', error => report.errors.push(String(error)));
        await page.route('**/*', route => {
          if (route.request().url().startsWith(server.resolvedUrls.local[0])) return route.continue();
          report.external.push(route.request().url());
          return route.abort();
        });
        try {
          await page.goto(`${server.resolvedUrls.local[0]}tests/launchpad-origin-fixture.html?mode=dense`);
          const cards = page.getByRole('option');
          await cards.first().waitFor();
          // Nine built-in native apps (the Embedding queue app was added with dev:read)
          // plus three visible synthetic iframe origins; the permission-locked one is absent.
          assert.equal(await cards.count(), 12);
          const apps = await page.evaluate(() => window.__launchpadFixture.apps.map(({id, kind, origin, title}) => ({id, kind, origin, title})));
          assert.equal(apps.filter(app => app.kind === 'native').length, 9);
          assert.equal(apps.filter(app => app.kind === 'iframe').length, 3);
          const sameTitle = apps.filter(app => app.id.startsWith('insights-'));
          assert.equal(sameTitle.length, 2);
          assert.equal(sameTitle[0].title, sameTitle[1].title);
          for (const app of sameTitle) {
            const url = new URL(app.origin);
            assert.ok(url.hostname.length > 60);
            assert.ok(url.hostname.split('.').every(label => label.length <= 63));
            assert.equal(await page.locator(`#launchpad-app-${app.id} .launchpad-source`).innerText(), url.host);
          }
          // Normal pointer interaction: no forced clicks or DOM-dispatched replacement.
          const staging = page.locator('#launchpad-app-insights-staging');
          await staging.scrollIntoViewIfNeeded();
          await staging.click({timeout: 2000});
          assert.deepEqual(await page.evaluate(() => window.__launchpadFixture.calls), ['insights-staging']);
          const pad = page.locator('.launchpad');
          const geometry = await pad.evaluate(element => {
            const grid = element.querySelector('.launchpad-grid').getBoundingClientRect();
            const hint = element.querySelector('.launchpad-hint').getBoundingClientRect();
            return {gridBottom: grid.bottom, hintTop: hint.top, scrollHeight: element.scrollHeight, clientHeight: element.clientHeight, horizontalOverflow: element.scrollWidth > element.clientWidth};
          });
          assert.ok(geometry.hintTop >= geometry.gridBottom, 'hint follows all wrapped origin rows in normal document flow');
          assert.equal(geometry.horizontalOverflow, false);
          assert.equal(await staging.evaluate(element => element.scrollWidth <= element.clientWidth), true);
          await page.screenshot({path: path.join(evidence, `full-catalogue-${width}x${height}.png`)});
          // Scroll the actual launcher until both last-row cards and the hint are reachable.
          await page.mouse.move(12, height - 80);
          await page.mouse.wheel(0, 2000);
          await page.waitForFunction(() => {
            const pad = document.querySelector('.launchpad');
            return pad.scrollTop >= pad.scrollHeight - pad.clientHeight - 1;
          });
          const scroll = await pad.evaluate(element => {
            const hint = element.querySelector('.launchpad-hint').getBoundingClientRect();
            const last = element.querySelector('#launchpad-app-reports').getBoundingClientRect();
            return {scrollTop: element.scrollTop, hintTop: hint.top, hintBottom: hint.bottom, lastTop: last.top, lastBottom: last.bottom, viewportHeight: innerHeight};
          });
          if (geometry.scrollHeight > geometry.clientHeight) assert.ok(scroll.scrollTop > 0);
          assert.ok(scroll.hintTop >= 30 && scroll.hintBottom <= height);
          assert.ok(scroll.lastTop >= 30 && scroll.lastBottom <= height);
          await page.screenshot({path: path.join(evidence, `full-catalogue-scrolled-${width}x${height}.png`)});
          await page.locator('#launchpad-app-reports').click();
          assert.deepEqual(await page.evaluate(() => window.__launchpadFixture.calls), ['insights-staging', 'reports']);
          assert.equal(await page.locator('iframe').count(), 0);
          report.checks.push({width, height, geometry, scroll});
        } finally {await page.close()}
      });
    }
    assert.deepEqual(report.errors, []);
    assert.deepEqual(report.external, []);
  } finally {
    await fs.writeFile(path.join(evidence, 'full-catalogue-report.json'), JSON.stringify(report, null, 2)+'\n');
    await browser.close();
    await server.close();
  }
});
