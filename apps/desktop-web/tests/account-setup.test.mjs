import test from 'node:test';
import assert from 'node:assert/strict';
import {APIRequestError, isUnregisteredAccountError} from '../src/api-error.js';

test('only the account authority not-registered response enters first-use setup', () => {
  assert.equal(isUnregisteredAccountError(new APIRequestError('account not registered', 404, 'ACCOUNT_NOT_FOUND')), true);
  assert.equal(isUnregisteredAccountError(new APIRequestError('not found', 404, 'TABLE_NOT_FOUND')), false);
  assert.equal(isUnregisteredAccountError(new APIRequestError('account not registered', 503, 'ACCOUNT_NOT_FOUND')), false);
  assert.equal(isUnregisteredAccountError(new Error('account not registered')), false);
});
