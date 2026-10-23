import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import net from 'node:net';
import {spawn} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium, expect} from '@playwright/test';
import {parseExactJSON} from '../src/library/json.js';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repositoryRoot = path.resolve(appRoot, '../..');
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => server.listen(0, '127.0.0.1', resolve).once('error', reject));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

test('ordinary row clicks protect Inspector drafts without changing the real API save contract', {timeout: 120000}, async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'xel-inspector-drafts-'));
  const evidence = path.join(repositoryRoot, 'runtime/verification/issue245');
  await fs.mkdir(evidence, {recursive: true});
  const port = await freePort();
  const apiOrigin = `http://127.0.0.1:${port}`;
  const manifestPath = path.join(temp, 'manifest.json');
  const apiProcess = spawn('uv', ['run', 'python', 'apps/desktop-web/tests/inspector-draft-local-api.py', '--data-dir', path.join(temp, 'data'), '--manifest', manifestPath, '--port', String(port)], {cwd: repositoryRoot, stdio: ['ignore', 'pipe', 'pipe']});
  let stderr = '', startupError;
  apiProcess.once('error', error => {startupError = error;});
  apiProcess.stderr.on('data', data => {stderr += data.toString();});
  let server, browser, page;
  const requests = [], pageErrors = [], external = [], checks = [];
  try {
    let ready = false;
    for (let attempt = 0; attempt < 200; attempt += 1) {
      if (startupError) throw startupError;
      if (apiProcess.exitCode !== null) throw new Error(`Local API exited: ${stderr}`);
      try { if ((await fetch(`${apiOrigin}/health/live`)).ok) {ready = true; break;} } catch {}
      await sleep(100);
    }
    assert.ok(ready, `Local API did not start: ${stderr}`);
    const manifest = JSON.parse(await fs.readFile(manifestPath, 'utf8'));
    const [a, b, c] = manifest.row_ids;
    const rowPath = id => `/api/v1/tables/${manifest.table_id}/rows/${id}`;
    const readRow = async id => {
      const response = await fetch(`${apiOrigin}${rowPath(id)}`, {headers: {Authorization: `Bearer ${manifest.token}`}});
      assert.equal(response.status, 200);
      return parseExactJSON(await response.text()).data;
    };
    server = await createServer({root: appRoot, cacheDir: path.join(temp, 'vite-cache'), optimizeDeps: {noDiscovery: true, include: ['react', 'react-dom', 'react-dom/client', 'react/jsx-runtime', 'react/jsx-dev-runtime']}, server: {host: '127.0.0.1', port: 0, watch: null, hmr: false, proxy: {'/api/v1': apiOrigin}}});
    await server.listen();
    const viteOrigin = new URL(server.resolvedUrls.local[0]).origin;
    const browserOrigin = `http://library.xenon.lan:${server.httpServer.address().port}`;
    browser = await chromium.launch({executablePath, headless: true});
    const context = await browser.newContext({viewport: {width: 1440, height: 1050}});
    await context.route('**/*', async route => {
      const request = route.request(), url = new URL(request.url());
      if (url.origin !== browserOrigin) {external.push(request.url()); return route.abort();}
      if (url.pathname.startsWith('/api/v1/')) requests.push({method: request.method(), path: url.pathname, body: request.postData(), ifMatch: request.headers()['if-match']});
      const json = value => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(value)});
      if (url.pathname === '/api/v1/auth/session/token') return json({token: manifest.token, expires: 600});
      if (url.pathname === '/api/v1/users/me') return json({data: {subject: 'issue245-fixture-owner', scopes: ['xelibrary:read', 'xelibrary:write', 'xelibrary:query', 'xelibrary:attach']}, meta: {}});
      // LibraryApp also syncs the account preferences; answer it so the shell
      // does not fall back to the SPA HTML and keep it out of the row checks.
      if (url.pathname === '/api/v1/users/me/preferences') return json({data: {}, meta: {}});
      return route.fulfill({response: await route.fetch({url: `${viteOrigin}${url.pathname}${url.search}`})});
    });
    page = await context.newPage();
    page.on('pageerror', error => pageErrors.push(error.message));
    const row = id => page.locator(`tr[data-row-id="${id}"]`);
    const field = name => page.locator('.inspector-field').filter({has: page.locator('.inspector-field-name').filter({hasText: new RegExp(`^${name}`)})}).locator('input,textarea,select');
    const modal = page.getByRole('dialog', {name: '未保存的字段修改', exact: true});
    const patches = () => requests.filter(request => request.method === 'PATCH' && request.path.includes('/tables/'));
    const active = id => expect(row(id)).toHaveClass(/is-active/);
    const save = async status => {
      const pending = page.waitForResponse(response => response.request().method() === 'PATCH' && new URL(response.url()).pathname === rowPath(a));
      await page.getByRole('button', {name: '保存记录', exact: true}).click();
      const response = await pending;
      assert.equal(response.status(), status, await response.text());
    };
    await page.goto(browserOrigin);
    await page.getByRole('button', {name: '刷新知识表目录'}).click();
    await page.locator('button.library-tree-table').click();
    await row(a).click();
    await page.getByRole('tab', {name: '字段', exact: true}).click();
    await expect(field('title')).toHaveValue('Source A');
    await row(b).click();
    await active(b);
    await expect(modal).toHaveCount(0);
    await row(a).click();
    await field('title').fill('Draft A');
    // The baseline control intentionally runs only the concrete lost-draft case.
    if (process.env.ISSUE245_BASELINE_CONTROL === '1') {
      await row(b).click();
      await active(b);
      await row(a).click();
      await expect(field('title')).toHaveValue('Source A');
      assert.equal(patches().length, 0);
      assert.equal((await readRow(a))._revision, 1);
      assert.equal((await readRow(b))._revision, 1);
      checks.push('negative control: ordinary B click drops A draft, API unchanged');
    } else {
      // Use real pointer clicks and input fill immediately after B→A. A reset
      // and the single edit can batch while changed.length stays exactly one.
      // Do not hide that race by waiting for an intermediate clean draft first.
      for (let attempt = 0; attempt < 6; attempt += 1) {
        const title = attempt ? `Rapid draft ${attempt}` : 'Draft A';
        if (attempt) {
          await row(b).click();
          await modal.getByRole('button', {name: '放弃修改并切换', exact: true}).click();
          await row(a).click();
          await field('title').fill(title);
        }
        await expect(field('title')).toHaveValue(title);
        await row(b).click();
        await expect(modal).toBeVisible();
        await active(a);
        await modal.getByRole('button', {name: '继续编辑', exact: true}).click();
        await expect(field('title')).toHaveValue(title);
      }
      assert.equal(patches().length, 0);
      assert.equal((await readRow(a))._revision, 1);
      assert.equal((await readRow(b))._revision, 1);
      checks.push('Six native pointer/fill B→A single-field edits keep dirty protection even when the changed-field count stays one');
      // Checkbox selection does not change the active row or its draft.
      await row(b).getByRole('checkbox').click();
      await active(a);
      await expect(page.locator('tr[aria-selected="true"]')).toHaveCount(2);
      const edits = {title: 'Draft A', notes: 'draft\nnotes', external_id: '-9223372036854775808', payload: '{"number":9007199254740993,"nested":{"minimum":-9223372036854775808}}', ratio: '2.5', optional: 'optional draft', day: '2026-10-04', moment: '2026-10-04T01:02:03Z', attachments: '["synthetic-attachment"]', status: 'draft-status', related: 'draft-reference', theorem: 'draft-theorem'};
      for (const [name, value] of Object.entries(edits)) await field(name).fill(value);
      await field('flag').selectOption('true');
      const values = async () => Object.fromEntries(await Promise.all([...Object.keys(edits), 'flag'].map(async name => [name, await field(name).inputValue()])));
      const draft = await values();
      await field('title').focus();
      await field('title').evaluate(element => element.setSelectionRange(2, 5, 'forward'));
      const beforeSelection = await page.locator('tr[aria-selected="true"]').evaluateAll(rows => rows.map(element => element.dataset.rowId));
      await row(b).click();
      await expect(modal).toBeVisible();
      await expect(modal).toHaveAttribute('aria-modal', 'true');
      await expect(modal.getByRole('button', {name: '关闭对话框', exact: true})).toBeFocused();
      await page.keyboard.press('Shift+Tab');
      await expect(modal.getByRole('button', {name: '放弃修改并切换', exact: true})).toBeFocused();
      await page.keyboard.press('Tab');
      await expect(modal.getByRole('button', {name: '关闭对话框', exact: true})).toBeFocused();
      await active(a);
      assert.deepEqual(await page.locator('tr[aria-selected="true"]').evaluateAll(rows => rows.map(element => element.dataset.rowId)), beforeSelection);
      await page.screenshot({path: path.join(evidence, 'dirty-confirmation.png'), fullPage: true});
      await modal.getByRole('button', {name: '继续编辑', exact: true}).click();
      await expect(modal).toHaveCount(0);
      assert.deepEqual(await values(), draft);
      await expect(field('title')).toBeFocused();
      assert.deepEqual(await field('title').evaluate(element => [element.selectionStart, element.selectionEnd, element.selectionDirection]), [2, 5, 'forward']);
      await row(a).click();
      await expect(modal).toHaveCount(0);
      assert.deepEqual(await values(), draft);
      checks.push('all types, null source, unsafe int64/JSON, same row, canceled row/selection/anchor and pre-blur caret');

      // Escape, header close, and backdrop use the same cancel semantics.
      for (const cancel of ['escape', 'close', 'backdrop']) {
        await field('notes').focus();
        await field('notes').evaluate(element => element.setSelectionRange(1, 4));
        await row(b).click();
        await expect(modal).toBeVisible();
        if (cancel === 'escape') await page.keyboard.press('Escape');
        else if (cancel === 'close') await modal.getByRole('button', {name: '关闭对话框', exact: true}).click();
        else await page.locator('.ds-modal-backdrop').click({position: {x: 5, y: 5}});
        await expect(modal).toHaveCount(0);
        await expect(field('notes')).toBeFocused();
        assert.deepEqual(await values(), draft);
        await active(a);
      }
      assert.equal(patches().length, 0);
      assert.equal((await readRow(a))._revision, 1);
      assert.equal((await readRow(b))._revision, 1);
      checks.push('all cancel paths preserve draft and focus; no PATCH; A/B revision 1 readback');

      // A pending ordinary click does not move the existing Shift-click anchor.
      const listResponse = await fetch(`${apiOrigin}/api/v1/tables/${manifest.table_id}/rows?limit=100`, {headers: {Authorization: `Bearer ${manifest.token}`}});
      const loadedIds = parseExactJSON(await listResponse.text()).data.map(item => item._row_id);
      const [from, to] = [loadedIds.indexOf(a), loadedIds.indexOf(c)].sort((left, right) => left - right);
      const expectedRange = loadedIds.slice(from, to + 1).sort();
      await row(c).click({modifiers: ['Shift']});
      assert.deepEqual((await page.locator('tr[aria-selected="true"]').evaluateAll(rows => rows.map(element => element.dataset.rowId))).sort(), expectedRange);
      await expect(modal).toHaveCount(0);
      await row(a).click();
      await active(a);
      await expect(field('title')).toHaveValue('Source A');
      await field('title').fill('discard me');
      await expect(page.getByText('将提交字段：title', {exact: true})).toBeVisible();
      await row(b).click();
      await modal.getByRole('button', {name: '放弃修改并切换', exact: true}).click();
      await active(b);
      await expect(field('title')).toHaveValue('Target B');
      await row(a).click();
      await expect(field('title')).toHaveValue('Source A');
      assert.equal(patches().length, 0);
      await field('title').fill('temporary edit');
      await field('title').fill('Source A');
      await row(b).click();
      await active(b);
      await expect(modal).toHaveCount(0);
      await row(a).click();
      await expect(field('title')).toHaveValue('Source A');
      await row(b).click({modifiers: ['Control']});
      await expect(page.locator('tr[aria-selected="true"]')).toHaveCount(2);
      await row(a).getByRole('checkbox').click();
      await expect(page.locator('tr[aria-selected="true"]')).toHaveCount(1);
      await active(b);
      const bIndex = loadedIds.indexOf(b);
      const nextId = loadedIds[bIndex > 0 ? bIndex - 1 : bIndex + 1];
      await page.getByRole('grid').focus();
      await page.keyboard.press(bIndex > 0 ? 'ArrowUp' : 'ArrowDown');
      await active(nextId);
      await row(a).click();
      await page.getByRole('tab', {name: '字段', exact: true}).click();
      await expect(modal).toHaveCount(0);
      checks.push('discard applies original ordinary selection; original Shift anchor, Ctrl, checkbox and keyboard behavior');

      // Even invalid, unsubmitted text stays in the same editor on cancel.
      await field('external_id').fill('not an integer');
      await field('payload').fill('{unfinished');
      await row(b).click();
      await modal.getByRole('button', {name: '继续编辑', exact: true}).click();
      await expect(field('external_id')).toHaveValue('not an integer');
      await expect(field('payload')).toHaveValue('{unfinished');
      await page.getByRole('button', {name: '保存记录', exact: true}).click();
      await page.getByRole('alert').filter({hasText: /字段 external_id/}).waitFor();
      assert.equal(patches().length, 0);
      checks.push('invalid int64 and JSON drafts survive cancel; validation failure stays dirty without request');

      // Exact normalization and conditional revision handling remain the original save path.
      for (const [name, value] of Object.entries(edits)) await field(name).fill(value);
      await field('flag').selectOption('true');
      await save(200);
      await expect(page.getByText('将提交字段：', {exact: false})).toHaveCount(0);
      const saved = await readRow(a);
      assert.equal(saved._revision, 2);
      assert.equal(saved.values.title, edits.title);
      assert.equal(saved.values.external_id, -9223372036854775808n);
      assert.equal(saved.values.payload.number, 9007199254740993n);
      assert.equal(saved.values.payload.nested.minimum, -9223372036854775808n);
      assert.equal(saved.values.flag, true);
      assert.equal(saved.values.ratio, 2.5);
      assert.deepEqual(saved.values.attachments, ['synthetic-attachment']);
      assert.equal(saved.values.status, edits.status);
      assert.equal(saved.values.related, edits.related);
      assert.equal(saved.values.theorem, edits.theorem);
      assert.equal(patches().at(-1).ifMatch, '"revision:1"');
      await row(b).click();
      await active(b);
      await expect(modal).toHaveCount(0);
      await row(a).click();
      await field('title').fill('conflicting draft');
      const externalSave = await fetch(`${apiOrigin}${rowPath(a)}`, {method: 'PATCH', headers: {Authorization: `Bearer ${manifest.token}`, 'Content-Type': 'application/json', 'If-Match': '"revision:2"'}, body: JSON.stringify({values: {title: 'concurrent server edit'}})});
      assert.equal(externalSave.status, 200, await externalSave.text());
      await save(412);
      await expect(field('title')).toHaveValue('conflicting draft');
      await row(b).click();
      await expect(modal).toBeVisible();
      await modal.getByRole('button', {name: '继续编辑', exact: true}).click();
      await expect(field('title')).toHaveValue('conflicting draft');
      await save(412);
      assert.equal(patches().at(-1).ifMatch, '"revision:2"');
      assert.equal(patches().at(-2).ifMatch, '"revision:2"');
      const conflicted = await readRow(a);
      assert.equal(conflicted._revision, 3);
      assert.equal(conflicted.values.title, 'concurrent server edit');
      assert.equal((await readRow(b))._revision, 1);
      await expect(page.locator('.inspector-conflict')).toContainText('本地 r2，服务端 r3');
      await page.locator('.inspector-body').evaluate(element => {element.scrollTop = 0;});
      await page.screenshot({path: path.join(evidence, 'failed-save-draft-preserved.png'), fullPage: true});
      checks.push('successful exact int64/JSON save clears dirty; conflict remains dirty and retains original If-Match on retry');
    }
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(external, []);
    await fs.writeFile(path.join(evidence, 'browser-report.json'), JSON.stringify({entry: 'production main.tsx Library', backend: 'real temporary API/SQLite; table/rows created via HTTP API', auth: 'synthetic local development token; auth/session and users/me fixture responses', checks, pageErrors, externalRequests: external, requests}, null, 2));
  } catch (error) {
    if (page) {
      await page.screenshot({path: path.join(evidence, 'failure.png'), fullPage: true}).catch(() => {});
      await fs.writeFile(path.join(evidence, 'failure.json'), JSON.stringify({error: String(error), text: await page.locator('body').innerText().catch(() => ''), pageErrors, requests, stderr}, null, 2));
    }
    throw error;
  } finally {
    await browser?.close();
    await server?.close();
    if (apiProcess.pid && apiProcess.exitCode === null && apiProcess.signalCode === null) {
      const closed = new Promise(resolve => apiProcess.once('close', resolve));
      apiProcess.kill('SIGTERM');
      await closed;
    }
    await fs.rm(temp, {recursive: true, force: true});
  }
});
