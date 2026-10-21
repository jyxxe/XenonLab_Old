import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium, expect} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const evidence = path.resolve(appRoot, '../../runtime/verification/issue232');
const negativeControl = process.env.ACCOUNT_FILTER_NEGATIVE_CONTROL === '1';
test('account filter narrows only loaded names/logins while preserving selection and draft', {timeout: 45000}, async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium required: ${executablePath}`);
  const temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'xeono-account-filter-'));
  const server = await createServer({root: appRoot, cacheDir: path.join(temporary, 'vite'), server: {host: '127.0.0.1', port: 0, hmr: false, watch: null}});
  let browser;
  const errors = [], external = [];
  try {
    await server.listen();
    const origin = new URL(server.resolvedUrls.local[0]).origin;
    browser = await chromium.launch({executablePath, headless: true});
    const page = await browser.newPage({viewport: {width: 1280, height: 720}});
    page.setDefaultTimeout(10000);
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/*', route => {
      if (new URL(route.request().url()).origin !== origin) {external.push(route.request().url()); return route.abort();}
      return route.continue();
    });
    await page.goto(`${origin}/tests/account-filter-fixture.html`);
    const tree = page.locator('.account-tree');
    const detail = page.locator('.account-detail');
    await expect(tree.getByRole('button', {name: /Research TEAM/})).toBeVisible();
    await expect(detail.locator('.key-list')).toContainText('Key for fixture-root');
    await fs.mkdir(evidence, {recursive: true});
    if (negativeControl) {
      assert.equal(await tree.locator('button').count(), 61);
      assert.equal(await tree.getByRole('searchbox').count(), 0);
      await page.screenshot({path: path.join(evidence, 'original-unfiltered.png')});
      await fs.writeFile(path.join(evidence, 'original.json'), JSON.stringify({rows: 61, filterControls: 0, errors, external}, null, 2));
      return;
    }
    const query = tree.getByRole('searchbox', {name: '筛选账号', exact: true});
    const entries = tree.locator('.account-tree-list button');
    await expect(entries).toHaveCount(61);
    const nameDraft = detail.locator(':scope > label').first().locator('input');
    await nameDraft.fill('Unsaved administrator name');
    const originalDetail = await detail.innerText();
    const originalCalls = await page.evaluate(() => window.accountFilterFixture.calls.length);
    const originalRows = await page.evaluate(() => JSON.stringify(window.accountFilterFixture.rows));
    await query.fill('rEsEaRcH team');
    await expect(entries).toHaveCount(1);
    await expect(entries.first()).toContainText('最后账号');
    await query.fill('SCIENTIST-60');
    await expect(entries).toHaveCount(1);
    await query.fill('团队 12');
    await expect(entries).toHaveCount(1);
    await query.fill('  scientist-60  ');
    await expect(entries).toHaveCount(1);
    await query.fill('no such authorized account');
    await expect(entries).toHaveCount(0);
    await expect(tree.getByText('没有匹配的账号', {exact: true})).toBeVisible();
    assert.equal(await detail.innerText(), originalDetail, 'hidden selected root keeps detail and permission/key context');
    await expect(nameDraft).toHaveValue('Unsaved administrator name');
    assert.equal(await page.evaluate(() => window.accountFilterFixture.calls.length), originalCalls, 'typing does not call any API');
    assert.equal(await page.evaluate(() => JSON.stringify(window.accountFilterFixture.rows)), originalRows, 'filter does not mutate loaded authorized accounts');
    await tree.getByRole('button', {name: '清除筛选', exact: true}).click();
    await expect(entries).toHaveCount(61);
    await expect(nameDraft).toHaveValue('Unsaved administrator name');
    assert.equal(await page.evaluate(() => window.accountFilterFixture.calls.length), originalCalls);
    await query.fill('scientist-60');
    await entries.first().focus();
    await page.keyboard.press('Enter');
    await expect(detail.locator('h3').first()).toHaveText('Research TEAM 最后账号');
    await expect(nameDraft).toHaveValue('Research TEAM 最后账号');
    await expect(detail.locator('.key-list')).toContainText('Key for fixture-child-60');
    assert.deepEqual(await page.evaluate(start => window.accountFilterFixture.calls.slice(start), originalCalls), [{path: '/auth/api-keys?owner=fixture-child-60', method: 'GET'}]);
    await page.screenshot({path: path.join(evidence, 'filtered-account.png')});
    await query.fill('no match');
    await expect(entries).toHaveCount(0);
    await expect(detail.locator('.key-list')).toContainText('Key for fixture-child-60');
    await tree.getByRole('button', {name: '清除筛选', exact: true}).click();
    await expect(entries).toHaveCount(61);
    await expect(entries.filter({hasText: 'Scientist-60'})).toHaveClass('active');
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    assert.ok(await page.evaluate(() => window.accountFilterFixture.calls.every(call => call.method === 'GET')));
    await fs.writeFile(path.join(evidence, 'observations.json'), JSON.stringify({source: 'production Accounts component; 61 in-memory authorized synthetic accounts', originalCalls, matching: ['name/case', 'login/case', 'Chinese name', 'trim/no-match/clear'], noRequestsOnTyping: true, draftAndKeyOwnerPreserved: true, explicitKeyboardSelection: true, errors, external}, null, 2));
  } finally {
    await browser?.close();
    await server.close();
    await fs.rm(temporary, {recursive: true, force: true});
  }
});
