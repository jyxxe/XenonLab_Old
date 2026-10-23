import test from 'node:test';
import assert from 'node:assert/strict';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {createServer} from 'vite';
import {chromium, expect} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

test('create-account form explains validation and maps server failures next to the form', async () => {
  const executablePath = process.env.CHROME_BIN || chromium.executablePath();
  assert.ok(existsSync(executablePath), `Chromium is required; expected executable at ${executablePath}`);
  const server = await createServer({root: appRoot, server: {host: '127.0.0.1', port: 0, hmr: false, watch: null}});
  await server.listen();
  const browser = await chromium.launch({executablePath, headless: true});
  const page = await browser.newPage();
  const pageErrors = [];
  page.on('pageerror', error => pageErrors.push(error.message));
  const createCalls = () => page.evaluate(() => window.__accountCreateCalls?.length ?? 0);
  try {
    await page.goto(`${server.resolvedUrls.local[0]}tests/account-create-fixture.html`);
    await page.locator('summary').filter({hasText: '建立子账号'}).click();

    const createForm = page.locator('details').filter({hasText: '建立子账号'});
    const createButton = createForm.getByRole('button', {name: '创建子账号'});
    const usernameInput = createForm.getByLabel(/^登录名/);
    const nameInput = createForm.getByLabel(/^显示名称/);
    const passwordInput = createForm.getByLabel(/^初始密码/);

    // The button is never silently disabled; hints are always visible.
    await expect(createButton).toBeEnabled();
    await expect(page.getByText('3–64 位，以字母或数字开头，可含 . _ -', {exact: true})).toBeVisible();
    await expect(page.getByText('至少 12 个字符', {exact: true})).toBeVisible();

    // Empty submit: per-field messages, first invalid field focused, no POST.
    await createButton.click();
    await expect(page.getByText('请填写登录名', {exact: true})).toBeVisible();
    await expect(page.getByText('请填写显示名称', {exact: true})).toBeVisible();
    await expect(page.getByText('请填写初始密码', {exact: true})).toBeVisible();
    await expect(usernameInput).toBeFocused();
    assert.equal(await usernameInput.getAttribute('aria-invalid'), 'true');
    assert.match(await usernameInput.getAttribute('aria-describedby'), /create-username-error/);
    assert.equal(await createCalls(), 0, 'invalid empty form must not POST');

    // Invalid username and short password stay client-side with their own messages.
    await usernameInput.fill('ab');
    await nameInput.fill('子账号');
    await passwordInput.fill('short');
    await createButton.click();
    await expect(page.getByText('登录名至少需要 3 个字符', {exact: true})).toBeVisible();
    await expect(page.getByText('初始密码至少需要 12 个字符', {exact: true})).toBeVisible();
    await expect(page.getByText('请填写显示名称', {exact: true})).toHaveCount(0);
    await expect(usernameInput).toHaveValue('ab');
    await expect(nameInput).toHaveValue('子账号');
    await expect(passwordInput).toHaveValue('short');
    assert.equal(await createCalls(), 0, 'client-side rejection must not POST');

    // Valid input sends exactly one POST with the documented body and shows success.
    await usernameInput.fill('child.01');
    await nameInput.fill('Child One');
    await passwordInput.fill('x'.repeat(12));
    await createButton.click();
    await page.waitForFunction(() => window.__accountCreateCalls?.length === 1);
    const validBody = await page.evaluate(() => window.__accountCreateCalls[0].body);
    assert.deepEqual(validBody, {
      parent_id: 'root', username: 'child.01', name: 'Child One',
      password: 'x'.repeat(12), inherit_all: false, grants: ['desktop:read'],
    });
    await expect(page.getByRole('status').filter({hasText: '子账号已创建，可使用统一登录入口'}).first()).toBeVisible();
    await expect(usernameInput).toHaveValue('');
    await expect(nameInput).toHaveValue('');
    await expect(passwordInput).toHaveValue('');

    // A stubbed 409 shows the mapped message next to the form and keeps input.
    await usernameInput.fill('taken');
    await nameInput.fill('Taken Name');
    await passwordInput.fill('y'.repeat(12));
    await createButton.click();
    await page.waitForFunction(() => window.__accountCreateCalls?.length === 2);
    assert.equal(await page.evaluate(() => window.__accountCreateCalls[1].body.username), 'taken');
    const formError = page.locator('.account-create-error');
    await expect(formError).toHaveText('登录名已存在，请换一个登录名');
    await expect(page.getByRole('alert').filter({hasText: '登录名已存在，请换一个登录名'})).toBeVisible();
    await expect(page.getByRole('status').filter({hasText: '登录名已存在，请换一个登录名'}).first()).toBeVisible();
    await expect(usernameInput).toHaveValue('taken');
    await expect(nameInput).toHaveValue('Taken Name');
    await expect(passwordInput).toHaveValue('y'.repeat(12));

    // The near-form error clears on the next successful submit.
    await usernameInput.fill('child.02');
    await createButton.click();
    await page.waitForFunction(() => window.__accountCreateCalls?.length === 3);
    await expect(formError).toHaveCount(0);
    await expect(page.getByRole('status').filter({hasText: '子账号已创建，可使用统一登录入口'}).first()).toBeVisible();
    assert.deepEqual(pageErrors, []);
  } finally {
    await browser.close();
    await server.close();
  }
});
