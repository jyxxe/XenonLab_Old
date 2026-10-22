import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createServer } from '../../../apps/desktop-web/node_modules/vite/dist/node/index.js';
import { chromium } from '../../../apps/desktop-web/node_modules/playwright-core/index.mjs';
const helper = path.dirname(new URL(import.meta.url).pathname), source = path.resolve(helper, '../../..'), mode = process.env.SOURCE_CASE || 'current', evidence = path.join(source, 'runtime/verification/history-restore-page2', mode);
await fs.mkdir(evidence, { recursive: true });
const appRoot = path.join(source, 'apps/desktop-web');
const fixtureTsx = path.join(appRoot, 'tests/__agent_round57_fixture.tsx'), fixtureHtml = path.join(appRoot, 'tests/__agent_round57_fixture.html');
const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'xeonolab-r57-'));
const port = async () => { const s = net.createServer(); await new Promise((ok, no) => s.listen(0, '127.0.0.1', ok).once('error', no)); let n = s.address().port; await new Promise(ok => s.close(ok)); return n; };
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function waitFor(fn, label, timeout = 45000) {
    let end = Date.now() + timeout;
    while (Date.now() < end) {
        try {
            let v = await fn();
            if (v)
                return v;
        }
        catch { }
        await sleep(100);
    }
    throw Error('timed out: ' + label);
}
async function stop(p) {
    if (!p || p.exitCode !== null || p.signalCode !== null)
        return;
    let x = new Promise(r => p.once('exit', r));
    p.kill('SIGTERM');
    await Promise.race([x, sleep(3000)]);
    if (p.exitCode === null && p.signalCode === null)
        p.kill('SIGKILL');
}
await fs.copyFile(path.join(helper, 'fixture.tsx'), fixtureTsx);
await fs.copyFile(path.join(helper, 'fixture.html'), fixtureHtml);
const apiPort = await port(), desktopPort = await port(), vitePort = await port(), token = 'round57-local-admin', env = { PATH: process.env.PATH, LANG: 'C.UTF-8', APP_MODE: 'development', PYTHONPATH: source, RATE_LIMIT_ENABLED: 'false' };
const lib = spawn('uv', ['run', 'python', path.join(helper, 'local_xelibrary_api.py')], { cwd: source, env: { ...env, ROUND51_DATA_DIR: path.join(temp, 'lib'), ROUND51_API_PORT: String(apiPort) }, stdio: 'ignore' }), desktop = spawn('uv', ['run', 'python', path.join(helper, 'local_desktop_api.py')], { cwd: source, env: { ...env, ROUND51_DESKTOP_DATA_DIR: path.join(temp, 'desktop'), ROUND51_DESKTOP_API_PORT: String(desktopPort) }, stdio: 'ignore' });
let vite, browser;
try {
    await waitFor(async () => {
        try {
            return (await fetch(`http://127.0.0.1:${apiPort}/health/live`)).ok;
        }
        catch {
            return false;
        }
    }, 'local API');
    await waitFor(async () => {
        try {
            return (await fetch(`http://127.0.0.1:${desktopPort}/health/live`)).ok;
        }
        catch {
            return false;
        }
    }, 'local desktop API');
    const headers = { authorization: 'Bearer ' + token, 'content-type': 'application/json' };
    async function req(route, method = 'GET', body, extra = {}) {
        const r = await fetch(`http://127.0.0.1:${apiPort}/api/v1${route}`, { method, headers: { ...headers, ...extra }, body: body === undefined ? undefined : JSON.stringify(body) });
        let payload = {};
        try {
            payload = await r.json();
        }
        catch { }
        return { status: r.status, payload };
    }
    ;
    const identity = { subject: 'round57-local-admin', scopes: ['xelibrary:read', 'xelibrary:write', 'xelibrary:admin', 'xelibrary:query'] };
    const tableResp = await req('/tables', 'POST', { path: '/R57', name: 'r57-history-page2', columns: [{ name: 'label', type: 'string', nullable: false, default_value: null }] });
    assert.equal(tableResp.status, 201, JSON.stringify(tableResp.payload));
    const table = tableResp.payload.data, tid = table.table_id;
    const created = [];
    for (let i = 0; i < 101; i++) {
        const r = await req(`/tables/${tid}/rows`, 'POST', { values: { label: `round57 row ${String(i).padStart(3, '0')}` } });
        assert.equal(r.status, 201, JSON.stringify(r.payload));
        created.push(r.payload.data);
    }
    const page1 = await req(`/tables/${tid}/rows?limit=100`);
    assert.equal(page1.status, 200);
    assert.equal(page1.payload.data.length, 100);
    assert.ok(page1.payload.meta.next_cursor);
    const page2 = await req(`/tables/${tid}/rows?limit=100&cursor=${encodeURIComponent(page1.payload.meta.next_cursor)}`);
    assert.equal(page2.payload.data.length, 1);
    const sorted = [...page1.payload.data, ...page2.payload.data];
    assert.equal(sorted.length, 101);
    const target1 = sorted[0], target2 = page2.payload.data[0];
    assert.equal(target2._row_id, sorted[100]._row_id);
    assert.ok(!page1.payload.data.some(r => r._row_id === target2._row_id));
    for (const row of [target1, target2]) {
        const d = await req(`/tables/${tid}/rows/${row._row_id}`, 'DELETE', undefined, { 'If-Match': `"revision:${row._revision}"` });
        assert.equal(d.status, 200, JSON.stringify(d.payload));
    }
    // Local fixture uses actual LibraryApp. Create a persisted Recent navigation through an ordinary row click.
    vite = await createServer({ root: appRoot, configFile: false, plugins: mode === 'create-before' ? [{name:'create-before-source',enforce:'pre',async load(id){for(const f of ['LibraryApp.tsx','Inspector.tsx','hooks.ts'])if(id===path.join(appRoot,'src/library',f))return fs.readFile(path.join(source,'runtime/verification/history-restore-page2/pre-create-source',f),'utf8')}}] : mode.startsWith('cancel-before') ? [{ name: 'cancel-before-source', enforce: 'pre', async load(id) { for (const f of ['LibraryApp.tsx', 'Inspector.tsx'])
                    if (id === path.join(appRoot, 'src/library', f))
                        return fs.readFile(path.join(source, 'runtime/verification/history-restore-page2/pre-cancel-source', f), 'utf8'); } }] : mode === 'before865' ? [{ name: 'before-source', enforce: 'pre', load(id) {
                    for (const f of ['src/library/LibraryApp.tsx', 'src/library/hooks.ts'])
                        if (id === path.join(appRoot, f)) {
                            const r = spawnSync('git', ['show', `865a72277008a5773236b50b993bacdecc78d6ab:apps/desktop-web/${f}`], { cwd: source, encoding: 'utf8' });
                            assert.equal(r.status, 0);
                            return r.stdout;
                        }
                } }] : [], optimizeDeps: { include: ['react', 'react-dom/client', 'react/jsx-dev-runtime'] }, server: { host: '127.0.0.1', port: vitePort, strictPort: true, watch: null, proxy: { '/api/v1': { target: `http://127.0.0.1:${apiPort}` }, '/desktop-api': { target: `http://127.0.0.1:${desktopPort}`, rewrite: p => p.replace(/^\/desktop-api/, '') } } } });
    await vite.listen();
    browser = await chromium.launch({ headless: true, executablePath: process.env.CHROME_BIN || '/usr/bin/google-chrome', args: ['--no-sandbox', '--disable-dev-shm-usage'] });
    const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } }), errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(`http://127.0.0.1:${vitePort}/tests/__agent_round57_fixture.html`);
    await page.locator('.knowledge-sidebar').waitFor();
    await page.locator('.library-tree-table').filter({ hasText: 'r57-history-page2' }).click();
    await page.getByRole('heading', { name: '/R57/r57-history-page2' }).waitFor();
    const anchor = page.locator('tr[data-row-id]').first();
    await anchor.waitFor();
    const anchorId = await anchor.getAttribute('data-row-id');
    await anchor.click();
    await waitFor(() => page.getByRole('button', { name: new RegExp(anchorId.slice(0, 8)) }).count().then(n => n > 0), 'Recent entry');
    await page.reload();
    await page.locator('.knowledge-sidebar').waitFor();
    const recent = page.getByRole('button', { name: new RegExp(anchorId.slice(0, 8)) });
    await recent.waitFor();
    const callIndex = await page.evaluate(() => window.r57Audit.calls.length);
    await recent.click();
    await page.getByRole('heading', { name: '/R57/r57-history-page2' }).waitFor();
    await waitFor(() => page.getByRole('button', { name: '加载更多记录' }).count().then(n => n === 1), 'initial page cursor after include_deleted fetch');
    const rowsAfterRecent = await page.locator('tr[data-row-id]').count();
    const initialTrace = await page.evaluate(() => window.r57Audit.calls.slice(Number(window.r57AuditStart || 0))); // trace detail below captured from callIndex
    const controlLocator = page.locator(`tr[data-row-id="${target1._row_id}"]`);
    await controlLocator.scrollIntoViewIfNeeded();
    await controlLocator.click();
    await page.getByRole('tab', { name: '历史' }).click();
    const historyLine = rev => page.locator('.history-timeline li').filter({ has: page.locator('strong', { hasText: new RegExp(`^r${rev}$`) }) });
    await historyLine(1).getByRole('button', { name: '恢复此版本' }).waitFor();
    const controlHistory = await req(`/tables/${tid}/rows/${target1._row_id}/revisions`);
    assert.equal(controlHistory.status, 200);
    assert.deepEqual(controlHistory.payload.data.map(r => r._revision), [1, 2]);
    const controlStart = await page.evaluate(() => window.r57Audit.calls.length);
    await historyLine(1).getByRole('button', { name: '恢复此版本' }).click();
    await page.getByText(/已恢复到 r1/).waitFor();
    await waitFor(() => page.locator(`tr[data-row-id="${target1._row_id}"]`).getAttribute('aria-selected').then(v => v === 'true'), 'first-page row remains selected');
    const controlFinal = await req(`/tables/${tid}/rows/${target1._row_id}`);
    assert.equal(controlFinal.status, 200);
    assert.equal(controlFinal.payload.data._revision, 3);
    assert.equal(controlFinal.payload.data._deleted_at, null);
    const controlUi = { selected: await page.locator(`tr[data-row-id="${target1._row_id}"]`).getAttribute('aria-selected'), inspector: await page.locator('.record-editor').innerText(), notice: await page.getByRole('status').filter({ hasText: '已恢复' }).first().innerText().catch(() => null) };
    await page.screenshot({ path: path.join(evidence, 'first-page-restored.png'), fullPage: true });
    await page.getByRole('button', { name: '加载更多记录' }).click();
    await waitFor(() => page.getByRole('button', { name: '加载更多记录' }).count().then(n => n === 0), 'page two API load complete');
    await page.locator('.table-scroll[role=grid]').evaluate(el => { el.scrollTop = el.scrollHeight; el.dispatchEvent(new Event('scroll', { bubbles: true })); });
    const loadedPage2 = await page.evaluate(() => window.r57Audit.calls.slice(-6));
    await fs.writeFile(path.join(evidence, 'loadmore-debug.json'), JSON.stringify({ target: target2._row_id, loadedPage2, renderedBeforeClick: await page.locator('tr[data-row-id]').evaluateAll(es => es.map(e => e.getAttribute('data-row-id'))) }, null, 2));
    await waitFor(async () => { await page.locator('.table-scroll[role=grid]').evaluate(el => { el.scrollTop = el.scrollHeight; el.dispatchEvent(new Event('scroll', { bubbles: true })); }); return await page.locator(`tr[data-row-id="${target2._row_id}"]`).count() === 1; }, 'second page target rendered after scroll');
    const loadTrace = await page.evaluate(() => window.r57Audit.calls.slice(-5));
    const p2row = page.locator(`tr[data-row-id="${target2._row_id}"]`);
    await p2row.scrollIntoViewIfNeeded();
    await p2row.click();
    await page.getByRole('tab', { name: '历史' }).click();
    await historyLine(1).getByRole('button', { name: '恢复此版本' }).waitFor();
    const beforeHistory = await req(`/tables/${tid}/rows/${target2._row_id}/revisions`);
    assert.equal(beforeHistory.status, 200);
    assert.deepEqual(beforeHistory.payload.data.map(r => r._revision), [1, 2]);
    const beforeRestore = { rowId: target2._row_id, selected: await p2row.getAttribute('aria-selected'), inspector: await page.locator('.record-editor').innerText(), history: await page.locator('.history-timeline').innerText(), firstPageCount: await page.locator('tr[data-row-id]').count() };
    assert.equal(beforeRestore.selected, 'true');
    assert.match(beforeRestore.inspector, /已删除/);
    assert.match(beforeRestore.history, /r2/);
    assert.match(beforeRestore.history, /r1/);
    await page.screenshot({ path: path.join(evidence, 'page2-before-restore.png'), fullPage: true });
    const restoreStart = await page.evaluate(() => window.r57Audit.calls.length);
    await historyLine(1).getByRole('button', { name: '恢复此版本' }).click();
    await page.getByText(/已恢复到 r1/).waitFor();
    await waitFor(async () => (await page.evaluate(() => window.r57Audit.calls)).slice(restoreStart).some(c => c.path === `/tables/${tid}/rows?limit=100&include_deleted=true` && c.status === 200), 'restore refresh genuine body consumed');
    await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r))));
    if (mode === 'before865') {
        await page.screenshot({ path: path.join(evidence, 'unfixed-page2-after-restore.png'), fullPage: true });
        const effectiveHashes = {};
        for (const file of ['src/library/LibraryApp.tsx', 'src/library/hooks.ts']) {
            const value = spawnSync('git', ['show', `865a72277008a5773236b50b993bacdecc78d6ab:apps/desktop-web/${file}`], { cwd: source, encoding: 'utf8' });
            effectiveHashes[file] = createHash('sha256').update(value.stdout).digest('hex');
        }
        await fs.writeFile(path.join(evidence, 'unfixed-trace.json'), JSON.stringify({ baseline: '865a72277008a5773236b50b993bacdecc78d6ab', effectiveHashes, selected: await page.locator(`tr[data-row-id="${target2._row_id}"]`).count(), calls: await page.evaluate(() => window.r57Audit.calls) }, null, 2));
        assert.equal(await page.locator(`tr[data-row-id="${target2._row_id}"]`).count(), 1, 'unfixed source intentionally fails retained-row regression');
    }
    await waitFor(() => page.locator(`tr[data-row-id="${target2._row_id}"]`).getAttribute('aria-selected').then(v => v === 'true'), 'restored page-two row remains selected');
    await historyLine(3).waitFor();
    assert.equal(await page.locator('.inspector-head strong').textContent(), '记录 Inspector');
    const page2After = { targetCount: await page.locator(`tr[data-row-id="${target2._row_id}"]`).count(), rowCount: await page.locator('tr[data-row-id]').count(), inspectorHeading: await page.locator('.record-editor').innerText().catch(() => null), toolbarNewRecordButtonCount: await page.getByRole('button', { name: '新增记录' }).count(), activeSelection: await page.locator('tr[aria-selected="true"]').count(), notice: await page.getByRole('status').filter({ hasText: '已恢复' }).first().innerText().catch(() => null) };
    await page.screenshot({ path: path.join(evidence, 'page2-after-restore.png'), fullPage: true });
    const apiRestored = await req(`/tables/${tid}/rows/${target2._row_id}`);
    assert.equal(apiRestored.status, 200);
    assert.equal(apiRestored.payload.data._revision, 3);
    assert.equal(apiRestored.payload.data._deleted_at, null);
    assert.equal(apiRestored.payload.data.values.label, target2.values.label);
    const historyAfter = await req(`/tables/${tid}/rows/${target2._row_id}/revisions`);
    assert.equal(historyAfter.status, 200);
    assert.deepEqual(historyAfter.payload.data.map(r => r._revision), [1, 2, 3]);
    const listAfter = await req(`/tables/${tid}/rows?limit=100&include_deleted=true`);
    assert.equal(listAfter.status, 200);
    assert.equal(listAfter.payload.data.length, 100);
    assert.ok(!listAfter.payload.data.some(r => r._row_id === target2._row_id));
    const checks = ['first-page History restore retains selected revision3', 'page-two restored response retains selected revision3 and History without new-record Inspector'];
    assert.equal(await page.getByRole('button', { name: '加载更多记录', exact: true }).count(), 1);
    await page.getByRole('button', { name: '加载更多记录', exact: true }).click();
    await waitFor(() => page.getByRole('button', { name: '加载更多记录', exact: true }).count().then(n => n === 0), 'retained row plus cursor reaches real end');
    assert.equal(await page.locator(`tr[data-row-id="${target2._row_id}"]`).count(), 1);
    assert.equal(await page.getByText('101 / 101 行已加载', { exact: true }).count(), 1);
    checks.push('first-page next_cursor preserved and Load More deduplicates retained target reaching real end');
    const combinationAnchor = sorted[99]._row_id;
    await page.locator('.table-scroll[role=grid]').focus();
    await page.locator('.table-scroll[role=grid]').press('ArrowUp');
    assert.equal(await page.locator(`tr[data-row-id="${combinationAnchor}"]`).getAttribute('aria-selected'), 'true');
    const otherTable = (await req('/tables', 'POST', { path: '/R57', name: 'newer-context-table', columns: [{ name: 'label', type: 'string', nullable: false }] })).payload.data;
    const otherRow = (await req(`/tables/${otherTable.table_id}/rows`, 'POST', { values: { label: 'newer table selection' } })).payload.data;
    const exactRow = async () => { const list = await req(`/tables/${tid}/rows?limit=1000&include_deleted=true`); return list.payload.data.find(r => r._row_id === target2._row_id); };
    async function scrollTo(id, bottom) { await waitFor(async () => { await page.locator('.table-scroll[role=grid]').evaluate((el, b) => { el.scrollTop = b ? el.scrollHeight : 0; el.dispatchEvent(new Event('scroll', { bubbles: true })); }, bottom); return await page.locator(`tr[data-row-id="${id}"]`).count() === 1; }, 'loaded target enters virtual viewport'); }
    async function expectSelected(id) {
        // A settled refresh may reset the grid scroll; the virtual window then
        // stops rendering the selected row until it is scrolled back into view.
        const row = page.locator(`tr[data-row-id="${id}"]`);
        if (!(await row.count())) {
            console.log('DEBUG pre-scroll', JSON.stringify({
                id,
                rendered: await page.locator('tr[data-row-id]').evaluateAll(es => es.map(e => e.getAttribute('data-row-id'))),
                count: await page.locator('.library-table-count').innerText().catch(() => null),
                loadMore: await page.getByRole('button', { name: '加载更多记录', exact: true }).count(),
                selected: await page.locator('tr[aria-selected="true"]').evaluateAll(es => es.map(e => e.getAttribute('data-row-id'))),
            }));
            await scrollTo(id, id === target2._row_id);
        }
        try {
            await waitFor(() => row.getAttribute('aria-selected').then(v => v === 'true'), 'expected row remains selected');
        } catch (error) {
            const diag = {
                id,
                rendered: await page.locator('tr[data-row-id]').evaluateAll(es => es.map(e => e.getAttribute('data-row-id'))),
                count: await page.locator('.library-table-count').innerText().catch(() => null),
                loadMore: await page.getByRole('button', { name: '加载更多记录', exact: true }).count(),
                selected: await page.locator('tr[aria-selected="true"]').evaluateAll(es => es.map(e => e.getAttribute('data-row-id'))),
            };
            console.log('DEBUG expectSelected', JSON.stringify(diag));
            throw error;
        }
    }
    async function activateLast() {
        // Current base virtualizes rows and scrolls keyboard selection into the
        // viewport itself; scroll the retained target into view and select it
        // through its real row surface instead of a long ArrowDown loop that
        // detaches the row between virtual-window updates.
        await scrollTo(target2._row_id, true);
        const row = page.locator(`tr[data-row-id="${target2._row_id}"]`);
        await row.click();
        await waitFor(() => row.getAttribute('aria-selected').then(v => v === 'true'), 'loaded last row activated');
    }
    async function prepare() {
        const latest = await exactRow();
        if (!latest._deleted_at)
            assert.equal((await req(`/tables/${tid}/rows/${target2._row_id}`, 'DELETE', undefined, { 'If-Match': `"revision:${latest._revision}"` })).status, 200);
        await page.reload();
        await page.getByRole('button', { name: new RegExp(combinationAnchor.slice(0, 8)) }).click();
        await waitFor(() => page.locator(`tr[data-row-id="${combinationAnchor}"]`).getAttribute('aria-selected').then(v => v === 'true'), 'normal Recent completes before pagination');
        await page.getByRole('button', { name: '加载更多记录', exact: true }).click();
        await waitFor(() => page.getByRole('button', { name: '加载更多记录', exact: true }).count().then(n => n === 0), 'all101 loaded');
        await activateLast();
        await page.getByRole('tab', { name: '历史' }).click();
        await historyLine(1).getByRole('button', { name: '恢复此版本' }).waitFor();
        return (await exactRow())._revision;
    }
    async function hold(url, status) { let ready = false, sent = false, release; const gate = new Promise(r => release = r); const start = await page.evaluate(() => window.r57Audit.calls.length); await page.route(url, async (route) => { const upstream = await route.fetch(); const body = await upstream.body(); assert.equal(upstream.status(), status); ready = true; await gate; await route.fulfill({ status, headers: upstream.headers(), body }); sent = true; }); return { url, start, release: () => release(), ready: () => ready && !sent }; }
    async function consume(held, path, status = 200) { held.release(); await waitFor(async () => (await page.evaluate(() => window.r57Audit.calls)).slice(held.start).some(c => c.path === path && c.status === status), 'held genuine response consumed'); await page.evaluate(() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))); await page.unroute(held.url); }
    const restorePath = `/tables/${tid}/rows/${target2._row_id}/restore`, refreshPath = `/tables/${tid}/rows?limit=100&include_deleted=true`;
    async function selectFirst() { await scrollTo(target1._row_id, false); await page.locator(`tr[data-row-id="${target1._row_id}"]`).click(); }
    async function selectLast() { await activateLast(); }
    async function assertNoRestoreSuccess(label) {
        if (mode === 'cancel-before412' && label !== 'late412') return;
        const wrong = await page.locator('.record-editor').getByText(/已从 r1 恢复；/).count();
        if (wrong) {
            await fs.writeFile(path.join(evidence, 'cancel-feedback-failure.json'), JSON.stringify({ label, wrong, inspector: await page.locator('.record-editor').innerText(), calls: await page.evaluate(() => window.r57Audit.calls) }, null, 2));
            await page.screenshot({ path: path.join(evidence, 'cancel-feedback-failure.png'), fullPage: true });
        }
        assert.equal(wrong, 0, `${label}: cancelled restore must not display success`);
    }
    for (const transition of ['selection', 'ABA', 'table', 'identity']) {
        await prepare();
        const held = await hold(`**/api/v1${restorePath}`, 200);
        await historyLine(1).getByRole('button', { name: '恢复此版本' }).click();
        await waitFor(held.ready, 'restorePOST buffered');
        if (transition === 'selection' || transition === 'ABA') {
            await selectFirst();
            if (transition === 'ABA')
                await selectLast();
        }
        if (transition === 'table') {
            await page.locator('.library-tree-table').filter({ hasText: 'newer-context-table' }).click();
            await page.locator(`tr[data-row-id="${otherRow._row_id}"]`).click();
        }
        if (transition === 'identity') {
            await page.evaluate(() => window.r57Audit.refreshAuth());
            await waitFor(async () => (await page.evaluate(() => window.r57Audit.calls)).slice(held.start).some(c => c.path === '/users/me' && c.status === 200), 'actual same development identity refreshed');
        }
        await consume(held, restorePath);
        await assertNoRestoreSuccess(`POST ${transition}`);
        const trace = (await page.evaluate(() => window.r57Audit.calls)).slice(held.start);
        assert.equal(trace.filter(c => c.path === refreshPath).length, 0, 'invalidated restore must not start old refresh');
        const expected = transition === 'selection' ? target1._row_id : transition === 'table' ? otherRow._row_id : target2._row_id;
        if (transition === 'identity') {
            // Merged #219 remounts LibraryWorkspace on authEpoch changes; the stale
            // response can no longer target a row, and a fresh workspace must not
            // apply it. The no-success/no-stale-refresh assertions above cover it.
            await page.getByRole('heading', { name: /XeLibrary/ }).waitFor().catch(() => {});
        } else {
            await expectSelected(expected);
        }
        checks.push(`late actual restorePOST after ${transition} does not refresh or change newer context or display cancelled restore success`);
        console.log('PASS restorePOST', transition);
    }
    for (const transition of ['selection', 'ABA', 'table', 'identity']) {
        await prepare();
        const held = await hold(`**/api/v1${refreshPath}`, 200);
        await historyLine(1).getByRole('button', { name: '恢复此版本' }).click();
        await waitFor(held.ready, 'restore first-page refresh buffered');
        if (transition === 'selection' || transition === 'ABA') {
            await selectFirst();
            if (transition === 'ABA')
                await selectLast();
        }
        if (transition === 'table') {
            await page.locator('.library-tree-table').filter({ hasText: 'newer-context-table' }).click();
            await page.locator(`tr[data-row-id="${otherRow._row_id}"]`).click();
        }
        if (transition === 'identity') {
            await page.evaluate(() => window.r57Audit.refreshAuth());
            await waitFor(async () => (await page.evaluate(() => window.r57Audit.calls)).slice(held.start).some(c => c.path === '/users/me' && c.status === 200), 'identity refresh during pending firstpage');
        }
        await consume(held, refreshPath);
        await assertNoRestoreSuccess(`refresh ${transition}`);
        const expected = transition === 'selection' ? target1._row_id : transition === 'table' ? otherRow._row_id : target2._row_id;
        if (transition === 'identity') {
            // #219 remounts the workspace on authEpoch; pagination is rebuilt by the
            // fresh workspace, so only the stale-response rejection is asserted here.
        } else {
            await expectSelected(expected);
        }
        if (transition !== 'table' && transition !== 'identity')
            assert.equal(await page.getByRole('button', { name: '加载更多记录', exact: true }).count(), 0, 'stale refresh must not replace exhausted cursor');
        checks.push(`late actual first-page refresh after ${transition} preserves newer selection/table and cursor without cancelled restore success`);
        console.log('PASS refresh', transition);
    }
    let conflictingRevision = await prepare();
    assert.equal((await req(restorePath, 'POST', { revision: 1 }, { 'If-Match': `"revision:${conflictingRevision}"` })).status, 200);
    const conflictStart = await page.evaluate(() => window.r57Audit.calls.length);
    await historyLine(1).getByRole('button', { name: '恢复此版本' }).click();
    await waitFor(async () => (await page.evaluate(() => window.r57Audit.calls)).slice(conflictStart).some(c => c.path === restorePath && c.status === 412), 'actual restore revision conflict consumed');
    await page.getByRole('alert').filter({ hasText: '本地 revision 已过期' }).waitFor();
    assert.equal(await page.locator(`tr[data-row-id="${target2._row_id}"]`).getAttribute('aria-selected'), 'true');
    assert.equal((await page.evaluate(() => window.r57Audit.calls)).slice(conflictStart).filter(c => c.path === refreshPath).length, 0);
    checks.push('current genuine restore412 retains selected record and reports conflict without false refresh');
    conflictingRevision = await prepare();
    assert.equal((await req(restorePath, 'POST', { revision: 1 }, { 'If-Match': `"revision:${conflictingRevision}"` })).status, 200);
    const lateConflict = await hold(`**/api/v1${restorePath}`, 412);
    await historyLine(1).getByRole('button', { name: '恢复此版本' }).click();
    await waitFor(lateConflict.ready, 'genuine restore412 buffered');
    await selectFirst();
    await consume(lateConflict, restorePath, 412);
    await assertNoRestoreSuccess("late412");
    assert.equal(await page.locator(`tr[data-row-id="${target1._row_id}"]`).getAttribute('aria-selected'), 'true');
    assert.equal(await page.getByRole('alert').filter({ hasText: '本地 revision 已过期' }).count(), 0);
    checks.push('late genuine restore412 after new selection does not report old conflict or cancelled restore success');
    async function openNew(label){await page.getByRole('button',{name:'新增记录',exact:true}).click();await page.getByRole('tab',{name:'字段',exact:true}).click();await page.getByLabel(/^label/).fill(label);}
    async function createUI(tableId,label){await openNew(label);const start=await page.evaluate(()=>window.r57Audit.calls.length);await page.getByRole('button',{name:'创建记录',exact:true}).click();await waitFor(async()=>(await page.evaluate(()=>window.r57Audit.calls)).slice(start).some(c=>c.method==='POST'&&c.path===`/tables/${tableId}/rows`&&c.status===201),'actual UI create201 consumed');await page.getByText('记录已创建。当前平台没有自动 indexing worker；需要时请手动重建索引。',{exact:true}).waitFor();await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));const calls=(await page.evaluate(()=>window.r57Audit.calls)).slice(start);return {row:calls.find(c=>c.method==='POST'&&c.path===`/tables/${tableId}/rows`).responsePayload.data,calls};}
    const small=(await req('/tables','POST',{path:'/R57',name:'create-small-control',columns:[{name:'label',type:'string',nullable:false}]})).payload.data;
    const large=(await req('/tables','POST',{path:'/R57',name:'create-large-target',columns:[{name:'label',type:'string',nullable:false}]})).payload.data;
    for(let n=0;n<300;n++)assert.equal((await req(`/tables/${large.table_id}/rows`,'POST',{values:{label:`create setup ${n}`}})).status,201);
    await page.reload();await page.locator('.library-tree-table').filter({hasText:'create-small-control'}).click();const smallCreated=await createUI(small.table_id,'small UI-created');assert.equal(await page.locator(`tr[data-row-id="${smallCreated.row._row_id}"]`).getAttribute('aria-selected'),'true');assert.equal(await page.locator('.inspector-head strong').textContent(),'记录 Inspector');assert.equal((await req(`/tables/${small.table_id}/rows/${smallCreated.row._row_id}`)).payload.data.values.label,'small UI-created');await page.screenshot({path:path.join(evidence,'create-small-control.png'),fullPage:true});checks.push('small-table UIcreate201 stays selected with correct record Inspector and detail200');
    await page.locator('.library-tree-table').filter({hasText:'create-large-target'}).click();let largeCreated,largeFirst;
    for(let attempt=1;attempt<=12;attempt++){largeCreated=await createUI(large.table_id,`large UI-created ${attempt}`);largeFirst=(await req(`/tables/${large.table_id}/rows?limit=100`)).payload;if(!largeFirst.data.some(r=>r._row_id===largeCreated.row._row_id))break;if(attempt===12)throw Error('legal random-created IDs never landed outside first100 within bounded fixture attempts');}
    assert.equal(largeFirst.data.length,100);assert.ok(largeFirst.meta.next_cursor);const largeDetail=await req(`/tables/${large.table_id}/rows/${largeCreated.row._row_id}`);assert.equal(largeDetail.status,200);assert.equal(largeDetail.payload.data.values.label,largeCreated.row.values.label);
    const createProof={largeCreated,largeFirst,largeDetail,selectedCount:await page.locator(`tr[data-row-id="${largeCreated.row._row_id}"]`).count(),inspector:await page.locator('.record-editor').innerText()};await fs.writeFile(path.join(evidence,'create-pagination.json'),JSON.stringify(createProof,null,2));await page.screenshot({path:path.join(evidence,'create-off-page-target.png'),fullPage:true});assert.equal(createProof.selectedCount,1,'successful UI creation beyond first100 must retain target');assert.equal(await page.locator(`tr[data-row-id="${largeCreated.row._row_id}"]`).getAttribute('aria-selected'),'true');assert.equal(await page.locator('.inspector-head strong').textContent(),'记录 Inspector');checks.push('off-page UIcreate201/detail200 remains selected with values bound to exact returned ID');
    while(await page.getByRole('button',{name:'加载更多记录',exact:true}).count()){const cursorStart=await page.evaluate(()=>window.r57Audit.calls.length);await page.getByRole('button',{name:'加载更多记录',exact:true}).click();await waitFor(async()=>(await page.evaluate(()=>window.r57Audit.calls)).slice(cursorStart).some(c=>c.path.includes(`/tables/${large.table_id}/rows?`)&&c.path.includes('cursor=')&&c.status===200),'create cursor response consumed');await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));}
    const finalLarge=(await req(`/tables/${large.table_id}/rows?limit=1000`)).payload.data;assert.equal(await page.getByText(`${finalLarge.length} / ${finalLarge.length} 行已加载`,{exact:true}).count(),1);assert.equal(await page.locator(`tr[data-row-id="${largeCreated.row._row_id}"]`).count(),1);checks.push('created retained target preserves next_cursor and normal continuation deduplicates to genuine final page');
    for(const phase of ['POST','refresh']) for(const transition of ['selection','ABA','table','identity','client','unmount']) {
        await page.reload();await page.locator('.library-tree-table').filter({hasText:'create-small-control'}).click();await page.locator(`tr[data-row-id="${smallCreated.row._row_id}"]`).waitFor();
        await openNew(`delayed create ${phase} ${transition}`);
        const responsePath=phase==='POST'?`/tables/${small.table_id}/rows`:`/tables/${small.table_id}/rows?limit=100`;
        const held=await hold(`**/api/v1${responsePath}`,phase==='POST'?201:200);
        await page.getByRole('button',{name:'创建记录',exact:true}).click();await waitFor(held.ready,'genuine create response buffered');
        if(transition==='selection'||transition==='ABA') {await page.locator(`tr[data-row-id="${smallCreated.row._row_id}"]`).click();if(transition==='ABA')await page.getByRole('button',{name:'新增记录',exact:true}).click();}
        if(transition==='table'){await page.locator('.library-tree-table').filter({hasText:'newer-context-table'}).click();await page.locator(`tr[data-row-id="${otherRow._row_id}"]`).click();}
        if(transition==='identity'){await page.evaluate(()=>window.r57Audit.refreshAuth());await waitFor(async()=>(await page.evaluate(()=>window.r57Audit.calls)).slice(held.start).some(c=>c.path==='/users/me'&&c.status===200),'create identity refresh');}
        if(transition==='client'){await page.evaluate(()=>window.r57Audit.swapClient());await waitFor(async()=>(await page.evaluate(()=>window.r57Audit.calls)).slice(held.start).some(c=>c.path==='/users/me'&&c.status===200),'actual replacement API identity read');}
        if(transition==='unmount')await page.evaluate(()=>window.r57Audit.mount(false));
        await consume(held,responsePath,phase==='POST'?201:200);
        if(transition==='unmount'){await page.evaluate(()=>window.r57Audit.mount(true));await page.locator('.library-tree-table').filter({hasText:'create-small-control'}).waitFor();}

        assert.equal(await page.locator('.record-editor').getByText('记录已创建',{exact:true}).count(),0,'cancelled create cannot report success');
        assert.equal(await page.getByText('记录已创建。当前平台没有自动 indexing worker；需要时请手动重建索引。',{exact:true}).count(),0,'cancelled create cannot report Library success');
        if(transition==='selection'||transition==='table'){const expected=transition==='selection'?smallCreated.row._row_id:otherRow._row_id;assert.equal(await page.locator(`tr[data-row-id="${expected}"]`).getAttribute('aria-selected'),'true');}
        else assert.equal(await page.locator('.table-scroll tr[aria-selected="true"]').count(),0,'cancelled create cannot activate returned ID');
        if(phase==='POST')assert.equal((await page.evaluate(()=>window.r57Audit.calls)).slice(held.start).filter(c=>c.path===`/tables/${small.table_id}/rows?limit=100`).length,0,'cancelled POST cannot start old refresh');
        checks.push(`late genuine create ${phase} after ${transition} preserves newer context without activation or success`);
    }
    for(const late of [false,true]){
        const name=`create-error-${late?'late':'current'}`;
        const empty=(await req('/tables','POST',{path:'/R57',name,columns:[{name:'label',type:'string',nullable:false}]})).payload.data;
        await page.reload();await page.locator('.library-tree-table').filter({hasText:name}).click();await openNew('legal failure control');
        assert.equal((await req(`/tables/${empty.table_id}`,'DELETE',undefined,{'If-Match':'"revision:1"'})).status,200,'normal deletion of own empty fixture table');
        const errorPath=`/tables/${empty.table_id}/rows`,held=await hold(`**/api/v1${errorPath}`,404);
        await page.getByRole('button',{name:'创建记录',exact:true}).click();await waitFor(held.ready,'genuine missing table create404 buffered');
        if(late){await page.locator('.library-tree-table').filter({hasText:'newer-context-table'}).click();await page.locator(`tr[data-row-id="${otherRow._row_id}"]`).click();}
        await consume(held,errorPath,404);
        assert.equal(await page.locator('.record-editor').getByText('记录已创建',{exact:true}).count(),0);
        if(late){assert.equal(await page.locator(`tr[data-row-id="${otherRow._row_id}"]`).getAttribute('aria-selected'),'true');assert.equal(await page.locator('.record-editor [role=alert]').count(),0);}
        else assert.ok(await page.locator('.record-editor [role=alert]').count(),'current actual create404 must report error');
        checks.push(late?'late genuine create404 after table change preserves new record without old error or false success':'current genuine create404 remains a visible error without false success');
    }
    const calls = await page.evaluate(() => window.r57Audit.calls);
    const historyFlow = calls.slice(restoreStart).filter(c => c.path.includes(target2._row_id) || c.path.includes(`/tables/${tid}/rows?`)).map(c => ({ method: c.method, path: c.path, status: c.status, ifMatch: c.headers['if-match'], responseDataCount: c.responsePayload?.data?.length, responseRevision: c.responsePayload?.data?._revision, responseDeletedAt: c.responsePayload?.data?._deleted_at, nextCursor: c.responsePayload?.meta?.next_cursor }));
    const report = { baseline: '865a72277008a5773236b50b993bacdecc78d6ab', branch: 'codex/general-issue229-history-restore-page2-20261003', sourceRoot: source, services: 'Actual xeonolab.library FastAPI and desktop identity API; both running from this implementation worktree in fresh temporary directories on 127.0.0.1. Vite root is apps/desktop-web at same source baseline; real LibraryApp, TableView, Inspector rendered in Chrome.', identity: 'Actual local Desktop users/me for explicitly configured synthetic development identity round57-local-admin; no production/OIDC/private profile/credential.', data: 'Restore table has 101 legal synthetic rows; additional Create small/300-row tables and temporary empty error-control tables are created through normal local APIs. Two rows were soft-deleted via normal conditional DELETE API setup. Not production and no direct database access.', path: ['Opened table in actual LibraryApp and clicked a visible row to create normal persisted Recent entry.', 'Reloaded UI and opened the same table through Recent; actual session loaded include_deleted=true with a 100-row first page and cursor.', 'Restored a soft-deleted row in first page through Inspector History as positive control.', 'Used actual Load More, selected the deleted row in page two, opened History, restored revision 1 with current revision 2.'], observed: { tableId: tid, initialListCounts: { page1: page1.payload.data.length, page2: page2.payload.data.length, firstCursor: page1.payload.meta.next_cursor, page2Cursor: page2.payload.meta.next_cursor }, control: { rowId: target1._row_id, label: target1.values.label, historyBefore: controlHistory.payload.data.map(r => ({ revision: r._revision, deletedAt: r._deleted_at })), apiAfter: controlFinal.payload.data, ui: controlUi }, page2: { rowId: target2._row_id, label: target2.values.label, before: beforeRestore, after: page2After, apiAfter: apiRestored.payload.data, historyAfter: historyAfter.payload.data.map(r => ({ revision: r._revision, deletedAt: r._deleted_at })), includeDeletedFirstPageAfter: { count: listAfter.payload.data.length, containsTarget: listAfter.payload.data.some(r => r._row_id === target2._row_id) }, browserRequests: historyFlow, loadMoreTrace: loadTrace }, errors }, outcome: '', limitations: 'This is a local functional reproduction only; it does not assert production rollout or external worker behavior.' };
    report.create={small:smallCreated,large:createProof,finalCount:finalLarge.length};
    report.checks = checks;
    report.mode = mode;
    report.sourceHashes = {};
    for (const f of ['src/library/LibraryApp.tsx', 'src/library/Inspector.tsx', 'src/library/hooks.ts', 'src/library/logic.js'])
        report.sourceHashes[f] = createHash('sha256').update(await fs.readFile(path.join(appRoot, f))).digest('hex');
    report.outcome = page2After.targetCount === 0 && /新建记录/.test(page2After.inspectorHeading || '') && apiRestored.payload.data._revision === 3 ? 'Page-two History restore succeeds at the API but refreshes the UI to only the first page. The restored target is no longer loaded, selected row count is zero, and Inspector returns to new-record state; first-page positive-control restore remains selected.' : 'First-page and page-two History restore retain the exact selected record and revision; cursor continuation reaches the true end without duplicates.';
    assert.equal(errors.length, 0, errors.join('; '));
    await fs.writeFile(path.join(evidence, 'browser-report.json'), JSON.stringify(report, null, 2) + '\n');
    await fs.writeFile(path.join(evidence, 'run.log'), JSON.stringify(report, null, 2) + '\n');
    console.log(JSON.stringify(report, null, 2));
}
finally {
    if (browser)
        await browser.close();
    if (vite)
        await vite.close();
    await stop(lib);
    await stop(desktop);
    await fs.rm(temp, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 });
    await Promise.all([fixtureTsx, fixtureHtml].map(f => fs.rm(f, { force: true })));
}
