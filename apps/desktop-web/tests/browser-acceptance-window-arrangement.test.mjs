import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium, expect} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('Window menu half-screen arrangement: keyboard, measured chrome, persistence and recovery', async () => {
  const executablePath = process.env.CHROME_BIN || ['/usr/bin/google-chrome', '/usr/bin/chromium'].find(existsSync) || chromium.executablePath();
  assert.ok(existsSync(executablePath), 'Chrome/Chromium is required for this acceptance test');
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: 0, watch: null}});
  await server.listen();
  const base = server.resolvedUrls.local[0].replace(/\/$/, '');
  const browser = await chromium.launch({executablePath, headless: true});
  const context = await browser.newContext({viewport: {width: 1920, height: 1080}, reducedMotion: 'reduce'});
  const page = await context.newPage();
  const errors = [], unexpectedRequests = [], checks = [], measurements = [];
  page.on('pageerror', error => errors.push(error.message));
  await context.route('**/*', route => {
    if (new URL(route.request().url()).origin === base) return route.continue();
    unexpectedRequests.push(route.request().url());
    return route.abort();
  });
  await page.addInitScript(() => {
    const NativeObserver = window.ResizeObserver;
    const state = window.arrangementObservers = {created: 0, disconnected: 0};
    window.ResizeObserver = class extends NativeObserver {
      constructor(callback) {super(callback); state.created++;}
      disconnect() {state.disconnected++; super.disconnect();}
    };
  });
  const windowMenu = () => page.getByRole('button', {name: '窗口', exact: true});
  const frame = id => page.locator(`.window[data-app-id=${id}]`);
  const rect = async id => {
    const box = await frame(id).boundingBox();
    assert.ok(box, `${id} is visible`);
    return {x: box.x, y: box.y, width: box.width, height: box.height};
  };
  const openMenu = async () => {
    // Tab from the preceding menu reaches Window; every selection below uses keys.
    await page.getByRole('button', {name: '显示', exact: true}).focus();
    await page.keyboard.press('Tab');
    await expect(windowMenu()).toBeFocused();
    await page.keyboard.press('ArrowDown');
    return page.getByRole('menu', {name: '窗口', exact: true});
  };
  const choose = async label => {
    const menu = await openMenu();
    await expect(menu).toBeVisible();
    for (let i = 0; i < 16; i++) {
      if (await page.evaluate(label => document.activeElement?.querySelector('.ds-menu-label')?.textContent === label, label)) {
        await page.keyboard.press('Enter');
        await expect(menu).toHaveCount(0);
        await expect(windowMenu()).toBeFocused();
        return;
      }
      await page.keyboard.press('ArrowDown');
    }
    throw new Error(`Keyboard menu did not reach ${label}`);
  };
  const place = async (id, title, side) => {
    await choose(title);
    await expect(frame(id)).toHaveAttribute('data-window-active', 'true');
    await choose(`将当前窗口放到${side === 'left' ? '左' : '右'}半屏`);
  };
  const assertPair = async () => {
    const left = await rect('settings'), right = await rect('development');
    const chrome = await page.evaluate(() => ({menuBottom: document.querySelector('.menubar').getBoundingClientRect().bottom, dockTop: document.querySelector('nav.dock').getBoundingClientRect().top, width: innerWidth}));
    assert.equal(left.x, 12);
    assert.equal(right.x - left.x - left.width, 12);
    assert.equal(right.x + right.width, chrome.width - 12);
    for (const win of [left, right]) {
      assert.equal(win.y, chrome.menuBottom + 12);
      assert.equal(win.y + win.height, chrome.dockTop - 12);
    }
    assert.ok(left.width >= 680 && right.width >= 720 && left.height >= 480);
    return {left, right, chrome};
  };
  const disabled = async (reason, screenshot) => {
    const menu = await openMenu();
    await expect(menu.getByRole('menuitem', {name: '将当前窗口放到左半屏'})).toBeDisabled();
    await expect(menu.getByRole('menuitem', {name: '将当前窗口放到右半屏'})).toBeDisabled();
    await expect(menu.locator('.ds-menu-description')).toContainText(reason);
    const description = await windowMenu().getAttribute('aria-describedby');
    assert.ok(description, 'keyboard focused trigger exposes the reason to assistive technology');
    await expect(page.locator(`[id="${description}"]`)).toContainText(reason);
    for (let i = 0; i < 10; i++) {
      await page.keyboard.press('ArrowDown');
      assert.equal(await page.evaluate(() => document.activeElement?.disabled === true), false, 'keyboard skips disabled actions');
    }
    if (screenshot) await capture(screenshot);
    await page.keyboard.press('Escape');
  };
  const capture = async name => {
    if (!process.env.UI_SCREENSHOT_DIR) return;
    await fs.mkdir(process.env.UI_SCREENSHOT_DIR, {recursive: true});
    await page.screenshot({path: path.join(process.env.UI_SCREENSHOT_DIR, `${name}.png`)});
  };
  try {
    await page.goto(`${base}/tests/shell-fixture.html`);
    await expect(page.locator('nav.dock')).toBeVisible();
    await disabled('请先打开或恢复一个窗口');
    checks.push('no active window disables actions with an accessible reason');
    for (const title of ['系统设置', '开发']) await page.locator(`nav.dock button[title="${title}"]`).click();
    await page.evaluate(() => {window.originalSettings = document.querySelector('.window[data-app-id=settings]');});
    await place('development', '开发', 'right');
    await place('settings', '系统设置', 'left');
    const initial = await assertPair();
    assert.equal(initial.left.width, 942);
    assert.equal(await page.evaluate(() => window.originalSettings === document.querySelector('.window[data-app-id=settings]')), true);
    checks.push('1920x1080 opposite halves selected entirely through keyboard Window menus, frames retained');
    await capture('1920x1080-two-halves');

    const settings = frame('settings');
    const timezone = settings.locator('input[list=timezones]');
    await timezone.fill('Asia/Shanghai');
    await timezone.press('Enter');
    await expect.poll(() => page.evaluate(() => window.shellFixture.preferences.timezone)).toBe('Asia/Shanghai');
    const reads = await page.evaluate(() => window.shellFixture.calls.filter(call => call.path === '/work?limit=1000').length);
    await frame('development').getByRole('button', {name: '刷新', exact: true}).press('Enter');
    await expect.poll(() => page.evaluate(() => window.shellFixture.calls.filter(call => call.path === '/work?limit=1000').length)).toBeGreaterThan(reads);
    checks.push('both half-screen apps accept normal content keyboard actions');

    await choose('系统设置');
    await choose('缩放当前窗口');
    await expect(settings).toHaveClass(/ds-window-maximized/);
    await choose('还原当前窗口');
    assert.deepEqual(await rect('settings'), initial.left);
    await choose('缩放当前窗口');
    await choose('将当前窗口放到左半屏');
    await expect(settings).not.toHaveClass(/ds-window-maximized/);
    assert.deepEqual(await rect('settings'), initial.left);
    await settings.locator('.bar').focus();
    await page.keyboard.press('ArrowRight');
    await page.keyboard.press('Shift+ArrowUp');
    let moved = await rect('settings');
    assert.equal(moved.x, initial.left.x + 10);
    assert.equal(moved.height, initial.left.height - 10);
    const titlebar = await settings.locator('.window-title').boundingBox();
    await page.mouse.move(titlebar.x + titlebar.width / 2, titlebar.y + titlebar.height / 2);
    await page.mouse.down(); await page.mouse.move(titlebar.x + titlebar.width / 2 + 40, titlebar.y + titlebar.height / 2 + 20, {steps: 5}); await page.mouse.up();
    const dragged = await rect('settings');
    assert.equal(dragged.x, moved.x + 40);
    assert.equal(dragged.y, moved.y + 20);
    const handle = await settings.locator('.rs-se').boundingBox();
    await page.mouse.move(handle.x + 3, handle.y + 3); await page.mouse.down();
    await page.mouse.move(handle.x + 33, handle.y - 17, {steps: 5}); await page.mouse.up();
    assert.equal((await rect('settings')).width, dragged.width + 30);
    checks.push('half-screen max/restore, maximize-to-half, keyboard move/resize and pointer drag/resize remain free');
    await place('settings', '系统设置', 'left');
    await choose('最小化当前窗口');
    await expect(settings).toBeHidden();
    await choose('系统设置');
    await expect(settings).toBeVisible();
    assert.deepEqual(await rect('settings'), initial.left);
    await expect.poll(() => page.evaluate(() => window.shellFixture.layout.windows.find(win => win.app_id === 'settings')?.x)).toBe(12);
    await expect.poll(() => page.evaluate(() => window.shellFixture.layout.windows.find(win => win.app_id === 'development')?.x)).toBe(966);
    const saved = await page.evaluate(() => window.shellFixture.layout.windows);
    assert.equal(saved.some(win => 'snap' in win || 'placement' in win || 'restore' in win), false);
    const puts = await page.evaluate(() => window.shellFixture.calls.filter(call => call.path === '/users/me/layout' && call.method === 'PUT').length);
    await settings.getByRole('button', {name: '立即保存当前布局'}).press('Enter');
    await expect.poll(() => page.evaluate(() => window.shellFixture.calls.filter(call => call.path === '/users/me/layout' && call.method === 'PUT').length)).toBeGreaterThan(puts);
    await page.reload(); await expect(settings).toBeVisible();
    assert.deepEqual(await rect('settings'), initial.left);
    assert.deepEqual(await rect('development'), initial.right);
    checks.push('minimize/menu restore, autosave, explicit layout save and reload retain ordinary placement');

    for (const size of ['small', 'medium', 'large']) for (const autoHide of [false, true]) {
      await choose('系统设置');
      await settings.locator('select').filter({has: page.locator('option[value=small]')}).selectOption(size);
      const toggle = settings.getByRole('switch', {name: 'Dock 自动隐藏'});
      if ((await toggle.getAttribute('aria-checked') === 'true') !== autoHide) await toggle.click();
      await expect(page.locator('nav.dock')).toHaveClass(new RegExp(`dock-size-${size}`));
      await place('development', '开发', 'right'); await place('settings', '系统设置', 'left');
      const pair = await assertPair();
      // Keyboard focus reveals an auto-hidden Dock; its natural box stays reserved.
      await page.locator('nav.dock .dock-item').first().focus();
      await expect(page.locator('nav.dock')).toHaveCSS('opacity', '1');
      assert.deepEqual(await assertPair(), pair);
      measurements.push({size, autoHide, ...pair});
    }
    checks.push('all three Dock sizes and auto-hide keyboard reveal clear both placed windows');
    await capture('large-dock-keyboard-revealed');
    await page.setViewportSize({width: 1921, height: 1080});
    await place('development', '开发', 'right'); await place('settings', '系统设置', 'left');
    const odd = await assertPair();
    assert.equal(odd.right.width, odd.left.width + 1);
    checks.push('odd viewport remainder preserves 12px gap and outer margins');

    await page.setViewportSize({width: 1440, height: 900});
    await choose('开发');
    const beforeDisabled = await rect('development');
    await disabled('至少需要 720px');
    assert.deepEqual(await rect('development'), beforeDisabled);
    await choose('系统设置');
    await choose('将当前窗口放到左半屏');
    assert.equal((await rect('settings')).width, 702);
    checks.push('1440px per-app minima disable Development while Settings fits without changing the rejected window');
    await page.setViewportSize({width: 1023, height: 900});
    await disabled('窄屏使用单窗口布局', '1023px-disabled-reason');
    await page.setViewportSize({width: 1920, height: 500});
    await disabled('工作区高度不足');
    checks.push('compact CSS breakpoint and short viewport disable arrangement with visible and accessible reasons');
    await page.setViewportSize({width: 1920, height: 1080});
    await place('settings', '系统设置', 'left');
    assert.ok((await rect('settings')).height >= 480, 'window remains recoverable after rejected arrangements');
    await page.evaluate(() => window.shellFixture.unmount());
    await expect(page.locator('.desktop')).toHaveCount(0);
    const observers = await page.evaluate(() => window.arrangementObservers);
    assert.equal(observers.created, observers.disconnected, 'observers disconnect on unmount');
    checks.push('viewport recovery and observer cleanup');
    await page.goto(`${base}/tests/shell-fixture.html?scenario=desktop-error`);
    await expect(page.getByText('无法加载桌面', {exact: true})).toBeVisible();
    await disabled('桌面尚未加载');
    checks.push('initial loadError menu disables arrangement safely');
    assert.deepEqual(errors, []);
    assert.deepEqual(unexpectedRequests, [], 'no production request is allowed by the fixture test');
    if (process.env.UI_SCREENSHOT_DIR) await fs.writeFile(path.join(process.env.UI_SCREENSHOT_DIR, 'acceptance.json'), JSON.stringify({source: 'Actual Chrome/DesktopShell, injected synthetic local API; no production identity, service or data.', checks, measurements, observers, errors, unexpectedRequests}, null, 2));
    console.log(`${checks.length} arrangement checks passed; no page errors or non-local requests`);
  } finally {
    await context.close(); await browser.close(); await server.close();
  }
});
