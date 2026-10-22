import test from 'node:test';
import assert from 'node:assert/strict';
import {calendarMonth, calendarToday, shiftCalendarMonth} from '../src/calendar.js';

test('civil months include leap years, century rules and their correct starting weekday', () => {
  for (const [year, days] of [[2024, 29], [2023, 28], [1900, 28], [2000, 29], [24, 29]]) {
    assert.equal(calendarMonth({year, month: 2}).days, days);
  }
  assert.deepEqual(calendarMonth({year: 2026, month: 10}), {weekday: 4, days: 31, label: '2026年10月'});
  assert.deepEqual(calendarMonth({year: 2024, month: 2}), {weekday: 4, days: 29, label: '2024年2月'});
});

test('month navigation rolls over years in both directions independently of date and timezone', () => {
  assert.deepEqual(shiftCalendarMonth({year: 2023, month: 12}, 1), {year: 2024, month: 1});
  assert.deepEqual(shiftCalendarMonth({year: 2024, month: 1}, -1), {year: 2023, month: 12});
  assert.deepEqual(shiftCalendarMonth({year: 2024, month: 1}, 13), {year: 2025, month: 2});
  assert.deepEqual(shiftCalendarMonth({year: 2024, month: 1}, -13), {year: 2022, month: 12});
  assert.deepEqual(shiftCalendarMonth({year: 99, month: 12}, 1), {year: 100, month: 1});
});

test('today uses the configured timezone across civil day, month and year boundaries', () => {
  const yearEnd = new Date('2023-12-31T12:30:00Z');
  assert.deepEqual(calendarToday(yearEnd, 'UTC'), {year: 2023, month: 12, day: 31});
  assert.deepEqual(calendarToday(yearEnd, 'Pacific/Kiritimati'), {year: 2024, month: 1, day: 1});
  const monthStart = new Date('2026-10-01T00:30:00Z');
  assert.deepEqual(calendarToday(monthStart, 'America/Los_Angeles'), {year: 2026, month: 9, day: 30});
  assert.deepEqual(calendarToday(monthStart, 'UTC'), {year: 2026, month: 10, day: 1});
});

test('system or invalid timezone consistently falls back to the browser calendar and label', () => {
  const now = new Date('2026-10-03T01:30:00Z');
  const expected = {year: now.getFullYear(), month: now.getMonth() + 1, day: now.getDate()};
  assert.deepEqual(calendarToday(now, 'system'), expected);
  assert.deepEqual(calendarToday(now, 'not/a-timezone'), expected);
  assert.equal(calendarMonth(calendarToday(now, 'not/a-timezone')).label, `${expected.year}年${expected.month}月`);
});
