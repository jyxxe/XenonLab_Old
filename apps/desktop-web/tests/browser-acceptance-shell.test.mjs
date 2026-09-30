import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import {spawn} from 'node:child_process';
import {createServer} from 'vite';
import {fileURLToPath} from 'node:url';

const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

async function port() {
  const server = net.createServer();
  await new Promise((resolve, reject) => server.listen(0, '127.0.0.1', resolve).once('error', reject));
  const value = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return value;
}

async function waitFor(fn, label) {
  const until = Date.now() + 20000;
  while (Date.now() < until) {
    try {
      const result = await fn();
      if (result) return result;
    } catch {
      /* keep polling */
    }
    await sleep(100);
  }
  throw new Error(`Timed out waiting for ${label}`);
}

function waitForExit(child, timeoutMs) {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve(true);
  return new Promise(resolve => {
    const onExit = () => {
      clearTimeout(timer);
      resolve(true);
    };
    const timer = setTimeout(() => {
      child.off('exit', onExit);
      resolve(false);
    }, timeoutMs);
    child.once('exit', onExit);
  });
}

async function stopProcess(child) {
  if (child.exitCode !== null || child.signalCode !== null) return;
  child.kill('SIGTERM');
  if (await waitForExit(child, 3000)) return;
  child.kill('SIGKILL');
  if (!await waitForExit(child, 3000)) throw new Error('Chrome did not exit after SIGKILL');
}

async function removeTree(directory) {
  for (let attempt = 0; ; attempt += 1) {
    try {
      await fs.rm(directory, {recursive: true, force: true});
      return;
    } catch (error) {
      if (!['ENOTEMPTY', 'EBUSY'].includes(error.code) || attempt >= 5) throw error;
      await sleep(100 * (attempt + 1));
    }
  }
}

class CDP {
  constructor(socket) {
    this.socket = socket;
    this.id = 0;
    this.pending = new Map();
    this.events = [];
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (!message.id) {
        this.events.push(message);
        return;
      }
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message));
      else pending.resolve(message.result);
    });
  }
  static async connect(url) {
    const socket = new WebSocket(url);
    await new Promise((resolve, reject) => {
      socket.addEventListener('open', resolve, {once: true});
      socket.addEventListener('error', reject, {once: true});
    });
    return new CDP(socket);
  }
  call(method, params = {}) {
    const id = ++this.id;
    this.socket.send(JSON.stringify({id, method, params}));
    return new Promise((resolve, reject) => this.pending.set(id, {resolve, reject}));
  }
  async evaluate(expression) {
    const response = await this.call('Runtime.evaluate', {expression, awaitPromise: true, returnByValue: true});
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.text || 'Browser evaluation failed');
    return response.result?.value;
  }
  async drag(from, to) {
    await this.call('Input.dispatchMouseEvent', {type: 'mousePressed', x: from.x, y: from.y, button: 'left', buttons: 1, clickCount: 1});
    await sleep(40);
    for (let step = 1; step <= 5; step += 1) {
      await this.call('Input.dispatchMouseEvent', {
        type: 'mouseMoved',
        x: from.x + ((to.x - from.x) * step) / 5,
        y: from.y + ((to.y - from.y) * step) / 5,
        button: 'left',
        buttons: 1,
      });
      await sleep(16);
    }
    await this.call('Input.dispatchMouseEvent', {type: 'mouseReleased', x: to.x, y: to.y, button: 'left', buttons: 0, clickCount: 1});
  }
  async key(key, code, modifiers = 0, text = '') {
    await this.call('Input.dispatchKeyEvent', {type: 'rawKeyDown', key, code, modifiers, windowsVirtualKeyCode: key === 'Enter' ? 13 : undefined});
    if (text) await this.call('Input.insertText', {text});
    await this.call('Input.dispatchKeyEvent', {type: 'keyUp', key, code, modifiers});
  }
}

async function rectOf(cdp, selector) {
  return cdp.evaluate(`(() => {
    const element = document.querySelector(${JSON.stringify(selector)});
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    return {x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height)};
  })()`);
}

async function centerOf(cdp, selector) {
  return cdp.evaluate(`(() => {
    const element = document.querySelector(${JSON.stringify(selector)});
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    return {x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2)};
  })()`);
}

