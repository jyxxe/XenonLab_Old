import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('theorem recents show real titles and visible stable identifiers with original click and keyboard navigation', async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: 0, watch: null}});
  await server.listen();
  const browser = await chromium.launch({executablePath, headless: true});
  const context = await browser.newContext({viewport: {width: 1100, height: 800}});
  const page = await context.newPage();
  const evidence = path.resolve(appRoot, '../../runtime/verification/issue205');
  const errors = [];
  const external = [];
  const origin = new URL(server.resolvedUrls.local[0]).origin;
  await context.route('**/*', route => {
    if (new URL(route.request().url()).origin === origin) return route.continue();
    external.push(route.request().url());
    return route.abort();
  });
  page.on('pageerror', error => errors.push(error.message));
  const recent = page.locator('.library-sidebar-section').filter({has: page.getByText('最近', {exact: true})});
  const detail = page.getByRole('region', {name: '定理详情'});
  const go = async (subject, title) => {
    await page.goto(`${origin}/tests/library-fixture.html?subject=${subject}${title === undefined ? '' : `&theoremTitle=${encodeURIComponent(title)}`}`);
    await page.getByRole('navigation', {name: '知识库视图'}).getByRole('button', {name: '定理', exact: true}).click();
    await page.locator('.library-theorem-list button').first().waitFor();
  };
  const originalID = async (id, invoke) => {
    await page.evaluate(() => window.libraryFixture.resetCalls());
    await invoke();
    await page.waitForFunction(id => window.libraryFixture.calls.some(call => call.path === `/theorems/${id}/evidence`), id);
    assert.equal(await detail.locator('h3').count(), 1);
    assert.match(await detail.innerText(), id === 'thm-1' ? /一致收敛蕴含逐点收敛/ : /收敛序列的极限唯一/);
    const requested = await page.evaluate(() => window.libraryFixture.calls.map(call => call.path));
    assert.ok(requested.includes(`/theorems/${id}`));
    assert.equal(requested.some(url => url === `/theorems/${id === 'thm-1' ? 'thm-2' : 'thm-1'}`), false);
  };
  try {
    await fs.mkdir(evidence, {recursive: true});
    await go('recent-distinct');
    await page.locator('.library-theorem-list button').nth(0).click();
    await recent.getByRole('button', {name: '一致收敛定理', exact: true}).waitFor();
    await page.locator('.library-theorem-list button').nth(1).click();
    await recent.getByRole('button', {name: '极限唯一性定理', exact: true}).waitFor();
    assert.equal(await recent.locator('button.library-recent-theorem').count(), 2);
    await originalID('thm-1', () => recent.getByRole('button', {name: '一致收敛定理', exact: true}).click());
    const second = recent.getByRole('button', {name: '极限唯一性定理', exact: true});
    await second.focus();
    await originalID('thm-2', () => page.keyboard.press('Enter'));
    await page.screenshot({path: path.join(evidence, 'distinct-titles.png'), fullPage: true});
    await page.reload();
    await recent.getByRole('button', {name: '一致收敛定理', exact: true}).waitFor();
    assert.equal(await recent.getByRole('button', {name: '极限唯一性定理', exact: true}).count(), 1, 'saved titles survive reload');

    const longTitle = '同名且很长的定理标题'.repeat(8);
    await go('recent-duplicates', longTitle);
    await page.locator('.library-theorem-list button').nth(0).click();
    await page.locator('.library-theorem-list button').nth(1).click();
    for (const id of ['thm-1', 'thm-2']) {
      const button = recent.locator(`button[title="${longTitle} · ${id}"]`);
      const identifier = button.locator('.library-recent-identifier');
      await identifier.waitFor();
      const visible = await identifier.evaluate(element => {
        const own = element.getBoundingClientRect();
        const parent = element.parentElement.getBoundingClientRect();
        return {inside: own.left >= parent.left && own.right <= parent.right && own.bottom <= parent.bottom, complete: element.scrollWidth <= element.clientWidth};
      });
      assert.deepEqual(visible, {inside: true, complete: true}, `${id} identifier remains visible beside ellipsized title`);
      await originalID(id, () => button.click());
    }
    await page.screenshot({path: path.join(evidence, 'long-duplicate-titles.png'), fullPage: true});

    // Existing per-account local entries keep their IDs and need no metadata prefetch.
    await page.goto(`${origin}/tests/library-fixture.html?subject=recent-legacy`);
    await page.evaluate(() => localStorage.setItem('xeonolab.library.recent-legacy.recent', JSON.stringify([
      {kind: 'theorem', id: 'thm-1', title: '定理', at: 2},
      {kind: 'theorem', id: 'thm-2', title: '', at: 1},
    ])));
    await page.reload();
    for (const id of ['thm-1', 'thm-2']) await recent.getByRole('button', {name: `定理 · ${id}`, exact: true}).waitFor();
    assert.equal(await page.evaluate(() => window.libraryFixture.calls.some(call => call.path.startsWith('/theorems'))), false);
    await originalID('thm-1', () => recent.getByRole('button', {name: '定理 · thm-1', exact: true}).click());
    await recent.getByRole('button', {name: '定理 · thm-2', exact: true}).focus();
    await originalID('thm-2', () => page.keyboard.press('Space'));
    await page.screenshot({path: path.join(evidence, 'legacy-titles.png'), fullPage: true});

    // Missing API titles use the same fallback; search/relation retry also carry metadata.
    await go('recent-missing-title', '');
    await page.locator('.library-theorem-list button').nth(0).click();
    await page.locator('.library-theorem-list button').nth(1).click();
    for (const id of ['thm-1', 'thm-2']) await recent.getByRole('button', {name: `定理 · ${id}`, exact: true}).waitFor();
    await go('recent-search');
    await page.getByRole('searchbox', {name: '搜索定理'}).fill('一致收敛');
    await page.getByRole('searchbox', {name: '搜索定理'}).press('Enter');
    await page.waitForFunction(() => window.libraryFixture.calls.some(call => call.path === '/theorems/search'));
    await page.evaluate(() => {window.libraryFixture.state.denyTheoremRelations = true;});
    await page.locator('.library-theorem-list button').first().click();
    await detail.getByText('XL_FORBIDDEN', {exact: false}).waitFor();
    await recent.getByRole('button', {name: '一致收敛定理', exact: true}).waitFor();
    await page.evaluate(() => {window.libraryFixture.state.denyTheoremRelations = false;});
    await originalID('thm-1', () => detail.getByRole('button', {name: '重试', exact: true}).click());
    assert.equal(await recent.getByRole('button', {name: '一致收敛定理', exact: true}).count(), 1);

    // Real UUID lengths exercise both the normal suffix and complete-ID collision fallback.
    // These historical synthetic records test rendering only, not backend existence or access.
    const uuidIDs = ['00000003-0000-4000-8000-000000000205', '00000001-0000-4000-8000-123456789abc', '00000002-0000-4000-8000-123456789abc'];
    await page.goto(`${origin}/tests/library-fixture.html?subject=recent-uuid`);
    await page.evaluate(({ids, title}) => localStorage.setItem('xeonolab.library.recent-uuid.recent', JSON.stringify(ids.map((id, at) => ({kind: 'theorem', id, title, at})))), {ids: uuidIDs, title: longTitle});
    await page.reload();
    for (const [index, id] of uuidIDs.entries()) {
      const identifier = index === 0 ? id.slice(-8) : id;
      const button = recent.locator(`button[title="${longTitle} · ${identifier}"]`);
      const marker = button.locator('.library-recent-identifier');
      await marker.waitFor();
      const visible = await marker.evaluate(element => {
        const own = element.getBoundingClientRect();
        const button = element.parentElement.getBoundingClientRect();
        const sidebar = element.closest('.knowledge-sidebar').getBoundingClientRect();
        return {inside: own.left >= button.left && own.right <= button.right && own.bottom <= button.bottom && own.right <= sidebar.right, complete: element.scrollWidth <= element.clientWidth && element.scrollHeight <= element.clientHeight};
      });
      assert.deepEqual(visible, {inside: true, complete: true}, `${identifier} remains visible in the sidebar`);
      assert.equal((await marker.innerText()).trim(), `· ${identifier}`);
    }
    assert.equal(await page.evaluate(() => window.libraryFixture.calls.some(call => call.path.startsWith('/theorems'))), false, 'historical UUID rendering does not claim backend access');
    await page.screenshot({path: path.join(evidence, 'uuid-and-collision-identifiers.png'), fullPage: true});
    assert.deepEqual(errors, []);
    assert.deepEqual(external, []);
    await fs.writeFile(path.join(evidence, 'browser-report.json'), JSON.stringify({synthetic: true, checks: ['distinct titles', 'reload persistence', 'long duplicate titles visible', 'legacy and missing API title fallback', 'original ID click', 'Enter/Space keyboard', 'no metadata prefetch', 'search metadata', 'relation denial/retry', '8-character UUID suffix visible', 'colliding complete UUID identifiers wrap without clipping'], pageErrors: errors, externalRequests: external}, null, 2));
  } finally {
    await browser.close();
    await server.close();
  }
});
