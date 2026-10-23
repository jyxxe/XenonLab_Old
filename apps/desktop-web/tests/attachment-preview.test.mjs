import test from 'node:test';
import assert from 'node:assert/strict';
import {PREVIEW_MEDIA_TYPES, canPreviewAttachment} from '../src/attachment-preview.js';

test('only server-detected PNG attachments get an in-app preview', () => {
  assert.deepEqual([...PREVIEW_MEDIA_TYPES], ['image/png']);
  for (const value of ['image/png', 'IMAGE/PNG', ' image/png ']) {
    assert.equal(canPreviewAttachment(value), true, String(value));
  }
  for (const value of ['application/pdf', 'image/jpeg', 'text/plain', '', null, undefined, 42, {}]) {
    assert.equal(canPreviewAttachment(value), false, String(value));
  }
});
