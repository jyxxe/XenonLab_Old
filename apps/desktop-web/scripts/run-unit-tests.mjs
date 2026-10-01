import {readdirSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const testFiles = readdirSync(path.join(appRoot, 'tests'))
  .filter(file => file.endsWith('.test.mjs') && !file.startsWith('browser-acceptance-') && file !== 'browser-smoke.test.mjs')
  .sort()
  .map(file => path.join('tests', file));

if (!testFiles.length) {
  console.error('No frontend unit tests were found.');
  process.exit(2);
}

const result = spawnSync(process.execPath, ['--test', ...testFiles], {cwd: appRoot, stdio: 'inherit'});
if (result.error) throw result.error;
process.exit(result.status ?? 1);
