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

// Command-palette state for keyboard selection. Reading it in one evaluate
// keeps the item order and selected index consistent (issue #385: the old
// blind ArrowDown loop raced React's filtered re-render under CI load).
async function paletteState(cdp) {
  return cdp.evaluate(`(() => {
    const items = [...document.querySelectorAll('.command-item')];
    return {
      texts: items.map(item => item.innerText.trim()),
      selected: items.findIndex(item => item.getAttribute('aria-selected') === 'true'),
    };
  })()`);
}

async function paletteTargetSelected(cdp) {
  return cdp.evaluate(
    "Boolean(document.querySelector('.command-item[aria-selected=\"true\"]')?.innerText.includes('任务'))",
  );
}

// Press ArrowDown and wait for the selection to actually move before pressing
// again, so a dropped/early keypress cannot desynchronise the loop.
async function advancePaletteSelection(cdp) {
  const before = (await paletteState(cdp)).selected;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await cdp.key('ArrowDown', 'ArrowDown');
    for (let poll = 0; poll < 20; poll += 1) {
      if ((await paletteState(cdp)).selected !== before) return true;
      await sleep(50);
    }
  }
  return false;
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
    if (response.exceptionDetails) throw new Error(response.exceptionDetails.exception?.description || response.exceptionDetails.text || 'Browser evaluation failed');
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

