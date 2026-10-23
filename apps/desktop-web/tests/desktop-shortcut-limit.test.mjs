import test from 'node:test';
import assert from 'node:assert/strict';
import {SHORTCUT_LIMIT, shortcutLimitHint, toggleShortcutApp} from '../src/desktop-shortcut-limit.js';

test('toggling adds and removes shortcut ids without replacing others', () => {
  const selected = ['a', 'b'];
  assert.deepEqual(toggleShortcutApp(selected, 'c'), {selected: ['a', 'b', 'c'], blocked: false});
  assert.deepEqual(toggleShortcutApp(selected, 'a'), {selected: ['b'], blocked: false});
  assert.deepEqual(selected, ['a', 'b'], 'the input list is not mutated');
});

test('the 13th shortcut is blocked instead of silently replacing the oldest entry', () => {
  const full = Array.from({length: SHORTCUT_LIMIT}, (_, index) => `app-${index}`);
  const result = toggleShortcutApp(full, 'app-extra');
  assert.equal(result.blocked, true);
  assert.deepEqual(result.selected, full, 'a blocked toggle keeps the saved list unchanged');
  // Removing an existing selection still works at the limit.
  assert.deepEqual(toggleShortcutApp(full, 'app-0'), {
    selected: full.slice(1),
    blocked: false,
  });
});

test('invalid selections are tolerated without throwing', () => {
  assert.deepEqual(toggleShortcutApp(null, 'a'), {selected: ['a'], blocked: false});
  assert.deepEqual(toggleShortcutApp(['a', 3, '', null], 'b'), {selected: ['a', 'b'], blocked: false});
  assert.deepEqual(toggleShortcutApp(['a'], ''), {selected: ['a'], blocked: false});
  assert.deepEqual(toggleShortcutApp(['a'], 7), {selected: ['a'], blocked: false});
});

test('the hint shows the current count and the cap', () => {
  assert.equal(SHORTCUT_LIMIT, 12);
  assert.equal(shortcutLimitHint([]), '已选 0/12 项；最多 12 项，满额时请先取消一个已选应用。');
  assert.equal(shortcutLimitHint(['a', 'b']), '已选 2/12 项；最多 12 项，满额时请先取消一个已选应用。');
  assert.match(shortcutLimitHint(null), /已选 0\/12/);
  assert.match(shortcutLimitHint(['a'], 5), /已选 1\/5/);
});
