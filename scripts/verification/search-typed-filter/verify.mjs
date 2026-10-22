import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { createServer } from '../../../apps/desktop-web/node_modules/vite/dist/node/index.js';
const repo = process.cwd(), sources = path.join(repo, 'scripts/verification/search-typed-filter'), appRoot = path.join(repo, 'apps/desktop-web'), evDir = path.join(repo, 'runtime/verification/issue159-typed-filter'), tsx = path.join(appRoot, '__agent_search_filter_fixture.tsx'), html = path.join(appRoot, 'tests/__agent_search_filter_fixture.html');
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function port() { const s = net.createServer(); await new Promise((ok, no) => s.listen(0, '127.0.0.1', ok).once('error', no)); const p = s.address().port; await new Promise(ok => s.close(ok)); return p; }
async function wait(fn, label, t = 30000) { const end = Date.now() + t; while (Date.now() < end) {
    try {
        const x = await fn();
        if (x)
            return x;
    }
    catch { }
    await sleep(100);
} throw Error('timeout ' + label); }
await fs.mkdir(evDir, { recursive: true });
await fs.copyFile(path.join(sources, 'fixture.tsx'), tsx);
await fs.copyFile(path.join(sources, 'fixture.html'), html);
const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'xeonolab-round12-search-'));
const apiPort = await port(), apiProc = spawn('uv', ['run', 'python', path.join(sources, 'local_api.py')], { cwd: repo, env: { ...process.env, ROUND6_XELIBRARY_DATA: path.join(temp, 'data'), ROUND6_XELIBRARY_PORT: String(apiPort) }, stdio: 'ignore' });
let vite, chrome, ws;
try {
    await wait(async () => { try {
        return (await fetch(`http://127.0.0.1:${apiPort}/health/live`)).ok;
    }
    catch {
        return false;
    } }, 'api');
    const base = `http://127.0.0.1:${apiPort}/api/v1`, auth = 'Bearer round6-local-test-token', headers = { Authorization: auth, 'Content-Type': 'application/json' };
    const created = await fetch(`${base}/tables`, { method: 'POST', headers, body: JSON.stringify({ path: '/round12/search', name: 'typed-filter', columns: [{ name: 'title', type: 'string', nullable: false, default_value: null }, { name: 'confidence', type: 'float64', nullable: true, default_value: null }] }) });
    assert.equal(created.status, 201);
    const table = (await created.json()).data;
    const rowRes = await fetch(`${base}/tables/${table.table_id}/rows`, { method: 'POST', headers, body: JSON.stringify({ values: { title: 'xenosearchr12 target', confidence: 0.91 } }) });
    assert.equal(rowRes.status, 201);
    const row = (await rowRes.json()).data;
    const directNumeric = await fetch(`${base}/search/text`, { method: 'POST', headers, body: JSON.stringify({ query: 'xenosearchr12', path: '/**', limit: 25, filters: { confidence: 0.91 } }) });
    assert.equal(directNumeric.status, 200);
    const directNumericData = (await directNumeric.json()).data;
    const directString = await fetch(`${base}/search/text`, { method: 'POST', headers, body: JSON.stringify({ query: 'xenosearchr12', path: '/**', limit: 25, filters: { confidence: '0.91' } }) });
    assert.equal(directString.status, 200);
    const directStringData = (await directString.json()).data;
    const legacyParser = await fs.readFile(path.join(sources, 'legacy-parser.js'), 'utf8');
    let legacyApplied = false;
    let useLegacy = true;
    const vitePort = await port();
    vite = await createServer({ root: appRoot, plugins: [{ name: 'pre-fix-filter-reproduction', enforce: 'pre', transform(code, id) { if (useLegacy && id.split('?')[0].endsWith('/src/library/logic.js')) {
                    legacyApplied = true;
                    const a = code.indexOf('export function parseFieldFilters');
                    const b = code.indexOf('export function searchWarnings', a);
                    assert.ok(a >= 0 && b > a);
                    return code.slice(0, a) + legacyParser + '\n' + code.slice(b);
                } return null; } }], server: { host: '127.0.0.1', port: vitePort, strictPort: true, watch: null, proxy: { '/api/v1': { target: `http://127.0.0.1:${apiPort}`, changeOrigin: false } } } });
    await vite.listen();
    const profile = path.join(temp, 'chrome');
    chrome = spawn(process.env.CHROME_BIN || '/usr/bin/google-chrome', ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage', `--user-data-dir=${profile}`, '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1', '--remote-allow-origins=*', 'about:blank'], { stdio: 'ignore' });
    const cdp = await wait(async () => { try {
        return Number((await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split(/\r?\n/)[0]);
    }
    catch {
        return 0;
    } }, 'cdp');
    const target = await (await fetch(`http://127.0.0.1:${cdp}/json/new?about%3Ablank`, { method: 'PUT' })).json();
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((ok, no) => { ws.onopen = ok; ws.onerror = no; });
    let seq = 0;
    const pending = new Map();
    ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) {
        const p = pending.get(m.id);
        pending.delete(m.id);
        m.error ? p.reject(Error(m.error.message)) : p.resolve(m.result);
    } };
    const call = (method, params = {}) => { let id = ++seq; ws.send(JSON.stringify({ id, method, params })); return new Promise((resolve, reject) => pending.set(id, { resolve, reject })); };
    const ev = async (expression) => { const x = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }); if (x.exceptionDetails)
        throw Error(JSON.stringify(x.exceptionDetails)); return x.result?.value; };
    const txt = async (selector) => ev(`document.querySelector(${JSON.stringify(selector)})?.innerText||''`);
    const setValue = async (selector, value) => { const expression = `(()=>{const e=document.querySelector(${JSON.stringify(selector)});if(!e)return false;const p=e instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;Object.getOwnPropertyDescriptor(p,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));return true})()`; assert.equal(await ev(expression), true); };
    await call('Page.enable');
    await call('Runtime.enable');
    await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
    await call('Page.navigate', { url: `http://127.0.0.1:${vitePort}/tests/__agent_search_filter_fixture.html` });
    await wait(() => ev("Boolean(document.querySelector('.library-nav'))"), 'LibraryApp');
    await ev("Array.from(document.querySelectorAll('.library-nav button')).find(b=>b.innerText.includes('搜索')).click()");
    await wait(() => ev("Boolean(document.querySelector('input[aria-label=\"搜索 XeLibrary\"]'))"), 'search view');
    await ev("document.querySelector('.library-search-kind input').click()");
    await setValue('input[aria-label="搜索 XeLibrary"]', 'xenosearchr12');
    await ev("Array.from(document.querySelectorAll('.library-search-filters button')).find(b=>b.innerText.includes('过滤条件')).click()");
    await setValue('textarea[aria-label="字段过滤"]', 'confidence = 0.91');
    await ev("Array.from(document.querySelectorAll('.library-search-bar button')).find(b=>b.innerText==='搜索').click()");
    await wait(() => ev("Boolean(document.querySelector('.library-search')?.innerText.includes('没有搜索结果'))"), 'completed filtered zero-result view');
    const filteredHits = Number(await ev("document.querySelectorAll('.library-results li').length"));
    const emptyText = await ev("document.querySelector('.library-search')?.innerText||''");
    await fs.writeFile(path.join(evDir, 'search-filter-no-hits.png'), Buffer.from((await call('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
    assert.equal(legacyApplied, true, 'baseline parser transform actually applied');
    assert.equal(filteredHits, 0, 'pre-fix parser reproduced zero numeric hits');
    assert.equal(await ev("window.searchFilterFixture.requests.at(-1).body.filters.confidence"), '0.91');
    useLegacy = false;
    vite.moduleGraph.invalidateAll();
    await call('Page.navigate', {url:'about:blank'});
    await wait(()=>ev("location.href === 'about:blank' && !document.querySelector('.library-nav')"),'blank navigation');
    await call('Page.navigate',{url:`http://127.0.0.1:${vitePort}/tests/__agent_search_filter_fixture.html?fixed=1`});
    await wait(() => ev("Boolean(document.querySelector('.library-nav'))"), 'reloaded fixed app');
    await ev("Array.from(document.querySelectorAll('.library-nav button')).find(b=>b.innerText.includes('搜索')).click()");
    await wait(() => ev("Boolean(document.querySelector('.library-search-kind input'))"), 'search');
    await ev("document.querySelector('.library-search-kind input').click()");
    await setValue('input[aria-label="搜索 XeLibrary"]', 'xenosearchr12');
    await ev("document.querySelector('.library-search-filters button').click()");
    const search = async (filter) => { await setValue('textarea[aria-label="字段过滤"]', filter); const previous = await ev('window.searchFilterFixture.requests.length'); await ev("document.querySelector('.library-search-bar button').click()"); await wait(() => ev(`window.searchFilterFixture.requests.length>${previous}`), 'query request'); await wait(() => ev("document.querySelector('.library-search-bar button').innerText==='搜索'"), 'query settled'); };
    await search('confidence = 0.91');
    assert.equal(await ev('window.searchFilterFixture.requests.at(-1).body.filters.confidence'), 0.91);
    assert.equal(await ev("document.querySelectorAll('.library-results li').length"), 1);
    await fs.writeFile(path.join(evDir, 'typed-number-hit.png'), Buffer.from((await call('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
    await search('confidence = "0.91"');
    assert.equal(await ev('window.searchFilterFixture.requests.at(-1).body.filters.confidence'), '0.91');
    assert.equal(await ev("document.querySelectorAll('.library-results li').length"), 0, 'string does not match numeric column');
    // Same field name in another table has string schema: no global schema guessing.
    const stringTableResponse = await fetch(`${base}/tables`, { method: 'POST', headers, body: JSON.stringify({ path: '/round12/search-strings', name: 'numeric-looking-string', columns: [{ name: 'title', type: 'string', nullable: false, default_value: null }, { name: 'confidence', type: 'string', nullable: false, default_value: null }] }) });
    assert.equal(stringTableResponse.status, 201);
    const stringTable = (await stringTableResponse.json()).data;
    const stringRowResponse = await fetch(`${base}/tables/${stringTable.table_id}/rows`, { method: 'POST', headers, body: JSON.stringify({ values: { title: 'xenosearchr12 string target', confidence: '0.91' } }) });
    assert.equal(stringRowResponse.status, 201);
    const stringRow = (await stringRowResponse.json()).data;
    await search('confidence = "0.91"');
    assert.equal(await ev("document.querySelectorAll('.library-results li').length"), 1);
    assert.ok((await txt('.library-results')).includes('string target'));
    await search('confidence = 0.91');
    assert.equal(await ev("document.querySelectorAll('.library-results li').length"), 1);
    assert.equal((await txt('.library-results')).includes('string target'), false);
    const beforeInvalid = await ev('window.searchFilterFixture.requests.length');
    for (const invalid of ['confidence = 01', 'confidence = 1e309', 'confidence = 9007199254740993', 'confidence = {bad}', 'title = xenosearchr12 target\nconfidence = {bad}']) {
        await setValue('textarea[aria-label="字段过滤"]', invalid);
        await wait(() => ev("Boolean(document.querySelector('.library-search-filters [role=alert]'))"), 'inline validation');
        await ev("document.querySelector('.library-search-bar button').click()");
        await wait(() => ev("document.querySelector('.library-search-bar button').innerText==='搜索'"), 'invalid settled');
        assert.equal(await ev('window.searchFilterFixture.requests.length'), beforeInvalid, 'invalid input sends no request');
    }
    await search('confidence = 0.91');
    assert.equal(await ev("document.querySelectorAll('.library-results li').length"), 1, 'valid correction can retry');
    await ev("document.querySelector('.library-results li button').click()");
    await wait(() => ev("Boolean(document.querySelector('.knowledge-inspector')?.innerText.includes(" + JSON.stringify(row._row_id) + "))"), 'correct source');
    const result = { baseline: '2aabf623aa88f68c0050774ddb03f2773681abfe', source: 'Actual LibraryApp/useSearch/SearchView + headless Chrome, loopback Vite proxies actual temporary FastAPI XeLibrary; synthetic development identity, tables/rows created via HTTP. Pre-fix parser is restored only through test Vite transform for baseline reproduction, then invalidated/reloaded to use unmodified fixed source.', baselineControl: { uiStringFilter: '0.91', uiHits: filteredHits, directNumberHits: directNumericData.map(x => x.row_id), directStringHits: directStringData.map(x => x.row_id) }, fixed: { numberMatches: row._row_id, quotedStringMatches: stringRow._row_id, invalidRequests: 0, retryAndSourceOpen: true }, outcome: 'PASS: number/string remain distinct across different table schemas; malformed and unrepresentable typed inputs make no request; correcting input and opening the numeric source succeeds.' };
    await fs.writeFile(path.join(evDir, 'search-filter-result.json'), JSON.stringify(result, null, 2));
    console.log(JSON.stringify(result, null, 2));
    assert(directNumericData.some(x => x.row_id === row._row_id));
    assert.equal(directStringData.length, 0);
}
finally {
    ws?.close();
    if (chrome) {
        chrome.kill('SIGTERM');
        await new Promise(r => chrome.once('exit', r));
    }
    if (vite)
        await vite.close();
    apiProc.kill('SIGTERM');
    await new Promise(r => apiProc.once('exit', r));
    await fs.rm(temp, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
    await Promise.all([tsx, html].map(f => fs.rm(f, { force: true })));
}