// CI runners occasionally swallow a single dispatched key event while the
// renderer is still settling; retry the keystroke until the expected UI shows
// up (or the attempts run out) instead of failing the whole acceptance run.
async function keyUntil(cdp, conditionExpression, key, code, modifiers = 0, attempts = 6) {
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    await cdp.key(key, code, modifiers);
    try {
      const until = Date.now() + 1200;
      while (Date.now() < until) {
        try {
          if (await cdp.evaluate(conditionExpression)) return;
        } catch {
          /* keep polling */
        }
        await sleep(80);
      }
    } catch {
      /* retry the keystroke */
    }
  }
  throw new Error(`keyboard condition not met after ${attempts} attempts: ${conditionExpression.slice(0, 80)}`);
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
  // The rootless runner's job container runs as UID 0; Chrome requires this flag for that UID.
  const chrome = spawn(
    chromePath,
    [
      '--headless=new',
      // Match Playwright's default; the rootless CI job has a 64 MiB /dev/shm.
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
      source: `window.__XEONOLAB_IFRAME_TIMEOUT__=600;window.__XEONOLAB_CHAT_ORIGIN__='http://127.0.0.1:${stalledPort}';`,
    });
    await cdp.call('Page.navigate', {url: `http://127.0.0.1:${portNumber}/tests/shell-fixture.html`});

    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.desktop'))"), 'desktop shell');
    await waitFor(() => cdp.evaluate("document.querySelectorAll('nav.dock .dock-item').length>=6"), 'dock apps');
    await waitFor(() => cdp.evaluate("document.querySelector('.widget-status')?.innerText.includes('1 项领取已过期')"), 'signed-in status widget shows expired claims');
    const widgetState = await cdp.evaluate("document.querySelector('.widget-status').innerText");
    assert.match(widgetState, /2 进行中/);
    assert.match(widgetState, /1 项领取已过期，可在任务中心重领/);
    await waitFor(() => cdp.evaluate("document.querySelector('nav.dock button[title=\\\"任务\\\"] .dock-badge')?.innerText==='2'"), 'app badges count queued plus active claims');

    const chromeState = await cdp.evaluate(`(() => ({
      menubar: [...document.querySelectorAll('.menubar button')].map(button=>button.innerText.trim()).filter(Boolean),
      dock: [...document.querySelectorAll('nav.dock .dock-item')].map(button=>button.title),
      labels: [...document.querySelectorAll('.bar .traffic button')].map(button=>button.getAttribute('aria-label')).filter(Boolean),
    }))()`);
    assert.ok(chromeState.menubar.includes('文件'), 'file menu is present');
    assert.ok(chromeState.menubar.includes('窗口'), 'window menu is present');
    for (const title of ['LaunchPad', '知识库', 'Embedding', '系统设置']) assert.ok(chromeState.dock.includes(title), `dock contains ${title}`);

    // Real pointer events exercise outside-click capture, separators and focus restoration.
    const click = async selector => {
      const point = await centerOf(cdp, selector);
      await cdp.call('Input.dispatchMouseEvent', {type: 'mousePressed', ...point, button: 'left', buttons: 1, clickCount: 1});
      await cdp.call('Input.dispatchMouseEvent', {type: 'mouseReleased', ...point, button: 'left', buttons: 0, clickCount: 1});
    };
    await click('.menubar-app-menu > button');
    await waitFor(() => cdp.evaluate("document.activeElement?.innerText.includes('关于 XeonoLab')"), 'menu initial focus');
    await cdp.key('ArrowDown', 'ArrowDown');
    assert.ok(await cdp.evaluate("document.activeElement?.innerText.includes('系统设置')"), 'ArrowDown skips the separator and focuses the intended entry');
    await cdp.key('Escape', 'Escape');
    assert.equal(await cdp.evaluate("document.activeElement===document.querySelector('.menubar-app-menu > button')"), true, 'Escape restores trigger focus');
    await click('.menubar-app-menu > button');
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.ds-menu-popover'))"), 'menu reopened');
    await click('.menubar-app-menu > button');
    await waitFor(() => cdp.evaluate("!document.querySelector('.ds-menu-popover')"), 'trigger toggles an already open menu closed');

    const pinnedTitles = () => cdp.evaluate("[...document.querySelectorAll('nav.dock .ds-pinned')].map(button=>button.title)");
    await cdp.evaluate("document.querySelector('nav.dock button[title=\"知识库\"]').focus()");
    await keyUntil(cdp, "Boolean(document.querySelector('.ds-context-menu'))", 'F10', 'F10', 8 /* Shift */);
    await cdp.evaluate("[...document.querySelectorAll('.ds-context-menu button')].find(button=>button.innerText.includes('向右移动')).setAttribute('data-test','right')");
    await click('.ds-context-menu [data-test=right]');
    await waitFor(async () => (await pinnedTitles())[1] === '知识库', 'Dock moves right with real pointer click');
    await cdp.evaluate("document.querySelector('nav.dock button[title=\"知识库\"]').focus()");
    await keyUntil(cdp, "Boolean(document.querySelector('.ds-context-menu'))", 'F10', 'F10', 8);
    await cdp.evaluate("[...document.querySelectorAll('.ds-context-menu button')].find(button=>button.innerText.includes('向左移动')).setAttribute('data-test','left')");
    await click('.ds-context-menu [data-test=left]');
    await waitFor(async () => (await pinnedTitles())[0] === '知识库', 'Dock moves left');

    // LaunchPad: search, keyboard selection, Enter to launch.
    await cdp.evaluate("document.querySelector('button[title=\"LaunchPad\"]').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.launchpad'))"), 'LaunchPad open');
    await cdp.evaluate(`(() => {const input=document.querySelector('.launchpad-search');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'知识');input.dispatchEvent(new Event('input',{bubbles:true}))})()`);
    await waitFor(() => cdp.evaluate("document.querySelector('.launchpad-grid')?.innerText.includes('知识库')"), 'LaunchPad search result');
    await cdp.key('Enter', 'Enter');
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.window[data-app-id=knowledge] .knowledge-sidebar'))"), 'knowledge window');

    // Knowledge provider carries a query into the existing native app, without hidden API work.
    await cdp.key('k', 'KeyK', 2);
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.command-input'))"), 'knowledge command palette');
    await cdp.evaluate(`(() => {const input=document.querySelector('.command-input');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'D100M');input.dispatchEvent(new Event('input',{bubbles:true}))})()`);
    await waitFor(() => cdp.evaluate("document.querySelector('.command-item[aria-selected=true]')?.innerText.includes('搜索 XeLibrary：D100M')"), 'knowledge provider query command');
    await cdp.key('Enter', 'Enter');
    await waitFor(() => cdp.evaluate("document.querySelector('.library-search input[type=search]')?.value==='D100M'"), 'query handed to Library search');
    assert.equal(await cdp.evaluate("window.shellFixture.calls.some(call=>call.path.startsWith('/search/'))"), false, 'palette never starts model searches while typing');
    await cdp.evaluate("[...document.querySelectorAll('.menubar > .ds-menu-button > button')].find(button=>button.innerText==='知识库').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.ds-menu-popover'))"), 'registry app menu');
    await cdp.evaluate("[...document.querySelectorAll('.ds-menu-popover button')].find(button=>button.innerText.includes('Query Console')).click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.library-query'))"), 'menu intent opens Query Console');

    // Embedding is a separate native app; both encoding and owner-scoped queue data
    // use the same authenticated API boundary and only render fixture responses.
    await cdp.evaluate('window.shellFixture.control.embeddingEnabled=true');
    await cdp.evaluate("document.querySelector('nav.dock button[title=\"Embedding\"]').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.window[data-app-id=embedding] .embedding-app'))"), 'Embedding app');
    await waitFor(() => cdp.evaluate("document.querySelector('.embedding-app').innerText.includes('D100M-Embedding')"), 'model readiness from API');
    await cdp.evaluate(`(() => {const input=document.querySelector('#embedding-text');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'fixture text');input.dispatchEvent(new Event('input',{bubbles:true}))})()`);
    await cdp.evaluate("[...document.querySelectorAll('.embedding-app button')].find(button=>button.innerText.includes('生成编码')).click()");
    await waitFor(() => cdp.evaluate("document.querySelector('.embedding-result')?.innerText.includes('3 维')"), 'real-shaped embedding response displayed');
    assert.match(await cdp.evaluate("document.querySelector('.embedding-queue-scroll').innerText"), /fixture-job-1/);
    assert.equal(await cdp.evaluate("window.shellFixture.calls.some(call=>call.path==='/embed/text'&&call.method==='POST'&&call.body.input==='fixture text')"), true, 'text is submitted through API boundary');
    const editEmbeddingText = value => cdp.evaluate(`(() => {const input=document.querySelector('#embedding-text');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,${JSON.stringify(value)});input.dispatchEvent(new Event('input',{bubbles:true}))})()`);
    const generateEmbedding = () => cdp.evaluate("[...document.querySelectorAll('.embedding-app button')].find(button=>button.innerText.includes('生成编码')).click()");
    const embeddingSettled = () => waitFor(() => cdp.evaluate("![...document.querySelectorAll('.embedding-app button')].find(button=>button.innerText.includes('生成编码')).hasAttribute('aria-busy')"), 'encoding request settled');
    await editEmbeddingText('edited text');
    assert.equal(await cdp.evaluate("Boolean(document.querySelector('.embedding-result'))"), false, 'editing completed input clears the old result');
    if (process.env.UI_SCREENSHOT_DIR) {
      await fs.mkdir(process.env.UI_SCREENSHOT_DIR, {recursive:true});
      const screenshot = await cdp.call('Page.captureScreenshot', {format:'png'});
      await fs.writeFile(path.join(process.env.UI_SCREENSHOT_DIR,'issue142-edited-input.png'),Buffer.from(screenshot.data,'base64'));
    }
    await generateEmbedding();
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.embedding-result'))"), 'encoding the edited input produces a new result');

    // Barrier-controlled responses exercise real React state without timing races.
    for (const failure of [false,true]) {
      await editEmbeddingText('pending original');
      await cdp.evaluate(`window.shellFixture.control.delayEmbedding=true;window.shellFixture.control.busyEmbedding=${failure}`);
      await generateEmbedding();
      await waitFor(() => cdp.evaluate('window.shellFixture.control.embeddingPending===1'), 'encoding blocked on fixture barrier');
      await editEmbeddingText('pending edited');
      assert.equal(await cdp.evaluate("[...document.querySelectorAll('.embedding-app button')].find(button=>button.innerText.includes('生成编码')).disabled"), true, 'editing keeps synchronous request busy until it settles');
      await editEmbeddingText('pending original');
      await cdp.evaluate('window.shellFixture.resolveEmbedding()');
      await embeddingSettled();
      assert.equal(await cdp.evaluate("Boolean(document.querySelector('.embedding-result,.embedding-error'))"), false, 'edited drafts discard old success and failure even after changing back');
      await cdp.evaluate('window.shellFixture.control.delayEmbedding=false;window.shellFixture.control.busyEmbedding=false');
    }
    await cdp.evaluate('window.shellFixture.control.qualityReady=true');
    await cdp.evaluate("document.querySelector('.embedding-app > header button').click()");
    await waitFor(() => cdp.evaluate("!document.querySelector('#embedding-model option[value=quality]').disabled"), 'second synthetic model ready');
    await cdp.evaluate('window.shellFixture.control.delayEmbedding=true');
    await generateEmbedding();
    await waitFor(() => cdp.evaluate('window.shellFixture.control.embeddingPending===1'), 'model-change request barrier');
    await cdp.evaluate(`(() => {const input=document.querySelector('#embedding-model');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(input,'quality');input.dispatchEvent(new Event('change',{bubbles:true}))})()`);
    await cdp.evaluate('window.shellFixture.resolveEmbedding()');
    await embeddingSettled();
    assert.equal(await cdp.evaluate("Boolean(document.querySelector('.embedding-result,.embedding-error'))"), false, 'changing model discards the previous model response');
    await cdp.evaluate('window.shellFixture.control.delayEmbedding=false');
    await cdp.evaluate(`(() => {const input=document.querySelector('#embedding-model');Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value').set.call(input,'fast');input.dispatchEvent(new Event('change',{bubbles:true}))})()`);
    await cdp.evaluate('window.shellFixture.control.busyEmbedding=true');
    await cdp.evaluate("[...document.querySelectorAll('.embedding-app button')].find(button=>button.innerText.includes('生成编码')).click()");
    await waitFor(() => cdp.evaluate("document.querySelector('.embedding-error')?.innerText.includes('D100M worker busy')"), '429 worker busy response remains visible and is not queued');
    await editEmbeddingText('fresh after failure');
    assert.equal(await cdp.evaluate("Boolean(document.querySelector('.embedding-error'))"), false, 'editing clears a completed error for the old input');
    await cdp.evaluate('window.shellFixture.control.busyEmbedding=false');
    // Batch encoding stays one synchronous request and maps out-of-order indexes.
    const addInput = () => cdp.evaluate("[...document.querySelectorAll('.embedding-app button')].find(button=>button.innerText.includes('新增输入项')).click()");
    await addInput();
    assert.equal(await cdp.evaluate("[...document.querySelectorAll('.embedding-app button')].find(button=>button.innerText.includes('生成编码')).disabled"), true, 'blank added item blocks submitting');
    await cdp.evaluate(`(() => {const input=document.querySelector('#embedding-text-1');Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(input,'second text');input.dispatchEvent(new Event('input',{bubbles:true}))})()`);
    await cdp.evaluate('window.shellFixture.resetCalls()');
    await generateEmbedding();
    await waitFor(() => cdp.evaluate("document.querySelectorAll('.embedding-result-item').length===2"), 'two indexed embedding results');
    const batchCalls = await cdp.evaluate("window.shellFixture.calls.filter(call=>call.path==='/embed/text')");
    assert.equal(batchCalls.length,1,'multiple items generate a single request');
    assert.deepEqual(batchCalls[0].body.input,['fresh after failure','second text']);
    await cdp.evaluate("document.querySelectorAll('.embedding-result-item details').forEach(details=>details.open=true)");
    assert.deepEqual(await cdp.evaluate("[...document.querySelectorAll('.embedding-result-item')].map(item=>({index:item.dataset.inputIndex,input:item.querySelector('.embedding-result-input').innerText,vector:JSON.parse(item.querySelector('details pre').innerText)}))"),
      [{index:'0',input:'fresh after failure',vector:[0.125,-0.5,0.25]},{index:'1',input:'second text',vector:[1.125,-0.5,0.25]}]);
    if (process.env.UI_SCREENSHOT_DIR) {
      await fs.mkdir(process.env.UI_SCREENSHOT_DIR,{recursive:true});
      await cdp.evaluate("document.querySelector('.embedding-result').scrollIntoView({block:'center'})");
      const screenshot=await cdp.call('Page.captureScreenshot',{format:'png'});
      await fs.writeFile(path.join(process.env.UI_SCREENSHOT_DIR,'issue145-batch-results.png'),Buffer.from(screenshot.data,'base64'));
      await fs.writeFile(path.join(process.env.UI_SCREENSHOT_DIR,'issue145-batch-report.json'),JSON.stringify({browser:chromePath,dataSource:'synthetic shell fixture',request:batchCalls[0],requestCount:batchCalls.length,rendered:await cdp.evaluate("[...document.querySelectorAll('.embedding-result-item')].map(item=>({index:item.dataset.inputIndex,input:item.querySelector('.embedding-result-input').innerText,vector:JSON.parse(item.querySelector('details pre').innerText)}))"),realModelInference:false},null,2)+'\n');
    }
    await cdp.evaluate('window.shellFixture.control.malformedEmbedding=true');
    await generateEmbedding();
    await waitFor(() => cdp.evaluate("document.querySelector('.embedding-error')?.innerText.includes('输入索引')"), 'malformed batch response shown as error');
    assert.equal(await cdp.evaluate("Boolean(document.querySelector('.embedding-result'))"),false,'malformed response never publishes partial vectors');
    await cdp.evaluate('window.shellFixture.control.malformedEmbedding=false;window.shellFixture.control.busyEmbedding=true');
    await generateEmbedding();
    await waitFor(() => cdp.evaluate("document.querySelector('.embedding-error')?.innerText.includes('D100M worker busy')"), 'batch worker429 stays an error and is not queued');
    await cdp.evaluate('window.shellFixture.control.busyEmbedding=false');
    for (let i=2;i<32;i++) await addInput();
    assert.equal(await cdp.evaluate("document.querySelectorAll('.embedding-input-item').length"),32,'input count reaches maximum32');
    assert.equal(await cdp.evaluate("[...document.querySelectorAll('.embedding-app button')].find(button=>button.innerText.includes('新增输入项')).disabled"),true,'cannot add item33');
    assert.equal(await cdp.evaluate("document.querySelector('#embedding-text').maxLength"),32768,'existing per-text character limit remains');
    for (let i=32;i>1;i--) await cdp.evaluate(`document.querySelector('button[aria-label="移除输入项 ${i}"]').click()`);
    assert.equal(await cdp.evaluate("document.querySelectorAll('.embedding-input-item').length"),1,'items can be removed back to single mode');
    assert.equal(await cdp.evaluate("document.querySelector('button[aria-label=\"移除输入项 1\"]').disabled"),true,'last input cannot be removed');
    await cdp.evaluate('window.shellFixture.control.embeddingReadOnly=true;window.shellFixture.resetCalls()');
    await cdp.evaluate("document.querySelector('.embedding-app > header button').click()");
    await waitFor(() => cdp.evaluate("document.querySelector('.embedding-app').innerText.includes('需要生成权限')"), 'read-only account cannot generate');
    assert.equal(await cdp.evaluate("window.shellFixture.calls.some(call=>call.path==='/embed/text')"),false,'no request without write scope');
    await cdp.evaluate('window.shellFixture.control.embeddingReadOnly=false');
    await cdp.evaluate('window.shellFixture.control.failEmbeddingQueue=true');
    await cdp.evaluate("document.querySelector('.embedding-app > header button').click()");
    await waitFor(() => cdp.evaluate("document.querySelectorAll('.embedding-panel')[1]?.innerText.includes('403 embedding:read required')"), 'queue API denial surfaced');
    await cdp.evaluate('window.shellFixture.control.failEmbeddingQueue=false;window.shellFixture.control.embeddingEnabled=false;window.shellFixture.resetCalls()');
    await cdp.evaluate("document.querySelector('.embedding-app > header button').click()");
    await waitFor(() => cdp.evaluate("document.querySelectorAll('.embedding-panel')[1]?.innerText.includes('需要 embedding:read 权限')"), 'queue hidden without verified embedding scope');
    assert.equal(await cdp.evaluate("window.shellFixture.calls.some(call=>call.path==='/embedding/jobs?limit=50')"), false, 'no queue request is sent without scope');
    await cdp.evaluate('window.shellFixture.control.embeddingEnabled=false');

    const keyboardBefore = await rectOf(cdp, '.window[data-app-id=knowledge]');
    await cdp.evaluate("document.querySelector('.window[data-app-id=knowledge] .bar').focus()");
    await cdp.key('ArrowRight', 'ArrowRight');
    await cdp.key('ArrowDown', 'ArrowDown', 8);
    const keyboardAfter = await rectOf(cdp, '.window[data-app-id=knowledge]');
    assert.equal(keyboardAfter.x, keyboardBefore.x + 10, 'focused titlebar supports keyboard moving');
    assert.equal(keyboardAfter.h, keyboardBefore.h + 10, 'Shift+ArrowDown supports keyboard resizing');

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

    // Window: maximize fills the viewport and temporarily hides the Dock.
    const beforeMax = await rectOf(cdp, '.window[data-app-id=knowledge]');
    await cdp.evaluate("document.querySelector('.window[data-app-id=knowledge] .bar .max').click()");
    await sleep(150);
    const maximized = await rectOf(cdp, '.window[data-app-id=knowledge]');
    assert.equal(maximized.x, 0);
    assert.equal(maximized.y, 30, 'maximized window starts immediately below the menu bar');
    assert.equal(maximized.w, 1440);
    assert.equal(maximized.y + maximized.h, await cdp.evaluate('innerHeight'));
    await waitFor(() => cdp.evaluate("getComputedStyle(document.querySelector('nav.dock')).opacity==='0'"), 'Dock hides while maximized');
    if (process.env.UI_SCREENSHOT_DIR) {
      await fs.mkdir(process.env.UI_SCREENSHOT_DIR, {recursive:true});
      const screenshot = await cdp.call('Page.captureScreenshot', {format:'png'});
      await fs.writeFile(path.join(process.env.UI_SCREENSHOT_DIR,'issue133-maximized.png'),Buffer.from(screenshot.data,'base64'));
    }

    // Compact widgets and each Dock size must leave maximized content reachable.
    for (const [width, height] of [[1366,768],[820,1180],[390,844]]) {
      await cdp.call('Emulation.setDeviceMetricsOverride', {width, height, deviceScaleFactor: 1, mobile: width < 768});
      await sleep(250);
      const rect = await rectOf(cdp, '.window[data-app-id=knowledge]');
      assert.deepEqual(rect, {x:0,y:30,w:width,h:height-30}, 'maximized frame follows viewport edges');
      if (width < 1024) assert.equal(await cdp.evaluate("getComputedStyle(document.querySelector('.widgets')).visibility"), 'hidden');
      for (const size of ['small','medium','large']) {
        await cdp.evaluate(`document.querySelector('nav.dock').classList.remove('dock-size-small','dock-size-medium','dock-size-large');document.querySelector('nav.dock').classList.add('dock-size-${size}')`);
        assert.equal(await cdp.evaluate("document.elementFromPoint(innerWidth/2,innerHeight-20)?.closest('.window')?.dataset.appId"), 'knowledge', `${size} Dock does not cover bottom content`);
      }
    }
    await cdp.call('Emulation.setDeviceMetricsOverride', {width:1440,height:900,deviceScaleFactor:1,mobile:false});
    await sleep(250);
    await cdp.evaluate("document.querySelector('.window[data-app-id=knowledge] .bar .min').click()");
    assert.equal(await cdp.evaluate("document.querySelector('nav.dock').classList.contains('ds-autohide')"), false, 'minimizing removes temporary Dock hiding');
    await cdp.evaluate("document.querySelector('.dock-minimized button').click()");
    assert.equal(await cdp.evaluate("document.querySelector('nav.dock').classList.contains('ds-autohide')"), true, 'restoring maximized window hides Dock again');
    await cdp.call('Input.dispatchMouseEvent', {type:'mouseMoved',x:720,y:899});
    await waitFor(() => cdp.evaluate("getComputedStyle(document.querySelector('nav.dock')).opacity==='1'"), 'bottom edge reveals Dock over maximized app');
    await click('nav.dock button[title="系统设置"]');
    assert.equal(await cdp.evaluate("document.querySelector('nav.dock').classList.contains('ds-autohide')"), false, 'normal foreground window shows Dock while maximized window remains behind');
    await cdp.evaluate("document.querySelector('.window[data-app-id=knowledge] .bar .max').focus()");
    await waitFor(() => cdp.evaluate("document.querySelector('nav.dock').classList.contains('ds-autohide')"), 'keyboard focus on maximized window hides Dock');
    await cdp.evaluate("document.querySelector('.window[data-app-id=knowledge] .bar .max').click()");
    await sleep(150);
    assert.equal(await cdp.evaluate("document.querySelector('nav.dock').classList.contains('ds-autohide')"), false, 'restore retains the non-autohide preference');
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
    await waitFor(() => cdp.evaluate("document.querySelector('.command-input')?.value === 'Harness'"), 'palette filter input');
    await waitFor(
      () => cdp.evaluate("[...document.querySelectorAll('.command-item')].some(item => item.innerText.includes('任务'))"),
      'filtered palette items',
    );
    if (!(await paletteTargetSelected(cdp))) {
      if ((await paletteState(cdp)).selected < 0) {
        await cdp.key('ArrowDown', 'ArrowDown');
        await waitFor(async () => (await paletteState(cdp)).selected >= 0, 'palette initial keyboard selection');
      }
      for (let step = 0; step < 15; step += 1) {
        if (await paletteTargetSelected(cdp)) break;
        if (!(await advancePaletteSelection(cdp))) break;
      }
    }
    if (!(await paletteTargetSelected(cdp))) {
      const state = await paletteState(cdp);
      throw new Error(
        `palette keyboard selection failed; items=${JSON.stringify(state.texts)} selected=${state.selected}`,
      );
    }
    await cdp.key('Enter', 'Enter');
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.window[data-app-id=issues] .issues-app'))"), 'issues app from palette');
    await waitFor(() => cdp.evaluate("document.querySelectorAll('.issues-table tbody tr').length===2"), 'active Issues excludes the expired claim');
    assert.match(await cdp.evaluate("[...document.querySelectorAll('.issues-toolbar [role=tab]')].find(tab=>tab.innerText.includes('进行中')).innerText"), /2/);
    await cdp.evaluate("[...document.querySelectorAll('.issues-app button')].find(button=>button.innerText.includes('加载更多'))?.click()");
    await waitFor(() => cdp.evaluate("document.querySelector('[role=tab][aria-selected=true]')?.innerText.includes('进行中') && document.querySelectorAll('.issues-table tbody tr').length===2"), 'active counter remains correct after pagination');
    await cdp.evaluate("[...document.querySelectorAll('.issues-toolbar [role=tab]')].find(tab=>tab.innerText.includes('已过期'))?.click()");
    await waitFor(() => cdp.evaluate("document.querySelectorAll('.issues-table tbody tr').length===1 && document.querySelector('.issues-table tbody').innerText.includes('领取已过期')"), 'expired Issue remains separately visible');
    assert.match(await cdp.evaluate("document.querySelector('.issues-table tbody').innerText"), /可重领/);
    assert.equal(await cdp.evaluate("document.querySelector('.issues-table tbody .ds-badge')?.classList.contains('ds-badge-warning')"),true,'expired claim receives the expired state tone');
    await cdp.evaluate("[...document.querySelectorAll('.issues-toolbar [role=tab]')].find(tab=>tab.innerText.includes('全部'))?.click()");
    await waitFor(() => cdp.evaluate("document.querySelectorAll('.issues-table tbody tr').length===5"), 'issues all filter');

    // The Development summary uses active counts while keeping expired work visible.
    await cdp.evaluate("document.querySelector('nav.dock button[title=\\\"开发\\\"]')?.click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.development-app'))"), 'Development task summary');
    const developmentState = await cdp.evaluate("[...document.querySelectorAll('.dev-card')].find(card=>card.innerText.includes('Harness 任务')).innerText");
    assert.match(developmentState, /2 进行中/);
    assert.match(developmentState, /已过期 · 可重领/);
    await cdp.evaluate("document.querySelector('.window[data-app-id=development] .bar .close')?.click()");

    // Settings persist through the platform API and drive the document theme.
    await cdp.evaluate("document.querySelector('nav.dock button[title=\"系统设置\"]').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.window[data-app-id=settings] .settings-panel'))"), 'settings window');
    await cdp.evaluate("[...document.querySelectorAll('.widget-options label')].find(label=>label.innerText.includes('日历')).querySelector('input').click()");
    await cdp.evaluate("[...document.querySelectorAll('.widget-options label')].find(label=>label.innerText.includes('应用快捷入口')).querySelector('input').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.widget-calendar')&&document.querySelector('.widget-shortcuts'))"), 'calendar and app shortcut widgets');
    // The 12-item shortcut cap is visible and counted instead of replacing silently.
    await waitFor(() => cdp.evaluate("document.querySelector('[data-testid=shortcut-limit-hint]')?.innerText.includes('已选 0/12')"), 'shortcut limit hint shows the empty count');
    await cdp.evaluate("[...document.querySelectorAll('.widget-shortcut-options label')].find(label=>label.innerText.includes('知识库')).querySelector('input').click()");
    await waitFor(() => cdp.evaluate("window.shellFixture.preferences.shortcut_apps.includes('knowledge')"), 'authorized app shortcut saved');
    await waitFor(() => cdp.evaluate("document.querySelector('[data-testid=shortcut-limit-hint]')?.innerText.includes('已选 1/12')"), 'shortcut limit hint updates with the selection');
    assert.match(await cdp.evaluate("document.querySelector('.widget-calendar').getAttribute('aria-label')"), /日历/);
    await cdp.evaluate("document.querySelector('.widget-shortcuts button[aria-label=\"打开知识库\"]').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.window[data-app-id=knowledge]'))"), 'authorized app opened from shortcut widget');
    const wallpaperPath = path.join(os.tmpdir(), `xeonolab-wallpaper-${process.pid}.png`);
    await fs.writeFile(wallpaperPath, Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/4ZsAAAAASUVORK5CYII=', 'base64'));
    const documentNode = await cdp.call('DOM.getDocument', {depth: -1});
    const wallpaperInput = await cdp.call('DOM.querySelector', {nodeId: documentNode.root.nodeId, selector: 'input[aria-label="选择自定义壁纸"]'});
    await cdp.call('DOM.setFileInputFiles', {files: [wallpaperPath], nodeId: wallpaperInput.nodeId});
    await waitFor(() => cdp.evaluate("window.shellFixture.preferences.wallpaper==='custom'"), 'custom wallpaper choice persisted through API');
    await waitFor(() => cdp.evaluate("document.querySelector('.desktop[data-wallpaper-custom=\"image\"]')?.style.backgroundImage.includes('blob:')"), 'saved wallpaper loaded for shell background');
    const renderedWallpaper = await cdp.evaluate(`(async () => {
      const background = getComputedStyle(document.querySelector('.desktop')).backgroundImage;
      const url = background.match(/url\\(["']?(blob:[^"')]+)["']?\\)/)?.[1];
      if (!url) return false;
      const image = new Image();
      image.src = url;
      await image.decode();
      return image.naturalWidth > 0 && image.naturalHeight > 0;
    })()`);
    assert.equal(renderedWallpaper, true, 'the rendered account wallpaper decodes in the browser');
    assert.ok(await cdp.evaluate("window.shellFixture.calls.some(call=>call.method==='PUT'&&call.path==='/users/me/wallpaper')"), 'wallpaper upload uses authenticated server API');
    await cdp.call('Emulation.setDeviceMetricsOverride', {width: 390, height: 844, deviceScaleFactor: 1, mobile: true});
    const mobileWallpaperControl = await cdp.evaluate("(() => {const input=document.querySelector('input[aria-label=\"选择自定义壁纸\"]');input.scrollIntoView({block:'center'});const rect=input.getBoundingClientRect();return {left:rect.left,right:rect.right,width:innerWidth}})()");
    assert.ok(mobileWallpaperControl.left >= 0 && mobileWallpaperControl.right <= mobileWallpaperControl.width, 'wallpaper control fits the 390px settings viewport');
    await cdp.call('Emulation.setDeviceMetricsOverride', {width: 1440, height: 913, deviceScaleFactor: 1, mobile: false});
    await fs.rm(wallpaperPath, {force: true});
    await cdp.evaluate("document.querySelector('button[role=switch][aria-label=\"减少动态效果\"]').click()");
    await waitFor(() => cdp.evaluate("document.documentElement.dataset.reducedMotion==='true'"), 'reduced motion applied');
    await cdp.evaluate("document.querySelector('button[role=switch][aria-label=\"Dock 自动隐藏\"]').click()");
    await waitFor(() => cdp.evaluate("document.querySelector('nav.dock').classList.contains('ds-autohide')"), 'dock autohide applied');

    // A real pointer can leave the tiny bottom-edge trigger before the moving Dock
    // reaches it. Keep the reveal alive across the user's slow entry path, and let
    // keyboard focus reveal the same auto-hidden Dock.
    await cdp.call('Emulation.setDeviceMetricsOverride', {width: 1440, height: 913, deviceScaleFactor: 1, mobile: false});
    const movePointer = (x, y, pointerType = 'mouse') => cdp.call('Input.dispatchMouseEvent', {type: 'mouseMoved', x, y, pointerType, button: 'none', buttons: 0});
    const dockState = () => cdp.evaluate(`(() => {
      const dock = document.querySelector('nav.dock');
      const edge = document.querySelector('.dock-edge');
      const style = getComputedStyle(dock);
      const rect = dock.getBoundingClientRect();
      return {opacity: style.opacity, pointerEvents: style.pointerEvents, hovered: dock.matches(':hover'), edgeHovered: edge?.matches(':hover') ?? false, pointerActive: dock.classList.contains('ds-pointer-active'), duration: style.transitionDuration, reducedMotion: document.documentElement.dataset.reducedMotion === 'true', x: rect.x, y: rect.y, width: rect.width, height: rect.height};
    })()`);
    const dockPointerTrace = [];
    const recordDockPointer = async (point, pointerType, dwellMs) => {
      dockPointerTrace.push({point, pointerType, requestedDwellMs: dwellMs, observedAt: new Date().toISOString(), ...(await dockState())});
    };
    const persistDockTrace = async failure => {
      const verificationRoot = path.resolve(appRoot, '..', '..', 'runtime', 'verification', 'issue-75');
      await fs.mkdir(verificationRoot, {recursive: true});
      await fs.writeFile(
        path.join(verificationRoot, 'dock-hover-path.json'),
        `${JSON.stringify({browser: 'Chrome/Chromium via CDP', viewport: '1440x913', failure: failure || null, trace: dockPointerTrace}, null, 2)}\n`,
      );
    };
    const assertDockVisible = async label => {
      try {
        await waitFor(async () => {
          const state = await dockState();
          return Number(state.opacity) > 0.98 && state.pointerEvents === 'auto' && state.hovered;
        }, label);
      } catch (error) {
        dockPointerTrace.push({point: `${label} FAILED`, observedAt: new Date().toISOString(), ...(await dockState())});
        await persistDockTrace(error instanceof Error ? error.message : String(error));
        throw error;
      }
    };
    assert.match((await dockState()).duration, /0s/, 'reduced-motion preference removes Dock transition');
    await movePointer(720, 500);
    await waitFor(async () => (await dockState()).opacity === '0', 'auto-hidden dock starts closed');
    await movePointer(720, 905);
    await sleep(90);
    await recordDockPointer('slow path y=905 (edge)', 'mouse', 90);
    await movePointer(720, 899);
    await sleep(90);
    await recordDockPointer('slow path y=899', 'mouse', 90);
    await movePointer(720, 889);
    await sleep(90);
    await recordDockPointer('slow path y=889', 'mouse', 90);
    await movePointer(720, 873);
    await sleep(90);
    await recordDockPointer('slow path y=873', 'mouse', 90);
    await movePointer(720, 860);
    await assertDockVisible('slow bottom-edge-to-Dock pointer path remains open with reduced motion');
    await recordDockPointer('slow path y=860 (Dock center)', 'mouse', 90);
    await movePointer(720, 600);
    await waitFor(async () => (await dockState()).opacity === '0', 'Dock hides again after pointer leaves');
    await recordDockPointer('slow path leave y=600 (hidden)', 'mouse', 520);

    const firstDockButton = await cdp.evaluate("document.querySelector('nav.dock .dock-item').focus(); getComputedStyle(document.querySelector('nav.dock')).opacity");
    assert.equal(firstDockButton, '1', 'keyboard focus reveals the auto-hidden Dock');
    await cdp.evaluate("document.activeElement.blur()");
    await waitFor(async () => (await dockState()).opacity === '0', 'Dock hides after keyboard focus leaves');

    // Restore full motion to verify the longer pop-out and direct, fast entry too.
    await cdp.evaluate("document.querySelector('button[role=switch][aria-label=\"减少动态效果\"]').click()");
    await waitFor(() => cdp.evaluate("document.documentElement.dataset.reducedMotion==='false'"), 'reduced motion turned off for animation check');
    assert.match((await dockState()).duration, /0\.42s/, 'Dock uses the slower 420ms transition');
    await movePointer(720, 500);
    await waitFor(async () => (await dockState()).opacity === '0', 'full-motion Dock starts closed');
    for (const [y, label] of [[905, 'edge'], [899, 'gap'], [889, 'gap'], [873, 'approach'], [860, 'center']]) {
      await movePointer(720, y);
      await sleep(90);
      await recordDockPointer(`full-motion slow path y=${y} (${label})`, 'mouse', 90);
    }
    await assertDockVisible('full-motion 90ms step path reaches Dock hover target');
    await movePointer(720, 600);
    await waitFor(async () => (await dockState()).opacity === '0', 'full-motion slow path hides after leaving');
    await recordDockPointer('full-motion slow path leave y=600 (hidden)', 'mouse', 940);

    // Re-enter before the hide grace expires to ensure a previous leave timer cannot
    // retract a Dock that has been entered again.
    await movePointer(720, 905);
    await sleep(90);
    await movePointer(720, 600);
    await sleep(250);
    await movePointer(720, 905);
    await sleep(90);
    await movePointer(720, 860);
    await assertDockVisible('re-entering during hide grace cancels the old timer');
    await sleep(600);
    await assertDockVisible('Dock stays open while the pointer remains over it');
    await recordDockPointer('mouse re-entry after canceled timer y=860', 'mouse', 600);
    await movePointer(720, 600);
    await waitFor(async () => (await dockState()).opacity === '0', 're-entered Dock hides after the next leave');
    await recordDockPointer('mouse second leave y=600 (hidden)', 'mouse', 940);

    // Pen hover follows the same reveal path as a mouse. Touch continues to use the
    // pre-existing CSS edge hover behavior and is excluded from the JS grace timer.
    await movePointer(720, 905, 'pen');
    await sleep(90);
    await movePointer(720, 860, 'pen');
    await assertDockVisible('pen pointer reveals Dock');
    await recordDockPointer('pen path y=860 (Dock center)', 'pen', 90);
    await movePointer(720, 600, 'pen');
    await waitFor(async () => (await dockState()).opacity === '0', 'pen-leave Dock returns to hidden state');
    await recordDockPointer('pen leave y=600 (hidden)', 'pen', 940);

    await cdp.call('Emulation.setTouchEmulationEnabled', {enabled: true, maxTouchPoints: 1});
    await cdp.call('Input.dispatchTouchEvent', {type: 'touchStart', touchPoints: [{x: 720, y: 905, id: 1}]});
    await sleep(90);
    const touchDockState = await dockState();
    await recordDockPointer('touch at y=905 (existing edge CSS behavior)', 'touch', 90);
    assert.equal(touchDockState.pointerActive, false, 'touch input does not start the mouse/pen hide timer');
    assert.equal(touchDockState.opacity, '0', 'touch input preserves the existing auto-hidden mobile behavior');
    await cdp.call('Input.dispatchTouchEvent', {type: 'touchEnd', touchPoints: []});
    await cdp.call('Emulation.setTouchEmulationEnabled', {enabled: false});
    await movePointer(720, 600);

    // Direct, fast mouse entry remains supported after repeated hover/timer changes.
    await movePointer(720, 500);
    await waitFor(async () => (await dockState()).opacity === '0', 'fast-entry Dock starts closed');
    await movePointer(720, 905);
    await movePointer(720, 860);
    await assertDockVisible('fast edge-to-center pointer entry reveals Dock');
    await recordDockPointer('fast path y=860 (Dock center)', 'mouse', 0);
    await movePointer(720, 600);
    await waitFor(async () => (await dockState()).opacity === '0', 'fast-entry Dock returns to auto-hidden state');
    await recordDockPointer('fast path leave y=600 (hidden)', 'mouse', 940);

    await cdp.evaluate("document.querySelector('button[role=switch][aria-label=\"Dock 自动隐藏\"]').click()");
    await waitFor(async () => !(await cdp.evaluate("document.querySelector('nav.dock').classList.contains('ds-autohide')")) && (await dockState()).opacity === '1', 'turning auto-hide off shows Dock');
    await cdp.evaluate("document.querySelector('button[role=switch][aria-label=\"Dock 自动隐藏\"]').click()");
    await waitFor(async () => (await cdp.evaluate("document.querySelector('nav.dock').classList.contains('ds-autohide')")) && (await dockState()).opacity === '0', 'turning auto-hide back on starts hidden without stale pointer state');
    await recordDockPointer('auto-hide off then on while pointer is away', 'mouse', 420);
    await persistDockTrace(null);
    await cdp.evaluate("document.querySelector('button[role=switch][aria-label=\"减少动态效果\"]').click()");
    await waitFor(() => cdp.evaluate("document.documentElement.dataset.reducedMotion==='true'"), 'reduced motion preference restored');
    await cdp.call('Emulation.setDeviceMetricsOverride', {width: 1440, height: 900, deviceScaleFactor: 1, mobile: false});

    await cdp.evaluate("document.querySelector('button[role=switch][aria-label=\"界面动画\"]').click()");
    await waitFor(() => cdp.evaluate("document.documentElement.dataset.animations==='off'"), 'animations disabled');
    await cdp.evaluate("document.querySelector('button[role=switch][aria-label=\"Dock 自动隐藏\"]').click()");
    await cdp.evaluate("document.querySelector('button[role=switch][aria-label=\"界面动画\"]').click()");
    await waitFor(() => cdp.evaluate("document.documentElement.dataset.animations==='on' && !document.querySelector('nav.dock').classList.contains('ds-autohide')"), 'desktop options restored');
    const preferenceCalls = await cdp.evaluate("window.shellFixture.calls.filter(call=>call.path==='/users/me/preferences'&&call.method==='PATCH').map(call=>call.body)");
    assert.ok(preferenceCalls.some(body => body.reduced_motion === true), 'preference patch recorded');

    // Settings exposes pin/unpin and order as keyboard-accessible controls.
    await cdp.evaluate("[...document.querySelectorAll('.pinned-apps li')].find(item=>item.innerText.includes('开发')).querySelector('input').click()");
    await waitFor(() => cdp.evaluate("!document.querySelector('nav.dock button[title=\"开发\"]')"), 'unpin from Settings');
    await cdp.evaluate("[...document.querySelectorAll('.pinned-apps li')].find(item=>item.innerText.includes('开发')).querySelector('input').click()");
    await waitFor(() => cdp.evaluate("document.querySelector('nav.dock .ds-pinned:last-of-type')?.title==='开发'"), 'pin from Settings');
    await cdp.evaluate("document.querySelector('.pinned-apps button[aria-label=\"向前移动 开发\"]').click()");
    await waitFor(async () => (await pinnedTitles()).at(-2) === '开发', 'reorder from Settings');

    // Settings receives working callbacks for explicit layout saving.
    const savedBefore=await cdp.evaluate("window.shellFixture.calls.filter(call=>call.method==='PUT').length");
    await cdp.evaluate("[...document.querySelectorAll('.preference-form button')].find(button=>button.innerText==='立即保存当前布局').click()");
    await waitFor(()=>cdp.evaluate(`window.shellFixture.calls.filter(call=>call.method==='PUT').length>${savedBefore}`),'explicit layout save');
    assert.ok((await cdp.evaluate("document.querySelector('.preference-form [role=status]').innerText")).includes('已保存'));
    // Dock preferences still persist when window restoration is switched off.
    await cdp.evaluate("document.querySelector('button[role=switch][aria-label=\"恢复上次窗口\"]').click()");
    await waitFor(()=>cdp.evaluate("window.shellFixture.preferences.restore_windows===false"),'restore preference off');
    const layoutBefore=await cdp.evaluate("window.shellFixture.calls.filter(call=>call.method==='PUT').length");
    await cdp.evaluate("document.querySelector('.window[data-app-id=settings] .bar .max').click()");
    await waitFor(()=>cdp.evaluate(`window.shellFixture.calls.filter(call=>call.method==='PUT').length>${layoutBefore}`),'layout saves with restore off');
    await cdp.evaluate("document.querySelector('button[role=switch][aria-label=\"恢复上次窗口\"]').click()");

    // System pages expose true missing-data and permission states.
    await cdp.evaluate('window.shellFixture.control.failWork=true');
    await cdp.evaluate('window.shellFixture.control.delayTables=true');
    await cdp.evaluate("document.querySelector('nav.dock button[title=\"系统状态\"]').click()");
    await waitFor(()=>cdp.evaluate("Boolean(document.querySelector('.system-app'))"),'System app');
    await waitFor(()=>cdp.evaluate(`(() => {
      const card=[...document.querySelectorAll('.mac-card')].find(item=>item.innerText.includes('知识库概览'));
      return card?.querySelector('.mac-badge')?.innerText==='读取中…' && window.shellFixture.control.tablesInFlight===1;
    })()`),'Dashboard catalogue loading state');
    assert.match(await cdp.evaluate("[...document.querySelectorAll('.mac-card')].find(item=>item.innerText.includes('知识库概览')).innerText"),/正在读取可见知识表/);
    assert.doesNotMatch(await cdp.evaluate("[...document.querySelectorAll('.mac-card')].find(item=>item.innerText.includes('知识库概览')).innerText"),/0 张表/);
    await cdp.evaluate('window.shellFixture.control.delayTables=false');
    await waitFor(()=>cdp.evaluate("Boolean([...document.querySelectorAll('.mac-card')].find(card=>card.innerText.includes('Harness 任务队列'))?.querySelector('[role=alert]'))"),'dashboard queue error state');
    const queueError = await cdp.evaluate(`(() => {
      const card=[...document.querySelectorAll('.mac-card')].find(item=>item.innerText.includes('Harness 任务队列'));
      return {message:card?.querySelector('[role=alert]')?.innerText, retry:card?.querySelector('[role=alert] button')?.innerText};
    })()`);
    assert.match(queueError.message, /无法读取任务队列/);
    assert.equal(queueError.retry, '重试');
    await cdp.evaluate(`(() => {
      window.shellFixture.control.failWork=false;
      const card=[...document.querySelectorAll('.mac-card')].find(item=>item.innerText.includes('Harness 任务队列'));
      card.querySelector('[role=alert] button').click();
    })()`);
    await waitFor(()=>cdp.evaluate("!([...document.querySelectorAll('.mac-card')].find(card=>card.innerText.includes('Harness 任务队列'))?.querySelector('[role=alert]'))"),'dashboard queue retry recovery');
    assert.ok(await cdp.evaluate("[...document.querySelectorAll('.mac-card')].find(card=>card.innerText.includes('Harness 任务队列'))?.innerText.includes('打开任务中心')"));
    await waitFor(()=>cdp.evaluate(`(() => {
      const card=[...document.querySelectorAll('.mac-card')].find(item=>item.innerText.includes('知识库概览'));
      return window.shellFixture.control.tablesInFlight===0 && card?.querySelector('.mac-badge')?.innerText==='1 张表' && Boolean(card?.querySelector('.mac-bars'));
    })()`),'Dashboard catalogue loaded');
    const knowledgeOverview = await cdp.evaluate(`(() => {
      const card=[...document.querySelectorAll('.mac-card')].find(item=>item.innerText.includes('知识库概览'));
      return {text:card?.innerText,chart:card?.querySelector('.mac-bars')?.getAttribute('aria-label'),requests:window.shellFixture.calls.filter(call=>call.path.startsWith('/tables?')).map(call=>call.path)};
    })()`);
    assert.match(knowledgeOverview.text,/1 张表/);
    assert.match(knowledgeOverview.chart,/目录：示例，1 张表/);
    assert.ok(knowledgeOverview.requests.includes('/tables?limit=1000'));
    await cdp.evaluate('window.shellFixture.control.delayTables=true');
    await cdp.evaluate("[...document.querySelectorAll('.dashboard-actions button')].find(button=>button.innerText==='刷新').click()");
    const refreshingOverview = await cdp.evaluate(`(() => {
      const card=[...document.querySelectorAll('.mac-card')].find(item=>item.innerText.includes('知识库概览'));
      return {badge:card?.querySelector('.mac-badge')?.innerText,text:card?.innerText};
    })()`);
    assert.equal(refreshingOverview.badge,'读取中…');
    assert.match(refreshingOverview.text,/正在读取可见知识表/);
    assert.doesNotMatch(refreshingOverview.text,/0 张表/);
    await waitFor(()=>cdp.evaluate('window.shellFixture.control.tablesInFlight===1'),'delayed Dashboard refresh request');
    await cdp.evaluate('window.shellFixture.control.delayTables=false');
    await waitFor(()=>cdp.evaluate(`(() => {
      const card=[...document.querySelectorAll('.mac-card')].find(item=>item.innerText.includes('知识库概览'));
      return window.shellFixture.control.tablesInFlight===0 && card?.querySelector('.mac-badge')?.innerText==='1 张表' && Boolean(card?.querySelector('.mac-bars'));
    })()`),'Dashboard refresh settled');
    const dashboardWork = await cdp.evaluate(`(() => {
      const card=[...document.querySelectorAll('.mac-card')].find(item=>item.innerText.includes('Harness 任务队列'));
      return {ring:card?.querySelector('.mac-ring')?.getAttribute('aria-label'),text:card?.innerText};
    })()`);
    assert.match(dashboardWork.ring,/排队 1.*进行中 1.*已过期 1.*已完成 1.*阻塞 1/);
    assert.match(dashboardWork.text,/已过期 · 可重领/);
    await cdp.evaluate("[...document.querySelectorAll('.system-sidebar button')].find(button=>button.innerText==='Agents').click()");
    await waitFor(()=>cdp.evaluate("document.querySelector('.system-content .system-table-scroll tbody')?.innerText.includes('领取已过期')"),'System Agents view marks expired claim');
    assert.match(await cdp.evaluate("document.querySelector('.system-content .system-table-scroll tbody').innerText"),/已过期 · 可重领/);
    await cdp.evaluate("[...document.querySelectorAll('.system-sidebar button')].find(button=>button.innerText==='存储').click()");
    await waitFor(()=>cdp.evaluate("document.querySelector('.system-content').innerText.includes('hf-cache:read')"),'Storage scope notice without pool numbers');
    await cdp.evaluate("[...document.querySelectorAll('.system-sidebar button')].find(button=>button.innerText==='审计').click()");
    await waitFor(()=>cdp.evaluate("document.querySelector('.system-content').innerText.includes('xelibrary:admin')"),'Audit permission denied');
    await cdp.evaluate("[...document.querySelectorAll('.system-sidebar button')].find(button=>button.innerText==='网络').click()");
    const lanEntry = await cdp.evaluate(`(() => {
      const row=document.querySelector('.lan-entry');
      return {origin:row?.querySelector('[data-testid=lan-entry-origin]')?.innerText,text:row?.innerText||'',buttons:[...document.querySelectorAll('.lan-entry button')].map(button=>button.innerText)};
    })()`);
    assert.equal(lanEntry.origin,'https://192.168.0.101:9443');
    assert.ok(lanEntry.buttons.includes('复制内网入口'));
    assert.match(lanEntry.text,/LAN CA/);
    await cdp.evaluate("document.querySelector('.window[data-app-id=system] .bar .close').click()");

    // Failed settings writes remain visible and are never labelled saved.
    await cdp.evaluate("window.shellFixture.control.failPreferences=true;document.querySelector('button[role=switch][aria-label=\"界面动画\"]').click()");
    await waitFor(()=>cdp.evaluate("document.querySelector('.preference-form [role=status]').innerText.includes('保存失败')"),'preference save failure');
    await cdp.evaluate("window.shellFixture.control.failPreferences=false;document.querySelector('button[role=switch][aria-label=\"界面动画\"]').click()");
    await waitFor(()=>cdp.evaluate("document.querySelector('.preference-form [role=status]').innerText.includes('已应用并保存')"),'preference save recovery');
    await cdp.evaluate("window.shellFixture.control.failLayout=true;[...document.querySelectorAll('.preference-form button')].find(button=>button.innerText==='立即保存当前布局').click()");
    await waitFor(()=>cdp.evaluate("document.querySelector('.preference-form [role=status]').innerText.includes('synthetic layout save failure')"),'layout save failure');
    await cdp.evaluate("window.shellFixture.control.failLayout=false");
    // Application changes refresh registry without resetting the workspace.
    await cdp.evaluate("[...document.querySelectorAll('.settings-tabs button')].find(button=>button.innerText==='应用').click()");
    await waitFor(()=>cdp.evaluate("Boolean(document.querySelector('.registry-panel form'))"),'registry settings');
    await cdp.evaluate(`(() => {
      const form=document.querySelector('.registry-panel form');
      const inputs=[...form.querySelectorAll('input')];
      for(const [index,value] of [[0,'example'],[1,'Synthetic Editor'],[3,'https://example.invalid']]) {
        Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(inputs[index],value);
        inputs[index].dispatchEvent(new Event('input',{bubbles:true}));
      }
    })()`);
    await cdp.evaluate("document.querySelector('.registry-panel form').dispatchEvent(new Event('submit',{bubbles:true,cancelable:true}))");
    await waitFor(()=>cdp.evaluate("document.querySelector('.registry-panel [role=status]').innerText.includes('应用目录已更新')"),'registry callback success');
    assert.equal(await cdp.evaluate("Boolean(document.querySelector('.window[data-app-id=settings]'))"),true,'registry refresh retains settings window');
    await cdp.evaluate("[...document.querySelectorAll('.settings-tabs button')].find(button=>button.innerText==='外观与桌面').click()");

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
    // Only the registered origin and this exact frame may report application status.
    const phase = () => cdp.evaluate("document.querySelector('.iframe-toolbar').innerText");
    await cdp.evaluate(`(() => {const frame=document.querySelector('.iframe-app iframe');window.dispatchEvent(new MessageEvent('message',{source:frame.contentWindow,origin:'https://untrusted.invalid',data:{type:'app.ready'}}))})()`);
    assert.ok((await phase()).includes('连接失败'), 'wrong-origin ready message ignored');
    await cdp.evaluate(`window.dispatchEvent(new MessageEvent('message',{source:window,origin:'http://127.0.0.1:${stalledPort}',data:{type:'app.ready'}}))`);
    assert.ok((await phase()).includes('连接失败'), 'wrong-frame ready message ignored');
    await cdp.evaluate(`(() => {const frame=document.querySelector('.iframe-app iframe');window.dispatchEvent(new MessageEvent('message',{source:frame.contentWindow,origin:'http://127.0.0.1:${stalledPort}',data:{type:'app.status',status:'auth-error'}}))})()`);
    await waitFor(() => cdp.evaluate("document.querySelector('.iframe-app-status')?.innerText.includes('需要重新授权')"), 'trusted iframe auth failure');
    await cdp.evaluate(`(() => {const frame=document.querySelector('.iframe-app iframe');window.dispatchEvent(new MessageEvent('message',{source:frame.contentWindow,origin:'http://127.0.0.1:${stalledPort}',data:{type:'app.status',status:'network-error'}}))})()`);
    await waitFor(() => cdp.evaluate("document.querySelector('.iframe-app-status')?.innerText.includes('网络连接失败')"), 'trusted iframe network failure');
    await cdp.evaluate(`(() => {const frame=document.querySelector('.iframe-app iframe');window.dispatchEvent(new MessageEvent('message',{source:frame.contentWindow,origin:'http://127.0.0.1:${stalledPort}',data:{type:'app.ready'}}))})()`);
    await waitFor(() => cdp.evaluate("document.querySelector('.iframe-toolbar')?.innerText.includes('应用已连接')"), 'trusted iframe ready');
    assert.equal(await cdp.evaluate("document.querySelector('.iframe-app iframe').getAttribute('sandbox')"), 'allow-same-origin allow-scripts allow-forms', 'sandbox restrictions retained');
    await cdp.evaluate("document.querySelector('.window[data-app-id=chat] .bar .max').click()");
    const chatMax = await rectOf(cdp, '.window[data-app-id=chat]');
    assert.deepEqual(chatMax, {x:0,y:30,w:1440,h:870}, 'OpenWebUI iframe window fills the desktop');
    await cdp.evaluate("document.querySelector('.window[data-app-id=chat] .bar .max').click()");
    const frameId=await cdp.evaluate("document.querySelector('.iframe-app iframe').id");
    await cdp.evaluate("document.querySelector('.window[data-app-id=chat] .bar .min').click()");
    assert.equal(await cdp.evaluate("Boolean(document.getElementById('"+frameId+"'))"), true, 'minimize retains connected iframe');
    await cdp.evaluate("document.querySelector('nav.dock button[title=\"AI 对话\"]').click()");


    if (process.env.UI_SCREENSHOT_DIR) {
      await fs.mkdir(process.env.UI_SCREENSHOT_DIR,{recursive:true});
      const snapshot=async name => {const shot=await cdp.call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});await fs.writeFile(path.join(process.env.UI_SCREENSHOT_DIR,name+'.png'),Buffer.from(shot.data,'base64'));};
      await cdp.evaluate("document.querySelector('nav.dock button[title=\"开发\"]').click()");
      await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.development-app'))"), 'Development dashboard');
      await snapshot('development-dark-1440x900');
      await cdp.evaluate("document.querySelector('nav.dock button[title=\"系统设置\"]').click()");
      await snapshot('settings-dark-1440x900');
      await cdp.evaluate("document.querySelector('nav.dock button[title=\"知识库\"]').click()");
      await snapshot('knowledge-query-dark-1440x900');
      await cdp.evaluate("document.querySelector('nav.dock button[title=\"AI 对话\"]').click()");
    }

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
      hiddenControls: [...document.querySelectorAll('.bar .traffic button')].filter(button=>button.closest('[aria-hidden=\"true\"]')).length,
      unlabeled: [...document.querySelectorAll('.ds-icon-button')].filter(button=>!button.getAttribute('aria-label')).length,
      dockTitles: [...document.querySelectorAll('nav.dock .dock-item')].every(button=>button.title),
      switches: [...document.querySelectorAll('button[role=switch]')].every(button=>button.getAttribute('aria-checked')!==null),
    }))()`);
    assert.equal(a11y.hiddenControls,0,'window controls exposed to assistive technology');
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
      ['820x1180-tablet', 820, 1180],
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
    // Phone switcher restores minimized windows without relying on the horizontally scrolling Dock.
    await cdp.evaluate("document.querySelector('.window[data-app-id=issues] .bar .min').click()");
    await click('.menubar-mobile-switcher button');
    await waitFor(() => cdp.evaluate("document.querySelector('.menubar-mobile-switcher .ds-menu')?.innerText.includes('任务（最小化）')"), 'mobile minimized window menu');
    await cdp.evaluate("[...document.querySelectorAll('.menubar-mobile-switcher .ds-menu button')].find(button=>button.innerText.includes('任务')).click()");
    await waitFor(() => cdp.evaluate("document.querySelector('.window[data-window-active=true]')?.dataset.appId==='issues'"), 'mobile window restored');
    assert.equal(await cdp.evaluate("[...document.querySelectorAll('.window')].filter(win=>getComputedStyle(win).visibility==='visible').length"), 1, 'phone shows one active app');
    await cdp.evaluate("document.querySelector('nav.dock button[title=\"系统设置\"]').click()");
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
    // Restore-disabled startup and Dock-only autosave preserve stored windows.
    const preserved={id:'preserved',app_id:'knowledge',title:'Knowledge',x:80,y:70,width:900,height:600,minimized:false};
    await cdp.evaluate(`sessionStorage.setItem('shell-fixture:preferences',JSON.stringify({...window.shellFixture.preferences,restore_windows:false}));sessionStorage.setItem('shell-fixture:layout',JSON.stringify({dock:['settings'],windows:[${JSON.stringify(preserved)}],dock_initialized:true}))`);
    await cdp.call('Page.reload');
    await waitFor(()=>cdp.evaluate("Boolean(document.querySelector('nav.dock'))"),'restore-disabled desktop');
    assert.equal(await cdp.evaluate("document.querySelectorAll('.window').length"),0,'no windows restored');
    await waitFor(()=>cdp.evaluate("window.shellFixture.calls.some(call=>call.method==='PUT')"),'Dock-only startup save');
    assert.equal(await cdp.evaluate("window.shellFixture.layout.windows[0]?.id"),'preserved','saved window layout remains intact');

    // An intentionally unpinned Dock stays empty after reloading.
    await cdp.evaluate("sessionStorage.setItem('shell-fixture:layout',JSON.stringify({dock:[],windows:[],dock_initialized:true}))");
    await cdp.call('Page.reload');
    await waitFor(()=>cdp.evaluate("document.querySelectorAll('nav.dock .dock-item').length===1"),'explicitly empty Dock restored');

    // A retired registry entry must not surface in the real shell. Existing server
    // layouts migrate that entry to the native app while preserving unrelated state.
    for (const scenario of ['legacy-xelibrary', 'legacy-xelibrary-duplicate']) {
      await cdp.call('Page.navigate', {url: `http://127.0.0.1:${portNumber}/tests/shell-fixture.html?scenario=${scenario}`});
      await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.window[data-app-id=knowledge]'))"), `${scenario} restored as native Knowledge`);
      await waitFor(() => cdp.evaluate("window.shellFixture.layout.dock.includes('knowledge') && !window.shellFixture.layout.dock.includes('xelibrary') && !window.shellFixture.layout.windows.some(win=>win.app_id==='xelibrary')"), `${scenario} layout migration persisted`);
      assert.equal(await cdp.evaluate("window.shellFixture.registry.some(app=>app.id==='xelibrary')"), true, 'fixture confirms the legacy registry entry was supplied');
      assert.equal(await cdp.evaluate("document.querySelectorAll('.window[data-app-id=xelibrary]').length"), 0, 'no legacy iframe window is rendered');
      assert.equal(await cdp.evaluate("document.querySelectorAll('.window[data-app-id=knowledge]').length"), 1, 'one native knowledge window is restored');
      assert.equal(await cdp.evaluate("Boolean(document.querySelector('nav.dock button[title=\"知识库\"]'))"), true, 'native Knowledge appears on the Dock');
      assert.equal(await cdp.evaluate("Boolean(document.querySelector('nav.dock button[title=\"AI 对话\"]'))"), true, 'unrelated chat window remains reachable from the Dock');
      assert.equal(await cdp.evaluate("document.documentElement.dataset.theme==='dark' && document.documentElement.dataset.wallpaper==='aurora' && Boolean(document.querySelector('.widget-clock')) && Boolean(document.querySelector('.widget-status'))"), true, 'unrelated theme, wallpaper, and widgets remain applied');
      const windows = await cdp.evaluate('window.shellFixture.layout.windows');
      const knowledge = windows.find(win => win.app_id === 'knowledge');
      const chat = windows.find(win => win.app_id === 'chat');
      assert.ok(knowledge, 'persisted layout uses the native knowledge app id');
      assert.deepEqual([knowledge.x, knowledge.y, knowledge.width, knowledge.height], [scenario.endsWith('duplicate') ? 120 : 70, scenario.endsWith('duplicate') ? 90 : 60, scenario.endsWith('duplicate') ? 1000 : 900, scenario.endsWith('duplicate') ? 650 : 600], 'the retained Knowledge window keeps its geometry');
      assert.deepEqual([chat.title, chat.x, chat.y, chat.width, chat.height, chat.minimized], ['AI 对话自定义标题', 160, 120, 850, 560, true], 'unrelated window personalization survives');

      await cdp.evaluate("document.querySelector('button[title=\"LaunchPad\"]').click()");
      await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.launchpad'))"), `${scenario} LaunchPad`);
      const launchpadTitles = await cdp.evaluate("[...document.querySelectorAll('.launchpad-app strong')].map(item=>item.textContent)");
      assert.equal(launchpadTitles.includes('XeLibrary legacy iframe'), false, 'LaunchPad hides the legacy external app');
      assert.equal(launchpadTitles.filter(title => title === '知识库').length, 1, 'LaunchPad exposes one native Knowledge app');
      await cdp.key('Escape', 'Escape');
    }

    // Exercise the real App/auth/session boundary with a synthetic server session.
    // Every refresh rotates its bearer token, reproducing production parent renders.
    await cdp.call('Page.addScriptToEvaluateOnNewDocument', {source: `(() => {
      const realFetch=window.fetch.bind(window);
      const state=window.sessionFixture={mode:'good',renewals:0,logoutCalls:0,userReads:0};
      const preferences={theme:'dark',accent:'blue',wallpaper:'silver',timezone:'UTC',widgets:[],restore_windows:true,dock_autohide:false,dock_size:'medium',animations:true,reduced_motion:false};
      window.fetch=async (input,init={}) => {
        const url=new URL(String(input),location.origin);
        const path=url.pathname;
        const response=(data,status=200) => new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
        if(path==='/api/v1/auth/session/token') {
          state.renewals++;
          if(state.mode==='temporary')return response({error:{code:'PROVIDER_UNAVAILABLE',message:'Synthetic identity provider unavailable'}},503);
          if(state.mode==='revoked')return response({error:{code:'SESSION_REVOKED',message:'Synthetic session revoked'}},401);
          return response({token:'synthetic-token-'+state.renewals,expires:9999999999});
        }
        if(path==='/api/v1/auth/session/logout'){state.logoutCalls++;return response({});}
        if(path==='/health/ready')return response({service:'desktop-web'});
        if(!path.startsWith('/api/v1/'))return realFetch(input,init);
        let data=[],meta={};
        if(path==='/api/v1/users/me'){state.userReads++;data={subject:'synthetic-session-user',kind:'human',scopes:['desktop:read','xelibrary:read','dev:read']};}
        else if(path==='/api/v1/capabilities')data=${JSON.stringify(JSON.parse(await fs.readFile(path.join(appRoot,'../../infra/plugins/builtin-capabilities.json'),'utf8')).capabilities.map(item=>({...item,source:'builtin',enabled:true})))};
        else if(path==='/api/v1/users/me/preferences'){if(init.method==='PATCH')Object.assign(preferences,JSON.parse(init.body));data={...preferences};}
        else if(path==='/api/v1/users/me/layout')data={windows:[],dock:[],dock_initialized:false};
        else if(path==='/api/v1/jobs')meta={counts:{queued:0,running:0,completed:0,failed:0}};
        return response({data,meta});
      };
    })()`});
    await cdp.call('Page.navigate', {url: `http://127.0.0.1:${portNumber}/index.html`});
    await waitFor(() => cdp.evaluate('Boolean(window.sessionFixture)'), 'synthetic session interception installed');
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('nav.dock button[title=\"系统设置\"]'))"), 'real App restores synthetic session');
    await cdp.evaluate("document.querySelector('nav.dock button[title=\"系统设置\"]').click()");
    await waitFor(() => cdp.evaluate("Boolean(document.querySelector('.preference-form input[list=timezones]'))"), 'real settings window');
    await cdp.evaluate(`(() => {
      const input=document.querySelector('.preference-form input[list=timezones]');
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'unsaved-session-draft');
      input.dispatchEvent(new Event('input',{bubbles:true}));
      window.sessionFixture.node=document.querySelector('.window[data-app-id=settings]');
      window.sessionFixture.userReadsBefore=window.sessionFixture.userReads;
    })()`);
    const renewalsBefore=await cdp.evaluate('window.sessionFixture.renewals');
    await cdp.evaluate("document.dispatchEvent(new Event('visibilitychange'))");
    await sleep(100);
    assert.equal(await cdp.evaluate('window.sessionFixture.renewals'), renewalsBefore, 'a fresh cached token is not rotated on visibility');
    await sleep(200);
    assert.equal(await cdp.evaluate("document.querySelector('.window[data-app-id=settings]')===window.sessionFixture.node && document.querySelector('input[list=timezones]').value==='unsaved-session-draft'"), true, 'visibility preserves the window node and local draft');
    assert.equal(await cdp.evaluate('window.sessionFixture.userReads'), await cdp.evaluate('window.sessionFixture.userReadsBefore'), 'visibility does not reload the desktop');
    assert.equal(await cdp.evaluate('window.sessionFixture.logoutCalls'), 0, 'visibility does not log out a valid cached session');

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
