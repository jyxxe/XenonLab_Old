/**
 * Pure window geometry / stacking logic for the XeonoLab desktop shell.
 * Plain JS so the Node test runner can import it without a bundler.
 */

export const MENUBAR_HEIGHT = 30;
export const DOCK_CLEARANCE = 96;
export const WINDOW_MARGIN = 12;
export const MIN_WIDTH = 320;
export const MIN_HEIGHT = 200;
export const MIN_VISIBLE_X = 96;
export const MIN_VISIBLE_Y = 40;

export function workArea(viewport) {
  const width = Math.max(MIN_WIDTH, viewport.width - WINDOW_MARGIN * 2);
  const height = Math.max(MIN_HEIGHT, viewport.height - MENUBAR_HEIGHT - DOCK_CLEARANCE);
  return {x: WINDOW_MARGIN, y: MENUBAR_HEIGHT + WINDOW_MARGIN, width, height};
}

export function clamp(value, min, max) {
  return Math.min(Math.max(value, min), Math.max(min, max));
}

export function minSizeOf(app) {
  return {
    width: Math.max(MIN_WIDTH, app?.minSize?.[0] ?? app?.min_size?.[0] ?? MIN_WIDTH),
    height: Math.max(MIN_HEIGHT, app?.minSize?.[1] ?? app?.min_size?.[1] ?? MIN_HEIGHT),
  };
}

/**
 * Keep a window reachable: its titlebar must stay below the menu bar and enough
 * of the frame must remain inside the viewport to grab and drag it back.
 */
export function clampRect(rect, viewport, minSize = {width: MIN_WIDTH, height: MIN_HEIGHT}) {
  const width = clamp(rect.width, minSize.width, Math.max(minSize.width, viewport.width));
  const height = clamp(rect.height, minSize.height, Math.max(minSize.height, viewport.height - MENUBAR_HEIGHT));
  const maxX = Math.max(0, viewport.width - MIN_VISIBLE_X);
  const maxY = Math.max(MENUBAR_HEIGHT, viewport.height - MIN_VISIBLE_Y);
  return {
    ...rect,
    width,
    height,
    x: clamp(rect.x, -width + MIN_VISIBLE_X, maxX),
    y: clamp(rect.y, MENUBAR_HEIGHT, maxY),
  };
}

export function defaultRect(app, viewport, index = 0) {
  const area = workArea(viewport);
  const min = minSizeOf(app);
  const width = clamp(app?.defaultSize?.[0] ?? app?.default_size?.[0] ?? 1000, min.width, area.width);
  const height = clamp(app?.defaultSize?.[1] ?? app?.default_size?.[1] ?? 700, min.height, area.height);
  const offset = (index % 6) * 26;
  return clampRect(
    {x: area.x + 40 + offset, y: area.y + 12 + offset, width, height},
    viewport,
    min,
  );
}

/** Resize from a handle; the opposite edge stays anchored and minimums are honoured. */
export function resizeRect(rect, minSize, handle, dx, dy, viewport) {
  const next = {...rect};
  if (handle.includes('e')) next.width = rect.width + dx;
  if (handle.includes('s')) next.height = rect.height + dy;
  if (handle.includes('w')) {
    next.width = rect.width - dx;
    next.x = rect.x + (rect.width - next.width);
  }
  if (handle.includes('n')) {
    next.height = rect.height - dy;
    next.y = rect.y + (rect.height - next.height);
  }
  next.width = clamp(next.width, minSize.width, Math.max(minSize.width, viewport.width));
  next.height = clamp(next.height, minSize.height, Math.max(minSize.height, viewport.height - MENUBAR_HEIGHT));
  if (handle.includes('w')) next.x = clamp(next.x, 0, viewport.width - minSize.width);
  if (handle.includes('n')) next.y = clamp(next.y, MENUBAR_HEIGHT, viewport.height - minSize.height);
  return next;
}

export function moveRect(rect, x, y, viewport) {
  return clampRect({...rect, x, y}, viewport);
}

export function toggleMaximize(win, viewport) {
  const area = workArea(viewport);
  if (win.maximized) {
    const restore = clampRect(win.restore ?? {...win}, viewport, minSizeOf({}));
    return {...win, ...restore, maximized: false, restore: undefined};
  }
  return {
    ...win,
    minimized: false,
    maximized: true,
    restore: {x: win.x, y: win.y, width: win.width, height: win.height},
    ...area,
  };
}

/** Move the referenced window to the top of the stack (end of the array). */
export function focusWindow(windows, id) {
  const index = windows.findIndex(win => win.id === id);
  if (index < 0 || index === windows.length - 1) return windows;
  return [...windows.slice(0, index), ...windows.slice(index + 1), windows[index]];
}

export function activeWindow(windows) {
  const visible = windows.filter(win => !win.minimized);
  return visible.length ? visible[visible.length - 1] : null;
}

/** Cycle app windows; direction 1 moves forward. Only non-minimized windows participate. */
export function nextWindowId(windows, activeId, direction = 1) {
  const visible = windows.filter(win => !win.minimized);
  if (!visible.length) return null;
  const index = visible.findIndex(win => win.id === activeId);
  if (index < 0) return visible[direction > 0 ? 0 : visible.length - 1].id;
  return visible[(index + direction + visible.length) % visible.length].id;
}

/** Drop unknown apps and geometry that no longer fits, keeping the order stable. */
export function restoreWindows(raw, knownApp, viewport) {
  if (!Array.isArray(raw)) return [];
  const seen = new Set();
  const windows = [];
  for (const item of raw.slice(0, 30)) {
    if (!item || typeof item.id !== 'string' || typeof item.app_id !== 'string') continue;
    if (seen.has(item.id) || !knownApp(item.app_id)) continue;
    seen.add(item.id);
    const app = knownApp(item.app_id);
    const min = minSizeOf(app);
    windows.push({
      id: item.id,
      app_id: item.app_id,
      title: typeof item.title === 'string' && item.title ? item.title : app.title,
      minimized: Boolean(item.minimized),
      maximized: Boolean(item.maximized),
      ...(item.maximized ? {restore: clampRect(item.restore && ['x','y','width','height'].every(key => Number.isFinite(item.restore[key])) ? item.restore : defaultRect(app, viewport), viewport, min)} : {}),
      ...clampRect(
        {
          x: Number.isFinite(item.x) ? item.x : WINDOW_MARGIN,
          y: Number.isFinite(item.y) ? item.y : MENUBAR_HEIGHT + WINDOW_MARGIN,
          width: Number.isFinite(item.width) ? item.width : app.defaultSize?.[0] ?? 1000,
          height: Number.isFinite(item.height) ? item.height : app.defaultSize?.[1] ?? 700,
        },
        viewport,
        min,
      ),
      ...(item.maximized ? workArea(viewport) : {}),
    });
  }
  return windows;
}

/** Serializable layout payload accepted by PUT /api/v1/users/me/layout. */
export function serializeWindows(windows) {
  return windows.slice(0, 30).map(win => ({
    id: win.id,
    app_id: win.app_id,
    title: win.title,
    x: Math.round(win.x),
    y: Math.round(win.y),
    width: Math.round(win.width),
    height: Math.round(win.height),
    minimized: Boolean(win.minimized),
    maximized: Boolean(win.maximized),
    ...(win.maximized && win.restore ? {restore: {x: Math.round(win.restore.x), y: Math.round(win.restore.y), width: Math.round(win.restore.width), height: Math.round(win.restore.height)}} : {}),
  }));
}
