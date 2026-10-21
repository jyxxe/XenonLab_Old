import test from 'node:test';
import assert from 'node:assert/strict';
import {createRequestLifetime} from '../src/library/request-lifetime.js';

test('expired workspaces cannot read late results or start follow-up requests', async () => {
  const lifetime = createRequestLifetime();
  let finish;
  let calls = 0;
  const read = lifetime.bind(() => { calls++; return new Promise(resolve => { finish = resolve; }); });
  await assert.rejects(read(), {code: 'SESSION_CHANGED'});
  assert.equal(calls, 0);
  lifetime.activate();
  const pending = read();
  lifetime.end();
  finish({privateValue: 'old owner'});
  await assert.rejects(pending, {code: 'SESSION_CHANGED'});
  await assert.rejects(read(), {code: 'SESSION_CHANGED'});
  assert.equal(calls, 1);
});

test('effect replay does not revive old requests; current failures keep their metadata', async () => {
  const lifetime = createRequestLifetime();
  lifetime.activate();
  let finish;
  const pending = lifetime.bind(() => new Promise(resolve => { finish = resolve; }))();
  lifetime.end();
  lifetime.activate();
  finish('old generation');
  await assert.rejects(pending, {code: 'SESSION_CHANGED'});
  const failure = Object.assign(new Error('forbidden'), {status: 403, requestId: 'current-id'});
  await assert.rejects(lifetime.bind(async () => { throw failure; })(), error => error === failure);
  assert.equal(await lifetime.bind(async () => 'current generation')(), 'current generation');
});
