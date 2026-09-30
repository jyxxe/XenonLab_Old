import test from 'node:test';
import assert from 'node:assert/strict';
import {isExpectedUnregisteredAccountTreeResponse, mayInitializeAccountTree} from '../../../scripts/account_tree_acceptance.mjs';

test('only the account-tree not-registered 404 is an expected first-use response', () => {
  const payload = {error: {code: 'ACCOUNT_NOT_FOUND', message: 'account not registered'}};
  assert.equal(isExpectedUnregisteredAccountTreeResponse('GET', '/api/v1/auth/accounts', 404, payload), true);
  assert.equal(isExpectedUnregisteredAccountTreeResponse('GET', '/api/v1/auth/accounts', 404, {error: {code: 'TABLE_NOT_FOUND'}}), false);
  assert.equal(isExpectedUnregisteredAccountTreeResponse('GET', '/api/v1/auth/accounts', 500, payload), false);
  assert.equal(isExpectedUnregisteredAccountTreeResponse('GET', '/api/v1/users/me', 404, payload), false);
  assert.equal(isExpectedUnregisteredAccountTreeResponse('POST', '/api/v1/auth/accounts', 404, payload), false);
});

test('account-tree initialization is offered only to human desktop administrators', () => {
  assert.equal(mayInitializeAccountTree({kind: 'human', scopes: ['desktop:read', 'desktop:admin']}), true);
  assert.equal(mayInitializeAccountTree({kind: 'human', scopes: ['desktop:read']}), false);
  assert.equal(mayInitializeAccountTree({kind: 'service', scopes: ['desktop:admin']}), false);
  assert.equal(mayInitializeAccountTree(null), false);
});
