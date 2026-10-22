import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function fixture(browser, server, query = '') {
  const page = await browser.newPage({viewport: {width: 1080, height: 900}});
  const errors = [];
  const external = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.route('**/*', route => {
    if (route.request().url().startsWith(server.resolvedUrls.local[0])) return route.continue();
    external.push(route.request().url());
    return route.abort();
  });
  await page.goto(`${server.resolvedUrls.local[0]}tests/plugin-panel-fixture.html${query}`);
  return {page, errors, external};
}

async function withFixture(callback) {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const server = await createServer({
    root: appRoot,
    // A private optimize cache keeps this fixture from forcing a dependency
    // re-optimization / full reload in the other browser acceptance files.
    cacheDir: 'node_modules/.vite-plugin-panel-acceptance',
    server: {host: '127.0.0.1', port: 0, watch: null},
  });
  await server.listen();
  const browser = await chromium.launch({executablePath, headless: true});
  try {
    await callback({server, browser});
  } finally {
    await browser.close();
    await server.close();
  }
}

test('plugin panel installs, selects a sandboxed widget and uninstalls per account', async () => {
  await withFixture(async ({server, browser}) => {
    const {page, errors, external} = await fixture(browser, server);
    const card = page.locator('.plugin-card').filter({hasText: 'Hello plugin'});
    try {
      await card.getByRole('button', {name: '安装'}).waitFor();
      const text = await card.innerText();
      assert.match(text, /应用 · Hello app/);
      assert.match(text, /桌面组件 · Hello widget/);
      assert.match(text, /xelibrary:read/);

      await card.getByRole('button', {name: '安装'}).click();
      await card.getByRole('button', {name: '卸载'}).waitFor();
      assert.equal(await card.getByLabel('启用').isChecked(), true);
      assert.equal(await page.evaluate(() => window.__pluginFixture.installed.has('hello-plugin')), true);

      await card.getByLabel('显示在桌面').check();
      const iframe = page.locator('.plugin-widget-fixture iframe');
      await iframe.waitFor();
      assert.equal(await iframe.getAttribute('title'), 'Hello widget');
      assert.equal(await iframe.getAttribute('sandbox'), 'allow-scripts allow-same-origin allow-forms');
      assert.equal(await iframe.getAttribute('referrerpolicy'), 'no-referrer');
      assert.equal(await iframe.getAttribute('src'), 'https://plugin.example.test/widget');
      assert.equal((await iframe.getAttribute('src')).includes('token'), false);
      assert.deepEqual(
        await page.evaluate(() => window.__pluginFixture.calls.find(([path, method]) => path === 'preferences' && method.plugin_widgets)),
        ['preferences', {plugin_widgets: ['hello-plugin']}],
      );
      assert.ok(external.includes('https://plugin.example.test/widget'));

      await page.setViewportSize({width: 360, height: 800});
      assert.equal(
        await page.locator('.plugin-panel').evaluate(element => element.scrollWidth <= element.clientWidth),
        true,
      );

      await card.getByRole('button', {name: '卸载'}).click();
      await card.getByRole('button', {name: '安装'}).waitFor();
      assert.equal(await page.locator('.plugin-widget-fixture iframe').count(), 0);
      assert.equal(await page.evaluate(() => window.__pluginFixture.installed.has('hello-plugin')), false);
      assert.deepEqual(errors, []);
    } finally {
      await page.close();
    }
  });
});

test('admin publish form adds a manifest to the catalog', async () => {
  await withFixture(async ({server, browser}) => {
    const {page, errors} = await fixture(browser, server, '?admin=1');
    try {
      await page.getByRole('heading', {name: /Hello plugin/}).waitFor();
      await page.locator('.plugin-publish summary').click();
      const manifest = {
        id: 'new-widget',
        name: '新组件',
        version: '1.0.0',
        description: '由验收夹具发布',
        author: 'fixture',
        permissions: [],
        widget: {
          id: 'new',
          title: '新组件',
          origin: 'https://new.example.test',
          entry: '/widget',
          icon: '◈',
          height: 160,
        },
      };
      await page.getByLabel('插件清单 JSON').fill(JSON.stringify(manifest));
      await page.getByRole('button', {name: '发布清单'}).click();
      await page.getByRole('heading', {name: /新组件/}).waitFor();
      const published = await page.evaluate(() =>
        window.__pluginFixture.calls.find(([path, method]) => path === '/plugins' && method === 'POST'),
      );
      assert.deepEqual(published, ['/plugins', 'POST']);
      assert.match(await page.locator('.plugin-card').filter({hasText: '新组件'}).innerText(), /桌面组件 · 新组件/);
      assert.deepEqual(errors, []);
    } finally {
      await page.close();
    }
  });
});
