import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath, pathToFileURL} from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(here, '../../..');
const evidence = path.join(repoRoot, 'runtime/verification/issue158-search-return');
await fs.mkdir(evidence, {recursive:true});
const appRoot = path.join(repoRoot, 'apps/desktop-web');
const {createServer} = await import(pathToFileURL(path.join(appRoot, 'node_modules/vite/dist/node/index.js')));
const {chromium} = await import(pathToFileURL(path.join(appRoot, 'node_modules/@playwright/test/index.mjs')));
test('search return restores only the originating result generation', async () => {
let transformed = false;
const fixturePlugin = {name: 'round10-many-search-hits', enforce: 'pre', transform(code, id) {
  if (!id.split('?')[0].endsWith('/tests/library-fixture.tsx')) return null;
  if (!code.includes("if (clean === '/search/semantic') {") || !code.includes("const clean = url.pathname;")) {
    throw new Error('Synthetic search fixture anchor changed; refusing an unverified rewrite');
  }
  transformed = true;
  return code
    .replace('const clean = url.pathname;', `const clean = url.pathname;
if (clean === '/tables/unknown') throw new APIRequestError('unknown synthetic table', 404, 'TABLE_NOT_FOUND');
if (clean === '/tables/t1/rows/row-025' && (window as any).__holdSource) {
  (window as any).__sourceStarted=true; (window as any).__sourceCompleted=false;
  await new Promise<void>(resolve => {(window as any).__releaseSource=resolve;});
  (window as any).__sourceCompleted=true;
}
if (clean === '/tables/t1/rows/row-025' && (window as any).__failSource) throw new APIRequestError('synthetic missing source', 404, 'ROW_NOT_FOUND');
if (clean === '/search/semantic' && (window as any).__failSearch) throw new APIRequestError('synthetic search failure', 503, 'SEARCH_UNAVAILABLE');
`)
    .replace("if (body.mode === 'quality') throw new APIRequestError('quality worker not configured', 503, 'QWEN_UNAVAILABLE');",
      "if (body.mode === 'quality') throw new APIRequestError('quality worker not configured', 503, 'QWEN_UNAVAILABLE');\n    if (body.query === 'empty') return {data: [], meta: {}};")
    .replace("table_id: 't1', row_id: `row-${String(index + 1).padStart(3, '0')}`",
      "table_id: (window as any).__unknownTable ? 'unknown' : 't1', row_id: `row-${String(index + 1).padStart(3, '0')}`");
}};
const server = await createServer({root: appRoot, plugins: [fixturePlugin], server: {host: '127.0.0.1', port: 0, strictPort: false, watch: null}});
let browser;
try {
  await server.listen();
  const port = server.httpServer.address().port;
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  browser = await chromium.launch({executablePath, headless: true, args: ['--no-sandbox', '--disable-dev-shm-usage']});
  const page = await browser.newPage({viewport: {width: 1440, height: 900}});
  const errors = [];
  page.on('pageerror', error => errors.push(String(error)));
  await page.goto(`http://127.0.0.1:${port}/tests/library-fixture.html?searchPool=25`);
  await page.locator('.knowledge-sidebar .library-nav button').filter({hasText: '搜索'}).click();
  await page.getByRole('searchbox', {name: '搜索 XeLibrary'}).fill('一致收敛');
  await page.getByRole('button',{name:'过滤条件',exact:true}).click();
  await page.getByRole('textbox',{name:'搜索路径模式'}).fill('/数学/**');
  await page.getByRole('textbox',{name:'字段过滤'}).fill('type = theorem');
  await page.locator('.library-search-bar button').click();
  await page.locator('.library-results li').nth(24).waitFor();
  const hitCount = await page.locator('.library-results li').count();
  assert.equal(transformed, true);
  assert.equal(hitCount, 25);
  const resultsScroller = page.locator('.knowledge-content');
  await resultsScroller.hover();
  await page.mouse.wheel(0, 1800);
  await page.waitForTimeout(200);
  await page.locator('.library-results li').nth(24).locator('button').scrollIntoViewIfNeeded();
  const before = await resultsScroller.evaluate(el => ({scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight, query: document.querySelector('.library-search input[type=search]')?.value, first: document.querySelector('.library-results li')?.innerText.slice(0, 50), last: document.querySelector('.library-results li:last-child')?.innerText.slice(0, 50)}));
  assert.ok(before.scrollTop > 0 && before.scrollHeight > before.clientHeight, JSON.stringify(before));
  await page.screenshot({path: path.join(evidence, 'search-results-scrolled.png')});
  await page.locator('.library-results li').nth(24).locator('button').click();
  await page.locator('.record-editor').waitFor();
  const openedRow = await page.evaluate(() => ({visible: document.querySelector('.record-editor') !== null, sourceReads: window.libraryFixture.calls.filter(call => call.path.endsWith('/rows/row-025'))}));
  const queryCallsAfterOpen = await page.evaluate(() => window.libraryFixture.calls.filter(call => call.path === '/search/semantic').length);
  await page.locator('.knowledge-sidebar .library-nav button').filter({hasText: '搜索'}).click();
  await page.locator('.library-results li').nth(24).waitFor();
  const after = await resultsScroller.evaluate(el => ({scrollTop: el.scrollTop, scrollHeight: el.scrollHeight, clientHeight: el.clientHeight, query: document.querySelector('.library-search input[type=search]')?.value, first: document.querySelector('.library-results li')?.innerText.slice(0, 50), last: document.querySelector('.library-results li:last-child')?.innerText.slice(0, 50)}));
  const queryCallsAfterReturn = await page.evaluate(() => window.libraryFixture.calls.filter(call => call.path === '/search/semantic').length);
  await page.screenshot({path: path.join(evidence, 'search-results-after-return.png')});
  const report = {
    source: 'Local Vite + actual headless Chromium and repository SearchView/LibraryApp; a test-only Vite transform expands the existing synthetic search fixture to 25 synthetic hits with valid row IDs. No production identity/data or live API was used.',
    workflow: ['Opened XeLibrary Search', 'Searched synthetic phrase 一致收敛', 'Scrolled down the 25-hit result list', 'Opened the 25th hit (row-025) in its source table/Inspector', 'Returned via the Library sidebar Search view'],
    observed: {hitCount, before, openedRow, queryCallsAfterOpen, after, queryCallsAfterReturn, pageErrors: errors, fixtureTransformApplied: transformed},
    finding: 'PASS: originating search list position restores without another search request; new submissions and ordinary navigation do not reuse old positions.',
    limit: 'This proves the list-position behavior with 25 synthetic fixture hits, not how frequently users open multiple search results in production.'
  };
  assert.equal(openedRow.visible, true);
  assert.ok(openedRow.sourceReads.some(call => call.method === 'GET'), 'selected result opened its source row');
  assert.equal(queryCallsAfterOpen, 1);
  assert.equal(after.query, before.query);
  assert.equal(queryCallsAfterReturn, 1, 'no extra search request is made on return');
  assert.ok(Math.abs(after.scrollTop-before.scrollTop)<=2, `restores actual departure: ${JSON.stringify({before,after})}`);
  assert.equal(errors.length, 0);
  const nav = name => page.locator('.knowledge-sidebar .library-nav button').filter({hasText: name});
  const top = () => resultsScroller.evaluate(el => el.scrollTop);
  const searchCalls = () => page.evaluate(() => window.libraryFixture.calls.filter(call => call.path.startsWith('/search/')).length);
  const run = async query => {
    await page.getByRole('searchbox', {name:'搜索 XeLibrary'}).fill(query);
    const previous = await searchCalls();
    await page.locator('.library-search-bar button').click();
    await page.waitForFunction(n => window.libraryFixture.calls.filter(call => call.path.startsWith('/search/')).length > n, previous);
  };
  const returnSearch = async () => {await nav('搜索').click(); await page.locator('.library-results li').nth(24).waitFor();};
  const openHit = async index => {
    const button = page.locator('.library-results li').nth(index).locator('button');
    await button.scrollIntoViewIfNeeded();
    const position = await top();
    await button.click();
    await page.locator('.inspector-head small').filter({hasText:`row-${String(index+1).padStart(3,'0')}`}).waitFor();
    return position;
  };
  assert.equal(await page.getByRole('textbox',{name:'搜索路径模式'}).inputValue(), '/数学/**');
  assert.equal(await page.getByRole('textbox',{name:'字段过滤'}).inputValue(), 'type = theorem');
  const secondPosition = await openHit(14); await returnSearch();
  assert.ok(Math.abs(await top()-secondPosition)<=2); assert.equal(await searchCalls(), 1);
  await nav('查询控制台').click(); await returnSearch(); assert.equal(await top(),0);
  await openHit(24); await nav('查询控制台').click(); await returnSearch(); assert.equal(await top(),0);
  await resultsScroller.evaluate(el=>{el.scrollTop=1200;}); await run('一致收敛'); assert.equal(await top(),0);
  await openHit(24); await returnSearch(); await run('new query'); assert.equal(await top(),0);
  await page.evaluate(()=>{window.__failSource=true;});
  await page.locator('.library-results li').nth(24).locator('button').scrollIntoViewIfNeeded();
  const failedDeparture=await top();
  await page.locator('.library-results li').nth(24).locator('button').click();
  await page.getByText('记录不存在，或当前身份没有读取权限。',{exact:true}).waitFor();
  await returnSearch(); assert.ok(Math.abs(await top()-failedDeparture)<=2);
  await page.evaluate(()=>{window.__failSource=false; window.__holdSource=true;});
  await page.locator('.library-results li').nth(24).locator('button').scrollIntoViewIfNeeded();
  const pendingDeparture=await top();
  await page.locator('.library-results li').nth(24).locator('button').click();
  await page.waitForFunction(()=>window.__sourceStarted === true && typeof window.__releaseSource === 'function');
  assert.equal(await page.evaluate(()=>window.__sourceCompleted),false);
  await nav('搜索').click();
  assert.ok(Math.abs(await top()-pendingDeparture)<=2);
  assert.equal(await page.evaluate(()=>window.__sourceCompleted),false);
  await page.evaluate(()=>{window.__holdSource=false;window.__releaseSource();});
  await page.waitForFunction(()=>window.__sourceCompleted === true);
  assert.ok(Math.abs(await top()-pendingDeparture)<=2); assert.equal(await page.locator('.library-results li').count(),25);
  await nav('查询控制台').click(); await returnSearch(); assert.equal(await top(),0);
  // A new query while the old source is still held invalidates its position.
  await page.evaluate(()=>{window.__holdSource=true;window.__sourceStarted=false;});
  await page.locator('.library-results li').nth(24).locator('button').click();
  await page.waitForFunction(()=>window.__sourceStarted===true);
  assert.equal(await page.evaluate(()=>window.__sourceCompleted),false);
  await nav('搜索').click(); await run('new query during held source'); assert.equal(await top(),0);
  await page.evaluate(()=>{window.__holdSource=false;window.__releaseSource();});
  await page.waitForFunction(()=>window.__sourceCompleted===true);
  assert.equal(await top(),0); assert.equal(await page.getByRole('searchbox',{name:'搜索 XeLibrary'}).inputValue(),'new query during held source');
  // Ordinary navigation also invalidates a source before its request completes.
  await page.evaluate(()=>{window.__holdSource=true;window.__sourceStarted=false;});
  await page.locator('.library-results li').nth(24).locator('button').click();
  await page.waitForFunction(()=>window.__sourceStarted===true);
  assert.equal(await page.evaluate(()=>window.__sourceCompleted),false);
  await nav('查询控制台').click();
  await page.evaluate(()=>{window.__holdSource=false;window.__releaseSource();});
  await page.waitForFunction(()=>window.__sourceCompleted===true); await returnSearch(); assert.equal(await top(),0);
  await page.evaluate(()=>{window.__unknownTable=true;});
  await run('unknown source'); await page.locator('.library-results li').nth(24).locator('button').click();
  await page.getByText('无法定位该记录所属的知识表。',{exact:true}).waitFor();
  assert.equal(await page.locator('.library-search').count(),1);
  assert.equal(await page.getByRole('searchbox',{name:'搜索 XeLibrary'}).inputValue(),'unknown source');
  assert.ok(await top()>1000, 'unknown table does not reset the visible search list');
  await nav('查询控制台').click(); await returnSearch(); assert.equal(await top(),0);
  await page.evaluate(()=>{window.__unknownTable=false;});
  await run('empty'); await page.getByText('没有搜索结果',{exact:true}).waitFor(); assert.equal(await top(),0);
  const beforeBlank=await searchCalls();
  await page.getByRole('searchbox',{name:'搜索 XeLibrary'}).fill('');
  await page.locator('.library-search-bar button').click();
  await page.getByText('请输入搜索内容',{exact:true}).waitFor();
  assert.equal(await searchCalls(),beforeBlank); assert.equal(await top(),0);
  await page.evaluate(()=>{window.__failSearch=true;}); await run('retry query');
  await page.locator('.library-search .ds-error-code').filter({hasText:'SEARCH_UNAVAILABLE'}).waitFor(); assert.equal(await top(),0);
  await page.evaluate(()=>{window.__failSearch=false;}); await page.getByRole('button',{name:'重试',exact:true}).click();
  await page.locator('.library-results li').nth(24).waitFor(); assert.equal(await top(),0);
  await openHit(24); await returnSearch(); await page.getByRole('tab',{name:'Quality',exact:true}).click(); assert.equal(await top(),0);
  await run('quality query'); await page.locator('.library-search .ds-error-code').filter({hasText:'QWEN_UNAVAILABLE'}).waitFor();
  await page.getByRole('tab',{name:'Fast',exact:true}).click(); assert.equal(await page.locator('.library-results li').count(),0);
  assert.equal(errors.length,0);
  report.observed.secondPosition=secondPosition;
  report.observed.failedDeparture=failedDeparture;
  report.observed.pendingDeparture=pendingDeparture;
  report.observed.pendingBarrier={started:true,completedBeforeReturn:false,completedAfterRelease:true};
  report.checks=['restores actual departing main position without re-query','preserves query/path/filters/hits','second source uses its own position','ordinary navigation clears snapshot','same-text and new searches reset','row-read failure returns to departure; unknown-table leaves search in place','held-source return restores departure; new query and ordinary navigation defeat late completion','empty/error retry/Fast-Quality unchanged'];
  await fs.writeFile(path.join(evidence, 'browser-report.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify({report: path.join(evidence, 'browser-report.json'), hitCount, beforeScrollTop: before.scrollTop, afterScrollTop: after.scrollTop, queryCallsAfterReturn, openedRow, pageErrors: errors.length}));
} finally {
  await browser?.close();
  await server.close();
}

});
