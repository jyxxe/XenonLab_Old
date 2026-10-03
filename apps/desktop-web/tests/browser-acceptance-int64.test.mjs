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

test('production Library entry preserves complete int64 values through the real local API', {timeout: 120000}, async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const temp = await fs.mkdtemp(path.join(os.tmpdir(), 'xel-int64-'));
  const evidence = path.join(repositoryRoot, 'runtime/verification/issue216');
  await fs.mkdir(evidence, {recursive: true});
  const port = await freePort();
  const apiOrigin = `http://127.0.0.1:${port}`;
  const manifestPath = path.join(temp, 'manifest.json');
  const apiProcess = spawn('uv', ['run', 'python', 'apps/desktop-web/tests/int64-local-api.py', '--data-dir', path.join(temp, 'data'), '--manifest', manifestPath, '--port', String(port)], {cwd: repositoryRoot, stdio: ['ignore', 'pipe', 'pipe']});
  let stderr = '';
  let startupError;
  apiProcess.once('error', error => { startupError = error; });
  apiProcess.stderr.on('data', data => {stderr += data.toString();});
  let server, browser, page;
  const requests = [];
  const pageErrors = [];
  const external = [];
  try {
    let ready = false;
    for (let attempt = 0; attempt < 200; attempt += 1) {
      if (startupError) throw new Error(`Local API could not start: ${startupError.message}. Install the pinned uv/Python fixture runtime.`);
      if (apiProcess.exitCode !== null) throw new Error(`Local API exited: ${stderr}`);
      try { if ((await fetch(`${apiOrigin}/health/live`)).ok) { ready = true; break; } } catch {}
      await sleep(100);
    }
    assert.ok(ready, `Local API did not start: ${stderr}`);
    const manifest = JSON.parse(await fs.readFile(manifestPath, 'utf8'));
    server = await createServer({root: appRoot, cacheDir: path.join(temp, 'vite-cache'), optimizeDeps: {noDiscovery: true, include: ['react', 'react-dom', 'react-dom/client', 'react/jsx-runtime', 'react/jsx-dev-runtime']}, server: {host: '127.0.0.1', port: 0, watch: null, hmr: false, proxy: {'/api/v1': apiOrigin}}});
    await server.listen();
    const viteOrigin = new URL(server.resolvedUrls.local[0]).origin;
    const browserOrigin = `http://library.xenon.lan:${server.httpServer.address().port}`;
    browser = await chromium.launch({executablePath, headless: true});
    const context = await browser.newContext({viewport: {width: 1440, height: 1050}});
    let malformedRows = false;
    await context.route('**/*', async route => {
      const request = route.request();
      const url = new URL(request.url());
      if (url.origin !== browserOrigin) {external.push(request.url()); return route.abort();}
      if (url.pathname.startsWith('/api/v1/')) requests.push({method: request.method(), path: url.pathname.replace('/api/v1', ''), body: request.postData()});
      const json = value => route.fulfill({status: 200, contentType: 'application/json', body: JSON.stringify(value)});
      if (url.pathname === '/api/v1/auth/session/token') return json({token: manifest.token, expires: 600});
      if (url.pathname === '/api/v1/users/me') return json({data: {subject: 'issue216-fixture-owner', scopes: ['xelibrary:read', 'xelibrary:write', 'xelibrary:query', 'xelibrary:attach', 'xelibrary:admin']}, meta: {}});
      if (malformedRows && url.pathname === `/api/v1/tables/${manifest.table_id}/rows` && request.method() === 'GET') return route.fulfill({status: 200, contentType: 'application/json', headers: {'X-Request-ID': 'synthetic-bad-json'}, body: '{"data":[{"values":{"external_id":1e999}}],"meta":{}}'});
      // Every document/module/API request physically goes to loopback. The
      // virtual hostname selects the unchanged production main.tsx Library entry.
      const response = await route.fetch({url: `${viteOrigin}${url.pathname}${url.search}`});
      return route.fulfill({response});
    });
    page = await context.newPage();
    page.on('pageerror', error => pageErrors.push(error.message));
    const field = name => page.locator('.inspector-field').filter({has: page.locator('.inspector-field-name').filter({hasText: new RegExp(`^${name}`)})}).locator('input,textarea');
    const readRow = async id => {
      const response = await fetch(`${apiOrigin}/api/v1/tables/${manifest.table_id}/rows/${id}`, {headers: {Authorization: `Bearer ${manifest.token}`}});
      assert.equal(response.status, 200);
      return parseExactJSON(await response.text()).data;
    };
    const save = async () => {
      const pending = page.waitForResponse(response => response.request().method() === 'PATCH' && new URL(response.url()).pathname === `/api/v1/tables/${manifest.table_id}/rows/${manifest.row_id}`);
      await page.getByRole('button', {name: '保存记录', exact: true}).click();
      const response = await pending;
      assert.equal(response.status(), 200, await response.text());
    };
    await page.goto(browserOrigin);
    await page.getByRole('button', {name: '刷新知识表目录'}).click();
    await page.locator('button.library-tree-table').waitFor();
    await page.locator('button.library-tree-table').click();
    await page.locator(`tr[data-row-id="${manifest.row_id}"]`).click();
    await page.getByRole('tab', {name: '字段', exact: true}).click();
    await expect(field('external_id')).toHaveValue('9007199254740993');
    await expect(field('minimum')).toHaveValue('-9223372036854775808');
    await expect(field('maximum')).toHaveValue('9223372036854775807');
    await page.screenshot({path: path.join(evidence, 'existing-exact-values.png'), fullPage: true});
    await field('title').fill('edited title');
    await save();
    const edited = await readRow(manifest.row_id);
    assert.equal(edited.values.title, 'edited title');
    assert.equal(edited.values.external_id, 9007199254740993n);
    assert.equal(edited.values.minimum, -9223372036854775808n);
    assert.equal(edited.values.maximum, 9223372036854775807n);
    assert.equal(edited.values.negative, -9007199254740993n);
    assert.equal(edited.values.metadata.number, 9223372036854775807n);
    const titlePatch = requests.filter(request => request.method === 'PATCH').at(-1);
    assert.deepEqual(parseExactJSON(titlePatch.body).values, {title: 'edited title'}, 'only changed fields are sent as an additional guard');

    let maximumRevision;
    for (const integer of ['-9007199254740993', '-9223372036854775808', '9223372036854775807', '9007199254740993', '42']) {
      await field('external_id').fill(integer);
      await save();
      const saved = await readRow(manifest.row_id);
      assert.equal(String(saved.values.external_id), integer);
      if (integer === '9223372036854775807') maximumRevision = saved._revision;
      await expect(field('external_id')).toHaveValue(integer);
      assert.match(requests.filter(request => request.method === 'PATCH').at(-1).body, new RegExp(`"external_id":${integer}(?:,|})`), 'wire int64 stays a JSON number');
    }
    for (const invalid of ['9223372036854775808', '-9223372036854775809', '1.5', '1e3']) {
      const count = requests.filter(request => request.method === 'PATCH').length;
      await field('external_id').fill(invalid);
      await page.getByRole('button', {name: '保存记录', exact: true}).click();
      await page.getByRole('alert').filter({hasText: /字段 external_id/}).waitFor();
      assert.equal(requests.filter(request => request.method === 'PATCH').length, count, 'invalid int64 is blocked before a request');
    }
    await field('external_id').fill('9007199254740993');
    await field('metadata').fill('{"number":9223372036854775807,"text":"9007199254740993","exponent":1.25e3,"__proto__":{"number":-9223372036854775808}}');
    await field('amount').fill('2.5');
    await save();
    const nested = (await readRow(manifest.row_id)).values;
    assert.equal(nested.metadata.number, 9223372036854775807n);
    assert.equal(nested.metadata.__proto__.number, -9223372036854775808n);
    assert.equal(nested.metadata.text, '9007199254740993');
    assert.equal(nested.metadata.exponent, 1250);
    assert.equal(nested.amount, 2.5);
    await page.getByRole('tab', {name: '历史', exact: true}).click();
    await page.locator('.history-timeline li').filter({has: page.getByText(`r${maximumRevision}`, {exact: true})}).getByRole('button', {name: '查看', exact: true}).click();
    await expect(page.locator('.history-compare')).toContainText('9223372036854775807');
    await page.screenshot({path: path.join(evidence, 'exact-history.png'), fullPage: true});

    // Existing defaults are read losslessly; a directly edited schema default
    // and newly created row use the same serializer as the production editor.
    await page.getByRole('button', {name: '字段定义', exact: true}).click();
    const schema = page.getByRole('dialog', {name: '字段定义', exact: true});
    await schema.locator('.schema-field').nth(3).getByLabel('默认值', {exact: true}).fill('9007199254740993');
    const schemaSaved = page.waitForResponse(response => response.request().method() === 'PATCH' && new URL(response.url()).pathname.endsWith('/schema'));
    await schema.getByRole('button', {name: '保存字段定义', exact: true}).click();
    assert.equal((await schemaSaved).status(), 200);
    await schema.waitFor({state: 'detached'});
    await page.getByRole('button', {name: '新增记录', exact: true}).click();
    await page.getByRole('tab', {name: '字段', exact: true}).click();
    await expect(field('maximum')).toHaveValue('9007199254740993');
    await field('title').fill('created exact row');
    await field('external_id').fill('-9223372036854775808');
    const createdResponse = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === `/api/v1/tables/${manifest.table_id}/rows`);
    await page.getByRole('button', {name: '创建记录', exact: true}).click();
    const createdHTTP = await createdResponse;
    assert.equal(createdHTTP.status(), 201, await createdHTTP.text());
    const created = parseExactJSON(await createdHTTP.text()).data;
    const readCreated = await readRow(created._row_id);
    assert.equal(readCreated.values.external_id, -9223372036854775808n);
    assert.equal(readCreated.values.minimum, -9223372036854775808n);
    assert.equal(readCreated.values.maximum, 9007199254740993n);
    await page.screenshot({path: path.join(evidence, 'created-with-exact-defaults.png'), fullPage: true});

    await page.getByRole('navigation', {name: '知识库视图'}).getByRole('button', {name: '查询控制台', exact: true}).click();
    await page.locator('.library-query-editor').fill('FROM "/**" LIMIT 100');
    await page.getByRole('button', {name: '运行', exact: true}).click();
    await expect(page.locator('.library-query-results')).toContainText('9223372036854775807');
    await page.screenshot({path: path.join(evidence, 'exact-query-values.png'), fullPage: true});
    await page.locator('button.library-tree-table').click();
    malformedRows = true;
    await page.getByRole('button', {name: '刷新记录', exact: true}).click();
    await page.getByRole('alert').filter({hasText: 'INVALID_RESPONSE'}).waitFor();
    await expect(page.getByRole('alert').filter({hasText: 'INVALID_RESPONSE'})).toContainText('synthetic-bad-json');
    await expect(page.getByRole('alert').filter({hasText: 'INVALID_RESPONSE'})).toContainText('无法精确解析');
    malformedRows = false;
    await page.getByRole('button', {name: '重试', exact: true}).click();
    await page.locator(`tr[data-row-id="${manifest.row_id}"]`).waitFor();

    await page.locator('button[title="新建知识表"]').click();
    const newTable = page.getByRole('dialog', {name: '新建知识表', exact: true});
    await newTable.getByLabel('表名', {exact: true}).fill('exact default table');
    await newTable.getByLabel('逻辑路径').fill('/new-precision');
    const newColumn = newTable.locator('.schema-field').nth(1);
    await newColumn.getByLabel('字段名', {exact: true}).fill('exact_id');
    await newColumn.getByRole('combobox').selectOption('int64');
    await newColumn.getByLabel('默认值', {exact: true}).fill('9223372036854775808');
    const tableRequests = requests.filter(request => request.method === 'POST' && request.path === '/tables').length;
    await newTable.getByRole('button', {name: '创建知识表', exact: true}).click();
    await newTable.getByRole('alert').filter({hasText: /exact_id.*int64 范围/}).waitFor();
    assert.equal(requests.filter(request => request.method === 'POST' && request.path === '/tables').length, tableRequests);
    await newColumn.getByLabel('默认值', {exact: true}).fill('9223372036854775807');
    const newTableResponse = page.waitForResponse(response => response.request().method() === 'POST' && new URL(response.url()).pathname === '/api/v1/tables');
    await newTable.getByRole('button', {name: '创建知识表', exact: true}).click();
    const tableHTTP = await newTableResponse;
    assert.equal(tableHTTP.status(), 201, await tableHTTP.text());
    const createdTable = parseExactJSON(await tableHTTP.text()).data;
    const schemaRead = await fetch(`${apiOrigin}/api/v1/tables/${createdTable.table_id}/schema`, {headers: {Authorization: `Bearer ${manifest.token}`}});
    const exactSchema = parseExactJSON(await schemaRead.text()).data;
    assert.equal(exactSchema.columns.find(column => column.name === 'exact_id').default_value, 9223372036854775807n);
    await newTable.waitFor({state: 'detached'});
    await expect(page.locator('.library-table-head')).toContainText('exact default table');
    assert.deepEqual(pageErrors, []);
    assert.deepEqual(external, []);
    await fs.writeFile(path.join(evidence, 'browser-report.json'), JSON.stringify({entry: 'production main.tsx standalone Library', backend: 'real temporary XeLibrary API', identity: 'synthetic development owner/admin; auth/users endpoints stubbed', checks: ['untouched unsafe +/- and signed64 bounds', 'partial title-only patch', 'direct full signed64 edit and safe integer', 'invalid int64 zero requests', 'nested JSON/string/prototype/exponent', 'float64 edit', 'history', 'schema default', 'create with full int64', 'new table exact default and invalid default zero requests', 'query display', 'malformed JSON visible correlation and retry'], pageErrors, externalRequests: external, apiRequests: requests}, null, 2));
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
