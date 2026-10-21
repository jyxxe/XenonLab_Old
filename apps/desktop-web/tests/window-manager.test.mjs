import assert from 'node:assert/strict';
import test from 'node:test';
import {
  activeWindow,
  clampRect,
  defaultRect,
  focusWindow,
  halfScreenPlacement,
  isRetiredRegistryAppId,
  migrateLegacyAppReferences,
  nextWindowId,
  restoreWindows,
  resizeRect,
  serializeWindows,
  toggleMaximize,
  workArea,
  maximizedRect,
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

test('maximize fills the viewport below the menu bar and restores the previous rectangle', () => {
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
  const area = maximizedRect(viewport);
  assert.equal(maximized.x, 0);
  assert.equal(maximized.width, viewport.width);
  assert.equal(maximized.y, area.y);
  assert.equal(maximized.height, area.height);
  assert.ok(maximized.y >= 30, 'maximize does not cover the menu bar');
  assert.equal(maximized.y + maximized.height, viewport.height, 'bottom edge reaches viewport');
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
  assert.deepEqual({x:loaded.x,y:loaded.y,width:loaded.width,height:loaded.height},maximizedRect(resized));
  const restored=toggleMaximize(loaded,resized);
  assert.equal(restored.x,original.x);
  assert.equal(restored.width,original.width);
  assert.equal(restored.maximized,false);
});

test('maximized geometry fits compact screens regardless of app minimum size', () => {
  for (const viewport of [{width:390,height:844},{width:820,height:1180}]) {
    const [win] = restoreWindows([{id:'compact',app_id:'knowledge',maximized:true}],()=>app,viewport);
    assert.deepEqual({x:win.x,y:win.y,width:win.width,height:win.height}, maximizedRect(viewport));
  }
});

test('half-screen placement clears measured menu and all Dock sizes with a 12px gap', () => {
  const viewport = {width: 1920, height: 1080};
  for (const dockTop of [994, 988, 980]) {
    const bounds = {menuBottom: 30, dockTop};
    const left = halfScreenPlacement(viewport, {width: 680, height: 480}, 'left', bounds);
    const right = halfScreenPlacement(viewport, {width: 720, height: 480}, 'right', bounds);
    assert.equal(left.reason, null);
    assert.equal(right.reason, null);
    assert.deepEqual(left.rect, {x: 12, y: 42, width: 942, height: dockTop - 54});
    assert.equal(right.rect.x, left.rect.x + left.rect.width + 12);
    assert.equal(right.rect.x + right.rect.width, viewport.width - 12);
    assert.equal(right.rect.y + right.rect.height, dockTop - 12);
  }
  const customChrome = halfScreenPlacement(viewport, {width: 680, height: 480}, 'left', {menuBottom: 45, dockTop: 930});
  assert.equal(customChrome.rect.y, 57, 'uses measured menu height');
  assert.equal(customChrome.rect.y + customChrome.rect.height, 918);
});

test('odd viewport widths split the remainder without overlap or escaping the workspace', () => {
  const viewport = {width: 1921, height: 1080};
  const bounds = {menuBottom: 30, dockTop: 980};
  const min = {width: 942, height: 480};
  const left = halfScreenPlacement(viewport, min, 'left', bounds).rect;
  const right = halfScreenPlacement(viewport, min, 'right', bounds).rect;
  assert.equal(left.width, 942);
  assert.equal(right.width, 943);
  assert.equal(right.x - left.x - left.width, 12);
  assert.equal(right.x + right.width, 1909);
  assert.equal(halfScreenPlacement(viewport, {width: 943, height: 480}, 'left', bounds).reason, 'width');
  assert.equal(halfScreenPlacement(viewport, {width: 943, height: 480}, 'right', bounds).reason, null);
});

test('half-screen placement disables compact CSS mode and honors each app minimum', () => {
  const bounds = {menuBottom: 30, dockTop: 688};
  assert.equal(halfScreenPlacement({width: 1023, height: 768}, {width: 320, height: 200}, 'left', bounds).reason, 'compact');
  assert.equal(halfScreenPlacement({width: 1024, height: 768}, {width: 320, height: 200}, 'left', bounds).reason, null);
  const viewport = {width: 1440, height: 768};
  assert.equal(halfScreenPlacement(viewport, {width: 680, height: 480}, 'left', bounds).reason, null, 'Settings fits');
  assert.equal(halfScreenPlacement(viewport, {width: 720, height: 480}, 'left', bounds).reason, 'width', 'Development does not fit');
  assert.equal(halfScreenPlacement(viewport, {width: 820, height: 520}, 'right', bounds).reason, 'width', 'Knowledge does not fit');
});

test('short or invalid work areas are rejected instead of inflating minimum sizes', () => {
  const viewport = {width: 1920, height: 500};
  const bounds = {menuBottom: 30, dockTop: 400};
  assert.equal(halfScreenPlacement(viewport, {width: 680, height: 480}, 'left', bounds).reason, 'height');
  const min = {width: 320, height: 200};
  for (const invalid of [null, {menuBottom: NaN, dockTop: 400}, {menuBottom: 30, dockTop: Infinity}, {menuBottom: -1, dockTop: 400}, {menuBottom: 30, dockTop: 501}, {menuBottom: 400, dockTop: 300}]) {
    assert.deepEqual(halfScreenPlacement(viewport, min, 'left', invalid), {rect: null, reason: 'unmeasured'});
  }
});

test('a half-screen rectangle persists as an ordinary window and survives maximize/restore', () => {
  const viewport = {width: 1920, height: 1080};
  const rect = halfScreenPlacement(viewport, {width: 820, height: 520}, 'right', {menuBottom: 30, dockTop: 980}).rect;
  const win = {id: 'half', app_id: 'knowledge', title: 'K', minimized: false, maximized: false, ...rect};
  const payload = serializeWindows([win]);
  assert.deepEqual(Object.keys(payload[0]).sort(), ['app_id', 'height', 'id', 'maximized', 'minimized', 'title', 'width', 'x', 'y']);
  const [loaded] = restoreWindows(payload, () => app, viewport);
  assert.deepEqual({x: loaded.x, y: loaded.y, width: loaded.width, height: loaded.height}, rect);
  const restored = toggleMaximize(toggleMaximize(loaded, viewport), viewport);
  assert.deepEqual({x: restored.x, y: restored.y, width: restored.width, height: restored.height}, rect);
});
