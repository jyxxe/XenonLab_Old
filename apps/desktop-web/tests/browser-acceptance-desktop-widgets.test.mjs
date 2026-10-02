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

class CDP {
  constructor(socket) {
    this.socket = socket;
    this.id = 0;
    this.pending = new Map();
    socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (!message.id) return;
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
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text || 'Browser evaluation failed');
    return response.result?.value;
  }
}

async function centerOf(cdp, selector) {
  return cdp.evaluate(`(() => {
    const element = document.querySelector(${JSON.stringify(selector)});
    if (!element) return null;
    const rect = element.getBoundingClientRect();
    return {x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2)};
  })()`);
}

// Acceptance for issue #116: additional desktop widgets (calendar, work queue,
// notes, search) and the custom wallpaper rendering path. Fixture data only.
test('Desktop widgets and custom wallpaper acceptance', async t => {
  const chromePath = process.env.CHROME_BIN || ['/usr/bin/google-chrome', '/usr/bin/chromium', '/usr/bin/chromium-browser'].find(file => existsSync(file));
  if (!chromePath) {
    t.skip('Chrome/Chromium unavailable; run with CHROME_BIN=/path/to/chrome');
    return;
  }
  const portNumber = await port();
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: portNumber, strictPort: true, watch: null}});
  await server.listen();
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'xeonolab-widgets-acceptance-'));
  const chrome = spawn(
    chromePath,
    [
      '--headless=new',
      '--disable-dev-shm-usage',
      ...(process.getuid?.() === 0 ? ['--no-sandbox'] : []),
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
      source: `sessionStorage.setItem('shell-fixture:preferences', JSON.stringify({
        theme:'dark',accent:'blue',wallpaper:'custom',
        wallpaper_mode:'gradient',wallpaper_color:'#101828',
        wallpaper_gradient_from:'#123456',wallpaper_gradient_to:'#c0ffee',
        wallpaper_gradient_angle:200,wallpaper_image:'',wallpaper_dim:20,
        timezone:'UTC',widgets:['clock','calendar','status','work','search','notes'],
        notes:'',dock_size:'medium',dock_autohide:false,restore_windows:true,
        animations:true,reduced_motion:false
      }));`,
    });
    await cdp.call('Page.navigate', {url: `http://127.0.0.1:${portNumber}/tests/shell-fixture.html`});

    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.desktop'))"), 'desktop shell');

    // Custom wallpaper: inline background wins, preset attribute stays available.
    await waitFor(() => cdp.evaluate("document.documentElement.dataset.wallpaper==='custom'"), 'custom wallpaper dataset');
    const desktopStyle = await cdp.evaluate(`(() => {
      const desktop = document.querySelector('.desktop');
      return {mode: desktop.getAttribute('data-wallpaper-custom'), background: desktop.style.backgroundImage, color: desktop.style.backgroundColor};
    })()`);
    assert.equal(desktopStyle.mode, 'gradient');
    // Chrome serialises inline gradient colours back as rgb(); accept both forms.
    assert.match(desktopStyle.background, /linear-gradient\(200deg, (?:#123456|rgb\(18, 52, 86\)), (?:#c0ffee|rgb\(192, 255, 238\))\)/);
    assert.match(desktopStyle.background, /rgba\(0, 0, 0, 0\.2\)/);
    assert.match(desktopStyle.color, /^(?:#101828|rgb\(16, 24, 40\))$/);

    // Calendar: current month header, today marked, six Monday-first rows.
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.widget-calendar table'))"), 'calendar widget');
    const calendar = await cdp.evaluate(`(() => {
      const widget = document.querySelector('.widget-calendar');
      const headers = [...widget.querySelectorAll('thead th')].map(th => th.innerText);
      const rows = widget.querySelectorAll('tbody tr').length;
      const today = widget.querySelector("td[aria-current='date']");
      const expected = new Intl.DateTimeFormat('en-CA', {timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit'}).format(new Date());
      const [year, month, day] = expected.split('-').map(Number);
      return {header: widget.querySelector('header strong').innerText, headers, rows, today: today?.innerText, day, month, year};
    })()`);
    assert.equal(calendar.header, `${calendar.year}年${calendar.month}月`);
    assert.deepEqual(calendar.headers, ['一', '二', '三', '四', '五', '六', '日']);
    assert.equal(calendar.rows, 6);
    assert.equal(calendar.today, String(calendar.day));

    // Work queue: fixture has one queued issue (#71) and one active claim (#70);
    // the expired claim (#72), completed (#73) and blocked (#74) items are excluded.
    await waitFor(() => cdp.evaluate("document.querySelector('.widget-work')?.innerText.includes('进行中')"), 'work widget summary');
    const work = await cdp.evaluate(`(() => {
      const widget = document.querySelector('.widget-work');
      return {items: [...widget.querySelectorAll('li')].map(li => li.innerText), footer: widget.querySelector('small:last-of-type').innerText};
    })()`);
    assert.equal(work.items.length, 2);
    assert.match(work.items[0], /^#71 Synthetic issue 71/);
    assert.match(work.items[0], /QUEUED/i);
    assert.match(work.items[1], /^#70 Synthetic issue 70/);
    assert.match(work.items[1], /CLAIMED/i);
    assert.equal(work.items.join('\n').includes('#72'), false);
    assert.equal(work.items.join('\n').includes('#74'), false);
    assert.match(work.footer, /2 项进行中/);

    // Notes: typing persists through the debounced preferences PATCH.
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.widget-notes textarea'))"), 'notes widget');
    await cdp.evaluate(`(() => {
      const input = document.querySelector('.widget-notes textarea');
      Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set.call(input, '验收便签内容');
      input.dispatchEvent(new Event('input', {bubbles: true}));
    })()`);
    await waitFor(() => cdp.evaluate("window.shellFixture.calls.some(call => call.method==='PATCH' && call.path==='/users/me/preferences' && call.body && call.body.notes==='验收便签内容')"), 'notes persisted');
    assert.equal(await cdp.evaluate("window.shellFixture.preferences.notes"), '验收便签内容');

    // Search widget: submitting opens the knowledge app window.
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.widget-search input'))"), 'search widget');
    await cdp.evaluate(`(() => {
      const input = document.querySelector('.widget-search input');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, '局部凸性');
      input.dispatchEvent(new Event('input', {bubbles: true}));
    })()`);
    const submit = await centerOf(cdp, '.widget-search button');
    await cdp.call('Input.dispatchMouseEvent', {type: 'mousePressed', ...submit, button: 'left', buttons: 1, clickCount: 1});
    await cdp.call('Input.dispatchMouseEvent', {type: 'mouseReleased', ...submit, button: 'left', buttons: 0, clickCount: 1});
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.window[data-app-id=knowledge]'))"), 'knowledge window opens from search widget');
    assert.match(await cdp.evaluate("document.querySelector('.window[data-app-id=knowledge]').innerText"), /局部凸性|知识库/);

    // Work widget click-through opens the development app.
    const workWidget = await centerOf(cdp, '.widget-work');
    await cdp.call('Input.dispatchMouseEvent', {type: 'mousePressed', ...workWidget, button: 'left', buttons: 1, clickCount: 1});
    await cdp.call('Input.dispatchMouseEvent', {type: 'mouseReleased', ...workWidget, button: 'left', buttons: 0, clickCount: 1});
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.window[data-app-id=development]'))"), 'development window opens from work widget');

    if (process.env.UI_SCREENSHOT_DIR) {
      await fs.mkdir(process.env.UI_SCREENSHOT_DIR, {recursive: true});
      const shot = await cdp.call('Page.captureScreenshot', {format: 'png', captureBeyondViewport: false});
      await fs.writeFile(path.join(process.env.UI_SCREENSHOT_DIR, 'desktop-widgets-wallpaper.png'), Buffer.from(shot.data, 'base64'));
    }
  } finally {
    await stopProcess(chrome).catch(() => undefined);
    await server.close();
    await fs.rm(tempDir, {recursive: true, force: true}).catch(() => undefined);
  }
});
