import React, {useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {ErrorState, Spinner, useToast} from '../design-system/primitives';
import {AppShortcutsWidget, CalendarWidget, ClockWidget, MenuClock, NotesWidget, PluginWidget, SearchWidget, StatusWidget, WorkWidget} from '../Widgets';
import {customWallpaperStyle} from '../wallpaper.js';
import {selectedPluginWidgets} from '../plugin-widgets.js';
import {mergeApps} from './apps';
import {CommandPalette, type Command, type CommandProvider} from './CommandPalette';
import {Dock, type DockSize} from './Dock';
import {LaunchPad} from './LaunchPad';
import {MenuBar} from './MenuBar';
import {WindowFrame} from './WindowFrame';
import {IFrameApp} from './IFrameApp';
import {isActiveWork} from '../work-state';
import {
  activeWindow,
  clampRect,
  defaultRect,
  focusWindow,
  halfScreenPlacement,
  LEGACY_XELIBRARY_APP_ID,
  migrateLegacyAppReferences,
  minSizeOf,
  nextWindowId,
  restoreWindows,
  serializeWindows,
  toggleMaximize,
  maximizedRect,
} from './window-manager.js';
import type {DesktopBounds, HalfScreenPlacement, HalfScreenSide} from './window-manager.js';
import type {API, APIPage, APIRaw, AppIntent, DesktopApp, PluginWidget as PluginWidgetInfo, PreferenceApplyOptions, RegistryApp, UserPreferences, WindowState} from './types';

const RECENT_KEY = 'xeonolab.recent-apps';

function placementReason(placement: HalfScreenPlacement, minSize: {width: number; height: number}): string | null {
  switch (placement.reason) {
    case 'unmeasured': return '左右排列不可用：正在测量桌面工作区';
    case 'compact': return '左右排列不可用：窄屏使用单窗口布局';
    case 'width': return `左右排列不可用：半屏宽度不足，当前窗口至少需要 ${minSize.width}px`;
    case 'height': return `左右排列不可用：工作区高度不足，当前窗口至少需要 ${minSize.height}px`;
    default: return null;
  }
}

const defaultPreferences: UserPreferences = {
  theme: 'system',
  accent: 'blue',
  wallpaper: 'aurora',
  wallpaper_mode: 'solid',
  wallpaper_color: '#101828',
  wallpaper_gradient_from: '#0b1220',
  wallpaper_gradient_to: '#1d4ed8',
  wallpaper_gradient_angle: 160,
  wallpaper_dim: 0,
  timezone: 'system',
  widgets: ['clock', 'status'],
  plugin_widgets: [],
  shortcut_apps: [],
  notes: '',
  custom_wallpaper_available: false,
  dock_size: 'medium',
  dock_autohide: false,
  restore_windows: true,
  animations: true,
  reduced_motion: false,
};

function loadRecent(): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(RECENT_KEY) || '[]');
    return Array.isArray(value) ? value.filter(item => typeof item === 'string').slice(0, 6) : [];
  } catch {
    return [];
  }
}

