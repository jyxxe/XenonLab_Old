import assert from 'node:assert/strict';
import test from 'node:test';
import {
  activeWindow,
  clampRect,
  defaultRect,
  focusWindow,
  isRetiredRegistryAppId,
  migrateLegacyAppReferences,
  nextWindowId,
  restoreWindows,
  resizeRect,
  serializeWindows,
  toggleMaximize,
  workArea,
} from '../src/shell/window-manager.js';

const viewport = {width: 1440, height: 900};
const app = {title: 'Knowledge', defaultSize: [1200, 800], minSize: [820, 520]};

test('legacy XeLibrary layout references migrate to native Knowledge without changing other preferences', () => {
  assert.equal(isRetiredRegistryAppId('xelibrary'), true);
  assert.equal(isRetiredRegistryAppId('custom-app'), false, 'unrelated external apps remain eligible');
  const source = {
    dock: ['settings', 'xelibrary', 'knowledge', 'chat'],
    dock_initialized: true,
    windows: [
      {id: 'legacy', app_id: 'xelibrary', title: 'Legacy library', x: 20, y: 40, width: 900, height: 600, minimized: true},
      {id: 'other', app_id: 'chat', title: 'Chat', x: 30, y: 50, width: 800, height: 500},
    ],
    revision: 7,
  };
  const migrated = migrateLegacyAppReferences(source);
  assert.deepEqual(migrated.dock, ['settings', 'knowledge', 'chat']);
  assert.deepEqual(migrated.windows[0], {...source.windows[0], app_id: 'knowledge', title: '知识库'});
  assert.deepEqual(migrated.windows[1], source.windows[1]);
  assert.equal(migrated.dock_initialized, true);
  assert.equal(migrated.revision, 7);
  assert.deepEqual(source.dock, ['settings', 'xelibrary', 'knowledge', 'chat'], 'migration does not mutate stored input');
});

test('default geometry stays inside the work area and respects minimums', () => {
  const rect = defaultRect(app, viewport);
  const area = workArea(viewport);
  assert.ok(rect.x >= area.x && rect.y >= area.y, 'origin inside work area');
  assert.ok(rect.x + rect.width <= area.x + area.width + 1, 'right edge inside work area');
  assert.ok(rect.width >= 820 && rect.height >= 520, 'minimum size honoured');
  const tiny = defaultRect({defaultSize: [100, 100], minSize: [400, 300]}, viewport);
  assert.ok(tiny.width >= 400 && tiny.height >= 300, 'undersized defaults are raised to the minimum');
});

test('clampRect keeps a reachable slice on screen', () => {
  const dragged = clampRect({x: 9999, y: 9999, width: 900, height: 600}, viewport);
  assert.ok(dragged.x <= viewport.width - 96, 'at least 96px remains horizontally');
  assert.ok(dragged.y <= viewport.height - 40, 'titlebar remains visible');
  const negative = clampRect({x: -5000, y: -500, width: 900, height: 600}, viewport);
  assert.ok(negative.x + negative.width >= 96, 'right edge reachable');
  assert.ok(negative.y >= 30, 'titlebar stays below the menu bar');
});

test('resizeRect anchors the opposite edge and clamps to the viewport', () => {
  const origin = {x: 200, y: 120, width: 800, height: 600};
  const wider = resizeRect(origin, {width: 400, height: 300}, 'se', 120, 80, viewport);
  assert.deepEqual(wider, {x: 200, y: 120, width: 920, height: 680});
  const fromWest = resizeRect(origin, {width: 400, height: 300}, 'sw', 100, 0, viewport);
  assert.equal(fromWest.x, 300, 'west edge moves');
  assert.equal(fromWest.width, 700, 'width shrinks by the same delta');
  const clamped = resizeRect(origin, {width: 400, height: 300}, 'se', -5000, -5000, viewport);
  assert.ok(clamped.width >= 400 && clamped.height >= 300, 'minimum size enforced');
});

test('maximize respects the menu bar and dock and restores the previous rectangle', () => {
  const win = {
    id: 'w1',
    app_id: 'knowledge',
    title: 'Knowledge',
    x: 100,
    y: 140,
    width: 900,
    height: 600,
    minimized: false,
  };
  const maximized = toggleMaximize(win, viewport);
  const area = workArea(viewport);
  assert.equal(maximized.y, area.y);
  assert.equal(maximized.height, area.height);
  assert.ok(maximized.y >= 30, 'maximize does not cover the menu bar');
  assert.ok(maximized.y + maximized.height <= viewport.height - 70, 'dock remains visible');
  const restored = toggleMaximize(maximized, viewport);
  assert.equal(restored.x, 100);
  assert.equal(restored.width, 900);
  assert.equal(restored.maximized, false);
});

