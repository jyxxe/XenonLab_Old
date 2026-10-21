import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('Embedding job opens its source table with current permissions and cancels stale navigation', async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: 0, watch: null}});
  await server.listen();
  const browser = await chromium.launch({executablePath, headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  const calls = () => page.evaluate(() => window.shellFixture.calls);
  const openSource = () => page.getByRole('button', {name: '在知识库中打开', exact: true}).click();
  const showQueue = () => page.locator('nav.dock button[title="Embedding"]').click();
  const sourceRows = page.locator('.table-scroll tbody');
  const knowledge = page.locator('.knowledge-content');
  try {
    await page.goto(`${server.resolvedUrls.local[0]}tests/shell-fixture.html`);
    await page.locator('nav.dock button[title="Embedding"]').waitFor();
    await page.evaluate(() => {
      window.shellFixture.control.embeddingEnabled = true;
      window.shellFixture.control.embeddingTableId = 'target-table';
    });
    await showQueue();
    await openSource();
    await sourceRows.getByText('target-table synthetic record', {exact: true}).waitFor();
    assert.match(await page.locator('.library-table-head').innerText(), /索引任务源表/);
    assert.equal(await page.locator('.library-tree-table').getByText('索引任务源表', {exact: true}).count(), 0, 'target is not in the loaded catalogue');
    assert.ok((await calls()).some(call => call.path === '/tables/target-table'), 'fresh ACL checked metadata API');

    // A repeated intent focuses the existing singleton and clears its previous
    // rows before errors. The queue grants no table ACL exception.
    for (const code of ['XL_FORBIDDEN', 'XL_TABLE_NOT_FOUND']) {
      await showQueue();
      await page.evaluate(code => {window.shellFixture.control.tableReadError = code; window.shellFixture.resetCalls();}, code);
      await openSource();
      await knowledge.getByText(code, {exact: false}).waitFor();
      assert.equal(await sourceRows.count(), 0, 'no old table contents remain after denial');
      assert.match(await knowledge.innerText(), /fixture-table-request/);
      assert.equal((await calls()).some(call => call.path.startsWith('/tables/target-table/')), false, 'no schema or row read after metadata denial');
      assert.equal(await page.locator('.window[data-app-id="knowledge"]').count(), 1);
    }
    await page.evaluate(() => {window.shellFixture.control.tableReadError = '';});
    await knowledge.getByRole('button', {name: '重试', exact: true}).click();
    await sourceRows.getByText('target-table synthetic record', {exact: true}).waitFor();

    // Even a visible cached directory entry is revalidated on job navigation.
    await showQueue();
    await page.evaluate(() => {window.shellFixture.control.embeddingTableId = 'example-table';});
    await page.locator('.embedding-app > header button').click();
    await page.waitForFunction(() => document.querySelector('.embedding-queue-scroll')?.innerText.includes('example-table'));
    await page.evaluate(() => {window.shellFixture.control.tableReadError = 'XL_FORBIDDEN';});
    await openSource();
    await knowledge.getByText('XL_FORBIDDEN', {exact: false}).waitFor();
    assert.equal(await sourceRows.count(), 0);

    // A delayed response must not override the user's later directory choice.
    await showQueue();
    await page.evaluate(() => {
      Object.assign(window.shellFixture.control, {embeddingTableId:'target-table', tableReadError:'', tableReadDelay:true});
    });
    await page.locator('.embedding-app > header button').click();
    await page.waitForFunction(() => document.querySelector('.embedding-queue-scroll')?.innerText.includes('target-table'));
    await openSource();
    await knowledge.getByText('正在打开知识表…', {exact: false}).waitFor();
    await page.locator('.library-tree-table').getByText('部署验收', {exact:true}).click();
    await sourceRows.getByText('example-table synthetic record', {exact:true}).waitFor();
    await page.evaluate(() => {window.shellFixture.control.tableReadDelay = false; window.shellFixture.control.releaseTableRead();});
    await page.waitForTimeout(100);
    assert.match(await page.locator('.library-table-head').innerText(), /部署验收/);
    assert.equal(await sourceRows.getByText('target-table synthetic record', {exact:true}).count(), 0);

    // Real table UUIDs must leave the action reachable inside the capped cell.
    const uuid = '00000000-0000-4000-8000-000000000197';
    await showQueue();
    await page.evaluate(id => {window.shellFixture.control.embeddingTableId = id;}, uuid);
    await page.locator('.embedding-app > header button').click();
    await page.waitForFunction(id => document.querySelector('.embedding-queue-scroll')?.innerText.includes(id), uuid);
    await openSource();
    await sourceRows.getByText(`${uuid} synthetic record`, {exact:true}).waitFor();

    await showQueue();
    await page.evaluate(() => {window.shellFixture.control.libraryEnabled = false; window.shellFixture.resetCalls();});
    await page.locator('.embedding-app > header button').click();
    await page.getByRole('button', {name:'在知识库中打开', exact:true}).waitFor({state:'detached'});
    assert.match(await page.locator('.embedding-queue-scroll').innerText(), /fixture-job-1/);
    assert.equal((await calls()).some(call => call.path.startsWith('/tables')), false, 'embedding:read alone does not fetch table contents');
    assert.deepEqual(pageErrors, []);
  } finally {
    await browser.close();
    await server.close();
  }
});
