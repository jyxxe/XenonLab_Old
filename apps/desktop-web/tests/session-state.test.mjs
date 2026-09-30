import assert from 'node:assert/strict';
import test from 'node:test';
import {initialSessionState, transitionSession} from '../src/session-state.js';

test('temporary failures preserve the connected workspace and cached token', () => {
  const current = {
    ...initialSessionState,
    phase: 'connected',
    token: 'still-held-in-memory',
    persistent: true,
    windows: [{id: 'window-1'}],
  };
  const next = transitionSession(current, {type: 'temporary-failure', message: 'provider timeout'});

  assert.equal(next.phase, 'retrying');
  assert.equal(next.token, current.token);
  assert.equal(next.persistent, true);
  assert.deepEqual(next.windows, current.windows);
});

test('reauthorization state preserves windows until an explicit user action', () => {
  const current = {
    ...initialSessionState,
    phase: 'connected',
    token: 'cached-token',
    persistent: true,
    windows: [{id: 'knowledge'}],
  };
  const next = transitionSession(current, {type: 'reauthorization-required', message: 'password changed'});

  assert.equal(next.phase, 'reauthorize');
  assert.equal(next.token, current.token);
  assert.equal(next.persistent, current.persistent);
  assert.deepEqual(next.windows, current.windows);
});

test('only the explicit logout event clears the session and workspace', () => {
  const current = {phase: 'reauthorize', message: '', token: 'cached', persistent: true, windows: [{id: 'w'}]};
  const required = transitionSession(current, {type: 'reauthorization-required'});
  const loggedOut = transitionSession(required, {type: 'user-logout'});

  assert.equal(required.token, current.token);
  assert.equal(loggedOut.phase, 'signed-out');
  assert.equal(loggedOut.token, '');
  assert.equal(loggedOut.persistent, false);
  assert.deepEqual(loggedOut.windows, []);
});
