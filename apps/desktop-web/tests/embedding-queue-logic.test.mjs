import assert from 'node:assert/strict';
import test from 'node:test';
import {
  appendUnique,
  formatQueueDuration,
  formatQueueTime,
  queueStatusLabel,
  queueStatusTone,
  summarizeQueue,
} from '../src/embedding-queue-logic.js';

test('queue status labels and tones cover known states with fallbacks', () => {
  assert.equal(queueStatusLabel('running'), '运行中');
  assert.equal(queueStatusLabel('completed'), '已完成');
  assert.equal(queueStatusLabel('failed'), '失败');
  assert.equal(queueStatusLabel('mystery'), 'mystery');
  assert.equal(queueStatusLabel(null), '—');
  assert.equal(queueStatusTone('running'), 'accent');
  assert.equal(queueStatusTone('completed'), 'success');
  assert.equal(queueStatusTone('failed'), 'danger');
  assert.equal(queueStatusTone('mystery'), 'neutral');
});

test('formatQueueDuration renders milliseconds and seconds', () => {
  assert.equal(formatQueueDuration(0), '0 ms');
  assert.equal(formatQueueDuration(999.4), '999 ms');
  assert.equal(formatQueueDuration(1250), '1.3 s');
  assert.equal(formatQueueDuration(null), '—');
  assert.equal(formatQueueDuration(undefined), '—');
  assert.equal(formatQueueDuration(-5), '—');
});

test('formatQueueTime renders epoch seconds and rejects invalid values', () => {
  const fixed = 1790822400;
  assert.match(formatQueueTime(fixed), /^\d{4}\/\d{1,2}\/\d{1,2}/);
  assert.equal(formatQueueTime(null), '—');
  assert.equal(formatQueueTime(undefined), '—');
  assert.equal(formatQueueTime('not-a-date'), '—');
});

test('appendUnique merges pages by id without duplicates', () => {
  const first = [{id: 3}, {id: 2}];
  assert.deepEqual(appendUnique(first, [{id: 2}, {id: 1}]), [{id: 3}, {id: 2}, {id: 1}]);
  assert.deepEqual(appendUnique([], []), []);
  assert.equal(first.length, 2, 'existing rows are not mutated');
});

test('summarizeQueue normalizes missing or malformed counts', () => {
  assert.deepEqual(summarizeQueue({running: 2, completed: 1000, failed: 3}), {running: 2, completed: 1000, failed: 3});
  assert.deepEqual(summarizeQueue(null), {running: 0, completed: 0, failed: 0});
  assert.deepEqual(summarizeQueue({running: 'many'}), {running: 0, completed: 0, failed: 0});
});
