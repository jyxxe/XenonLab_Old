import test from 'node:test';
import assert from 'node:assert/strict';
import {APIRequestError, apiErrorFromResponse, isUnregisteredAccountError} from '../src/api-error.js';
import {describeError} from '../src/library/logic.js';

test('API errors keep server request IDs distinct from semantic error codes', () => {
  const response = new Response('', {status: 403, headers: {'X-Request-ID': 'header-request'}});
  const error = apiErrorFromResponse(response, {error: {code: 'XL_FORBIDDEN', message: 'denied', request_id: 'body-request'}});
  assert.ok(error instanceof APIRequestError);
  assert.equal(error.status, 403);
  assert.equal(error.code, 'XL_FORBIDDEN');
  assert.equal(error.requestId, 'body-request');
  assert.equal(error.message, 'denied');
  const info = describeError(error);
  assert.equal(info.title, '权限不足');
  assert.equal(info.code, 'XL_FORBIDDEN');
  assert.equal(info.requestId, 'body-request');
});

test('HTML/raw/malformed errors use header correlation and retain HTTP context', () => {
  const response = new Response('', {status: 404, headers: {'X-Request-ID': 'raw-request'}});
  for (const payload of [null, 'gateway error', {error: {message: {}, code: [], request_id: 123}}, {error: {request_id: ' '}}]) {
    const error = apiErrorFromResponse(response, payload);
    assert.equal(error.message, 'HTTP 404');
    assert.equal(error.requestId, 'raw-request');
    assert.equal(error.code, undefined);
  }
  const validation = apiErrorFromResponse(response, {detail: [{msg: 'invalid upload'}]});
  assert.equal(validation.message, 'invalid upload');
});

test('missing request IDs are never invented; existing account classification stays exact', () => {
  const error = apiErrorFromResponse(new Response('', {status: 404}), {error: {code: 'ACCOUNT_NOT_FOUND', message: 'not registered'}});
  assert.equal(error.requestId, undefined);
  assert.equal(describeError(error).requestId, undefined);
  assert.equal(isUnregisteredAccountError(error), true);
  assert.equal(describeError(new APIRequestError('network', 0, 'NETWORK_ERROR')).requestId, undefined);
});