test('Desktop shell acceptance: launchpad, windows, palette, settings persistence', async t => {
  const chromePath = process.env.CHROME_BIN || ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'].find(file => existsSync(file));
  if (!chromePath) {
    t.skip('Chrome/Chromium unavailable; run with CHROME_BIN=/path/to/chrome');
    return;
  }
  const portNumber = await port();
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: portNumber, strictPort: true, watch: null}});
  await server.listen();

  // A socket that accepts connections but never answers, so the iframe wrapper must
  // time out instead of hanging in its loading state.
  const stalled = net.createServer(() => {});
  const stalledSockets = new Set();
  stalled.on('connection', socket => {
    stalledSockets.add(socket);
    socket.on('close', () => stalledSockets.delete(socket));
  });
  const stalledPort = await port();
  await new Promise(resolve => stalled.listen(stalledPort, '127.0.0.1', resolve));

  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'xeonolab-shell-acceptance-'));
  const chrome = spawn(
    chromePath,
    [
      '--headless=new',
      '--no-first-run',
      '--no-default-browser-check',
      `--user-data-dir=${path.join(tempDir, 'profile')}`,
      '--remote-debugging-port=0',
      '--remote-debugging-address=127.0.0.1',
      '--remote-allow-origins=*',
      'about:blank',
    ],
    {stdio: 'ignore'},
  );
  let cdp;
  try {
    const endpoint = await waitFor(async () => {
      const raw = await fs.readFile(path.join(tempDir, 'profile', 'DevToolsActivePort'), 'utf8').catch(() => null);
      return raw ? {port: Number(raw.split(/\r?\n/)[0])} : null;
    }, 'Chrome DevTools endpoint');
    const target = await (await fetch(`http://127.0.0.1:${endpoint.port}/json/new?about%3Ablank`, {method: 'PUT'})).json();
    cdp = await CDP.connect(target.webSocketDebuggerUrl);
    await cdp.call('Page.enable');
    await cdp.call('Runtime.enable');
    await cdp.call('Emulation.setDeviceMetricsOverride', {width: 1440, height: 900, deviceScaleFactor: 1, mobile: false});
    await cdp.call('Page.addScriptToEvaluateOnNewDocument', {
      source: `window.__XEONOLAB_IFRAME_TIMEOUT__=600;window.__XEONOLAB_CHAT_ORIGIN__='http://127.0.0.1:${stalledPort}';`,
    });
    await cdp.call('Page.navigate', {url: `http://127.0.0.1:${portNumber}/tests/shell-fixture.html`});

    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.desktop'))"), 'desktop shell');
    await waitFor(() => cdp.evaluate("document.querySelectorAll('nav.dock .dock-item').length>=6"), 'dock apps');

    const chromeState = await cdp.evaluate(`(() => ({
      menubar: [...document.querySelectorAll('.menubar button')].map(button=>button.innerText.trim()).filter(Boolean),
      dock: [...document.querySelectorAll('nav.dock .dock-item')].map(button=>button.title),
      labels: [...document.querySelectorAll('.bar .traffic button')].map(button=>button.getAttribute('aria-label')).filter(Boolean),
    }))()`);
    assert.ok(chromeState.menubar.includes('文件'), 'file menu is present');
    assert.ok(chromeState.menubar.includes('窗口'), 'window menu is present');
    for (const title of ['LaunchPad', '知识库', '系统设置']) assert.ok(chromeState.dock.includes(title), `dock contains ${title}`);

    // LaunchPad: search, keyboard selection, Enter to launch.
    await cdp.evaluate("document.querySelector('button[title=\"LaunchPad\"]').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.launchpad'))"), 'LaunchPad open');
    await cdp.evaluate(`(() => {const input=document.querySelector('.launchpad-search');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'知识');input.dispatchEvent(new Event('input',{bubbles:true}))})()`);
    await waitFor(() => cdp.evaluate("document.querySelector('.launchpad-grid')?.innerText.includes('知识库')"), 'LaunchPad search result');
    await cdp.key('Enter', 'Enter');
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.window[data-app-id=knowledge] .knowledge-sidebar'))"), 'knowledge window');

    // Window: move by dragging the titlebar.
    const beforeDrag = await rectOf(cdp, '.window[data-app-id=knowledge]');
    const titlebar = await centerOf(cdp, '.window[data-app-id=knowledge] .bar');
    await cdp.drag(titlebar, {x: titlebar.x + 70, y: titlebar.y + 50});
    await sleep(120);
    const afterDrag = await rectOf(cdp, '.window[data-app-id=knowledge]');
    assert.ok(Math.abs(afterDrag.x - (beforeDrag.x + 70)) <= 6, `window moved horizontally (${beforeDrag.x}→${afterDrag.x})`);
    assert.ok(Math.abs(afterDrag.y - (beforeDrag.y + 50)) <= 6, `window moved vertically (${beforeDrag.y}→${afterDrag.y})`);

    // Window: resize by dragging the south-east handle.
    const handle = await centerOf(cdp, '.window[data-app-id=knowledge] .rs-se');
    await cdp.drag(handle, {x: handle.x + 80, y: handle.y + 60});
    await sleep(120);
    const afterResize = await rectOf(cdp, '.window[data-app-id=knowledge]');
    assert.ok(afterResize.w >= afterDrag.w + 60, `window grew wider (${afterDrag.w}→${afterResize.w})`);
    assert.ok(afterResize.h >= afterDrag.h + 40, `window grew taller (${afterDrag.h}→${afterResize.h})`);

    // Window: minimize keeps the frame alive and dock shows a restore entry.
    await cdp.evaluate("document.querySelector('.window[data-app-id=knowledge] .bar .min').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.dock-minimized button'))"), 'minimized dock entry');
    const minimizedAlive = await cdp.evaluate("Boolean(document.querySelector('.window[data-app-id=knowledge]'))");
    assert.equal(minimizedAlive, true, 'minimized window (and its iframe) stays mounted');
    await cdp.evaluate("document.querySelector('.dock-minimized button').click()");
    await waitFor(() => cdp.evaluate("getComputedStyle(document.querySelector('.window[data-app-id=knowledge]')).visibility==='visible'"), 'restored window');

    // Window: maximize respects the menu bar and dock, and restores its previous rect.
    const beforeMax = await rectOf(cdp, '.window[data-app-id=knowledge]');
    await cdp.evaluate("document.querySelector('.window[data-app-id=knowledge] .bar .max').click()");
    await sleep(150);
    const maximized = await rectOf(cdp, '.window[data-app-id=knowledge]');
    assert.equal(maximized.y, 42, 'maximized window starts below the menu bar and keeps a margin');
    assert.ok(maximized.h <= 900 - 30 - 90, 'maximized window leaves room for the dock');
    await cdp.evaluate("document.querySelector('.window[data-app-id=knowledge] .bar .max').click()");
    await sleep(150);
    const restored = await rectOf(cdp, '.window[data-app-id=knowledge]');
    assert.ok(Math.abs(restored.x - beforeMax.x) <= 4 && Math.abs(restored.w - beforeMax.w) <= 4, 'restore returns the previous rectangle');

    // Window: double-click the titlebar toggles maximize.
    const doubleClick = await centerOf(cdp, '.window[data-app-id=knowledge] .window-title');
    await cdp.call('Input.dispatchMouseEvent', {type: 'mousePressed', x: doubleClick.x, y: doubleClick.y, button: 'left', buttons: 1, clickCount: 2});
    await cdp.call('Input.dispatchMouseEvent', {type: 'mouseReleased', x: doubleClick.x, y: doubleClick.y, button: 'left', buttons: 0, clickCount: 2});
    await waitFor(() => cdp.evaluate("document.querySelector('.window[data-app-id=knowledge]').classList.contains('ds-window-maximized')"), 'double click maximize');
    await cdp.evaluate("document.querySelector('.window[data-app-id=knowledge] .bar .max').click()");

    // Command palette opens with Ctrl+K and launches the Issues app.
    await cdp.key('k', 'KeyK', 2 /* Ctrl */);
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.command-palette'))"), 'command palette');
    await cdp.evaluate(`(() => {const input=document.querySelector('.command-input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'Harness');input.dispatchEvent(new Event('input',{bubbles:true}))})()`);
    await waitFor(
      () =>
        cdp.evaluate(
          "(()=>{const items=[...document.querySelectorAll('.command-item')];const selected=document.querySelector('.command-item[aria-selected=\"true\"]');return Boolean(selected&&selected.innerText.includes('任务')&&items.length===1)})()",
        ),
      'palette keyboard selection',
    );
    await cdp.key('Enter', 'Enter');
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.window[data-app-id=issues] .issues-app'))"), 'issues app from palette');
    await waitFor(() => cdp.evaluate("document.querySelectorAll('.issues-table tbody tr').length===3"), 'issues first page');
    await cdp.evaluate("[...document.querySelectorAll('.issues-app button')].find(button=>button.innerText.includes('加载更多'))?.click()");
    await waitFor(() => cdp.evaluate("document.querySelector('[role=tab][aria-selected=true]')?.innerText.includes('进行中') && document.querySelectorAll('.issues-table tbody tr').length===4"), 'issues second page');
    await cdp.evaluate("[...document.querySelectorAll('.issues-toolbar [role=tab]')].find(tab=>tab.innerText.includes('全部'))?.click()");
    await waitFor(() => cdp.evaluate("document.querySelectorAll('.issues-table tbody tr').length===5"), 'issues all filter');

    // Settings persist through the platform API and drive the document theme.
    await cdp.evaluate("document.querySelector('nav.dock button[title=\"系统设置\"]').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.window[data-app-id=settings] .settings-panel'))"), 'settings window');
    await cdp.evaluate("document.querySelector('button[role=switch][aria-label=\"减少动态效果\"]').click()");
    await waitFor(() => cdp.evaluate("document.documentElement.dataset.reducedMotion==='true'"), 'reduced motion applied');
    await cdp.evaluate("document.querySelector('button[role=switch][aria-label=\"Dock 自动隐藏\"]').click()");
    await waitFor(() => cdp.evaluate("document.querySelector('nav.dock').classList.contains('ds-autohide')"), 'dock autohide applied');
    await cdp.evaluate("document.querySelector('button[role=switch][aria-label=\"界面动画\"]').click()");
    await waitFor(() => cdp.evaluate("document.documentElement.dataset.animations==='off'"), 'animations disabled');
    await cdp.evaluate("document.querySelector('button[role=switch][aria-label=\"Dock 自动隐藏\"]').click()");
    await cdp.evaluate("document.querySelector('button[role=switch][aria-label=\"界面动画\"]').click()");
    await waitFor(() => cdp.evaluate("document.documentElement.dataset.animations==='on' && !document.querySelector('nav.dock').classList.contains('ds-autohide')"), 'desktop options restored');
    const preferenceCalls = await cdp.evaluate("window.shellFixture.calls.filter(call=>call.path==='/users/me/preferences'&&call.method==='PATCH').map(call=>call.body)");
    assert.ok(preferenceCalls.some(body => body.reduced_motion === true), 'preference patch recorded');

    // Iframe failure state: the stalled origin must surface an explicit error UI.
    await cdp.evaluate("document.querySelector('nav.dock button[title=\"AI 对话\"]').click()");
    await waitFor(() => cdp.evaluate("document.querySelector('.iframe-app-status')?.innerText.includes('无法加载')"), 'iframe timeout state');
    const iframeError = await cdp.evaluate(`(() => {
      const status=document.querySelector('.iframe-app-status');
      const actions=[...status.querySelectorAll('button')].map(button=>button.innerText.trim());
      return {text:status.innerText,actions};
    })()`);
    assert.ok(iframeError.actions.includes('重新加载'), 'iframe error offers reload');
    assert.ok(iframeError.actions.includes('在浏览器中打开'), 'iframe error offers external open');

    // Layout autosave records window geometry and dock order.
    await waitFor(async () => {
      const calls = await cdp.evaluate("window.shellFixture.calls.filter(call=>call.path==='/users/me/layout'&&call.method==='PUT').length");
      return calls > 0;
    }, 'layout autosave');
    const savedLayout = await cdp.evaluate("window.shellFixture.layout");
    assert.ok(savedLayout.windows.length >= 3, `layout stores open windows (${savedLayout.windows.length})`);
    assert.ok(savedLayout.windows.every(win => Number.isFinite(win.x) && Number.isFinite(win.width)), 'layout windows carry finite geometry');

    // Reload: window state and preferences are restored from the server-side layout.
    const settingsBefore = await rectOf(cdp, '.window[data-app-id=settings]');
    await cdp.call('Page.reload');
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.desktop'))"), 'desktop after reload');
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.window[data-app-id=settings]'))"), 'restored settings window');
    const settingsAfter = await rectOf(cdp, '.window[data-app-id=settings]');
    assert.ok(Math.abs(settingsAfter.x - settingsBefore.x) <= 4 && Math.abs(settingsAfter.w - settingsBefore.w) <= 4, 'window geometry restored after reload');
    assert.equal(await cdp.evaluate("document.documentElement.dataset.reducedMotion"), 'true', 'preference restored after reload');

    // Accessibility: icon-only controls carry labels and every dock entry has a title.
    const a11y = await cdp.evaluate(`(() => ({
      unlabeled: [...document.querySelectorAll('.ds-icon-button')].filter(button=>!button.getAttribute('aria-label')).length,
      dockTitles: [...document.querySelectorAll('nav.dock .dock-item')].every(button=>button.title),
      switches: [...document.querySelectorAll('button[role=switch]')].every(button=>button.getAttribute('aria-checked')!==null),
    }))()`);
    assert.equal(a11y.unlabeled, 0, 'icon buttons have aria-labels');
    assert.equal(a11y.dockTitles, true, 'dock items expose titles');
    assert.equal(a11y.switches, true, 'switches expose aria-checked');

    const exceptions = cdp.events.filter(event => event.method === 'Runtime.exceptionThrown');
    assert.equal(exceptions.length, 0, `no uncaught exceptions: ${exceptions.map(event => event.params?.exceptionDetails?.text).join('; ')}`);

    // Visual acceptance: capture the running shell at the viewports required by the
    // issue. Screenshots are only written when UI_SCREENSHOT_DIR is provided.
    const shotDir = process.env.UI_SCREENSHOT_DIR;
    if (shotDir) await fs.mkdir(shotDir, {recursive: true});
    const capture = async name => {
      if (!shotDir) return;
      const result = await cdp.call('Page.captureScreenshot', {format: 'png'});
      await fs.writeFile(path.join(shotDir, `${name}.png`), Buffer.from(result.data, 'base64'));
    };
    const viewports = [
      ['1920x1080', 1920, 1080],
      ['1440x900', 1440, 900],
      ['1366x768', 1366, 768],
      ['1024x768', 1024, 768],
      ['390x844-mobile', 390, 844],
    ];
    for (const [label, width, height] of viewports) {
      await cdp.call('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: width < 768});
      await sleep(250);
      const overflow = await cdp.evaluate('document.documentElement.scrollWidth - innerWidth');
      assert.ok(overflow <= 2, `no horizontal document overflow at ${label} (${overflow}px)`);
      assert.equal(await cdp.evaluate("Boolean(document.querySelector('.menubar') && document.querySelector('nav.dock'))"), true, `shell chrome visible at ${label}`);
      await capture(`desktop-dark-${label}`);
    }
    await cdp.call('Emulation.setDeviceMetricsOverride', {width: 1440, height: 900, deviceScaleFactor: 1, mobile: false});
    await cdp.evaluate(`(() => {const select=document.querySelector('.preference-form select');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(select,'light');select.dispatchEvent(new Event('change',{bubbles:true}))})()`);
    await waitFor(() => cdp.evaluate("document.documentElement.dataset.theme==='light'"), 'light theme applied');
    await sleep(200);
    await capture('desktop-light-1440x900');
    await cdp.evaluate(`(() => {const select=document.querySelector('.preference-form select');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(select,'dark');select.dispatchEvent(new Event('change',{bubbles:true}))})()`);
    await waitFor(() => cdp.evaluate("document.documentElement.dataset.theme==='dark'"), 'dark theme restored');

    // Visual acceptance for the launcher and command palette overlays.
    await cdp.evaluate("document.querySelector('button[title=\"LaunchPad\"]').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.launchpad'))"), 'LaunchPad for capture');
    await capture('desktop-launchpad-dark-1440x900');
    await cdp.key('Escape', 'Escape');
    await cdp.key('k', 'KeyK', 2);
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.command-palette'))"), 'palette for capture');
    await capture('desktop-command-palette-dark-1440x900');
    await cdp.key('Escape', 'Escape');
  } finally {
    cdp?.socket.close();
    try {
      await stopProcess(chrome);
    } finally {
      try {
        for (const socket of stalledSockets) socket.destroy();
        await new Promise(resolve => stalled.close(resolve));
      } finally {
        try {
          await server.close();
        } finally {
          await removeTree(tempDir);
        }
      }
    }
  }
});
