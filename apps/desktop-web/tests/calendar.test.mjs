import assert from 'node:assert/strict';
import test from 'node:test';
import {monthMatrix, zonedToday} from '../src/calendar.js';

test('monthMatrix lays out a Monday-first grid for March 2026', () => {
  // 2026-03-01 is a Sunday, so the first row leads with six February days.
  const weeks = monthMatrix(2026, 2);
  assert.equal(weeks.length, 6);
  weeks.forEach(week => assert.equal(week.length, 7));
  assert.deepEqual(weeks[0].map(cell => cell.day), [23, 24, 25, 26, 27, 28, 1]);
  assert.deepEqual(weeks[0].map(cell => cell.inMonth), [false, false, false, false, false, false, true]);
  assert.deepEqual(weeks[1].map(cell => cell.day), [2, 3, 4, 5, 6, 7, 8]);
  // March has 31 days; the trailing cells spill into April.
  const last = weeks[5];
  assert.deepEqual(last.map(cell => cell.day), [30, 31, 1, 2, 3, 4, 5]);
  assert.deepEqual(last.map(cell => cell.inMonth), [true, true, false, false, false, false, false]);
});

test('monthMatrix covers February with a Sunday first day', () => {
  // 2026-02-01 is a Sunday; the month fits entirely inside the six rows.
  const weeks = monthMatrix(2026, 1);
  assert.equal(weeks[0][6].day, 1);
  assert.equal(weeks[0][6].inMonth, true);
  const inMonth = weeks.flat().filter(cell => cell.inMonth);
  assert.equal(inMonth.length, 28);
  assert.deepEqual(inMonth.map(cell => cell.day), Array.from({length: 28}, (_, i) => i + 1));
});

test('monthMatrix supports a custom week start', () => {
  const weeks = monthMatrix(2026, 2, 0);
  // Sunday-first: March 1st leads the first row.
  assert.deepEqual(weeks[0].map(cell => cell.day), [1, 2, 3, 4, 5, 6, 7]);
});

test('monthMatrix rejects out-of-range input', () => {
  assert.throws(() => monthMatrix(2026, 12), RangeError);
  assert.throws(() => monthMatrix(2026, -1), RangeError);
  assert.throws(() => monthMatrix('2026', 2), RangeError);
});

test('zonedToday resolves the day in the given timezone', () => {
  // 2026-10-02 00:30 in Shanghai is still 2026-10-01 in UTC.
  const shanghai = zonedToday('Asia/Shanghai', new Date('2026-10-02T00:30:00+08:00'));
  assert.deepEqual(shanghai, {year: 2026, month: 9, day: 2});
  const utc = zonedToday('UTC', new Date('2026-10-02T00:30:00+08:00'));
  assert.deepEqual(utc, {year: 2026, month: 9, day: 1});
});

test('zonedToday falls back to the host zone for system and bad input', () => {
  const now = new Date('2026-10-02T12:00:00Z');
  const system = zonedToday('system', now);
  const host = zonedToday('', now);
  assert.deepEqual(system, host);
  assert.equal(zonedToday('Not/AZone', now), null);
});
