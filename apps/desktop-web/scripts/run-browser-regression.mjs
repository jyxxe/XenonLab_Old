import {spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {chromium} from '@playwright/test';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const executablePath = process.env.CHROME_BIN || chromium.executablePath();
if (!existsSync(executablePath)) {
  console.error(`Chromium is required for browser regressions; install it with "npx playwright install chromium". Missing: ${executablePath}`);
  process.exit(2);
}

console.log(`Browser regressions use ${executablePath}`);
const tests = [
  'tests/browser-acceptance-session.test.mjs',
  'tests/browser-acceptance-work.test.mjs',
  'tests/browser-acceptance-shell.test.mjs',
  'tests/browser-acceptance-issues-narrow.test.mjs',
  'tests/browser-acceptance-query-drafts.test.mjs',
  'tests/browser-acceptance-search-return.test.mjs',
  'tests/browser-acceptance-account-patch.test.mjs',
];
const result = spawnSync(process.execPath, ['--test', '--test-concurrency=1', ...tests], {
  cwd: appRoot,
  env: {...process.env, CHROME_BIN: executablePath},
  stdio: 'inherit',
});
if (result.error) throw result.error;
process.exit(result.status ?? 1);
