import assert from 'node:assert/strict';
import test from 'node:test';
import {selectedPluginWidgets} from '../src/plugin-widgets.js';

const widgets = [
  {plugin_id: 'alpha', id: 'a', height: 180},
  {plugin_id: 'beta', id: 'b', height: 120},
];

test('returns installed plugin widgets in preference order', () => {
  assert.deepEqual(selectedPluginWidgets(['beta', 'alpha'], widgets), [widgets[1], widgets[0]]);
});

test('stale, disabled and duplicate preference ids are ignored without duplicates', () => {
  assert.deepEqual(selectedPluginWidgets(['missing', 'alpha', 'alpha'], widgets), [widgets[0]]);
});

test('malformed plugin widget payloads and preferences are harmless', () => {
  assert.deepEqual(selectedPluginWidgets(null, widgets), []);
  assert.deepEqual(selectedPluginWidgets(['alpha'], null), []);
  assert.deepEqual(selectedPluginWidgets(['alpha'], [null, {}, {plugin_id: 3}]), []);
});

test('a plugin whose widget was removed is not rendered from a stale preference', () => {
  assert.deepEqual(selectedPluginWidgets(['alpha'], [{plugin_id: 'beta'}]), []);
});
