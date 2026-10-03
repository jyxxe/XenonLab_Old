import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {spawn, spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = path.resolve(appRoot, '../..');
const outputDir = path.join(repoRoot, 'runtime/verification/issue-215');
const python = path.join(repoRoot, '.venv/bin/python');
const copyButton = page => page.getByRole('button', {name: /复制当前结果页为 TSV/});
const copiedStatus = page => page.getByRole('status').filter({hasText: '已复制当前结果页'});
const copyAlert = page => page.getByRole('alert').filter({hasText: '复制失败'});
const waitCopyEnabled = page => page.waitForFunction(() => [...document.querySelectorAll('.library-query-toolbar button')].some(button => button.textContent.includes('复制当前结果页为 TSV') && !button.disabled));

// Independent standard-library parser: avoid testing an encoder with its own decoder.
function parseTSV(text) {
  const result = spawnSync(python, ['-c', 'import csv,json,sys,io; print(json.dumps(list(csv.reader(io.StringIO(sys.stdin.read(), newline=""), delimiter="\\t"))))'], {input: text, encoding: 'utf8'});
  assert.equal(result.status, 0, result.stderr);
  return JSON.parse(result.stdout);
}

async function startAPI() {
  const child = spawn(python, [path.join(appRoot, 'tests/query_copy_api.py')], {cwd: repoRoot, stdio: ['ignore', 'pipe', 'pipe']});
  let stderr = '';
  child.stderr.on('data', chunk => { stderr += chunk; });
  try {
    const port = await new Promise((resolve, reject) => {
      let stdout = '';
      const timeout = setTimeout(() => reject(new Error('Synthetic API startup timed out: ' + stderr)), 15000);
      child.once('exit', code => { clearTimeout(timeout); reject(new Error(`Synthetic API exited ${code}: ${stderr}`)); });
      child.stdout.on('data', chunk => {
        stdout += chunk;
        if (stdout.includes('\n')) {
          clearTimeout(timeout);
          try { resolve(JSON.parse(stdout.split('\n')[0]).port); } catch (error) { reject(error); }
        }
      });
    });
    const base = `http://127.0.0.1:${port}`;
    for (let attempt = 0; ; attempt += 1) {
      try { const response = await fetch(base + '/health/live'); if (response.ok) break; } catch {}
      if (attempt >= 50) throw new Error('Synthetic API did not become ready: ' + stderr);
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    return {base, stop: async () => {
      if (child.exitCode !== null || child.signalCode !== null) return;
      child.kill('SIGTERM');
      await new Promise(resolve => child.once('exit', resolve));
    }};
  } catch (error) { child.kill('SIGTERM'); throw error; }
}

async function withBrowser(t, realAPI, run) {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  if (!existsSync(executablePath)) return t.skip(`Chromium missing: ${executablePath}`);
  if (realAPI && !existsSync(python)) return t.skip('Real API companion requires uv run pytest first; standalone clipboard regressions still run.');
  await fs.mkdir(outputDir, {recursive: true});
  const backend = realAPI ? await startAPI() : null;
  let server;
  let browser;
  try {
    server = await createServer({root: appRoot, cacheDir: path.join(outputDir, 'vite-cache', realAPI ? 'api' : 'direct'), server: {host: '127.0.0.1', port: 0, watch: null, proxy: backend ? {'/api/v1': backend.base} : undefined}});
    await server.listen();
    browser = await chromium.launch({headless: true, executablePath, args: ['--no-first-run', '--no-default-browser-check', '--no-proxy-server']});
    const context = await browser.newContext({viewport: {width: 1180, height: 900}, permissions: ['clipboard-read', 'clipboard-write']});
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(String(error)));
    await page.goto(`http://127.0.0.1:${server.httpServer.address().port}/tests/query-copy-fixture.html${realAPI ? '' : '?mode=direct'}`);
    await run({page, backend});
    assert.deepEqual(errors, [], 'no browser page errors');
  } finally {
    await browser?.close();
    await server?.close();
    await backend?.stop();
  }
}

test('real temporary XeLibrary query copies only its current page to the native clipboard', async t => {
  await withBrowser(t, true, async ({page, backend}) => {
    const fixture = await (await fetch(backend.base + '/__fixture')).json();
    assert.equal(fixture.synthetic, true);
    await page.locator('.library-nav button').filter({hasText: '查询控制台'}).click();
    const editor = page.getByRole('textbox', {name: '查询表达式'});
    await editor.fill(fixture.expression);
    await page.getByRole('button', {name: '运行', exact: true}).click();
    await copyButton(page).waitFor();
    await page.waitForFunction(() => document.querySelectorAll('.library-query-results tbody tr').length === 3);
    const before = await page.evaluate(() => structuredClone(window.queryCopyFixture.calls));
    const backendBefore = (await (await fetch(backend.base + '/__fixture')).json()).calls;
    assert.equal(before.filter(call => call.path === '/query').length, 1);
    await copyButton(page).click();
    await copiedStatus(page).filter({hasText: '3 行'}).waitFor();
    const text = await page.evaluate(() => navigator.clipboard.readText());
    const parsed = parseTSV(text);
    assert.deepEqual(parsed[0], ['表', '路径', 'Row ID', 'Revision', '值']);
    assert.equal(parsed.length, 4);
    assert.equal(new Set(fixture.rows.map(row => row.table_id)).size, 2);
    for (let index = 0; index < 3; index += 1) {
      const row = fixture.rows[index];
      assert.equal(parsed[index + 1].length, 5);
      assert.deepEqual(parsed[index + 1].slice(0, 4), [fixture.names[row.table_id], row.path, row._row_id, String(row._revision)]);
      assert.deepEqual(JSON.parse(parsed[index + 1][4]), row.values);
      assert.ok(parsed[index + 1][4].length > 160, 'full JSON exceeds the truncated UI display');
      assert.equal(row.values.status, 'ready', 'draft is excluded by the actual Query API');
    }
    assert.deepEqual(parsed.slice(1).map(row => JSON.parse(row[4]).amount), [30, 20, 10]);
    assert.equal(parsed[1][0], '合成表 A\t"引号"\r\n第二行', 'quoted metadata delimiters roundtrip as one cell');
    assert.equal(await editor.inputValue(), fixture.expression);
    assert.deepEqual(await page.evaluate(() => window.queryCopyFixture.calls), before, 'copy triggers no API, row, export or pagination call');
    assert.deepEqual((await (await fetch(backend.base + '/__fixture')).json()).calls, backendBefore, 'server saw no new API requests');
    await fs.writeFile(path.join(outputDir, 'native-clipboard.tsv'), text);
    await fs.writeFile(path.join(outputDir, 'native-api-evidence.json'), JSON.stringify({synthetic: true, rows: fixture.rows, names: fixture.names, calls: before, parsed}, null, 2));
    await page.screenshot({path: path.join(outputDir, 'desktop-copy.png'), fullPage: true});
    await page.setViewportSize({width: 390, height: 844});
    await copiedStatus(page).scrollIntoViewIfNeeded();
    await page.screenshot({path: path.join(outputDir, 'mobile-copy.png'), fullPage: true});
    const bounds = await copyButton(page).boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= 390, 'copy button fits the mobile viewport');

    // The existing expression action keeps its DSL-only contract.
    await page.getByRole('button', {name: '复制查询', exact: true}).click();
    assert.equal(await page.evaluate(() => navigator.clipboard.readText()), fixture.expression);
    assert.deepEqual(await page.evaluate(() => window.queryCopyFixture.calls), before);

    await editor.fill(fixture.expression.replace('LIMIT 10', 'LIMIT 2'));
    await page.getByRole('button', {name: '运行', exact: true}).click();
    await page.waitForFunction(() => document.querySelectorAll('.library-query-results tbody tr').length === 2);
    await page.getByRole('button', {name: '下一页', exact: true}).click();
    await page.waitForFunction(() => document.querySelectorAll('.library-query-results tbody tr').length === 1);
    const pageCalls = await page.evaluate(() => structuredClone(window.queryCopyFixture.calls));
    const pageExpression = await editor.inputValue();
    const offset = await page.locator('.library-query-pagination').innerText();
    const queryBody = JSON.parse(pageCalls.filter(call => call.path === '/query').at(-1).body);
    assert.match(queryBody.expression, /LIMIT 2 OFFSET 2$/);
    await copyButton(page).click();
    await copiedStatus(page).filter({hasText: '1 行'}).waitFor();
    const finalPage = parseTSV(await page.evaluate(() => navigator.clipboard.readText()));
    assert.equal(finalPage.length, 2, 'only the last loaded page is copied');
    assert.equal(finalPage[1][2], fixture.rows[2]._row_id);
    assert.equal(JSON.parse(finalPage[1][4]).amount, 10);
    assert.deepEqual(await page.evaluate(() => window.queryCopyFixture.calls), pageCalls);
    assert.equal(await editor.inputValue(), pageExpression);
    assert.equal(await page.locator('.library-query-pagination').innerText(), offset);
  });
});

