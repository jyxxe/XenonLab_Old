import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';
import {fileURLToPath} from 'node:url';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

test('query drafts are subject isolated, recover from storage errors, and never execute on load', async t => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  if (!existsSync(executablePath)) return t.skip(`Chromium missing: ${executablePath}`);
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: 0, strictPort: false, watch: null}});
  await server.listen();
  const address = server.httpServer.address();
  const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'xeonolab-query-drafts-'));
  let browser;
  try {
    browser = await chromium.launch({headless: true, executablePath, args: ['--no-first-run', '--no-default-browser-check']});
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
