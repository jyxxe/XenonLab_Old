import assert from 'node:assert/strict';
import test from 'node:test';
import {
  REFRESH_SKEW_MS,
  accessExpired,
  canReplayRequest,
  classifyFailure,
  emptyAccess,
  failureMessage,
  rememberAccess,
  retryAfterDelayMs,
  shouldRefresh,
} from '../src/session-policy.js';

const NOW = 1_700_000_000_000;

test('rememberAccess converts the relative lifetime into an absolute expiry', () => {
  const next = rememberAccess(emptyAccess(), {token: 'abc', expires: 300}, NOW);
  assert.deepEqual(next, {token: 'abc', expiresAt: NOW + 300_000});
});

test('rememberAccess keeps the previous token when the response is unusable', () => {
  const previous = {token: 'old', expiresAt: NOW + 1000};
  assert.equal(rememberAccess(previous, null, NOW), previous);
  assert.equal(rememberAccess(previous, {token: '', expires: 10}, NOW), previous);
  assert.deepEqual(rememberAccess(previous, {token: 'opaque', expires: 'x'}, NOW), {token: 'opaque', expiresAt: 0});
});

test('shouldRefresh refreshes missing, expired and near-expiry tokens only', () => {
  assert.equal(shouldRefresh(emptyAccess(), NOW), true);
  assert.equal(shouldRefresh({token: 'live', expiresAt: NOW + 10 * 60_000}, NOW), false);
  assert.equal(shouldRefresh({token: 'soon', expiresAt: NOW + 30_000}, NOW), true, 'inside the refresh skew');
  assert.equal(shouldRefresh({token: 'gone', expiresAt: NOW - 1}, NOW), true);
  assert.equal(shouldRefresh({token: 'opaque', expiresAt: 0}, NOW), false, 'kept until a 401 rejects it');
});

test('accessExpired only reports tokens with a known past expiry', () => {
  assert.equal(accessExpired({token: 'x', expiresAt: NOW - 1}, NOW), true);
  assert.equal(accessExpired({token: 'x', expiresAt: NOW + 1}, NOW), false);
  assert.equal(accessExpired({token: 'x', expiresAt: 0}, NOW), false);
  assert.equal(accessExpired(emptyAccess(), NOW), false);
});

test('classifyFailure never turns transient failures into reauthorization', () => {
  assert.equal(classifyFailure(401, 'NO_SESSION'), 'signed-out');
  assert.equal(classifyFailure(401, 'DESKTOP_ACCESS_REVOKED'), 'reauthorize');
  assert.equal(classifyFailure(401, undefined), 'reauthorize');
  assert.equal(classifyFailure(429, 'AUTHORIZATION_RATE_LIMITED'), 'retry');
  assert.equal(classifyFailure(500, 'INTERNAL_ERROR'), 'retry');
  assert.equal(classifyFailure(503, 'AUTHORIZATION_UNAVAILABLE'), 'retry');
  assert.equal(classifyFailure(403, 'XL_FORBIDDEN'), 'retry');
});

test('failureMessage explains the wait and that the workspace is kept', () => {
  assert.match(failureMessage('retry', 30, '请求过于频繁'), /30/);
  assert.match(failureMessage('retry', 0, '服务暂时不可用'), /工作区将保留/);
  assert.match(failureMessage('reauthorize', 0, ''), /重新授权/);
  assert.equal(REFRESH_SKEW_MS > 0, true);
});

test('retryAfterDelayMs honors delta seconds and HTTP dates', () => {
  assert.equal(retryAfterDelayMs('2',NOW),2000);
  assert.equal(retryAfterDelayMs(new Date(NOW+3500).toUTCString(),NOW),3000);
  assert.equal(retryAfterDelayMs('not-a-date',NOW),0);
  assert.equal(retryAfterDelayMs('0',NOW),0);
});

test('canReplayRequest accepts reusable reads but excludes one-shot POST bodies', () => {
  assert.equal(canReplayRequest('/attachments/1/content', 'GET'), true);
  assert.equal(canReplayRequest('/attachments/1/content', 'GET', 'unexpected body'), false);
  assert.equal(canReplayRequest('/attachments/1/content', 'GET', new ReadableStream()), false);
  assert.equal(canReplayRequest('/query', 'POST', JSON.stringify({expression: 'FROM "/**"'})), true);
  assert.equal(canReplayRequest('/search/semantic', 'POST', JSON.stringify({query: 'fixture'})), true);
  assert.equal(canReplayRequest('/query', 'POST', new FormData()), false);
  assert.equal(canReplayRequest('/query', 'POST', new ReadableStream()), false);
  assert.equal(canReplayRequest('/attachments', 'POST', 'form data'), false);
});
