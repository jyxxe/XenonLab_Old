import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('Table View applies one field value to multiple selected rows through bulkUpdate', async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: 0, watch: null}});
  await server.listen();
  const browser = await chromium.launch({executablePath, headless: true});
  const page = await browser.newPage({viewport: {width: 1440, height: 1000}});
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const calls = () => page.evaluate(() => window.libraryFixture.calls);
  try {
    await page.goto(`${server.resolvedUrls.local[0]}tests/library-fixture.html`);
    await page.locator('.knowledge-sidebar .library-tree-table').first().waitFor();
    await page.locator('.knowledge-sidebar .library-tree-table', {hasText: 'uniform-convergence'}).click();
    await page.locator('.table-scroll tbody tr').first().waitFor();
    await page.evaluate(() => window.libraryFixture.resetCalls());
    // Select two rows through their accessible checkboxes.
    await page.getByLabel('选择记录 row-001').check();
    await page.getByLabel('选择记录 row-002').check();
    const field = page.locator('select[aria-label="批量字段"]').first();
    await field.waitFor();
    await field.selectOption('title');
    await page.locator('input[aria-label="批量值"]').first().fill('共享分类值');
    await page.locator('button', {hasText: '应用到已选'}).first().click();
    await page.getByText('已更新 2 行「title」').waitFor();
    const bulkCalls = (await calls()).filter(call => String(call.path).includes('rows:bulkUpdate'));
    assert.ok(bulkCalls.length >= 1, `bulkUpdate request recorded (got ${bulkCalls.length})`);
    const body = bulkCalls[0].body;
    assert.equal(body.rows.length, 2);
    assert.deepEqual(body.rows.map(row => row.values), [{title: '共享分类值'}, {title: '共享分类值'}]);
    assert.ok(body.batch_id);
  } finally {
    await browser.close();
    await server.close();
  }
  assert.deepEqual(errors, []);
});
