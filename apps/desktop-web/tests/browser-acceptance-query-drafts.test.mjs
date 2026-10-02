import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import {fileURLToPath} from 'node:url';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
async function freePort() {
  const listener = net.createServer();
  await new Promise((resolve, reject) => listener.listen(0, '127.0.0.1', resolve).once('error', reject));
  const value = listener.address().port;
  await new Promise(resolve => listener.close(resolve));
  return value;
}

test('query drafts are subject isolated, recover from storage errors, and never execute on load', async t => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  if (!existsSync(executablePath)) return t.skip(`Chromium missing: ${executablePath}`);
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: 0, strictPort: false, watch: null}});
  await server.listen();
  const address = server.httpServer.address();
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'xeonolab-query-drafts-'));
  let browser;
  try {
    browser = await chromium.launch({headless: true, executablePath, args: ['--no-first-run', '--no-default-browser-check', '--no-proxy-server', '--host-resolver-rules=MAP library.xenon.lan 127.0.0.1']});
    const context = await browser.newContext({viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true});
    const page = await context.newPage();
    await page.addInitScript(() => {
      const get = Storage.prototype.getItem;
      const set = Storage.prototype.setItem;
      window.__storageTouches = [];
      window.__nativeGetItem = get;
      window.__nativeSetItem = set;
      Storage.prototype.getItem = function(key) {
        window.__storageTouches.push(['get', key]);
        if (window.__throwQueryRead && key === window.__throwQueryRead) throw new DOMException('blocked', 'SecurityError');
        return get.call(this, key);
      };
      Storage.prototype.setItem = function(key, value) {
        window.__storageTouches.push(['set', key]);
        if (window.__throwQueryWrite && key === window.__throwQueryWrite) throw new DOMException('quota', 'QuotaExceededError');
        return set.call(this, key, value);
      };
    });
    const url = subject => `http://127.0.0.1:${address.port}/tests/library-fixture.html?subject=${encodeURIComponent(subject)}`;
    const openQuery = async () => {
      await page.locator('.library-nav button').filter({hasText: '查询控制台'}).click();
      await page.locator('.library-query-editor').waitFor();
    };
    const setExpression = async value => page.locator('.library-query-editor').evaluate((element, next) => {
      const textarea = element;
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
      setter.call(textarea, next);
      textarea.dispatchEvent(new Event('input', {bubbles: true}));
      textarea.dispatchEvent(new Event('change', {bubbles: true}));
    }, value);
    const keyFor = subject => `xeonolab.library.${encodeURIComponent(subject)}.query-drafts.v1`;

    await page.goto(url('alice'));
    await openQuery();
    const aliceKey = keyFor('alice');
    const first = 'FROM "/数学/**" LIMIT 77';
    await setExpression(first);
    await page.waitForFunction(key => Boolean(localStorage.getItem(key)), aliceKey);
    await page.getByRole('button', {name: '运行'}).click();
    await page.locator('.library-query-results tbody tr').waitFor();
    let saved = await page.evaluate(key => JSON.parse(localStorage.getItem(key)), aliceKey);
    assert.equal(saved.draft.expression, first);
    assert.equal(saved.recent[0].expression, first, 'only a validated, explicitly run query enters recent history');
    assert.deepEqual(Object.keys(saved), ['version', 'draft', 'recent']);
    assert.equal(JSON.stringify(saved).includes('_row_id'), false, 'query results are not persisted');
    assert.equal(JSON.stringify(saved).includes('access_token'), false, 'credentials are not persisted');

    const queryCalls = () => page.evaluate(() => window.libraryFixture.calls.filter(call => call.path === '/query').length);
    let latest = first;
    for (let index = 0; index < 21; index += 1) {
      latest = `FROM "/recent/${index}" LIMIT 1`;
      await setExpression(latest);
      const beforeRun = await queryCalls();
      await page.getByRole('button', {name: '运行'}).click();
      await page.waitForFunction(previous => window.libraryFixture.calls.filter(call => call.path === '/query').length > previous, beforeRun);
    }
    saved = await page.evaluate(key => JSON.parse(localStorage.getItem(key)), aliceKey);
    assert.equal(saved.recent.length, 20, 'the browser history retains only its newest 20 validated expressions');
    await setExpression(latest);
    let beforeRun = await queryCalls();
    await page.getByRole('button', {name: '运行'}).click();
    await page.waitForFunction(previous => window.libraryFixture.calls.filter(call => call.path === '/query').length > previous, beforeRun);
    saved = await page.evaluate(key => JSON.parse(localStorage.getItem(key)), aliceKey);
    assert.equal(saved.recent.length, 20);
    assert.equal(saved.recent.filter(item => item.expression === latest).length, 1, 're-running the same expression deduplicates history');

    const beforeLoad = await queryCalls();
    await setExpression('FROM "/**" LIMIT 11');
    await page.locator('.library-query-history summary').click();
    await page.getByRole('button', {name: '载入草稿'}).first().click();
    assert.equal(await page.locator('.library-query-editor').inputValue(), latest);
    assert.equal(await queryCalls(), beforeLoad, 'loading recent history restores text without executing it');

    await page.reload();
    await openQuery();
    assert.equal(await page.locator('.library-query-editor').inputValue(), latest, 'refresh restores the current subject draft');
    await page.locator('.library-query-history summary').click();
    await page.getByRole('button', {name: '删除这条最近查询'}).first().click();
    assert.equal((await page.evaluate(key => JSON.parse(localStorage.getItem(key)), aliceKey)).recent.length, 19, 'delete removes only the selected entry');
    await setExpression('FROM "/clear-me/**" LIMIT 1');
    beforeRun = await queryCalls();
    await page.getByRole('button', {name: '运行'}).click();
    await page.waitForFunction(previous => window.libraryFixture.calls.filter(call => call.path === '/query').length > previous, beforeRun);
    await page.getByRole('button', {name: '清空最近查询'}).click();
    assert.equal(await page.locator('.library-query-history summary').innerText(), '最近查询（0 / 20）');
    assert.equal((await page.evaluate(key => JSON.parse(localStorage.getItem(key)), aliceKey)).recent.length, 0, 'clear removes all history');

    await page.goto(url('bob'));
    await openQuery();
    assert.notEqual(await page.locator('.library-query-editor').inputValue(), first, 'Bob never sees Alice’s draft');
    const bobDraft = 'FROM "/计算机/**" LIMIT 31';
    await setExpression(bobDraft);
    const bobKey = keyFor('bob');
    await page.waitForFunction(key => Boolean(localStorage.getItem(key)), bobKey);
    assert.equal(await page.evaluate(key => JSON.parse(localStorage.getItem(key)).draft.expression, aliceKey), 'FROM "/clear-me/**" LIMIT 1');
    assert.equal(await page.evaluate(key => JSON.parse(localStorage.getItem(key)).draft.expression, bobKey), bobDraft);

    await page.goto(url('__none__'));
    await openQuery();
    await setExpression('FROM "/**" LIMIT 5');
    const anonymousTouches = await page.evaluate(() => window.__storageTouches.filter(([, key]) => key.includes('.anonymous.')));
    assert.deepEqual(anonymousTouches, [], 'unverified identity does not read or write shared anonymous preferences');

    await page.goto(url('charlie'));
    await page.addInitScript(key => { window.__throwQueryRead = key; }, keyFor('charlie'));
    await page.reload();
    await openQuery();
    await page.getByRole('alert').getByRole('button', {name: '重试本地存储'}).waitFor();
    await page.evaluate(() => { Storage.prototype.getItem = window.__nativeGetItem; });
    await page.getByRole('button', {name: '重试本地存储'}).click();
    await page.getByRole('status').filter({hasText: '重新读取'}).waitFor();

    const charlieKey = keyFor('charlie');
    await page.evaluate(key => { window.__throwQueryWrite = key; }, charlieKey);
    await setExpression('FROM "/重试/**" LIMIT 9');
    await page.getByRole('alert').getByRole('button', {name: '重试本地存储'}).waitFor();
    await page.evaluate(() => { Storage.prototype.setItem = window.__nativeSetItem; });
    await page.getByRole('button', {name: '重试本地存储'}).click();
    await page.getByRole('status').filter({hasText: '已保存'}).waitFor();
    assert.equal(await page.evaluate(key => JSON.parse(localStorage.getItem(key)).draft.expression, charlieKey), 'FROM "/重试/**" LIMIT 9');

    const maxLengthExpression = 'x'.repeat(8192);
    await setExpression(maxLengthExpression);
    assert.equal(await page.locator('.library-query-editor').evaluate(element => Array.from(element.value).length), 8192);
    await setExpression('x'.repeat(8193));
    await page.getByRole('status').filter({hasText: '最多 8192'}).waitFor();
    assert.equal(await page.locator('.library-query-editor').evaluate(element => Array.from(element.value).length), 8192);
    await setExpression('FROM "/**" LIMIT 2');
    const charlieQueriesBefore = await queryCalls();
    await page.getByRole('button', {name: '运行'}).click();
    await page.locator('.library-query-results tbody tr').waitFor();
    await page.waitForFunction(previous => window.libraryFixture.calls.filter(call => call.path === '/query').length > previous, charlieQueriesBefore);
    assert.ok((await queryCalls()) > charlieQueriesBefore, 'normal explicit queries still run after storage and length checks');
  } finally {
    await browser?.close();
    await server.close();
    await fs.rm(tempDir, {recursive: true, force: true});
  }
});

