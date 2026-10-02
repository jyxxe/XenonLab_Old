import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('account edits omit unchanged grants and remove revoked grants only on explicit action', async t => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required; expected executable at ${executablePath}`);
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: 0, watch: null}});
  await server.listen();
  const browser = await chromium.launch({executablePath, headless: true});
  const page = await browser.newPage();
  try {
    await page.goto(`${server.resolvedUrls.local[0]}tests/account-patch-fixture.html`);
    await page.getByRole('button', {name: /Child/}).click();
    await page.locator('.account-detail > label').first().locator('input').fill('Renamed child');
    await page.getByRole('button', {name: '保存账号设置'}).click();
    await page.waitForFunction(() => window.__accountPatchCalls?.length === 1);
    assert.deepEqual(await page.evaluate(() => window.__accountPatchCalls[0]), {
      id: 'child', body: {name: 'Renamed child'},
    });

    await page.getByLabel('启用账号').uncheck();
    await page.getByRole('button', {name: '保存账号设置'}).click();
    await page.waitForFunction(() => window.__accountPatchCalls?.length === 2);
    assert.deepEqual(await page.evaluate(() => window.__accountPatchCalls[1]), {
      id: 'child', body: {enabled: false},
    });
    await page.getByText('已不可授予的旧权限').waitFor();
    await page.locator('.account-detail .scope-grid').first().getByLabel('查询知识库').check();
    await page.getByRole('button', {name: '保存账号设置'}).click();
    await page.getByRole('status').filter({hasText: '请先明确移除所有超出父账号当前权限的旧 grants'}).waitFor();
    assert.equal(await page.evaluate(() => window.__accountPatchCalls.length), 2);

    await page.getByRole('button', {name: '移除 编辑知识库'}).click();
    await page.getByRole('button', {name: '保存账号设置'}).click();
    await page.waitForFunction(() => window.__accountPatchCalls?.length === 3);
    assert.deepEqual(await page.evaluate(() => window.__accountPatchCalls[2]), {
      id: 'child', body: {grants: ['desktop:read', 'xelibrary:query', 'xelibrary:read']},
    });
    await page.getByText('已不可授予的旧权限').waitFor({state: 'detached'});
    assert.equal(await page.getByRole('status').filter({hasText: '账号已更新'}).count(), 1);

    // Root-only signed candidates are visible but never preselected; a child
    // remains limited to the parent's active grants.
    assert.equal(await page.getByLabel('读取向量').count(), 0);
    await page.getByRole('button', {name: /Root/}).click();
    const embeddingRead = page.getByLabel('读取向量');
    await embeddingRead.waitFor();
    await page.waitForTimeout(100); // Let the selection effect load this account's grants.
    assert.equal(await embeddingRead.isChecked(), false);
    await embeddingRead.check();
    await page.getByRole('button', {name: '保存账号设置'}).click();
    await page.waitForFunction(() => window.__accountPatchCalls?.length === 4);
    assert.deepEqual(await page.evaluate(() => window.__accountPatchCalls[3]), {
      id: 'root', body: {grants: ['accounts:manage', 'desktop:admin', 'desktop:read', 'embedding:read', 'xelibrary:query', 'xelibrary:read']},
    });
  } finally {
    await browser.close();
    await server.close();
  }
});
