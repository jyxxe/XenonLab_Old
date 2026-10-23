import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {existsSync} from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium, expect} from '@playwright/test';

// Real System component in headless Chromium. The prompt bodies come from the
// repository docs via Vite `?raw` imports, so this acceptance run also proves the
// panel does not depend on a runtime static-file route.
const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const promptsDir = path.resolve(appRoot, '../../docs/agent-prompts');
const evidence = path.resolve(appRoot, '../../runtime/verification/agent-prompts-panel');

test('Xenon Panel prompt catalogue lists, previews, copies and downloads bundled prompts', async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required: ${executablePath}`);
  const manifest = JSON.parse(await fs.readFile(path.join(promptsDir, 'manifest.json'), 'utf8'));
  const bodies = Object.fromEntries(
    await Promise.all(manifest.prompts.map(async prompt => [prompt.id, await fs.readFile(path.join(promptsDir, prompt.file), 'utf8')])),
  );
  const server = await createServer({
    root: appRoot,
    server: {host: '127.0.0.1', port: 0, watch: null},
    cacheDir: path.join(evidence, 'vite-cache'),
  });
  let browser;
  const pageErrors = [], staticDocRequests = [], external = [];
  try {
    await fs.mkdir(evidence, {recursive: true});
    await server.listen();
    const origin = new URL(server.resolvedUrls.local[0]).origin;
    browser = await chromium.launch({executablePath, headless: true});
    const context = await browser.newContext({viewport: {width: 1000, height: 800}, locale: 'zh-CN', acceptDownloads: true});
    await context.grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {});
    await context.route('**/*', route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) { external.push(url.href); return route.abort(); }
      return route.continue();
    });
    const page = await context.newPage();
    page.on('pageerror', error => pageErrors.push(error.message));
    page.on('request', request => {
      const url = new URL(request.url());
      if (url.origin === origin && url.pathname.startsWith('/docs/')) staticDocRequests.push(url.pathname);
    });

    await page.goto(`${origin}/tests/agent-prompts-fixture.html`);
    const promptsTab = page.getByRole('button', {name: '提示词', exact: true});
    await promptsTab.click();
    await expect(promptsTab).toHaveAttribute('aria-current', 'page');

    const items = page.locator('[data-testid="agent-prompt-item"]');
    await expect(items).toHaveCount(5);
    const preview = page.locator('[data-testid="agent-prompt-preview"]');
    await expect(preview).toBeVisible();
    await expect(page.getByText('提示词清单（5）', {exact: true})).toBeVisible();
    await expect(page.getByText(/凭据只存放在私有 0600 身份文件/)).toBeVisible();

    // Every catalogue entry renders title, audience, LAN/WAN badge and summary.
    for (const prompt of manifest.prompts) {
      const item = items.filter({hasText: prompt.title});
      await expect(item).toHaveCount(1);
      await expect(item).toContainText(prompt.audience);
      await expect(item).toContainText(prompt.summary);
      await expect(item).toContainText(prompt.network === 'lan' ? '内网' : '外网');
    }

    // The first prompt is selected initially; switching shows a different full body.
    await expect(items.nth(0)).toHaveAttribute('aria-current', 'true');
    const firstPreview = await preview.evaluate(node => node.textContent);
    assert.equal(firstPreview, bodies[manifest.prompts[0].id], 'default preview shows the selected prompt body');
    await items.nth(1).click();
    await expect(items.nth(1)).toHaveAttribute('aria-current', 'true');
    await expect(items.nth(0)).not.toHaveAttribute('aria-current', 'true');
    const secondPreview = await preview.evaluate(node => node.textContent);
    assert.equal(secondPreview, bodies[manifest.prompts[1].id], 'preview shows the newly selected prompt body');
    assert.notEqual(secondPreview, firstPreview, 'switching prompts changes the preview text');
    await expect(page.getByRole('heading', {name: manifest.prompts[1].title, exact: true})).toBeVisible();

    // Copy uses navigator.clipboard and always reports an accessible status.
    const copy = page.getByTestId('agent-prompt-copy');
    await expect(copy).toBeVisible();
    await expect(copy).toHaveAccessibleName(/复制/);
    await copy.click();
    const status = page.getByRole('status');
    await expect(status).toContainText(/已复制|复制失败/);
    if (/已复制/.test(await status.innerText())) {
      const clipboard = await page.evaluate(() => navigator.clipboard.readText());
      assert.equal(clipboard, secondPreview, 'copy places the selected prompt body on the clipboard');
    }

    // Download builds a Blob and an <a download> whose name matches the manifest file.
    const downloadControl = page.getByTestId('agent-prompt-download');
    await expect(downloadControl).toBeVisible();
    await expect(downloadControl).toHaveAccessibleName(/下载/);
    const expectedFile = manifest.prompts[1].file;
    const tempDir = await fs.mkdtemp(path.join(os.tmpdir(), 'xeonolab-agent-prompts-'));
    try {
      const [download] = await Promise.all([page.waitForEvent('download'), downloadControl.click()]);
      assert.equal(download.suggestedFilename(), expectedFile, 'download uses the manifest file name');
      const saved = path.join(tempDir, expectedFile);
      await download.saveAs(saved);
      assert.equal(await fs.readFile(saved, 'utf8'), secondPreview, 'downloaded Markdown equals the previewed body');
    } finally {
      await fs.rm(tempDir, {recursive: true, force: true});
    }

    // 390px: no horizontal overflow and controls stay reachable.
    await page.setViewportSize({width: 390, height: 844});
    await expect(items).toHaveCount(5);
    await expect(preview).toBeVisible();
    await expect(copy).toBeVisible();
    await expect(downloadControl).toBeVisible();
    const metrics = await page.evaluate(() => ({scrollWidth: document.documentElement.scrollWidth, bodyScrollWidth: document.body.scrollWidth, viewport: innerWidth}));
    assert.ok(metrics.scrollWidth <= metrics.viewport, `document does not overflow horizontally at 390px (scrollWidth ${metrics.scrollWidth})`);
    assert.ok(metrics.bodyScrollWidth <= metrics.viewport, `body does not overflow horizontally at 390px (scrollWidth ${metrics.bodyScrollWidth})`);
    const copyBox = await copy.boundingBox();
    assert.ok(copyBox && copyBox.x >= 0 && copyBox.x + copyBox.width <= 390, 'copy control is fully inside the 390px viewport');

    // Selection keeps working on the narrow viewport and stays inside the page.
    await items.nth(4).click();
    await expect(items.nth(4)).toHaveAttribute('aria-current', 'true');
    const narrowPreview = await preview.evaluate(node => node.textContent);
    assert.equal(narrowPreview, bodies[manifest.prompts[4].id], 'narrow viewport preview shows the selected prompt body');
    const narrowMetrics = await page.evaluate(() => ({scrollWidth: document.documentElement.scrollWidth, viewport: innerWidth}));
    assert.ok(narrowMetrics.scrollWidth <= narrowMetrics.viewport, `document stays within 390px after switching prompts (scrollWidth ${narrowMetrics.scrollWidth})`);
    await page.screenshot({path: path.join(evidence, 'prompts-390.png'), fullPage: true});

    assert.deepEqual(staticDocRequests, [], 'no runtime /docs/... static route requests; content comes from bundled modules');
    assert.deepEqual(external, [], 'no external requests');
    assert.deepEqual(pageErrors, [], 'no page errors');
    await fs.writeFile(path.join(evidence, 'browser-report.json'), JSON.stringify({
      outcome: 'PASS',
      source: 'Real Chromium renders the repository System component with Vite `?raw` prompt imports; API/identity is inert synthetic fixture.',
      checks: ['5 catalogue entries', 'title/audience/LAN-WAN badge/summary', 'preview equals bundled body', 'switching changes preview', 'clipboard copy status', 'Blob download equals body', 'no static docs route', 'no external requests', 'no page errors', '390px no horizontal overflow'],
      staticDocRequests,
      external,
      pageErrors,
    }, null, 2));
  } catch (error) {
    await fs.writeFile(path.join(evidence, 'browser-failure.json'), JSON.stringify({pageErrors, staticDocRequests, external, error: String(error)}, null, 2)).catch(() => {});
    throw error;
  } finally {
    try { await browser?.close(); } finally { await server.close(); }
  }
});
