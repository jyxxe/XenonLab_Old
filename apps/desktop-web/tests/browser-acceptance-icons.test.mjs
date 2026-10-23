import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium, expect} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const evidence = path.resolve(appRoot, '../../runtime/verification/issue228');

test('shared icons retain shell and Library names, keyboard actions, themes and custom text', {timeout: 60000}, async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'xeono-icons-'));
  const server = await createServer({root: appRoot, cacheDir: path.join(temporary, 'vite'), server: {host: '127.0.0.1', port: 0, watch: null, hmr: false}});
  let browser;
  const errors = [], external = [];
  try {
    await fs.mkdir(evidence, {recursive: true});
    await server.listen();
    const origin = new URL(server.resolvedUrls.local[0]).origin;
    browser = await chromium.launch({executablePath, headless: true});
    const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      if (new URL(route.request().url()).origin !== origin) {
        external.push(route.request().url());
        return route.abort();
      }
      return route.continue();
    });
    await page.goto(`${origin}/tests/shell-fixture.html`);
    const dock = page.getByRole('navigation', {name: '应用程序', exact: true});
    // 9 dock apps: knowledge/chat/development/issues/system/embedding/settings plus
    // the added Embedding queue app (dev:read) and any built-in extras.
    await expect(dock.locator('svg[data-icon]')).toHaveCount(9);
    for (const [title, icon] of [['知识库', 'library'], ['AI 对话', 'chat'], ['开发', 'code'], ['任务', 'issues'], ['系统状态', 'monitor'], ['Embedding', 'embedding'], ['系统设置', 'settings']]) {
      await expect(dock.getByRole('button', {name: new RegExp(`^${title}(?: [0-9]+)?$`)}).locator('svg')).toHaveAttribute('data-icon', icon);
    }
    const dockSize = () => dock.locator('svg[data-icon="library"]').evaluate(svg => svg.getBoundingClientRect().width);
    assert.equal(await dockSize(), 22);
    await page.screenshot({path: path.join(evidence, 'desktop-dark.png')});
    await dock.getByRole('button', {name: 'LaunchPad', exact: true}).focus();
    await page.keyboard.press('Enter');
    const launchpad = page.getByRole('dialog', {name: 'LaunchPad', exact: true});
    await expect(launchpad.locator('svg[data-icon]')).toHaveCount(9);
    await expect(launchpad.getByRole('option', {name: /知识库/}).locator('svg')).toHaveAttribute('aria-hidden', 'true');
    await page.screenshot({path: path.join(evidence, 'launchpad-dark.png')});
    await page.keyboard.press('Escape');
    await expect(launchpad).toHaveCount(0);
    await page.keyboard.press('Control+k');
    const palette = page.getByRole('dialog', {name: '命令面板', exact: true});
    await expect(palette.locator('svg[data-icon]').first()).toBeVisible();
    await expect(palette.getByRole('combobox', {name: '搜索命令与应用'})).toBeVisible();
    // Click the command instead of focusing it and pressing Enter: under load the
    // palette can re-render between focus and key delivery and drop the command.
    await palette.getByRole('option', {name: '切换浅色 / 深色外观', exact: true}).click();
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    assert.equal(await dockSize(), 22);
    await page.screenshot({path: path.join(evidence, 'desktop-light.png')});
    await page.setViewportSize({width: 390, height: 844});
    assert.equal(await dockSize(), 19);
    await page.screenshot({path: path.join(evidence, 'desktop-mobile.png')});
    await page.setViewportSize({width: 1440, height: 1000});

    await page.goto(`${origin}/tests/library-fixture.html`);
    await page.locator('button.library-tree-table').filter({hasText: 'uniform-convergence'}).click();
    await expect(page.getByRole('button', {name: '刷新知识表目录', exact: true}).locator('svg')).toHaveAttribute('data-icon', 'refresh');
    await expect(page.getByRole('button', {name: '新建知识表', exact: true}).locator('svg')).toHaveAttribute('data-icon', 'plus');
    await expect(page.locator('svg[data-icon="attachment"]').first()).toBeVisible();
    await page.locator('tr[data-row-id="row-001"]').click();
    await expect(page.getByRole('button', {name: '关闭 Inspector', exact: true}).locator('svg')).toHaveAttribute('data-icon', 'close');
    await page.screenshot({path: path.join(evidence, 'library-icons.png')});

    await page.goto(`${origin}/tests/icon-fixture.html`);
    await page.getByRole('button', {name: '刷新示例', exact: true}).focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('output')).toHaveText('1');
    await expect(page.getByRole('button', {name: '收藏示例', exact: true})).toBeVisible();
    await expect(page.getByRole('img', {name: '知识库图标', exact: true})).toBeVisible();
    for (const value of ['__proto__', 'constructor', '<custom>', '🦊', 'CUSTOM']) {
      const icon = page.locator(`[data-custom=${JSON.stringify(value)}]`).locator('.ui-icon-custom');
      await expect(icon).toHaveText(value);
      assert.ok(await icon.evaluate(element => element.scrollWidth <= element.getBoundingClientRect().width + 1), 'custom text retains its intrinsic width beside labels');
    }
    assert.equal(await page.locator('custom,img').count(), 0);
    const svgState = await page.locator('svg.ui-icon').evaluateAll(icons => icons.map(svg => ({viewBox: svg.getAttribute('viewBox'), stroke: svg.getAttribute('stroke'), focusable: svg.getAttribute('focusable')})));
    assert.ok(svgState.every(svg => svg.viewBox === '0 0 24 24' && svg.stroke === 'currentColor' && svg.focusable === 'false'));
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    await fs.writeFile(path.join(evidence, 'observations.json'), JSON.stringify({dataSource: 'synthetic shell/library/compatibility fixtures', svgState, errors, external}, null, 2));
  } finally {
    await browser?.close();
    await server.close();
    await fs.rm(temporary, {recursive: true, force: true});
  }
});
