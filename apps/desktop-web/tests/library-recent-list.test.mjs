import test from 'node:test';
import assert from 'node:assert/strict';
import {RECENT_VISIBLE_LIMIT, recentListDisplay} from '../src/library/logic.js';

const entry = index => ({kind: 'row', id: `row-${index}`, title: `Row ${index}`, at: 1000 - index});
const recents = count => Array.from({length: count}, (_, index) => entry(index));

test('six or fewer recents need no expand control', () => {
  assert.equal(RECENT_VISIBLE_LIMIT, 6);
  for (const count of [0, 1, 6]) {
    const display = recentListDisplay(recents(count), false);
    assert.equal(display.visible.length, count);
    assert.equal(display.toggle, false);
    assert.equal(display.hidden, 0);
    assert.equal(display.label, '');
    assert.equal(recentListDisplay(recents(count), true).expanded, false, 'an unnecessary expand stays collapsed');
  }
});

test('more than six recents expose an accessible show-all and collapse control', () => {
  const collapsed = recentListDisplay(recents(8), false);
  assert.equal(collapsed.visible.length, 6);
  assert.equal(collapsed.hidden, 2);
  assert.equal(collapsed.toggle, true);
  assert.equal(collapsed.expanded, false);
  assert.equal(collapsed.label, '显示全部 8 项');

  const expanded = recentListDisplay(recents(8), true);
  assert.equal(expanded.visible.length, 8);
  assert.equal(expanded.hidden, 0);
  assert.equal(expanded.expanded, true);
  assert.equal(expanded.label, '收起到 6 项');

  const boundary = recentListDisplay(recents(7), false);
  assert.equal(boundary.visible.length, 6);
  assert.equal(boundary.hidden, 1);
  assert.equal(boundary.label, '显示全部 7 项');
});

test('invalid recent values are tolerated without throwing', () => {
  for (const value of [undefined, null, {}, 'not-a-list', 42]) {
    const display = recentListDisplay(value, true);
    assert.deepEqual(display.visible, []);
    assert.equal(display.toggle, false);
    assert.equal(display.label, '');
  }
});
