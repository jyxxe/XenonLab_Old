import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync, mkdirSync, mkdtempSync, rmSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium, expect} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const evidence = path.resolve(appRoot, '../../runtime/verification/issue211');

test('System dashboard opens usable tasks by pointer and keyboard and respects app availability', async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const cacheDir = mkdtempSync(path.join(os.tmpdir(), 'xeonolab-dashboard-'));
  const server = await createServer({root: appRoot, cacheDir, optimizeDeps: {include: ['react', 'react-dom/client', 'react/jsx-runtime', 'react/jsx-dev-runtime']}, server: {host: '127.0.0.1', port: 0, watch: null}});
  await server.listen();
  const browser = await chromium.launch({executablePath, headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [];
  const externalRequests = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => {
    if (!['127.0.0.1', 'localhost'].includes(new URL(request.url()).hostname)) externalRequests.push(request.url());
  });
  const system = page.locator('.window[data-app-id="system"]');
  const tasks = page.locator('.window[data-app-id="issues"]');
  const taskEntry = system.getByRole('button', {name: '打开任务中心', exact: true});
  const openSystem = () => page.locator('nav.dock button[title="系统状态"]').click();
  const closeTasks = () => tasks.getByRole('button', {name: '关闭 任务', exact: true}).click();
  try {
    // All API functions are the shell's injected synthetic fixture. No external
    // authentication, registry or task service is accessed by this acceptance.
    await page.goto(`${server.resolvedUrls.local[0]}tests/shell-fixture.html?scenario=dashboard-tasks`);
    await openSystem();
    await expect(taskEntry).toBeVisible();
    mkdirSync(evidence, {recursive: true});
    await page.screenshot({path: path.join(evidence, 'available-task-entry.png')});
    await taskEntry.click();
    await expect(tasks).toBeVisible();
    await expect(tasks.getByText('Synthetic issue 70', {exact: true})).toBeVisible();
    await tasks.getByRole('tab', {name: /全部/}).click();
    await tasks.getByRole('searchbox', {name: '搜索任务'}).fill('Synthetic issue 71');
    await expect(tasks.getByText('Synthetic issue 71', {exact: true})).toBeVisible();
    await expect(tasks.getByText('Synthetic issue 70', {exact: true})).toHaveCount(0);
    await page.screenshot({path: path.join(evidence, 'pointer-task-window.png')});

    await closeTasks();
    await openSystem();
    // Status legend entries are buttons that open the task center pre-filtered.
    await system.getByRole('button', {name: '查看排队任务'}).click();
    await expect(tasks).toBeVisible();
    await expect(tasks.getByRole('tab', {name: /排队/})).toHaveAttribute('aria-selected', 'true');
    await expect(tasks.getByText('Synthetic issue 71', {exact: true})).toBeVisible();
    await expect(tasks.getByText('Synthetic issue 70', {exact: true})).toHaveCount(0);
    await closeTasks();
    await openSystem();
    await system.locator('.dashboard-actions button').focus();
    for (let i = 0; i < 20; i++) {
      await page.keyboard.press('Tab');
      if (await taskEntry.evaluate(button => button === document.activeElement)) break;
    }
    assert.equal(await taskEntry.evaluate(button => button === document.activeElement), true, 'task entry is reachable with normal Tab navigation');
    await page.keyboard.press('Enter');
    await expect(tasks).toBeVisible();
    await expect(tasks.getByRole('searchbox', {name: '搜索任务'})).toHaveValue('');
    await closeTasks();

    await page.locator('nav.dock button[title="任务"]').click();
    await expect(tasks.getByText('Synthetic issue 70', {exact: true})).toBeVisible();
    await openSystem();
    await taskEntry.click();
    await expect(tasks).toBeVisible();
    await expect(tasks).toHaveCount(1);
    await page.screenshot({path: path.join(evidence, 'keyboard-dock-task-window.png')});

    // A fresh synthetic identity without dev:read still has System. Its actual
    // filtered app list contains no tasks, so there is no silent action button.
    await page.goto(`${server.resolvedUrls.local[0]}tests/shell-fixture.html?scenario=no-task-app`);
    await openSystem();
    await expect(system.getByText('当前身份没有可用的任务应用。', {exact: true})).toBeVisible();
    await expect(taskEntry).toHaveCount(0);
    await expect(page.locator('nav.dock button[title="任务"]')).toHaveCount(0);
    await expect(tasks).toHaveCount(0);
    const calls = await page.evaluate(() => window.shellFixture.calls);
    assert.equal(calls.some(call => call.path === '/work?limit=100'), false, 'unavailable task app never opens or fetches its list');
    await page.screenshot({path: path.join(evidence, 'task-app-unavailable.png')});
    assert.deepEqual(errors, []);
    assert.deepEqual(externalRequests, []);
  } finally {
    await browser.close();
    await server.close();
    rmSync(cacheDir, {recursive: true, force: true});
  }
});
