import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { spawn } from 'node:child_process';
import { createServer } from '../../../apps/desktop-web/node_modules/vite/dist/node/index.js';
const repo = process.cwd(), sources = path.join(repo, 'scripts/verification/inspector-context'), app = path.join(repo, 'apps/desktop-web'), evidence = path.join(repo, 'runtime/verification/issue153-inspector-context'), tsx = path.join(app, '__agent_inspector_fixture.tsx'), html = path.join(app, 'tests/__agent_inspector_fixture.html');
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function port() { const s = net.createServer(); await new Promise((ok, no) => s.listen(0, '127.0.0.1', ok).once('error', no)); const p = s.address().port; await new Promise(ok => s.close(ok)); return p; }
async function wait(fn, label, t = 30000) { const end = Date.now() + t; while (Date.now() < end) {
    try {
        const x = await fn();
        if (x)
            return x;
    }
    catch { }
    await sleep(80);
} throw Error('timeout ' + label); }
await fs.mkdir(evidence, {recursive: true});
await fs.copyFile(path.join(sources, 'fixture.tsx'), tsx);
await fs.copyFile(path.join(sources, 'fixture.html'), html);
const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'xeonolab-round8-inspector-'));
const ap = await port(), apiProc = spawn('uv', ['run', 'python', path.join(sources, 'local_api.py')], { cwd: repo, env: { ...process.env, ROUND6_XELIBRARY_DATA: path.join(temp, 'data'), ROUND6_XELIBRARY_PORT: String(ap) }, stdio: 'ignore' });
let vite, chrome, ws;
try {
    await wait(async () => { try {
        return (await fetch(`http://127.0.0.1:${ap}/health/live`)).ok;
    }
    catch {
        return false;
    } }, 'API');
    const base = `http://127.0.0.1:${ap}/api/v1`, auth = 'Bearer round6-local-test-token', headers = { Authorization: auth, 'Content-Type': 'application/json' };
    const c = await fetch(`${base}/tables`, { method: 'POST', headers, body: JSON.stringify({ path: '/round8/inspector', name: 'save-switch', columns: [{ name: 'title', type: 'string', nullable: false, default_value: '' }] }) });
    assert.equal(c.status, 201);
    const table = (await c.json()).data;
    async function add(title) { const x = await fetch(`${base}/tables/${table.table_id}/rows`, { method: 'POST', headers, body: JSON.stringify({ values: { title } }) }); assert.equal(x.status, 201); return (await x.json()).data; }
    const a = await add('record A'), b = await add('record B');
    const vitePort = await port();
    vite = await createServer({ root: app, server: { host: '127.0.0.1', port: vitePort, strictPort: true, watch: null, proxy: { '/api/v1': { target: `http://127.0.0.1:${ap}`, changeOrigin: false } } } });
    await vite.listen();
    const profile = path.join(temp, 'chrome');
    chrome = spawn(process.env.CHROME_BIN || '/usr/bin/google-chrome', ['--headless=new', '--no-sandbox', '--disable-dev-shm-usage', `--user-data-dir=${profile}`, '--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1', '--remote-allow-origins=*', 'about:blank'], { stdio: 'ignore' });
    const cp = await wait(async () => { try {
        return Number((await fs.readFile(path.join(profile, 'DevToolsActivePort'), 'utf8')).split(/\r?\n/)[0]);
    }
    catch {
        return 0;
    } }, 'CDP');
    const target = await (await fetch(`http://127.0.0.1:${cp}/json/new?about%3Ablank`, { method: 'PUT' })).json();
    ws = new WebSocket(target.webSocketDebuggerUrl);
    await new Promise((ok, no) => { ws.onopen = ok; ws.onerror = no; });
    let seq = 0;
    const pending = new Map();
    ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending.has(m.id)) {
        const p = pending.get(m.id);
        pending.delete(m.id);
        m.error ? p.reject(Error(m.error.message)) : p.resolve(m.result);
    } };
    const call = (method, params = {}) => { const id = ++seq; ws.send(JSON.stringify({ id, method, params })); return new Promise((resolve, reject) => pending.set(id, { resolve, reject })); };
    const ev = async (expression) => { const r = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true }); if (r.exceptionDetails)
        throw Error(r.exceptionDetails.text); return r.result?.value; };
    await call('Page.enable');
    await call('Runtime.enable');
    await call('Emulation.setDeviceMetricsOverride', {width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false});
    await call('Page.navigate', { url: `http://127.0.0.1:${vitePort}/tests/__agent_inspector_fixture.html?table=${table.table_id}` });
    await wait(() => ev("document.querySelectorAll('.library-table tbody tr[data-row-id]').length===2"), 'rows');
    await ev(`document.querySelector('[data-row-id="${a._row_id}"]').click()`);
    await wait(() => ev("Boolean(document.querySelector('.record-editor'))"), 'Inspector');
    await ev("[...document.querySelectorAll('.inspector-tabs button')].find(b=>b.innerText.includes('字段')).click()");
    await wait(() => ev("Boolean(document.querySelector('.inspector-fields input'))"), 'fields');
    const setValue = `(selector,value)=>{const el=document.querySelector(selector),set=Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set;set.call(el,value);el.dispatchEvent(new Event('input',{bubbles:true}));el.dispatchEvent(new Event('change',{bubbles:true}))}`;
    const select = async (row) => { await ev(`document.querySelector('[data-row-id="${row._row_id}"]').click()`); await wait(() => ev(`document.querySelector('.inspector-head small')?.innerText.includes('${row._row_id.slice(0, 12)}')`), 'selected row'); };
    const click = text => ev(`[...document.querySelectorAll('.record-editor button')].find(b=>b.innerText.trim()===${JSON.stringify(text)}).click()`);
    const edit = text => ev(`(${setValue})('.inspector-fields input',${JSON.stringify(text)})`);
    const status = () => ev("document.querySelector('.inspector-status')?.innerText||null");
    const waitPending = n => wait(() => ev(`window.inspectorFixture.pending.length===${n}`), 'pending request');
    const release = async () => { const prev = await ev('window.inspectorFixture.settled'); await ev('window.inspectorFixture.pending.shift()()'); await wait(() => ev(`window.inspectorFixture.settled>${prev}`), 'real response'); await sleep(120); };
    const fetchRow = async (row) => (await (await fetch(`${base}/tables/${table.table_id}/rows/${row._row_id}`, { headers: { Authorization: auth } })).json()).data;
    await edit('record A edited');
    await click('保存记录');
    await waitPending(1);
    await select(b);
    assert.equal(await status(), null);
    assert.equal(await ev("document.querySelector('.inspector-fields button').disabled"), false);
    await release();
    assert.equal(await status(), null);
    assert.equal((await fetchRow(a)).values.title, 'record A edited');
    assert.equal((await fetchRow(b)).values.title, 'record B');
    await select(a);
    assert.equal(await ev("document.querySelector('.inspector-fields input').value"), 'record A edited');
    await edit('normal A saved');
    await click('保存记录');
    await waitPending(1);
    await release();
    assert.equal(await status(), '已保存');
    await edit('round trip A');
    await click('保存记录');
    await waitPending(1);
    await select(b);
    await select(a);
    await release();
    assert.equal(await status(), null);
    await edit('A overlap');
    await click('保存记录');
    await waitPending(1);
    await select(b);
    await edit('B own save');
    await click('保存记录');
    await waitPending(2);
    await release();
    assert.equal(await ev("document.querySelector('.inspector-fields button').disabled"), true);
    await release();
    assert.equal(await status(), '已保存');
    await select(a);
    const current = await fetchRow(a);
    const external = await fetch(`${base}/tables/${table.table_id}/rows/${a._row_id}`, { method: 'PATCH', headers: { ...headers, 'If-Match': `"revision:${current._revision}"` }, body: JSON.stringify({ values: { title: 'external A' } }) });
    assert.equal(external.status, 200);
    await edit('must not overwrite');
    await click('保存记录');
    await waitPending(1);
    await select(b);
    await release();
    assert.equal(await status(), null);
    assert.equal(await ev("Boolean(document.querySelector('.record-editor [role=alert]'))"), false);
    assert.equal((await fetchRow(a)).values.title, 'external A');
    await select(a);
    await edit('still stale');
    await click('保存记录');
    await waitPending(1);
    await release();
    assert.equal(await ev("Boolean(document.querySelector('.inspector-conflict'))"), true);
    assert.equal((await fetchRow(a)).values.title, 'external A');
    await click('重新加载');
    await wait(() => ev("document.querySelector('.inspector-fields input').value==='external A'"), 'reload');
    assert.equal(await ev("Boolean(document.querySelector('.record-editor [role=alert]'))"), false, 'reload clears both local error and parent conflict');
    await edit('A retry after reload');
    await click('保存记录');
    await waitPending(1);
    await release();
    assert.equal(await status(), '已保存', 'save remains usable after conflict reload');
    assert.equal((await fetchRow(a)).values.title, 'A retry after reload');
    await ev("[...document.querySelectorAll('.inspector-tabs button')].find(b=>b.innerText.includes('历史')).click()");
    await wait(() => ev("Boolean(document.querySelector('.history-timeline button:not(:disabled)'))"), 'history');
    await ev("[...document.querySelectorAll('.history-timeline button')].find(b=>b.innerText==='恢复此版本'&&!b.disabled).click()");
    await waitPending(1);
    await select(b);
    await release();
    assert.equal(await status(), null);
    await select(a);
    await ev("[...document.querySelectorAll('.inspector-tabs button')].find(b=>b.innerText.includes('字段')).click()");
    await click('软删除记录');
    await waitPending(1);
    await select(b);
    await release();
    assert.equal(await status(), null);
    assert.equal((await fetch(`${base}/tables/${table.table_id}/rows/${a._row_id}`, { headers: { Authorization: auth } })).status, 404);
    const result = { baseline: '2aabf623aa88f68c0050774ddb03f2773681abfe', source: 'Real local FastAPI XeLibrary + actual TableView/Inspector + headless Chrome; synthetic development identity and temporary rows; browser barrier delays forwarding legitimate API mutations, no mocked responses', checks: ['success scoped to record', 'reopen saved A value', 'normal save feedback', 'A-B-A invalidation', 'old finally cannot clear B busy', 'old 412 hidden on B', 'current 412 shown and server data preserved', 'reload clears error and conflict; retry persists server value', 'restore and remove feedback isolated'], outcome: 'PASS' };
    await fs.writeFile(path.join(evidence, 'inspector-context-result.json'), JSON.stringify(result, null, 2));
    await ev("document.querySelector('.record-editor').scrollIntoView({block:'center'})");
    await fs.writeFile(path.join(evidence, 'inspector-context.png'), Buffer.from((await call('Page.captureScreenshot', { format: 'png' })).data, 'base64'));
    console.log(JSON.stringify(result, null, 2));
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
