import test from 'node:test';
import assert from 'node:assert/strict';
import {addQueryRecent, QUERY_EXPRESSION_LIMIT, QUERY_RECENT_LIMIT, queryDraftsStorageKey, queryExpressionLength, readQueryDrafts, writeQueryDrafts} from '../src/library/query-drafts.js';

function memoryStorage() {
  const values = new Map();
  return {
    values,
    getItem(key) { return values.get(key) ?? null; },
    setItem(key, value) { values.set(key, value); },
  };
}

test('query draft keys require an account subject and are isolated per subject', () => {
  const storage = memoryStorage();
  assert.equal(queryDraftsStorageKey(null), null);
  assert.equal(queryDraftsStorageKey('  '), null);
  assert.equal(readQueryDrafts(storage, null).ready, false);
  assert.equal(storage.values.size, 0, 'unconfirmed identities never touch a shared anonymous key');
  assert.notEqual(queryDraftsStorageKey('alice'), queryDraftsStorageKey('bob'));
});

test('saved data contains only expression and timestamps; recent is deduplicated and capped at 20', () => {
  const storage = memoryStorage();
  const expression = 'FROM "/**" LIMIT 100';
  let recent = [];
  for (let index = 0; index < 23; index += 1) recent = addQueryRecent(recent, `query-${index}`, `2026-10-${String(index + 1).padStart(2, '0')}`);
  recent = addQueryRecent(recent, expression, '2026-10-30');
  recent = addQueryRecent(recent, expression, '2026-10-31');
  assert.equal(recent.length, QUERY_RECENT_LIMIT);
  assert.equal(recent.filter(item => item.expression === expression).length, 1);
  const result = writeQueryDrafts(storage, 'alice', {expression, updated_at: '2026-10-31'}, recent);
  assert.equal(result.error, null);
  const saved = JSON.parse(storage.values.get(queryDraftsStorageKey('alice')));
  assert.deepEqual(Object.keys(saved).sort(), ['draft', 'recent', 'version']);
  assert.deepEqual(Object.keys(saved.draft).sort(), ['expression', 'updated_at']);
  assert.equal('rows' in saved || 'token' in saved || 'authorization' in saved, false);
  assert.deepEqual(readQueryDrafts(storage, 'alice').draft, {expression, updated_at: '2026-10-31'});
  assert.equal(readQueryDrafts(storage, 'bob').draft, null);
});

test('expression limit matches Python code-point length for BMP and supplementary characters', () => {
  assert.equal(QUERY_EXPRESSION_LIMIT, 8192);
  assert.equal(queryExpressionLength('😀'.repeat(8192)), 8192);
  assert.equal(queryExpressionLength('😀'.repeat(8193)), 8193);
  assert.equal(writeQueryDrafts(memoryStorage(), 'alice', {expression: 'x'.repeat(8193), updated_at: ''}, []).error instanceof Error, true);
});

test('storage read and write exceptions are returned for UI recovery instead of thrown', () => {
  const readFailure = {getItem() { throw new DOMException('blocked', 'SecurityError'); }};
  const written = readQueryDrafts(readFailure, 'alice');
  assert.equal(written.ready, false);
  assert.equal(written.error.name, 'SecurityError');
  const writeFailure = {setItem() { throw new DOMException('quota', 'QuotaExceededError'); }};
  const saved = writeQueryDrafts(writeFailure, 'alice', {expression: 'FROM "/**"', updated_at: ''}, []);
  assert.equal(saved.error.name, 'QuotaExceededError');
});

test('unknown stored schema versions fail closed instead of being overwritten as empty history', () => {
  const storage = memoryStorage();
  storage.setItem(queryDraftsStorageKey('alice'), JSON.stringify({version: 2, draft: {expression: 'from future'}, recent: []}));
  const result = readQueryDrafts(storage, 'alice');
  assert.equal(result.ready, false);
  assert.match(result.error.message, /Unsupported/);
  assert.equal(result.draft, null);
});
