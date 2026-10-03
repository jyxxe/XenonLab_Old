import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('Dashboard directory chart covers loaded tables with full names and exact remainder', async t => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: 0, watch: null}});
  await server.listen();
  const browser = await chromium.launch({executablePath, headless: true});
  try {
    for (const [mode, width] of [['eight', 1080], ['six', 1080], ['zero', 1080], ['truncated', 1080], ['arbitrary', 1080], ['eight', 360], ['long', 360]]) {
      await t.test(`${mode}-${width}`, async () => {
        const page = await browser.newPage({viewport: {width, height: 800}});
        const errors = [], external = [];
        page.on('pageerror', error => errors.push(String(error)));
        page.on('request', request => {if (!request.url().startsWith(server.resolvedUrls.local[0])) external.push(request.url())});
        try {
          await page.goto(`${server.resolvedUrls.local[0]}tests/directory-distribution-fixture.html?mode=${mode}`);
          const card = page.locator('.mac-card').filter({has: page.getByRole('heading', {name: '知识库概览'})});
          await page.waitForFunction(() => document.querySelector('.dashboard-head small')?.textContent.startsWith('更新于'));
          const tables = await page.evaluate(() => window.__directoryFixture.tables);
          const counts = new Map();
          for (const table of tables) {const name = table.path.split('/').filter(Boolean)[0] || '/'; counts.set(name, (counts.get(name) || 0) + 1)}
          const sorted = [...counts].sort((a, b) => b[1] - a[1] || (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0));
          if (mode === 'zero') {
            await card.getByText('暂无可见知识表。').waitFor();
            assert.equal(await card.locator('.mac-bar').count(), 0);
            assert.equal(await card.locator('.directory-legend').count(), 0);
          } else {
            const bars = card.locator('.mac-bar');
            const entries = card.locator('.directory-legend li');
            assert.equal(await bars.count(), Math.min(7, sorted.length));
            assert.equal(await entries.count(), Math.min(7, sorted.length));
            for (const [index, [name, count]] of sorted.slice(0, 6).entries()) {
              const expected = `目录：${name}，${count} 张表`;
              assert.equal(await bars.nth(index).getAttribute('title'), expected);
              assert.equal(await bars.nth(index).getAttribute('aria-label'), expected);
              assert.equal(await card.getByRole('img', {name: expected, exact: true}).count(), 1);
              assert.equal(await bars.nth(index).locator('span').innerText(), String(index + 1));
              assert.equal(await entries.nth(index).locator('span').innerText(), `目录：${name}`);
              assert.equal(await entries.nth(index).locator('em').innerText(), `${count} 张表`);
              await bars.nth(index).hover();
            }
            if (sorted.length > 6) {
              const remaining = sorted.slice(6);
              const count = remaining.reduce((sum, [, value]) => sum + value, 0);
              const title = await bars.nth(6).getAttribute('title');
              assert.ok(title.startsWith(`其他（汇总）：${remaining.length} 个目录，${count} 张表`));
              for (const [name, value] of remaining) assert.ok(title.includes(`${name}（${value} 张表）`));
              assert.equal(await bars.nth(6).getAttribute('aria-label'), title);
              assert.equal(await entries.nth(6).getAttribute('title'), title);
              assert.equal(await entries.nth(6).locator('em').innerText(), `${count} 张表`);
              const heights = await bars.locator('.mac-plot i').evaluateAll(items => items.map(item => item.getBoundingClientRect().height));
              assert.ok(heights[0] > 4, 'count bars occupy the chart height');
              assert.ok(Math.abs(heights[6] / heights[0] - count / sorted[0][1]) < 0.1, 'bar height represents the exact count ratio');
              await bars.nth(6).hover();
            } else assert.equal(await entries.filter({hasText: '个目录汇总'}).count(), 0);
            const values = await entries.locator('em').allTextContents();
            assert.equal(values.reduce((sum, text) => sum + Number.parseInt(text), 0), tables.length);
            // Both shared-prefix names remain visible as distinct full text.
            if (mode === 'eight' || mode === 'six') {
              assert.equal(await entries.filter({hasText: '数学基础课程线性代数'}).count(), 1);
              assert.equal(await entries.filter({hasText: '数学基础课程高等代数'}).count(), 1);
            }
            assert.equal(await card.locator('.directory-distribution').evaluate(element => element.scrollWidth <= element.clientWidth), true);
            if (mode === 'long') {
              assert.equal(await entries.first().locator('span').evaluate(element => element.getBoundingClientRect().height > 40 && element.scrollHeight <= element.clientHeight), true);
            }
          }
          assert.equal(await card.getByText('路径统计仅覆盖当前载入的前 1000 张表。').count(), mode === 'truncated' ? 1 : 0);
          assert.equal(await card.locator('.mac-badge').innerText(), mode === 'truncated' ? '1000+ 张表' : `${tables.length} 张表`);
          assert.deepEqual(await page.evaluate(() => window.__directoryFixture.calls.sort()), ['/jobs?limit=50', '/tables?limit=1000', '/work?limit=1000']);
          assert.deepEqual(await page.evaluate(() => window.__directoryFixture.opened), []);
          assert.deepEqual(errors, []);
          assert.deepEqual(external, []);
        } finally {await page.close()}
      });
    }
  } finally {await browser.close(); await server.close()}
});