async function installClipboard(page, mode) {
  await page.evaluate(initialMode => {
    window.copyWrites = [];
    window.copyPending = [];
    window.copyMode = initialMode;
    Object.defineProperty(navigator, 'clipboard', {configurable: true, value: {writeText(text) {
      window.copyWrites.push(text);
      if (window.copyMode === 'reject') return Promise.reject(new DOMException('synthetic denial', 'NotAllowedError'));
      if (window.copyMode === 'pending') return new Promise((resolve, reject) => window.copyPending.push({resolve, reject}));
      return Promise.resolve();
    }}});
  }, mode);
}

test('clipboard denial and missing API preserve the result page for an in-place retry', async t => {
  await withBrowser(t, false, async ({page}) => {
    await copyButton(page).waitFor();
    await copyButton(page).click();
    await copiedStatus(page).waitFor();
    const nativeText = await page.evaluate(() => navigator.clipboard.readText());
    assert.match(nativeText, /unknown-table-complete-123456789/);
    assert.match(nativeText, /complete-first-page-row-0/);
    await installClipboard(page, 'reject');
    await copyButton(page).click();
    await copyAlert(page).waitFor();
    assert.equal(await page.locator('.library-query-results tbody tr').count(), 3);
    assert.equal(await copyButton(page).isEnabled(), true);
    await page.evaluate(() => { window.copyMode = 'resolve'; });
    await copyButton(page).click();
    await copiedStatus(page).filter({hasText: '3 行'}).waitFor();
    const copiedText = await page.evaluate(() => window.copyWrites.at(-1));
    assert.match(copiedText, /unknown-table-complete-123456789/, 'unknown table fallback uses the full source ID');
    assert.match(copiedText, /complete-first-page-row-0/);
    await page.evaluate(() => Object.defineProperty(navigator, 'clipboard', {configurable: true, value: undefined}));
    await copyButton(page).click();
    await copyAlert(page).waitFor();
    assert.equal(await copiedStatus(page).count(), 0, 'missing clipboard is never reported as success');
    assert.equal(await page.locator('.library-query-results tbody tr').count(), 3);
    await page.setViewportSize({width: 390, height: 844});
    await page.screenshot({path: path.join(outputDir, 'mobile-copy-failure.png'), fullPage: true});
    await installClipboard(page, 'resolve');
    await copyButton(page).click();
    await copiedStatus(page).waitFor();
    assert.deepEqual(await page.evaluate(() => window.queryCopyFixture.calls), []);
  });
});

