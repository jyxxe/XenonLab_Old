import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

// Content checks for the fixed docs/agent-prompts interface consumed by
// apps/desktop-web/src/agent-prompts.ts (Vite `?raw` imports).
const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const repoRoot = path.resolve(appRoot, '../..');
const promptsDir = path.join(repoRoot, 'docs/agent-prompts');
const expectedIds = ['codex', 'dsh', 'opencode', 'general-agent-lan', 'general-agent-wan'];
const networks = ['lan', 'wan'];

async function readManifest() {
  return JSON.parse(await fs.readFile(path.join(promptsDir, 'manifest.json'), 'utf8'));
}

async function readBody(file) {
  return fs.readFile(path.join(promptsDir, file), 'utf8');
}

test('prompt manifest declares exactly the five expected prompt ids and files', async () => {
  const manifest = await readManifest();
  assert.equal(typeof manifest.version, 'number', 'manifest.version is numeric');
  assert.match(String(manifest.updated), /^\d{4}-\d{2}-\d{2}/, 'manifest.updated is an ISO date');
  assert.ok(Array.isArray(manifest.prompts), 'manifest.prompts is an array');
  assert.equal(manifest.prompts.length, 5, 'manifest contains exactly five prompts');
  assert.deepEqual(manifest.prompts.map(prompt => prompt.id), expectedIds, 'ids are exactly the fixed five, in order');
  assert.deepEqual(manifest.prompts.map(prompt => prompt.file), expectedIds.map(id => `${id}.md`), 'each id maps to its own Markdown file');
  for (const prompt of manifest.prompts) {
    for (const field of ['title', 'audience', 'summary']) {
      assert.equal(typeof prompt[field], 'string', `${prompt.id}.${field} is a string`);
      assert.ok(prompt[field].trim().length > 0, `${prompt.id}.${field} is not empty`);
    }
    assert.ok(networks.includes(prompt.network), `${prompt.id}.network is lan or wan`);
  }
});

// These are the top-level sections required of every role prompt (task contract).
// Wording is free-form, so match section keywords rather than exact headings.
const requiredSections = [
  ['角色/边界', /^##\s+.*(角色|边界)/m],
  ['入口/工具', /^##\s+.*(入口|工具|获取信息|自主获取)/m],
  ['工作循环', /^##\s+.*工作循环/m],
  ['验证/证据', /^##\s+.*(验证|证据)/m],
  ['禁止事项', /^##\s+.*禁止/m],
];

test('every prompt body is non-empty Markdown with the required top-level sections', async () => {
  const manifest = await readManifest();
  for (const prompt of manifest.prompts) {
    const body = await readBody(prompt.file);
    assert.ok(body.trim().length > 0, `${prompt.file} is not empty`);
    assert.match(body, /^#\s+\S/m, `${prompt.file} starts with a Markdown H1`);
    for (const [label, pattern] of requiredSections) {
      assert.match(body, pattern, `${prompt.file} contains the ${label} section`);
    }
  }
});

test('prompt bodies contain no credential material', async () => {
  const manifest = await readManifest();
  for (const prompt of manifest.prompts) {
    const body = await readBody(prompt.file);
    assert.doesNotMatch(body, /BEGIN [A-Z ]*PRIVATE KEY/i, `${prompt.file} has no private key marker`);
    assert.doesNotMatch(body, /[0-9a-fA-F]{40,}/, `${prompt.file} has no 40+ character hex run (token/secret-looking)`);
    assert.doesNotMatch(body, /client_secret\s*[=:]\s*\S+/i, `${prompt.file} has no client_secret assignment`);
  }
  const note = String((await readManifest()).note ?? '');
  assert.match(note, /credential|凭据/i, 'manifest note states the prompts carry no credentials');
});

test('LAN and WAN general-agent prompts differ substantively', async () => {
  const lan = await readBody('general-agent-lan.md');
  const wan = await readBody('general-agent-wan.md');
  assert.ok(lan.length > 400, 'LAN prompt has substantive body content');
  assert.ok(wan.length > 400, 'WAN prompt has substantive body content');
  assert.notEqual(lan, wan, 'LAN and WAN prompts are not copies');
  assert.match(lan, /192\.168\.0\.\d|192\.168\.0\.x/, 'LAN prompt documents the 192.168.0.x intranet entry');
  const intranetLines = wan.split('\n').filter(line => /192\.168\.0\./.test(line));
  assert.ok(intranetLines.length > 0, 'WAN prompt explicitly discusses the intranet addresses it must not use');
  assert.ok(
    intranetLines.some(line => /禁止|不得|不可|不要|严禁/.test(line)),
    'WAN prompt explicitly forbids/rejects 192.168.0.x intranet addresses',
  );
  assert.match(wan, /xenon\.jyxxe\.com/, 'WAN prompt documents the public origin');
});

test('agent-prompts module imports the manifest and all five bodies with Vite ?raw', async () => {
  const source = await fs.readFile(path.join(appRoot, 'src/agent-prompts.ts'), 'utf8');
  assert.match(source, /docs\/agent-prompts\/manifest\.json\?raw/, 'manifest is imported with ?raw');
  for (const id of expectedIds) {
    assert.match(source, new RegExp(`docs/agent-prompts/${id}\\.md\\?raw`), `${id}.md is imported with ?raw`);
  }
});
