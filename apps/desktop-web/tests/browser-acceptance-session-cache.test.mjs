import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import { existsSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { createServer } from 'vite';
import { chromium } from '@playwright/test';
const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = path.resolve(appRoot, '../..');
const evidence = path.join(repoRoot, 'runtime/verification/issue219');
const A = 'cycle5-user-a-synthetic', B = 'cycle5-user-b-synthetic';
const scopesA = ['xelibrary:read', 'xelibrary:write', 'xelibrary:query', 'xelibrary:attach', 'xelibrary:admin', 'xelibrary:theorem:read', 'xelibrary:theorem:propose'];
async function freePort() {
    const s = net.createServer();
    await new Promise((resolve, reject) => s.listen(0, '127.0.0.1', resolve).once('error', reject));
    const port = s.address().port;
    await new Promise(resolve => s.close(resolve));
    return port;
}
test('Library isolates verified workspaces across identity changes and late reads', async (t) => {
    const executablePath = process.env.CHROME_BIN || chromium.executablePath();
    if (!existsSync(executablePath))
        return t.skip(`Chromium missing: ${executablePath}`);
    const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'xelibrary-session-cache-'));
    await fs.mkdir(evidence, { recursive: true });
    const apiPort = await freePort(), manifestPath = path.join(temp, 'manifest.json');
    let browser, vite, apiProcess, hold, failIdentity = false;
    const releases = [], requests = [], checks = [], pageErrors = [], externalRequests = [];
    const holdNext = predicate => {
        let started, release;
        const seen = new Promise(resolve => { started = resolve; });
        const gate = new Promise(resolve => { release = resolve; });
        releases.push(release);
        hold = { predicate, started, gate };
        return { seen, release };
    };
    try {
        apiProcess = spawn('uv', ['run', 'python', 'apps/desktop-web/tests/session-cache-local-api.py', '--data-dir', path.join(temp, 'data'), '--manifest', manifestPath, '--port', String(apiPort)], { cwd: repoRoot, stdio: ['ignore', 'pipe', 'pipe'] });
        let stderr = '', ready = false, spawnFailure;
        apiProcess.on('error', error => { spawnFailure = error; });
        apiProcess.stderr.on('data', data => { stderr += data.toString(); });
        for (let i = 0; i < 200; i++) {
            if (spawnFailure)
                throw new Error(`Local API could not start: ${spawnFailure.message}`);
            if (apiProcess.exitCode !== null)
                throw new Error(`API exited: ${stderr}`);
            try {
                if ((await fetch(`http://127.0.0.1:${apiPort}/health/live`)).ok) {
                    ready = true;
                    break;
                }
            }
            catch { }
            await new Promise(resolve => setTimeout(resolve, 100));
        }
        assert(ready, stderr);
        const manifest = JSON.parse(await fs.readFile(manifestPath, 'utf8'));
        const apiURL = `http://127.0.0.1:${apiPort}/api/v1`;
        const read = (token, p) => fetch(`${apiURL}${p}`, { headers: { Authorization: `Bearer ${token}` } });
        assert.equal((await read(B, `/tables/${manifest.table_id}/rows/${manifest.row_id}`)).status, 403);
        assert.deepEqual((await read(B, '/tables?limit=100').then(r => r.json())).data.map(t => t.id), [manifest.shared_id]);
        checks.push('real ACL denies B private row; B catalogue contains only granted table');
        const vitePort = await freePort();
        vite = await createServer({ root: appRoot, cacheDir: path.join(temp, 'vite-cache'), optimizeDeps: { noDiscovery: true, include: ['react', 'react-dom', 'react-dom/client', 'react/jsx-runtime', 'react/jsx-dev-runtime'] }, server: { host: '127.0.0.1', port: vitePort, strictPort: true, allowedHosts: true, watch: null, hmr: false } });
        await vite.listen();
        browser = await chromium.launch({ headless: true, executablePath, args: ['--no-first-run', '--no-default-browser-check', '--no-proxy-server'] });
        const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
        await context.route('**/*', async (route) => {
            const request = route.request(), url = new URL(request.url());
            if (!['127.0.0.1', 'library.xenon.lan', 'xenon.jyxxe.com'].includes(url.hostname)) {
                externalRequests.push(request.url());
                return route.abort();
            }
            if (!url.pathname.startsWith('/api/v1')) {
                url.protocol = 'http:';
                url.hostname = '127.0.0.1';
                url.port = String(vitePort);
                return route.fulfill({ response: await route.fetch({ url: url.toString() }) });
            }
            const p = url.pathname.slice('/api/v1'.length), token = (request.headers().authorization || '').replace(/^Bearer /, '');
            requests.push({ path: p, token, method: request.method() });
            let response;
            if (p === '/auth/session/token')
                response = { status: 200, body: JSON.stringify({ token: A, expires: 600 }) };
            else if (p === '/users/me')
                response = failIdentity ? { status: 503, body: JSON.stringify({ error: { code: 'SYNTHETIC_IDENTITY_FAILURE', message: 'Synthetic identity unavailable', request_id: 'synthetic-identity-id' } }) } : { status: 200, body: JSON.stringify({ data: { subject: token === A ? 'cycle5-user-a' : token === B ? 'cycle5-user-b' : null, scopes: token === A ? scopesA : ['xelibrary:read', 'xelibrary:query'] }, meta: {} }) };
            else
                response = { response: await route.fetch({ url: `${apiURL}${p}${url.search}` }) };
            const current = hold;
            if (current?.predicate({ path: p, token, method: request.method() })) {
                hold = null;
                current.started();
                await current.gate;
            }
            return route.fulfill({ ...response, contentType: 'application/json' });
        });
        const page = await context.newPage();
        page.on('pageerror', e => pageErrors.push(e.message));
        await page.goto(`http://127.0.0.1:${vitePort}/tests/session-cache-fixture.html`);
        let surface = page;
        const change = user => page.getByRole('button', { name: `Use synthetic ${user}`, exact: true }).click();
        const privateTable = () => surface.locator('button.library-tree-table').filter({ hasText: 'user A private table' });
        const bTable = () => surface.locator('button.library-tree-table').filter({ hasText: 'B readable table' });
        const openPrivate = async () => { await privateTable().click(); await surface.getByText('A_ONLY_CONFIDENTIAL_ROW', { exact: true }).waitFor(); };
        const noA = async () => {
            await surface.waitForFunction(() => !document.querySelector('.knowledge')?.textContent.includes('user A private table') && !document.querySelector('.knowledge')?.textContent.includes('A_ONLY_CONFIDENTIAL_ROW'));
            assert.equal(await surface.locator('.library-inspector').count(), 0);
            assert.equal(await surface.locator('.library-table-head').count(), 0);
            assert.equal(await surface.locator('.ds-modal').count(), 0);
        };
        const settledCount = pathname => page.evaluate(p => window.__settledRequests.filter(r => r.path === p && r.token === 'cycle5-user-a-synthetic').length, pathname);
        const waitSettled = (pathname, previous) => page.waitForFunction(({ p, n }) => window.__settledRequests.filter(r => r.path === p && r.token === 'cycle5-user-a-synthetic').length > n, { p: pathname, n: previous });
        await openPrivate();
        await page.getByText('A_ONLY_CONFIDENTIAL_ROW', { exact: true }).click();
        await page.getByRole('tab', { name: '字段', exact: true }).click();
        assert.equal(await page.locator('.inspector-field input').first().inputValue(), 'A_ONLY_CONFIDENTIAL_ROW');
        await page.screenshot({ path: path.join(evidence, 'user-a-view.png'), fullPage: true });
        const identity = holdNext(r => r.path === '/users/me' && r.token === B);
        await change('B');
        await identity.seen;
        await page.getByRole('status').filter({ hasText: '正在确认知识库身份' }).waitFor();
        await noA();
        assert.equal(requests.filter(r => r.token === B && r.path !== '/users/me').length, 0);
        await page.screenshot({ path: path.join(evidence, 'identity-pending.png'), fullPage: true });
        identity.release();
        await bTable().waitFor();
        await noA();
        await bTable().click();
        await page.getByText('B_READABLE_ROW', { exact: true }).waitFor();
        await page.screenshot({ path: path.join(evidence, 'user-b-view.png'), fullPage: true });
        checks.push('pending identity hides resources before B verification; B granted row loads');
        await change('A');
        await openPrivate();
        await page.getByText('A_ONLY_CONFIDENTIAL_ROW', { exact: true }).click();
        checks.push('A to B to A reloads current catalogue');
        const rowsPath = `/tables/${manifest.table_id}/rows`, rowsBefore = await settledCount(rowsPath);
        const lateRows = holdNext(r => r.path === `/tables/${manifest.table_id}/rows` && r.token === A);
        await page.locator('.library-mini-list button').filter({ hasText: manifest.row_id.slice(0, 8) }).first().click();
        await lateRows.seen;
        await change('B');
        await noA();
        lateRows.release();
        await waitSettled(rowsPath, rowsBefore);
        await bTable().waitFor();
        assert.equal(requests.filter(r => r.token === B && r.path.startsWith(`/tables/${manifest.table_id}`)).length, 0);
        await noA();
        checks.push('late row discarded; expired navigation cannot follow up with B token');
        const catalogueBefore = await settledCount('/tables');
        const lateCatalogue = holdNext(r => r.path === '/tables' && r.token === A);
        await change('A');
        await lateCatalogue.seen;
        await change('B');
        await bTable().waitFor();
        lateCatalogue.release();
        await waitSettled('/tables', catalogueBefore);
        await noA();
        checks.push('late A catalogue body fully decoded but cannot repopulate B workspace');
        await change('A');
        await privateTable().waitFor();
        await page.locator('.library-nav button').filter({ hasText: '搜索' }).click();
        await page.getByLabel('关键词优先', { exact: false }).check();
        await page.getByRole('searchbox', { name: '搜索 XeLibrary', exact: true }).fill('A_ONLY_CONFIDENTIAL_ROW');
        const searchBefore = await settledCount('/search/text');
        const lateSearch = holdNext(r => r.path === '/search/text' && r.token === A);
        await page.locator('.library-search-bar').getByRole('button', { name: '搜索', exact: true }).click();
        await lateSearch.seen;
        await change('B');
        await bTable().waitFor();
        lateSearch.release();
        await waitSettled('/search/text', searchBefore);
        await noA();
        assert.equal(await page.locator('.library-search').count(), 0);
        checks.push('late real keyword-search response cannot revive A hits or search UI');
        await change('A');
        await privateTable().waitFor();
        const theoremPath = `/theorems/${manifest.theorem_id}`, theoremsBefore = await settledCount(theoremPath);
        const lateTheorem = holdNext(r => r.path === `/theorems/${manifest.theorem_id}` && r.token === A);
        await page.locator('.library-nav button').filter({ hasText: '定理' }).click();
        await page.getByRole('button').filter({ hasText: 'A_PRIVATE_THEOREM' }).click();
        await lateTheorem.seen;
        await change('B');
        lateTheorem.release();
        await waitSettled(theoremPath, theoremsBefore);
        await bTable().waitFor();
        assert.equal(requests.filter(r => r.token === B && r.path.startsWith(`/theorems/${manifest.theorem_id}/`)).length, 0);
        assert.equal((await page.locator('.knowledge').innerText()).includes('A_PRIVATE_THEOREM'), false);
        checks.push('late theorem details cannot expose evidence or start relation reads for B');
        await change('A');
        await openPrivate();
        const same = holdNext(r => r.path === '/users/me' && r.token === A);
        await change('A');
        await same.seen;
        await noA();
        same.release();
        await privateTable().waitFor();
        assert.equal(await surface.locator('.library-table-head').count(), 0);
        checks.push('same-subject new epoch discards active resources');
        await openPrivate();
        failIdentity = true;
        await change('B');
        await page.getByRole('alert').filter({ hasText: '无法确认知识库身份' }).waitFor();
        await noA();
        assert.match(await page.getByRole('alert').innerText(), /synthetic-identity-id/);
        await page.screenshot({ path: path.join(evidence, 'identity-error.png'), fullPage: true });
        failIdentity = false;
        await page.getByRole('button', { name: '重试', exact: true }).click();
        await bTable().waitFor();
        await noA();
        checks.push('identity failure shows request ID; manual retry recovers');
        // Browser generates origin/source from a real iframe parent; every physical
        // asset/API request still terminates on the local synthetic environment.
        await page.goto('https://xenon.jyxxe.com/tests/session-cache-parent.html');
        surface = page.frames().find(frame => frame.url().startsWith('https://library.xenon.lan'));
        assert(surface, 'real child Library iframe exists');
        await openPrivate();
        await surface.getByText('A_ONLY_CONFIDENTIAL_ROW', { exact: true }).click();
        await surface.getByRole('button', { name: 'Ask AI', exact: true }).click();
        await surface.getByRole('dialog').waitFor();
        assert.match(await surface.getByRole('dialog').locator('textarea').last().inputValue(), /A_ONLY_CONFIDENTIAL_ROW/);
        const prod = holdNext(r => r.path === '/users/me' && r.token === B);
        await page.getByRole('button', { name: 'Switch child to synthetic B', exact: true }).click();
        await prod.seen;
        await noA();
        prod.release();
        await bTable().waitFor();
        await noA();
        await page.screenshot({ path: path.join(evidence, 'production-main-user-b.png'), fullPage: true });
        checks.push('production main native postMessage from real virtual trusted parent reloads B catalogue');
        assert.deepEqual(pageErrors, []);
        assert.deepEqual(externalRequests, []);
        await fs.writeFile(path.join(evidence, 'browser-report.json'), JSON.stringify({ backend: 'real isolated Library API', identity: 'fixed synthetic dev tokens; users/me/auth-session stubbed', entry: 'actual LibraryApp stable API fixture and production main', checks, pageErrors, externalRequests, requests }, null, 2));
    }
    catch (error) {
        await fs.writeFile(path.join(evidence, 'failure.json'), JSON.stringify({ message: error.message, settled: await browser?.contexts()[0]?.pages().at(-1)?.evaluate(() => window.__settledRequests), pageErrors, externalRequests, requests }, null, 2));
        await browser?.contexts()[0]?.pages().at(-1)?.screenshot({ path: path.join(evidence, 'failure.png'), fullPage: true }).catch(() => { });
        throw error;
    }
    finally {
        releases.forEach(release => release());
        await browser?.close();
        await vite?.close();
        if (apiProcess?.pid && apiProcess.exitCode === null) {
            apiProcess.kill('SIGTERM');
            await new Promise(resolve => apiProcess.once('exit', resolve));
        }
        await fs.rm(temp, { recursive: true, force: true });
    }
});
