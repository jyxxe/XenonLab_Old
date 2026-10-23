# Issue #285: visible create-account validation and failure feedback

Baseline `b70631a` (`origin/feature/platform-v1`; originally authored on `d78b50e`, rebased as the base advanced through #234/#72/#99); branch `general-agent/issue-285-account-create-feedback-20261022`.

Previously the 「创建子账号」 button was silently disabled whenever the username or display name was empty or the initial password was shorter than 12 characters, so clicking appeared to do nothing. When a submission did fail, the error only appeared in the distant top `role="status"` paragraph.

## What changed

Desktop-web only:

- New pure module `apps/desktop-web/src/account-create.js` (+ `account-create.d.ts`, matching the existing plain-JS + declaration pattern of `api-error.js`/`acl-state.js`) exports the server-mirroring rules: username 3–64 characters starting alphanumeric and limited to letters/digits/`.`/`_`/`-`, required display name, initial password 12–256 characters, `validateNewAccount(fields)` returning per-field Chinese messages and the first invalid field, and `formatCreateAccountError(error)` mapping `409`/`USERNAME_EXISTS` → 登录名已存在，请换一个登录名, `422` → the server validation message (or a generic server-validation message), and `403 INTERACTIVE_LOGIN_REQUIRED` → 请通过统一登录交互登录后再创建子账号.
- `apps/desktop-web/src/Accounts.tsx` create form:
  - the 「创建子账号」 button stays enabled unless a request is in flight; clicking with invalid input shows visible per-field messages (`role="alert"`), focuses the first invalid input and sends no `POST`;
  - always-visible hints under 登录名 (3–64 位，以字母或数字开头，可含 . _ -) and 初始密码 (至少 12 个字符), wired through `aria-describedby`, plus `aria-invalid` on the three inputs;
  - submission errors are shown both adjacent to the create form (`role="alert"`, near-form `.account-create-error`) and in the existing top `role="status"`, mapped through `formatCreateAccountError`; entered values are preserved and the near-form error clears on the next submit/success;
  - the successful path is unchanged: fields reset, tree reloads, created account is selected, and 子账号已创建，可使用统一登录入口 is shown.
- Tests: `apps/desktop-web/tests/account-create.test.mjs` (6 `node:test` tests covering username/name/password boundaries, first-invalid-field ordering and error mapping), browser acceptance test `tests/browser-acceptance-account-create.test.mjs` with synthetic in-memory fixture `tests/account-create-fixture.html`, registered in `scripts/run-browser-regression.mjs`. The fixture uses only synthetic inputs and public placeholder passwords; it never contacts a real account API.

No backend, API/schema, dependency, migration or production change. The rest of the Accounts component behavior is untouched.

## Validation (actual results)

From `apps/desktop-web`:

```sh
npm test
```

Result: 128 tests passed, 0 failed, 0 cancelled, 0 skipped (includes 6 new `account-create.test.mjs` tests). The new file alone: 6 passed, 0 failed.

```sh
npm run build
```

Result: `tsc --noEmit` passed and `vite build` succeeded (95 modules transformed, `dist/assets/index-*.js` 526.30 kB; only the pre-existing >500 kB chunk-size advisory). Exit 0.

From the worktree root:

```sh
git add -A && git diff --check
```

Result: no whitespace errors. `git diff --cached --check` also passed. The `apps/desktop-web/node_modules` symlink is excluded by the common git excludes file and is not staged/committed; `git status --short` showed only the intended source/test/doc files.

## Browser acceptance (executed)

`tests/browser-acceptance-account-create.test.mjs` (registered in
`scripts/run-browser-regression.mjs`) renders the real `Accounts` component in
headless Chrome against a synthetic in-memory identity/account/key API and
verifies:

- the create button is never silently disabled and both hints are always
  visible;
- an empty submit shows 请填写登录名/请填写显示名称/请填写初始密码, focuses
  登录名, sets `aria-invalid`/`aria-describedby`, and sends no `POST`;
- `ab` + a short password show the length messages, preserve input and send no
  `POST`;
- valid input sends exactly one `POST /auth/accounts` with
  `{parent_id, username, name, password, inherit_all: false, grants: ['desktop:read']}`
  and shows the success message with reset fields;
- a stubbed `409 USERNAME_EXISTS` shows 登录名已存在，请换一个登录名 next to the
  form (and in the top status) while keeping the entered values;
- the near-form error disappears on the next successful submit;
- no page errors occur.

The Playwright Chromium runtime (headless shell `chromium-1243`) was installed
into the harness browser cache only for this verification; no repository or
production dependency was added. Run:

```sh
cd apps/desktop-web
CHROME_BIN=/var/lib/devagent/.cache/ms-playwright/chromium-1243/chrome-linux64/chrome \
  node --test tests/browser-acceptance-account-create.test.mjs
```

Result: 1 passed, 0 failed. One locator had to be scoped to the create `<details>`
because 显示名称 also exists in the account-detail form; that is a test-only fix
and the browser run then passed.

Full registered browser regression (`npm run test:browser-regression`) also passed
on the rebased base: 57 tests passed, 0 failed, about 284 s. This machine disables
unprivileged user namespaces via AppArmor, so the direct-`spawn` acceptance
tests need a wrapper script that adds `--no-sandbox`; that wrapper exists only in
`/tmp` for this run and is not part of the repository. Playwright-launched tests
(the new one included) need no wrapper.

## Operations and rollback

No production deployment, identity/account mutation, dependency change or secret/runtime change occurred; only synthetic fixture data and public placeholder passwords are used in tests. No migration is required. Rollback: revert `apps/desktop-web/src/Accounts.tsx`, delete `apps/desktop-web/src/account-create.js` and `apps/desktop-web/src/account-create.d.ts`, delete the `tests/*account-create*` files and this document, and remove the one registration line from `apps/desktop-web/scripts/run-browser-regression.mjs`.