export function DesktopShell({
  api,
  apiPage,
  apiRaw,
  onLogout,
}: {
  api: API;
  apiPage: APIPage;
  apiRaw: APIRaw;
  onLogout: () => Promise<void>;
}) {
  const {push} = useToast();
  const [apps, setApps] = useState<DesktopApp[]>([]);
  const [pluginWidgets, setPluginWidgets] = useState<PluginWidgetInfo[]>([]);
  const [windows, setWindows] = useState<WindowState[]>([]);
  const [dockIds, setDockIds] = useState<string[]>([]);
  const [preferences, setPreferences] = useState<UserPreferences>(defaultPreferences);
  const [wallpaperError, setWallpaperError] = useState('');
  const [wallpaperImageUrl, setWallpaperImageUrl] = useState('');
  const [subject, setSubject] = useState('');
  const [userLabel, setUserLabel] = useState('');
  const [accountScopes, setAccountScopes] = useState<string[]>([]);
  const [ready, setReady] = useState(false);
  const [loadError, setLoadError] = useState('');
  const loadedRef = useRef(false);
  const [launchpadOpen, setLaunchpadOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [recent, setRecent] = useState<string[]>(loadRecent);
  const [badges, setBadges] = useState<Record<string, number | null>>({});
  const storedWindows = useRef<Array<Record<string,unknown>>>([]);
  const windowsEdited = useRef(false);
  const editWindows = useCallback((update: React.SetStateAction<WindowState[]>) => {windowsEdited.current = true;setWindows(update)}, []);
  const layoutQueue = useRef<Promise<unknown>>(Promise.resolve());
  const preferenceQueue = useRef<Promise<unknown>>(Promise.resolve());
  const preferenceVersion = useRef(0);
  const preferenceOwner = useRef('');
  const preferenceLifetime = useRef(true);
  const currentAPI = useRef(api);
  if (currentAPI.current !== api) {preferenceOwner.current = ''; ++preferenceVersion.current;}
  currentAPI.current = api;
  useLayoutEffect(() => {preferenceLifetime.current = true; return () => {preferenceLifetime.current = false;};}, []);
  const viewport = useRef({width: innerWidth, height: innerHeight});
  const [menuElement, setMenuElement] = useState<HTMLElement | null>(null);
  const [dockElement, setDockElement] = useState<HTMLElement | null>(null);
  const [arrangementBounds, setArrangementBounds] = useState<(DesktopBounds & {width: number; height: number}) | null>(null);
  const readArrangementBounds = useCallback(() => {
    if (!menuElement || !dockElement) return null;
    return {width: innerWidth, height: innerHeight, menuBottom: menuElement.getBoundingClientRect().bottom, dockTop: dockElement.getBoundingClientRect().top};
  }, [menuElement, dockElement]);
  useLayoutEffect(() => {
    const measure = () => {
      const next = readArrangementBounds();
      setArrangementBounds(current => current && next && current.width === next.width && current.height === next.height
        && current.menuBottom === next.menuBottom && current.dockTop === next.dockTop ? current : next);
    };
    measure();
    if (!menuElement || !dockElement) return;
    // Observe the natural nav box: auto-hide transforms its child, not this box.
    const observer = new ResizeObserver(measure);
    observer.observe(menuElement);
    observer.observe(dockElement);
    addEventListener('resize', measure);
    return () => {observer.disconnect(); removeEventListener('resize', measure);};
  }, [menuElement, dockElement, readArrangementBounds]);

  const [appIntents, setAppIntents] = useState<Record<string, AppIntent>>({});

  const active = activeWindow(windows);
  const activeId = active?.id ?? null;
  const maximizedActive = Boolean(active?.maximized);
  const activeApp = useMemo(() => {
    const win = windows.find(item => item.id === activeId);
    return win ? apps.find(app => app.id === win.app_id) ?? null : null;
  }, [activeId, windows, apps]);
  const activeMinSize = minSizeOf(activeApp);
  const arrangementReasons: Record<HalfScreenSide, string | null> = {left: null, right: null};
  for (const side of ['left', 'right'] as const) {
    arrangementReasons[side] = !activeApp ? '左右排列不可用：请先打开或恢复一个窗口'
      : placementReason(halfScreenPlacement(arrangementBounds ?? viewport.current, activeMinSize, side, arrangementBounds), activeMinSize);
  }
  const customWallpaper = useMemo(() => customWallpaperStyle(preferences, wallpaperImageUrl), [preferences, wallpaperImageUrl]);
  const knowledgeSearchAvailable = useMemo(() => apps.some(app => app.id === 'knowledge') && accountScopes.includes('xelibrary:query'), [apps, accountScopes]);
  const workAvailable = useMemo(() => apps.some(app => app.id === 'issues'), [apps]);
  const shownPluginWidgets = useMemo(
    () => selectedPluginWidgets(preferences.plugin_widgets || [], pluginWidgets),
    [preferences.plugin_widgets, pluginWidgets],
  );

  const connect = useCallback(async () => {
    setLoadError('');
    try {
      const [me, registry, widgets] = await Promise.all([
        api('/users/me'),
        api('/apps'),
        api('/users/me/plugin-widgets').catch(() => [] as PluginWidgetInfo[]),
      ]);
      const [layout, stored] = await Promise.all([api('/users/me/layout'), api('/users/me/preferences')]);
      if (currentAPI.current !== api || !preferenceLifetime.current) return;
      const available = mergeApps(registry as RegistryApp[], me.scopes || []);
      const migratedLayout = migrateLegacyAppReferences(layout);
      const migratedRecent = [...new Set(loadRecent().map(id => id === LEGACY_XELIBRARY_APP_ID ? 'knowledge' : id))].slice(0, 6);
      setRecent(migratedRecent);
      try {
        localStorage.setItem(RECENT_KEY, JSON.stringify(migratedRecent));
      } catch {
        /* Recent apps are cosmetic; private modes may disable storage. */
      }
      const prefs: UserPreferences = {...defaultPreferences, ...stored};
      const known = (id: string) => available.find(app => app.id === id);
      const storedDock = Array.isArray(migratedLayout.dock) ? migratedLayout.dock.filter((id: string) => known(id)) : [];
      const nextDock = migratedLayout.dock_initialized || storedDock.length ? storedDock : available.filter(app => app.dock).map(app => app.id);
      setApps(available);
      setPluginWidgets(Array.isArray(widgets) ? widgets as PluginWidgetInfo[] : []);
      setAccountScopes(Array.isArray(me.scopes) ? me.scopes : []);
      setDockIds(nextDock);
      setPreferences(prefs);
      preferenceOwner.current = me.subject || '';
      setSubject(me.subject || '');
      setUserLabel(me.subject ? String(me.subject).slice(0, 8) : '本地账户');
      if (!loadedRef.current) {
        setWindows(prefs.restore_windows === false ? [] : restoreWindows(migratedLayout.windows, known, viewport.current));
        storedWindows.current = Array.isArray(migratedLayout.windows) ? migratedLayout.windows : [];
        windowsEdited.current = false;
        loadedRef.current = true;
      }
      setReady(true);
    } catch (error) {
      setLoadError(String(error));
    }
  }, [api]);

  useEffect(() => {
    void connect();
  }, [connect]);

  useEffect(() => {
    const media = matchMedia('(prefers-color-scheme: dark)');
    const apply = () => {
      const theme = preferences.theme === 'system' ? (media.matches ? 'dark' : 'light') : preferences.theme;
      document.documentElement.dataset.theme = theme;
      document.documentElement.dataset.accent = preferences.accent;
      document.documentElement.dataset.wallpaper = preferences.wallpaper;
      document.documentElement.dataset.reducedMotion = preferences.reduced_motion ? 'true' : 'false';
      document.documentElement.dataset.animations = preferences.animations ? 'on' : 'off';
    };
    apply();
    media.addEventListener('change', apply);
    return () => media.removeEventListener('change', apply);
  }, [preferences]);

  useEffect(() => {
    setWallpaperImageUrl('');
    setWallpaperError('');
    if (preferences.wallpaper !== 'custom' || preferences.wallpaper_mode !== 'image' || !preferences.custom_wallpaper_available) return;
    let active = true;
    let objectUrl = '';
    void apiRaw('/users/me/wallpaper').then(async response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      if (!active) return;
      objectUrl = URL.createObjectURL(blob);
      setWallpaperImageUrl(objectUrl);
    }).catch(() => {
      if (active) setWallpaperError('无法读取自定义壁纸，请在系统设置中重新上传。');
    });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [apiRaw, preferences.wallpaper, preferences.wallpaper_mode, preferences.custom_wallpaper_available]);

  const saveNotes = useCallback(async (notes: string, keepalive = false) => {
    const pending = preferenceQueue.current.catch(() => undefined).then(async () => {
      if (!keepalive) {
        await api('/users/me/preferences', {method: 'PATCH', body: JSON.stringify({notes})});
        return;
      }
      const response = await apiRaw('/users/me/preferences', {
        method: 'PATCH', body: JSON.stringify({notes}), keepalive: true,
      });
      let payload: any = null;
      try { payload = await response.json(); } catch { /* handled as an invalid API response below */ }
      if (!response.ok) throw new Error(payload?.error?.message || `保存失败（HTTP ${response.status}）`);
    });
    preferenceQueue.current = pending;
    await pending;
    setPreferences(current => ({...current, notes}));
  }, [api, apiRaw]);

  const saveLayout = useCallback(
    async (overrides?: {windows?: WindowState[]; dock?: string[]}) => {
      const payload = {
        dock_initialized: true,
        dock: overrides?.dock ?? dockIds,
        windows: overrides?.windows ? serializeWindows(overrides.windows) : windowsEdited.current ? serializeWindows(windows) : storedWindows.current,
      };
      const pending = layoutQueue.current.catch(() => undefined).then(() => api('/users/me/layout', {method: 'PUT', body: JSON.stringify(payload)}));
      layoutQueue.current = pending;
      await pending;
    },
    [api, dockIds, windows],
  );

  // Persist window geometry and dock order shortly after the user stops changing them.
  useEffect(() => {
    if (!ready) return;
    const timer = setTimeout(() => {
      void saveLayout().catch(error => push({tone: 'danger', title: '布局未保存', description: String(error)}));
    }, 900);
    return () => clearTimeout(timer);
  }, [windows, dockIds, ready, saveLayout, push]);

  useEffect(() => {
    const onResize = () => {
      viewport.current = {width: innerWidth, height: innerHeight};
      setWindows(current =>
        current.map(win => {
          const app = apps.find(item => item.id === win.app_id);
          return {...win, ...(win.maximized ? maximizedRect(viewport.current) : clampRect(win, viewport.current, minSizeOf(app)))};
        }),
      );
    };
    addEventListener('resize', onResize);
    return () => removeEventListener('resize', onResize);
  }, [apps]);

  const openApp = useCallback(
    (id: string, intent?: Omit<AppIntent, 'id'>) => {
      const app = apps.find(item => item.id === id);
      if (!app) return;
      if (intent) setAppIntents(current => ({...current, [id]: {...intent, id: crypto.randomUUID()}}));
      setLaunchpadOpen(false);
      setPaletteOpen(false);
      setRecent(current => {
        const next = [id, ...current.filter(item => item !== id)].slice(0, 6);
        try {
          localStorage.setItem(RECENT_KEY, JSON.stringify(next));
        } catch {
          /* storage can be unavailable in private modes; recents are cosmetic */
        }
        return next;
      });
      editWindows(current => {
        const existing = current.find(win => win.app_id === id);
        if (app.singleton && existing) {
          return focusWindow(
            current.map(win => (win.id === existing.id ? {...win, minimized: false, title: win.title || app.title} : win)),
            existing.id,
          );
        }
        const rect = defaultRect(app, viewport.current, current.length);
        return [...current, {id: crypto.randomUUID(), app_id: id, title: app.title, minimized: false, maximized: false, ...rect}];
      });
    },
    [apps, editWindows],
  );

  const refreshApps = useCallback(async () => {
    const [me, registry, widgets] = await Promise.all([
      api('/users/me'),
      api('/apps'),
      api('/users/me/plugin-widgets').catch(() => [] as PluginWidgetInfo[]),
    ]);
    const available = mergeApps(registry, me.scopes || []);
    const known = new Set(available.map(app => app.id));
    setApps(available);
    setPluginWidgets(Array.isArray(widgets) ? widgets as PluginWidgetInfo[] : []);
    editWindows(current => current.filter(win => known.has(win.app_id)));
    setDockIds(current => current.filter(id => known.has(id)));
  }, [api, editWindows]);

  const saveLayoutWithFeedback = useCallback(async () => {
    try {
      await saveLayout();
      push({tone: 'success', title: '布局已保存'});
    } catch (error) {
      push({tone: 'danger', title: '布局未保存', description: String(error)});
    }
  }, [saveLayout, push]);

  const closeWindow = useCallback((id: string) => editWindows(current => current.filter(win => win.id !== id)), [editWindows]);
  const minimizeWindow = useCallback((id: string) => editWindows(current => current.map(win => (win.id === id ? {...win, minimized: true} : win))), [editWindows]);
  const restoreWindow = useCallback(
    (id: string) => editWindows(current => focusWindow(current.map(win => (win.id === id ? {...win, minimized: false} : win)), id)),
    [editWindows],
  );
  const toggleWindowMaximize = useCallback((id: string) => {
    editWindows(current => current.map(win => (win.id === id ? toggleMaximize(win, viewport.current) : win)));
  }, [editWindows]);
  const arrangeWindow = useCallback((id: string, side: HalfScreenSide) => {
    const win = windows.find(item => item.id === id);
    const app = apps.find(item => item.id === win?.app_id);
    if (!win || !app) return;
    const minSize = minSizeOf(app);
    const bounds = readArrangementBounds();
    const placement = halfScreenPlacement({width: innerWidth, height: innerHeight}, minSize, side, bounds);
    if (!placement.rect) {
      push({tone: 'warning', title: '窗口未排列', description: placementReason(placement, minSize) ?? undefined});
      return;
    }
    const rect = placement.rect;
    editWindows(current => focusWindow(current.map(item => item.id === id
      ? {...item, ...rect, minimized: false, maximized: false, restore: undefined} : item), id));
  }, [apps, windows, readArrangementBounds, editWindows, push]);
  const updateRect = useCallback(
    (id: string, rect: {x: number; y: number; width: number; height: number}) => {
      editWindows(current =>
        current.map(win =>
          win.id === id ? {...win, ...clampRect(rect, viewport.current, minSizeOf(apps.find(item => item.id === win.app_id)))} : win,
        ),
      );
    },
    [apps, editWindows],
  );
  const focus = useCallback((id: string) => editWindows(current => focusWindow(current, id)), [editWindows]);

  const applyPreferences = useCallback(
    async (patch: Partial<UserPreferences>, options?: PreferenceApplyOptions) => {
      if (!preferenceLifetime.current || currentAPI.current !== api || !preferenceOwner.current) throw new Error('账户上下文已变化，未保存旧偏好');
      const owner = preferenceOwner.current;
      const version = ++preferenceVersion.current;
      setPreferences(current => ({...current, ...patch}));
      if (options?.previewOnly) return;
      const pending = preferenceQueue.current.catch(() => undefined).then(async () => {
        if (!preferenceLifetime.current || currentAPI.current !== api || preferenceOwner.current !== owner) throw new Error('账户上下文已变化，未保存旧偏好');
        return api('/users/me/preferences', {method: 'PATCH', body: JSON.stringify(patch), ...(options?.keepalive ? {keepalive: true} : {})});
      });
      preferenceQueue.current = pending;
      try {
        const saved = await pending;
        // A full response also contains ranges whose preview/save may still be
        // pending or failed. Other PATCHes must not confirm those fields.
        if (preferenceLifetime.current && preferenceOwner.current === owner && currentAPI.current === api && version === preferenceVersion.current) {
          const confirmed = {...saved};
          if (!Object.hasOwn(patch, 'wallpaper_gradient_angle')) delete confirmed.wallpaper_gradient_angle;
          if (!Object.hasOwn(patch, 'wallpaper_dim')) delete confirmed.wallpaper_dim;
          setPreferences(current => ({...current, ...confirmed}));
        }
      } catch (error) {
        if (preferenceLifetime.current && preferenceOwner.current === owner && currentAPI.current === api) push({tone: 'danger', title: '偏好保存失败', description: String(error)});
        throw error;
      }
    },
    [api, push],
  );

  const toggleLaunchPad = useCallback(() => {
    setLaunchpadOpen(value => !value);
    setPaletteOpen(false);
  }, []);

  // Keyboard shortcuts. Browsers reserve a few of these combinations; the command
  // palette exposes the same actions when a shortcut is not deliverable.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const mod = event.metaKey || event.ctrlKey;
      if (!mod || event.defaultPrevented) return;
      if ((event.target as HTMLElement)?.closest?.('[aria-modal="true"]') && event.key.toLowerCase() !== 'k') return;
      const key = event.key.toLowerCase();
      if (key === 'k') {
        event.preventDefault();
        setPaletteOpen(value => !value);
        setLaunchpadOpen(false);
      } else if (key === 'w' && activeId) {
        event.preventDefault();
        closeWindow(activeId);
      } else if (key === 'm' && activeId) {
        event.preventDefault();
        minimizeWindow(activeId);
      } else if (key === '`') {
        const next = nextWindowId(windows, activeId, event.shiftKey ? -1 : 1);
        if (next) {
          event.preventDefault();
          restoreWindow(next);
        }
      } else if (key === ',' && !event.shiftKey) {
        event.preventDefault();
        openApp('settings');
      } else if (key === ' ') {
        event.preventDefault();
        toggleLaunchPad();
      }
    };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, [activeId, closeWindow, minimizeWindow, openApp, restoreWindow, toggleLaunchPad, windows]);

  useEffect(() => {
    const onFocusRefresh = () => {
      void apiPage('/work?limit=1000')
        .then(page => {
          const data = page.data as Array<{status: string; claim_active?: boolean}>;
          const active = data.filter(isActiveWork).length;
          setBadges(current => ({...current, issues: page.meta.next_cursor ? null : active, development: page.meta.next_cursor ? null : active}));
        })
        .catch(() => setBadges(current => ({...current, issues: null, development: null})));
    };
    onFocusRefresh();
    const timer = setInterval(onFocusRefresh, 60000);
    return () => clearInterval(timer);
  }, [apiPage]);

  // Messages from sandboxed iframe apps: only whitelisted types and known frames.
  useEffect(() => {
    const allowed = new Set(['app.ready', 'app.request_resize', 'app.set_title', 'app.open_app', 'app.notify']);
    const handler = (event: MessageEvent) => {
      const win = windows.find(candidate => {
        const app = apps.find(item => item.id === candidate.app_id);
        if (!app || app.kind !== 'iframe' || app.origin !== event.origin) return false;
        const frame = document.getElementById(`frame-${candidate.id}`) as HTMLIFrameElement | null;
        return frame?.contentWindow === event.source;
      });
      if (!win || !event.data || typeof event.data !== 'object' || !allowed.has(event.data.type)) return;
      const app = apps.find(item => item.id === win.app_id);
      if (event.data.type === 'app.ready' && app?.origin) {
        (event.source as Window | null)?.postMessage(
          {type: 'desktop.ready', theme: preferences.theme, accent: preferences.accent},
          app.origin,
        );
      }
      if (event.data.type === 'app.set_title' && typeof event.data.title === 'string') {
        editWindows(current => current.map(item => (item.id === win.id ? {...item, title: event.data.title.slice(0, 80)} : item)));
      }
      if (event.data.type === 'app.request_resize') {
        editWindows(current =>
          current.map(item =>
            item.id === win.id
              ? {
                  ...item,
                  ...clampRect(
                    {
                      x: item.x,
                      y: item.y,
                      width: Number(event.data.width) || item.width,
                      height: Number(event.data.height) || item.height,
                    },
                    viewport.current,
                    minSizeOf(app),
                  ),
                }
              : item,
          ),
        );
      }
      if (event.data.type === 'app.notify' && typeof event.data.message === 'string') {
        push({title: event.data.message.slice(0, 200)});
      }
      if (event.data.type === 'app.open_app' && typeof event.data.app_id === 'string') {
        openApp(event.data.app_id);
      }
    };
    addEventListener('message', handler);
    return () => removeEventListener('message', handler);
  }, [windows, apps, preferences, push, openApp]);

  const providers: CommandProvider[] = useMemo(
    () => [
      {
        id: 'applications',
        group: '应用程序',
        provide: query => {
          const needle = query.trim().toLowerCase();
          return apps
            .filter(app => !needle || `${app.title} ${app.id} ${app.description || ''}`.toLowerCase().includes(needle))
            .map(
              (app): Command => ({
                id: `app-${app.id}`,
                title: app.title,
                subtitle: app.description,
                group: '应用程序',
                icon: app.icon,
                run: () => openApp(app.id),
              }),
            );
        },
      },
      {
        id: 'windows',
        group: '窗口',
        provide: query => {
          const needle = query.trim().toLowerCase();
          return windows
            .filter(win => !needle || win.title.toLowerCase().includes(needle))
            .map(
              (win): Command => ({
                id: `window-${win.id}`,
                title: win.title,
                subtitle: win.minimized ? '最小化' : '切换到窗口',
                group: '窗口',
                icon: '▭',
                run: () => restoreWindow(win.id),
              }),
            );
        },
      },
      {
        id: 'xelibrary',
        group: '知识库',
        provide: query => apps.some(app => app.id === 'knowledge') ? [{
          id: 'knowledge-search', title: query.trim() ? `搜索 XeLibrary：${query.trim()}` : '搜索 XeLibrary',
          subtitle: '在知识库中编辑搜索条件并运行', group: '知识库', icon: '⌕',
          run: () => openApp('knowledge', {action: 'search', query: query.trim()}),
        }] : [],
      },
      {
        id: 'system',
        group: '系统命令',
        provide: query => {
          const needle = query.trim().toLowerCase();
          const commands: Command[] = [
            {id: 'cmd-settings', title: '打开设置', group: '系统命令', icon: '⚙', run: () => openApp('settings')},
            {id: 'cmd-system', title: '打开系统状态', group: '系统命令', icon: '◎', run: () => openApp('system')},
            {id: 'cmd-launchpad', title: '打开 LaunchPad', group: '系统命令', icon: '▦', run: () => setLaunchpadOpen(true)},
            {id: 'cmd-theme', title: '切换浅色 / 深色外观', group: '系统命令', icon: '◐', run: () => void applyPreferences({theme: preferences.theme === 'dark' ? 'light' : 'dark'}).catch(() => undefined)},
            {id: 'cmd-save-layout', title: '保存窗口布局', group: '系统命令', icon: '⇩', run: () => void saveLayoutWithFeedback()},
            {id: 'cmd-logout', title: '退出登录', group: '系统命令', icon: '⏻', run: () => void onLogout()},
          ];
          if (activeApp) {
            commands.unshift({
              id: 'cmd-close-window',
              title: `关闭窗口：${activeApp.title}`,
              group: '系统命令',
              icon: '✕',
              run: () => activeId && closeWindow(activeId),
            });
            commands.unshift({
              id: 'cmd-minimize-window',
              title: `最小化窗口：${activeApp.title}`,
              group: '系统命令',
              icon: '—',
              run: () => activeId && minimizeWindow(activeId),
            });
          }
          return needle ? commands.filter(command => command.title.toLowerCase().includes(needle)) : commands;
        },
      },
    ],
    [apps, windows, activeApp, activeId, openApp, restoreWindow, applyPreferences, preferences.theme, saveLayoutWithFeedback, push, onLogout, closeWindow, minimizeWindow],
  );

  const closeLaunchPad = useCallback(() => setLaunchpadOpen(false), []);
  const closePalette = useCallback(() => setPaletteOpen(false), []);

  const userWithoutWork = !subject;
  if (loadError) {
    return (
      <div className="desktop ds-compact">
        <MenuBar
          userLabel={userLabel}
          activeApp={null}
          windows={[]}
          activeWindowId={null}
          preferences={preferences}
          onOpenApp={() => undefined}
          onOpenLaunchPad={() => undefined}
          onOpenPalette={() => undefined}
          onRestoreWindow={() => undefined}
          onCloseWindow={() => undefined}
          onMinimizeWindow={() => undefined}
          onToggleMaximize={() => undefined}
          onArrangeWindow={() => undefined}
          arrangementReasons={{left: '左右排列不可用：桌面尚未加载', right: '左右排列不可用：桌面尚未加载'}}
          onSaveLayout={() => undefined}
          onLogout={() => void onLogout()}
          onApplyPreferences={patch => void applyPreferences(patch).catch(() => undefined)}
          clock={<MenuClock timezone={preferences.timezone} />}
        />
        <div className="desktop-error">
          <ErrorState
            title="无法加载桌面"
            message={loadError}
            onRetry={() => void connect()}
            retryLabel="重试加载"
          />
        </div>
      </div>
    );
  }

  if (!ready) {
    return (
      <div className="desktop ds-compact desktop-loading" role="status">
        <Spinner size="lg" label="正在加载桌面" />
        <p>正在加载桌面…</p>
      </div>
    );
  }

  return (
    <div className="desktop ds-compact" data-maximized-active={maximizedActive ? 'true' : undefined} data-has-widgets={((preferences.widgets || []).length > 0 || shownPluginWidgets.length > 0) ? 'true' : undefined} data-wallpaper-custom={customWallpaper ? (preferences.wallpaper_mode || 'solid') : undefined} style={customWallpaper ? {
      backgroundColor: customWallpaper.backgroundColor,
      backgroundImage: customWallpaper.backgroundImage,
      backgroundSize: customWallpaper.backgroundSize,
      backgroundPosition: customWallpaper.backgroundPosition,
      backgroundRepeat: customWallpaper.backgroundRepeat,
    } : undefined}>
      <MenuBar
        userLabel={userLabel}
        activeApp={activeApp}
        windows={windows}
        activeWindowId={activeId}
        preferences={preferences}
        onOpenApp={openApp}
        onOpenLaunchPad={toggleLaunchPad}
        onOpenPalette={() => setPaletteOpen(true)}
        onRestoreWindow={restoreWindow}
        onCloseWindow={closeWindow}
        onMinimizeWindow={minimizeWindow}
        onToggleMaximize={toggleWindowMaximize}
        onArrangeWindow={arrangeWindow}
        arrangementReasons={arrangementReasons}
        elementRef={setMenuElement}
        onSaveLayout={() => void saveLayoutWithFeedback()}
        onLogout={() => void onLogout()}
        onApplyPreferences={patch => void applyPreferences(patch).catch(() => undefined)}
        clock={<MenuClock timezone={preferences.timezone} />}
        status={
          <>
            <i className={`ds-status-dot ${userWithoutWork ? 'ds-status-neutral' : 'ds-status-success'}`} aria-hidden="true" />
            {userWithoutWork ? '受限身份' : '已连接'}
          </>
        }
      />

      <div className="window-layer">
        {windows.map(win => {
          const app = apps.find(item => item.id === win.app_id);
          if (!app) return null;
          const index = windows.indexOf(win);
          const isActive = win.id === activeId;
          return (
            <WindowFrame
              key={win.id}
              win={win}
              app={app}
              active={isActive}
              accentIndex={index}
              onFocus={() => focus(win.id)}
              onMove={rect => updateRect(win.id, rect)}
              onResize={rect => updateRect(win.id, rect)}
              onMinimize={() => minimizeWindow(win.id)}
              onToggleMaximize={() => toggleWindowMaximize(win.id)}
              onClose={() => closeWindow(win.id)}
            >
              {app.kind === 'iframe' ? (
                <IFrameApp
                  app={app}
                  windowId={win.id}
                  theme={preferences.theme}
                  active={isActive}
                  intent={appIntents[app.id]}
                />
              ) : app.component ? (
                <app.component
                  api={api}
                  saveLayout={saveLayout}
                  refreshApps={refreshApps}
                  apiPage={apiPage}
                  apiRaw={apiRaw}
                  app={app}
                  apps={apps}
                  openApp={openApp}
                  dockIds={dockIds}
                  togglePin={id => setDockIds(current => current.includes(id) ? current.filter(item => item !== id) : [...current, id])}
                  movePinned={(id, direction) => setDockIds(current => {
                    const from = current.indexOf(id), to = from + direction;
                    if (from < 0 || to < 0 || to >= current.length) return current;
                    const next = [...current];
                    [next[from], next[to]] = [next[to], next[from]];
                    return next;
                  })}
                  preferences={preferences}
                  applyPreferences={applyPreferences}
                  notify={push}
                  windowId={win.id}
                  intent={appIntents[app.id]}
                />
              ) : (
                <ErrorState title="应用不可用" message={`${app.title} 没有可用的前端实现。`} />
              )}
            </WindowFrame>
          );
        })}
      </div>

      {((preferences.widgets || []).length > 0 || shownPluginWidgets.length > 0) && (
        <div className="widgets">
          {(preferences.widgets || []).includes('clock') && <ClockWidget timezone={preferences.timezone} />}
          {(preferences.widgets || []).includes('status') && <StatusWidget apiPage={apiPage} onOpen={() => openApp('system')} />}
          {(preferences.widgets || []).includes('calendar') && <CalendarWidget timezone={preferences.timezone} />}
          {(preferences.widgets || []).includes('app-shortcuts') && <AppShortcutsWidget apps={apps} selected={preferences.shortcut_apps || []} onOpen={openApp} />}
          {(preferences.widgets || []).includes('work') && <WorkWidget apiPage={apiPage} allowed={workAvailable} onOpen={() => openApp('issues')} />}
          {(preferences.widgets || []).includes('search') && <SearchWidget available={knowledgeSearchAvailable} onSearch={query => openApp('knowledge', {action: 'search', query})} />}
          {(preferences.widgets || []).includes('notes') && <NotesWidget notes={preferences.notes || ''} onSave={saveNotes} />}
          {shownPluginWidgets.map(widget => <PluginWidget key={widget.plugin_id} widget={widget} />)}
        </div>
      )}

      {wallpaperError && <div className="wallpaper-load-error" role="alert">{wallpaperError}</div>}

      <Dock
        elementRef={setDockElement}
        apps={apps}
        windows={windows}
        activeWindowId={activeId}
        dockIds={dockIds}
        autoHide={preferences.dock_autohide || maximizedActive}
        size={(preferences.dock_size || 'medium') as DockSize}
        badges={badges}
        onLaunch={openApp}
        onRestore={restoreWindow}
        onToggleLaunchPad={toggleLaunchPad}
        onTogglePin={id =>
          setDockIds(current => (current.includes(id) ? current.filter(item => item !== id) : [...current, id]))
        }
        onReorder={(source, target) =>
          setDockIds(current => {
            const index = current.indexOf(target);
            if (index < 0 || !current.includes(source)) return current;
            const next = current.filter(id => id !== source);
            next.splice(index, 0, source);
            return next;
          })
        }
        onCloseWindow={closeWindow}
      />

      {launchpadOpen && <LaunchPad apps={apps} recentIds={recent} onLaunch={openApp} onClose={closeLaunchPad} />}
      <CommandPalette open={paletteOpen} providers={providers} onClose={closePalette} />
    </div>
  );
}
