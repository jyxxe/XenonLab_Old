import React from 'react';
import {MenuButton, type MenuItem} from '../design-system/primitives';
import {appEntryUrl, forgejoBase} from './apps';
import type {AppIntent, DesktopApp, UserPreferences, WindowState} from './types';

export function MenuBar({
  userLabel,
  activeApp,
  windows,
  activeWindowId,
  preferences,
  onOpenApp,
  onOpenLaunchPad,
  onOpenPalette,
  onRestoreWindow,
  onCloseWindow,
  onMinimizeWindow,
  onToggleMaximize,
  onSaveLayout,
  onLogout,
  onApplyPreferences,
  clock,
  status,
}: {
  userLabel: string;
  activeApp: DesktopApp | null;
  windows: WindowState[];
  activeWindowId: string | null;
  preferences: UserPreferences;
  onOpenApp: (id: string, intent?: Omit<AppIntent, 'id'>) => void;
  onOpenLaunchPad: () => void;
  onOpenPalette: () => void;
  onRestoreWindow: (id: string) => void;
  onCloseWindow: (id: string) => void;
  onMinimizeWindow: (id: string) => void;
  onToggleMaximize: (id: string) => void;
  onSaveLayout: () => void;
  onLogout: () => void;
  onApplyPreferences: (patch: Partial<UserPreferences>) => void;
  clock: React.ReactNode;
  status?: React.ReactNode;
}) {
  const active = windows.find(win => win.id === activeWindowId) ?? null;

  const systemMenu: MenuItem[] = [
    {id: 'about', label: '关于 XeonoLab', onSelect: () => onOpenApp('system')},
    {separator: true},
    {id: 'settings', label: '系统设置…', shortcut: '⌘,', onSelect: () => onOpenApp('settings')},
    {id: 'palette', label: '命令面板…', shortcut: '⌘K', onSelect: onOpenPalette},
    {separator: true},
    {id: 'layout', label: '保存窗口布局', onSelect: onSaveLayout},
    {separator: true},
    {id: 'logout', label: '退出登录', danger: true, onSelect: onLogout},
  ];

  const fileMenu: MenuItem[] = [
    {id: 'launchpad', label: '打开 LaunchPad', onSelect: onOpenLaunchPad},
    {separator: true},
    {id: 'close', label: '关闭窗口', shortcut: 'Ctrl+W', disabled: !active, onSelect: () => active && onCloseWindow(active.id)},
  ];

  const viewMenu: MenuItem[] = [
    {id: 'launchpad-view', label: '显示 LaunchPad', onSelect: onOpenLaunchPad},
    {id: 'palette-view', label: '命令面板…', shortcut: '⌘K', onSelect: onOpenPalette},
    {separator: true},
    {id: 'theme-system', label: '外观：跟随系统', checked: preferences.theme === 'system', onSelect: () => onApplyPreferences({theme: 'system'})},
    {id: 'theme-light', label: '外观：浅色', checked: preferences.theme === 'light', onSelect: () => onApplyPreferences({theme: 'light'})},
    {id: 'theme-dark', label: '外观：深色', checked: preferences.theme === 'dark', onSelect: () => onApplyPreferences({theme: 'dark'})},
  ];

  const windowMenu: MenuItem[] = [
    ...windows.map(win => ({
      id: `win-${win.id}`,
      label: win.title,
      checked: win.id === activeWindowId,
      onSelect: () => onRestoreWindow(win.id),
    })),
    ...(windows.length ? [{separator: true} as MenuItem] : []),
    {id: 'minimize', label: '最小化当前窗口', shortcut: 'Ctrl+M', disabled: !active, onSelect: () => active && onMinimizeWindow(active.id)},
    {id: 'zoom', label: active?.maximized ? '还原当前窗口' : '缩放当前窗口', disabled: !active, onSelect: () => active && onToggleMaximize(active.id)},
  ];

  const appMenu: MenuItem[] = activeApp?.menu?.map(entry => ({
    id: entry.id,
    label: entry.label,
    disabled: (entry.action === 'external' || entry.action === 'reload') && !activeApp.origin,
    onSelect: () => {
      if (entry.intent) onOpenApp(activeApp.id, entry.intent);
      else if (entry.action === 'external') window.open(appEntryUrl(activeApp), '_blank', 'noopener');
      else window.dispatchEvent(new CustomEvent(`xeonolab.app.${entry.action}`, {detail: {windowId: activeWindowId}}));
    },
  })) ?? [{id: 'app-placeholder', label: '此应用没有额外菜单', disabled: true}];

  const helpMenu: MenuItem[] = [
    {id: 'shortcuts', label: '快捷键：⌘K 命令面板 · ⌘W 关闭 · ⌘M 最小化 · ⌘` 切换窗口', disabled: true},
    {separator: true},
    {id: 'issues', label: '打开 Forgejo Issues', onSelect: () => window.open(`${forgejoBase()}/xenon-admin/XeonoLab/issues`, '_blank', 'noopener')},
  ];

  return (
    <header className="menubar">
      <MenuButton label={<span className="menubar-brand"><b>✦</b> XeonoLab</span>} items={systemMenu} className="menubar-app-menu" />
      <MenuButton label={activeApp ? activeApp.title : 'Desktop'} items={appMenu} />
      <MenuButton label="文件" items={fileMenu} />
      <MenuButton label="显示" items={viewMenu} />
      <MenuButton label="窗口" items={windowMenu} />
      <MenuButton label="帮助" items={helpMenu} />
      <span className="menubar-spacer" />
      <div className="menubar-mobile-switcher"><MenuButton label="切换应用" items={[
        ...windows.map(win => ({id: `mobile-${win.id}`, label: `${win.title}${win.minimized ? '（最小化）' : ''}`, checked: win.id === activeWindowId, onSelect: () => onRestoreWindow(win.id)})),
        ...(windows.length ? [{separator: true} as MenuItem] : []),
        {id: 'mobile-launchpad', label: '打开 LaunchPad', onSelect: onOpenLaunchPad},
      ]} align="right" /></div>
      {status ? <span className="menubar-status menubar-secondary">{status}</span> : null}
      <button type="button" className="menubar-icon" aria-label="命令面板" title="命令面板 (⌘K)" onClick={onOpenPalette}>⌕</button>
      <span className="menubar-clock">{clock}</span>
      <MenuButton
        label={<span title={userLabel}>{userLabel.slice(0, 12) || '账户'}</span>}
        align="right"
        className="menubar-user-menu"
        items={[
          {id: 'account', label: `当前身份：${userLabel}`, disabled: true},
          {separator: true},
          {id: 'settings-user', label: '系统设置…', onSelect: () => onOpenApp('settings')},
          {id: 'logout-user', label: '退出登录', danger: true, onSelect: onLogout},
        ]}
      />
    </header>
  );
}
