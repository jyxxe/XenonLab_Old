import {spawnSync} from 'node:child_process';
import {existsSync} from 'node:fs';
import {chromium} from '@playwright/test';

const executablePath = process.env.CHROME_BIN || chromium.executablePath();
if (!existsSync(executablePath)) {
  console.error(`Chromium is required for browser smoke tests; install it with "npx playwright install chromium". Missing: ${executablePath}`);
  process.exit(2);
}

console.log(`Browser smoke uses ${executablePath}`);
const result = spawnSync(process.execPath, ['--test', 'tests/browser-smoke.test.mjs'], {
  cwd: new URL('..', import.meta.url),
  env: {...process.env, CHROME_BIN: executablePath},
  stdio: 'inherit',
});
if (result.error) throw result.error;
process.exit(result.status ?? 1);