test('trusted same-page session change revalidates identity and discards an old in-flight query', async t => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  if (!existsSync(executablePath)) return t.skip(`Chromium missing: ${executablePath}`);
  const portNumber = await freePort();
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: portNumber, strictPort: true, allowedHosts: true, watch: null}});
  await server.listen();
  const address = server.httpServer.address();
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'xeonolab-session-switch-'));
  let browser;
  let releaseAliceQuery;
  let aliceQueryStarted;
  const aliceQueryStartedPromise = new Promise(resolve => { aliceQueryStarted = resolve; });
  let holdBobIdentity = false;
  let releaseBobIdentity;
  let bobIdentityStarted;
  const bobIdentityStartedPromise = new Promise(resolve => { bobIdentityStarted = resolve; });
  const requests = [];
  try {
    browser = await chromium.launch({headless: true, executablePath, args: ['--no-first-run', '--no-default-browser-check', '--no-proxy-server', '--host-resolver-rules=MAP library.xenon.lan 127.0.0.1']});
    const context = await browser.newContext({viewport: {width: 1100, height: 780}});
    const page = await context.newPage();
    const respond = (route, value) => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(value)});
    await page.route('**/api/v1/**', async route => {
      const request = route.request();
      const requestUrl = new URL(request.url());
      const path = requestUrl.pathname.replace('/api/v1', '');
      const token = (request.headers().authorization || '').replace(/^Bearer\s+/i, '');
      requests.push({path, token});
      if (path === '/auth/session/token') return respond(route, {token: 'alice-token', expires: 600});
      if (path === '/users/me') {
        if (token === 'bob-token-1' && holdBobIdentity) {
          bobIdentityStarted();
          await new Promise(resolve => { releaseBobIdentity = resolve; });
        }
        const subject = token === 'alice-token' ? 'alice' : token.startsWith('bob-token') ? 'bob' : null;
        return respond(route, {data: {subject, scopes: ['xelibrary:read', 'xelibrary:query']}, meta: {}});
      }
      if (path === '/tables') return respond(route, {data: [], meta: {next_cursor: null}});
      if (path === '/query/validate') return respond(route, {data: {path: '/**', limit: 10, offset: 0, order_by: null, descending: false, execution: 'bounded AST interpreter'}, meta: {}});
      if (path === '/query' && token === 'alice-token') {
        aliceQueryStarted();
        await new Promise(resolve => { releaseAliceQuery = resolve; });
        return respond(route, {data: [{table_id: 'old', path: '/', _row_id: 'ALICE-ONLY-RESULT', _revision: 1, values: {title: 'ALICE-ONLY-RESULT'}}], meta: {}});
      }
      if (path === '/query') return respond(route, {data: [{table_id: 'new', path: '/', _row_id: 'BOB-RESULT', _revision: 1, values: {title: 'BOB-RESULT'}}], meta: {}});
      return respond(route, {data: [], meta: {next_cursor: null}});
    });
    await page.goto(`http://library.xenon.lan:${portNumber}/`);
    await page.waitForFunction(() => window.location.hostname === 'library.xenon.lan');
    await page.locator('.library-nav').waitFor();
    await page.waitForTimeout(100);
    const aliceIdentityResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/users/me') && response.request().headers()['authorization'] === 'Bearer alice-token');
    await page.evaluate(() => window.dispatchEvent(new MessageEvent('message', {
      origin: 'https://xenon.jyxxe.com', source: window,
      data: {type: 'auth.session_changed', access_token: 'alice-token'},
    })));
    await aliceIdentityResponse;
    const identityRequestsBeforeSpoof = requests.filter(request => request.path === '/users/me').length;
    await page.evaluate(() => {
      window.dispatchEvent(new MessageEvent('message', {origin: 'https://evil.example', source: window, data: {type: 'auth.session_changed', access_token: 'mallory-token'}}));
      window.dispatchEvent(new MessageEvent('message', {origin: 'https://xenon.jyxxe.com', source: null, data: {type: 'auth.session_changed', access_token: 'mallory-token'}}));
    });
    await page.waitForTimeout(50);
    assert.equal(requests.filter(request => request.path === '/users/me').length, identityRequestsBeforeSpoof, 'untrusted origin or source cannot trigger identity change');
    await page.locator('.library-nav button').filter({hasText: '查询控制台'}).click();
    const editor = page.locator('.library-query-editor');
    await editor.waitFor();
    await page.waitForFunction(() => {
      const run = [...document.querySelectorAll('.library-query-toolbar button')].find(button => button.textContent.trim() === '运行');
      return run && !run.disabled;
    });
    assert.ok(requests.some(request => request.path === '/users/me' && request.token === 'alice-token'), 'initial identity is resolved by the API');
    await editor.evaluate(element => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
      setter.call(element, 'FROM "/alice-delayed/**" LIMIT 1');
      element.dispatchEvent(new Event('input', {bubbles: true}));
      element.dispatchEvent(new Event('change', {bubbles: true}));
    });
    await page.getByRole('button', {name: '运行'}).click();
    await aliceQueryStartedPromise;

    holdBobIdentity = true;
    const bobIdentityResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/users/me') && response.request().headers()['authorization'] === 'Bearer bob-token-1');
    await page.evaluate(() => window.dispatchEvent(new MessageEvent('message', {
      origin: 'https://xenon.jyxxe.com',
      source: window,
      data: {type: 'auth.session_changed', access_token: 'bob-token-1'},
    })));
    await bobIdentityStartedPromise;
    await page.waitForFunction(() => document.querySelector('.library-query-history')?.textContent.includes('身份正在验证'));
    assert.equal(await editor.inputValue(), 'FROM "/**" LIMIT 100', 'unverified transition hides the previous account draft immediately');
    assert.equal(await page.getByRole('button', {name: '运行'}).isDisabled(), true, 'queries stay disabled while the new identity is unverified');
    releaseBobIdentity();
    await bobIdentityResponse;
    holdBobIdentity = false;
    await page.waitForFunction(() => !document.querySelector('.library-query-history')?.textContent.includes('身份正在验证'));
    assert.ok(requests.some(request => request.path === '/users/me' && request.token === 'bob-token-1'), 'the new token is verified through the protected identity API');
    releaseAliceQuery();
    await page.waitForTimeout(100);
    assert.equal(await page.locator('.library-query-results').count(), 0);
    assert.equal((await page.locator('.library-query').innerText()).includes('ALICE-ONLY-RESULT'), false, 'late Alice response cannot leak into Bob’s session');

    const bobExpression = 'FROM "/bob/**" LIMIT 1';
    await editor.evaluate((element, value) => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
      setter.call(element, value);
      element.dispatchEvent(new Event('input', {bubbles: true}));
      element.dispatchEvent(new Event('change', {bubbles: true}));
    }, bobExpression);
    await page.waitForFunction(expression => {
      const value = JSON.parse(localStorage.getItem('xeonolab.library.bob.query-drafts.v1') || 'null');
      return value?.draft?.expression === expression;
    }, bobExpression);
    const aliceStored = await page.evaluate(() => JSON.parse(localStorage.getItem('xeonolab.library.alice.query-drafts.v1')));
    assert.equal(aliceStored.draft.expression, 'FROM "/alice-delayed/**" LIMIT 1');
    const bobQueriesBefore = requests.filter(request => request.path === '/query' && request.token === 'bob-token-1').length;
    const bobQueryResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/query') && response.request().headers()['authorization'] === 'Bearer bob-token-1');
    await page.getByRole('button', {name: '运行'}).click();
    await bobQueryResponse;
    await page.waitForFunction(() => document.querySelector('.library-query-results')?.textContent.includes('BOB-RESULT'));
    assert.ok(requests.filter(request => request.path === '/query' && request.token === 'bob-token-1').length > bobQueriesBefore);

    const bobRefreshIdentityResponse = page.waitForResponse(response => response.url().endsWith('/api/v1/users/me') && response.request().headers()['authorization'] === 'Bearer bob-token-2');
    await page.evaluate(() => window.dispatchEvent(new MessageEvent('message', {
      origin: 'https://xenon.jyxxe.com', source: window,
      data: {type: 'auth.session_changed', access_token: 'bob-token-2'},
    })));
    await bobRefreshIdentityResponse;
    await page.waitForFunction(() => !document.querySelector('.library-query-history')?.textContent.includes('身份正在验证'));
    assert.equal(await editor.inputValue(), bobExpression, 'same-user session refresh restores that subject’s draft');
    assert.equal(await page.locator('.library-query-results').count(), 0, 'session change clears old result rows even for the same subject');
  } finally {
    releaseAliceQuery?.();
    releaseBobIdentity?.();
    await browser?.close();
    await server.close();
    await fs.rm(tempDir, {recursive: true, force: true});
  }
});
