import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

test('搜索结果条数上限：25/50/100 选择、请求体 limit、最多/返回计数与条件保留', async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: 0, watch: null}});
  await server.listen();
  const browser = await chromium.launch({executablePath, headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  const base = server.resolvedUrls.local[0];
  const searchNav = page.locator('.library-nav button', {hasText: '搜索'}).first();
  const searchInput = page.locator('input[aria-label="搜索 XeLibrary"]');
  const searchButton = page.locator('.library-search-bar button', {hasText: '搜索'});
  const limitSelect = page.getByLabel('结果条数上限');
  const resultCount = page.locator('.library-result-count');
  const semanticCalls = () => page.evaluate(() => window.libraryFixture.calls.filter(call => call.path === '/search/semantic'));

  const runSearch = async () => {
    await searchButton.click();
    await page.locator('.library-results li').first().waitFor();
  };

  try {
    await page.goto(`${base}tests/library-fixture.html?searchPool=60`);
    await searchNav.waitFor();
    await page.evaluate(() => window.libraryFixture.resetCalls());
    await searchNav.click();
    await searchInput.fill('一致收敛');

    // Default stays 25: 25 of the 60 fixture rows render and the summary names both numbers.
    await runSearch();
    let calls = await semanticCalls();
    assert.equal(calls.at(-1).body.limit, 25, 'the default request still sends limit 25');
    assert.equal(await page.locator('.library-results li').count(), 25, 'default renders at most 25 rows');
    assert.match(await resultCount.innerText(), /最多 25 条 · 返回 25 条/);

    // Selecting 50 reruns with the same query and the raised cap.
    await limitSelect.selectOption('50');
    await runSearch();
    calls = await semanticCalls();
    assert.equal(calls.at(-1).body.limit, 50, 'the selected limit reaches the request body');
    assert.equal(calls.at(-1).body.query, '一致收敛', 'query text survives the limit change');
    assert.equal(await page.locator('.library-results li').count(), 50, '50 rows render');
    assert.match(await resultCount.innerText(), /最多 50 条 · 返回 50 条/);

    // Changing the choice without rerunning keeps the summary honest about the
    // results currently on screen (they answered the previous request).
    await limitSelect.selectOption('100');
    assert.match(await resultCount.innerText(), /最多 50 条 · 返回 50 条/, 'no rerun means the old cap is still displayed');

    await runSearch();
    calls = await semanticCalls();
    assert.equal(calls.at(-1).body.limit, 100, 'limit 100 reaches the request body');
    assert.equal(await page.locator('.library-results li').count(), 60, 'all 60 fixture rows render under the 100 cap');
    assert.match(await resultCount.innerText(), /最多 100 条 · 返回 60 条/);
    assert.doesNotMatch(await page.locator('.library-result-count-main').innerText(), /共|总/, 'the summary never implies a corpus-wide total');

    // Path and field filters survive the limit change and rerun.
    await page.locator('.library-search-filters .library-link').click();
    await page.locator('textarea[aria-label="字段过滤"]').fill('type = theorem');
    await page.locator('input[aria-label="搜索路径模式"]').fill('/数学/**');
    await limitSelect.selectOption('25');
    await runSearch();
    calls = await semanticCalls();
    assert.equal(calls.at(-1).body.limit, 25, 'the lowered limit is sent');
    assert.equal(calls.at(-1).body.filters.type, 'theorem', 'field filters survive');
    assert.equal(calls.at(-1).body.path, '/数学/**', 'path scope survives');
    assert.equal(calls.at(-1).body.query, '一致收敛', 'query survives');
    assert.match(await resultCount.innerText(), /最多 25 条 · 返回 25 条/);

    // A late response from an older search cannot replace the newest results.
    await page.evaluate(() => { window.libraryFixture.state.delaySearch = 450; });
    await limitSelect.selectOption('50');
    await searchButton.click();
    let raceCalls = await semanticCalls();
    assert.equal(raceCalls.at(-1).body.limit, 50, 'the slower search was issued with limit 50');
    await page.evaluate(() => { window.libraryFixture.state.delaySearch = 0; });
    await limitSelect.selectOption('100');
    await searchButton.click();
    await page.locator('.library-results li').nth(59).waitFor();
    await sleep(700);
    raceCalls = await semanticCalls();
    assert.equal(raceCalls.at(-1).body.limit, 100, 'the newest search used limit 100');
    assert.equal(await page.locator('.library-results li').count(), 60, 'the stale limit-50 response did not replace the results');
    assert.match(await resultCount.innerText(), /最多 100 条 · 返回 60 条/, 'the summary still describes the newest request');
  } finally {
    await browser.close();
    await server.close();
  }
  assert.deepEqual(pageErrors, []);
});