test('copy snapshots isolate delayed completion, double clicks, page changes, identity and unmount', async t => {
  await withBrowser(t, false, async ({page}) => {
    await copyButton(page).waitFor();
    await installClipboard(page, 'pending');
    await copyButton(page).evaluate(button => { button.click(); button.click(); });
    assert.equal(await page.evaluate(() => window.copyWrites.length), 1, 'two synchronous clicks write once');
    assert.equal(await copyButton(page).isDisabled(), true);
    await page.getByRole('button', {name: '下一页', exact: true}).click();
    assert.equal(await copyButton(page).isEnabled(), true, 'a pending old page does not block the new page');
    await copyButton(page).click();
    assert.equal(await page.evaluate(() => window.copyWrites.length), 2);
    await page.evaluate(() => window.copyPending[0].resolve());
    assert.equal(await copiedStatus(page).count(), 0, 'old success is not shown on the new page');
    assert.equal(await copyButton(page).isDisabled(), true, 'old finally cannot clear the new copy busy flag');
    await page.evaluate(() => window.copyPending[1].resolve());
    await copiedStatus(page).filter({hasText: '1 行'}).waitFor();

    for (const change of [{running: true}, {validating: true}, {error: 'synthetic query failure'}, {queryEnabled: false}]) {
      await page.evaluate(() => window.queryCopyFixture.reset());
      await copyButton(page).waitFor();
      await waitCopyEnabled(page);
      await installClipboard(page, 'pending');
      await copyButton(page).click();
      await page.evaluate(change => window.queryCopyFixture.set(change), change);
      await page.waitForFunction(change => Object.entries(change).every(([key, value]) => window.queryCopyFixture.state[key] === value), change);
      assert.equal(await copyButton(page).isDisabled(), true, 'copy is paused while its result is unavailable');
      await page.evaluate(() => window.queryCopyFixture.set({running: false, validating: false, error: null, queryEnabled: true}));
      await waitCopyEnabled(page);
      await page.evaluate(() => window.copyPending[0].reject(new Error('late synthetic rejection')));
      assert.equal(await copyAlert(page).count(), 0, 'old rejection is not shown after a state/session change');
      assert.equal(await copiedStatus(page).count(), 0);
      assert.equal(await copyButton(page).isEnabled(), true);
    }

    await installClipboard(page, 'pending');
    await copyButton(page).click();
    await page.getByRole('textbox', {name: '查询表达式'}).fill('FROM "/changed/**" LIMIT 1');
    await page.evaluate(() => window.copyPending[0].resolve());
    assert.equal(await copyButton(page).count(), 0, 'empty/cleared result has no copy action');
    assert.equal(await copiedStatus(page).count(), 0);
    await page.evaluate(() => window.queryCopyFixture.reset());
    await installClipboard(page, 'pending');
    await copyButton(page).click();
    await page.evaluate(() => window.queryCopyFixture.set({mounted: false}));
    await page.waitForFunction(() => !document.querySelector('.library-query'));
    await page.evaluate(() => window.copyPending[0].resolve());
    await page.evaluate(() => window.queryCopyFixture.set({mounted: true}));
    await copyButton(page).waitFor();
    assert.equal(await copiedStatus(page).count(), 0, 'unmounted copy cannot publish into a new console');
    assert.equal(await copyButton(page).isEnabled(), true);
    assert.deepEqual(await page.evaluate(() => window.queryCopyFixture.calls), []);
  });
});
