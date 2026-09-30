import assert from 'node:assert/strict';
import test from 'node:test';
import {firstEnabledIndex, lastEnabledIndex, nextEnabledIndex, typeaheadIndex} from '../src/design-system/menu-logic.js';

const items = [
  {label: '打开'},
  {label: '保存', disabled: true},
  {separator: true},
  {label: '关闭'},
  {label: '设置', disabled: true},
];

test('nextEnabledIndex skips disabled items and separators', () => {
  assert.equal(nextEnabledIndex(items, -1, 1), 0);
  assert.equal(nextEnabledIndex(items, 0, 1), 3, 'skips disabled and separator');
  assert.equal(nextEnabledIndex(items, 3, 1), 0, 'wraps to the start');
  assert.equal(nextEnabledIndex(items, 3, -1), 0);
  assert.equal(nextEnabledIndex(items, 0, -1), 3, 'wraps backwards');
});

test('firstEnabledIndex and lastEnabledIndex find real targets', () => {
  assert.equal(firstEnabledIndex(items), 0);
  assert.equal(lastEnabledIndex(items), 3);
  assert.equal(firstEnabledIndex([{separator: true}, {label: 'x', disabled: true}]), -1);
});

test('typeaheadIndex matches prefixes, wraps, and ignores disabled items', () => {
  assert.equal(typeaheadIndex(items, -1, '关'), 3);
  assert.equal(typeaheadIndex(items, 0, '打开'), 0);
  assert.equal(typeaheadIndex(items, 0, '设'), -1, 'disabled items are not reachable');
  assert.equal(typeaheadIndex(items, 3, '打'), 0, 'wraps past the end');
  assert.equal(typeaheadIndex(items, 0, ''), -1);
  assert.equal(typeaheadIndex(items, 0, ' zz'), -1);
});