test('focusWindow raises a window to the top of the stack', () => {
  const windows = [
    {id: 'a', app_id: 'a', title: 'A', x: 0, y: 0, width: 100, height: 100, minimized: false},
    {id: 'b', app_id: 'b', title: 'B', x: 0, y: 0, width: 100, height: 100, minimized: false},
    {id: 'c', app_id: 'c', title: 'C', x: 0, y: 0, width: 100, height: 100, minimized: false},
  ];
  assert.deepEqual(focusWindow(windows, 'a').map(win => win.id), ['b', 'c', 'a']);
  assert.equal(activeWindow(focusWindow(windows, 'b')).id, 'b');
  assert.equal(activeWindow(windows.map(win => ({...win, minimized: true}))), null);
});

test('nextWindowId cycles only visible windows and handles unknown actives', () => {
  const windows = [
    {id: 'a', app_id: 'a', title: 'A', x: 0, y: 0, width: 100, height: 100, minimized: false},
    {id: 'b', app_id: 'b', title: 'B', x: 0, y: 0, width: 100, height: 100, minimized: true},
    {id: 'c', app_id: 'c', title: 'C', x: 0, y: 0, width: 100, height: 100, minimized: false},
  ];
  assert.equal(nextWindowId(windows, 'a', 1), 'c', 'minimized windows are skipped');
  assert.equal(nextWindowId(windows, 'c', 1), 'a', 'cycling wraps');
  assert.equal(nextWindowId(windows, 'missing', 1), 'a');
  assert.equal(nextWindowId(windows, null, -1), 'c');
  assert.equal(nextWindowId([], null, 1), null);
});

test('restoreWindows filters unknown apps and normalises hostile geometry', () => {
  const known = id => (id === 'knowledge' ? app : null);
  const restored = restoreWindows(
    [
      {id: 'w1', app_id: 'knowledge', x: 99999, y: -200, width: 10, height: 10},
      {id: 'w2', app_id: 'unknown', x: 0, y: 0, width: 100, height: 100},
      {id: 'w1', app_id: 'knowledge', x: 1, y: 1, width: 1, height: 1},
      {id: 42, app_id: 'knowledge'},
      null,
    ],
    known,
    viewport,
  );
  assert.equal(restored.length, 1, 'only the first valid entry survives');
  assert.equal(restored[0].id, 'w1');
  assert.ok(restored[0].width >= 820, 'minimum size reapplied');
  assert.ok(restored[0].y >= 30, 'position clamped below the menu bar');
});

test('serializeWindows emits the server layout shape', () => {
  const payload = serializeWindows([
    {id: 'w1', app_id: 'knowledge', title: 'K', x: 10.4, y: 42.6, width: 800.5, height: 600.4, minimized: false, maximized: false, restore: {x: 1, y: 1, width: 2, height: 2}},
  ]);
  assert.deepEqual(payload, [
    {id: 'w1', app_id: 'knowledge', title: 'K', x: 10, y: 43, width: 801, height: 600, minimized: false, maximized: false},
  ]);
});

test('restoreWindows caps the number of restored windows', () => {
  const raw = Array.from({length: 50}, (_, index) => ({
    id: `w${index}`,
    app_id: 'knowledge',
    x: 40,
    y: 60,
    width: 900,
    height: 600,
  }));
  assert.equal(restoreWindows(raw, id => (id === 'knowledge' ? app : null), viewport).length, 30);
});


test('maximized geometry survives save/reload and restores after viewport changes', () => {
  const original={id:'max',app_id:'knowledge',title:'K',x:75,y:82,width:930,height:620,minimized:false};
  const saved=serializeWindows([toggleMaximize(original,viewport)]);
  const resized={width:1366,height:768};
  const [loaded]=restoreWindows(saved,()=>app,resized);
  assert.equal(loaded.maximized,true);
  assert.deepEqual({x:loaded.x,y:loaded.y,width:loaded.width,height:loaded.height},workArea(resized));
  const restored=toggleMaximize(loaded,resized);
  assert.equal(restored.x,original.x);
  assert.equal(restored.width,original.width);
  assert.equal(restored.maximized,false);
});
