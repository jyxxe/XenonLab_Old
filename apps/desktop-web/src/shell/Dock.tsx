import React, {useEffect, useRef, useState} from 'react';
import {flushSync} from 'react-dom';
import {ContextMenu, type MenuItem} from '../design-system/primitives';
import type {DesktopApp, WindowState} from './types';

export type DockSize = 'small' | 'medium' | 'large';

// Give the pointer time to travel from the edge trigger to the moving Dock.
// The reveal animation itself is deliberately slower than the general UI motion.
const DOCK_HIDE_DELAY_MS = 520;

export function Dock({
  apps,
  windows,
  activeWindowId,
  dockIds,
  autoHide,
  size,
  badges,
  onLaunch,
  onRestore,
  onToggleLaunchPad,
  onTogglePin,
  onReorder,
  onCloseWindow,
}: {
  apps: DesktopApp[];
  windows: WindowState[];
  activeWindowId: string | null;
  dockIds: string[];
  autoHide: boolean;
  size: DockSize;
  badges: Record<string, number | null>;
  onLaunch: (id: string) => void;
  onRestore: (windowId: string) => void;
  onToggleLaunchPad: () => void;
  onTogglePin: (id: string) => void;
  onReorder: (sourceId: string, targetId: string) => void;
  onCloseWindow: (windowId: string) => void;
}) {
  const [context, setContext] = useState<{x: number; y: number; appId: string} | null>(null);
  const [dragging, setDragging] = useState<string | null>(null);
  const [pointerActive, setPointerActive] = useState(false);
  const hideTimer = useRef<number | null>(null);

  useEffect(() => () => {
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
  }, []);

  useEffect(() => {
    if (autoHide) return;
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    hideTimer.current = null;
    setPointerActive(false);
  }, [autoHide]);

  function revealForPointer(event: React.PointerEvent) {
    if (event.pointerType === 'touch') return;
    if (hideTimer.current !== null) {
      window.clearTimeout(hideTimer.current);
      hideTimer.current = null;
    }
    // Commit the reveal before the next pointer event can leave the edge trigger.
    flushSync(() => setPointerActive(true));
  }

  function schedulePointerHide(event: React.PointerEvent) {
    if (event.pointerType === 'touch') return;
    if (hideTimer.current !== null) window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => {
      hideTimer.current = null;
      setPointerActive(false);
    }, DOCK_HIDE_DELAY_MS);
  }

  const known = (id: string) => apps.find(app => app.id === id);
  const pinned = dockIds.filter(known);
  const runningUnpinned = windows
    .map(win => win.app_id)
    .filter((id, index, list) => list.indexOf(id) === index && !pinned.includes(id) && known(id));
  const visibleIds = [...pinned, ...runningUnpinned];
  const minimized = windows.filter(win => win.minimized);
  const openWindows = (id: string) => windows.filter(win => win.app_id === id);

  function contextItems(appId: string): MenuItem[] {
    const app = known(appId);
    if (!app) return [];
    const pinnedNow = pinned.includes(appId);
    const index = dockIds.indexOf(appId);
    return [
      {id: 'open', label: '打开', onSelect: () => onLaunch(appId)},
      {separator: true},
      {
        id: 'pin',
        label: pinnedNow ? '从 Dock 移除' : '固定到 Dock',
        onSelect: () => onTogglePin(appId),
      },
      {id: 'left', label: '向左移动', disabled: !pinnedNow || index <= 0, onSelect: () => onReorder(appId, dockIds[index - 1])},
      {
        id: 'right',
        label: '向右移动',
        disabled: !pinnedNow || index < 0 || index >= dockIds.length - 1,
        onSelect: () => onReorder(appId, dockIds[index + 1]),
      },
      {separator: true},
      {
        id: 'close',
        label: '关闭所有窗口',
        danger: true,
        disabled: !openWindows(appId).length,
        onSelect: () => openWindows(appId).forEach(win => onCloseWindow(win.id)),
      },
    ];
  }

  return (
    <>
      {autoHide && (
        <div
          className="dock-edge"
          aria-hidden="true"
          onPointerEnter={revealForPointer}
          onPointerLeave={schedulePointerHide}
        />
      )}
      <nav
        className={[
          'dock',
          `dock-size-${size}`,
          autoHide ? 'ds-autohide' : '',
          pointerActive ? 'ds-pointer-active' : '',
        ]
          .filter(Boolean)
          .join(' ')}
        aria-label="应用程序"
        onPointerEnter={revealForPointer}
        onPointerLeave={schedulePointerHide}
      >
        <button type="button" className="dock-item" title="LaunchPad" onClick={onToggleLaunchPad}>
          <span className="dock-icon" aria-hidden="true">▦</span>
          <span className="dock-label">LaunchPad</span>
        </button>
        {visibleIds.map(id => {
          const app = known(id)!;
          const open = openWindows(id);
          const isActive = open.some(win => win.id === activeWindowId);
          const badge = badges[id];
          return (
            <button
              key={id}
              type="button"
              className={[
                'dock-item',
                pinned.includes(id) ? 'ds-pinned' : '',
                open.length ? 'ds-running' : '',
                isActive ? 'ds-active' : '',
                dragging === id ? 'ds-dragging' : '',
              ]
                .filter(Boolean)
                .join(' ')}
              title={app.title}
              aria-pressed={isActive}
              draggable
              onDragStart={event => {
                setDragging(id);
                event.dataTransfer.effectAllowed = 'move';
              }}
              onDragEnd={() => setDragging(null)}
              onDragOver={event => event.preventDefault()}
              onDrop={event => {
                event.preventDefault();
                if (dragging && dragging !== id) onReorder(dragging, id);
                setDragging(null);
              }}
              onKeyDown={event => {
                if (event.key === 'ContextMenu' || (event.shiftKey && event.key === 'F10')) {
                  event.preventDefault();
                  const rect = event.currentTarget.getBoundingClientRect();
                  setContext({x: rect.left, y: rect.top, appId: id});
                }
              }}
              onClick={() => onLaunch(id)}
              onContextMenu={event => {
                event.preventDefault();
                setContext({x: event.clientX, y: event.clientY, appId: id});
              }}
            >
              <span className="dock-icon" aria-hidden="true">{app.icon}</span>
              <span className="dock-label">{app.title}</span>
              {badge === null ? <span className="dock-badge" title="队列数量尚未完整确认" aria-label="队列数量尚未完整确认">?</span> : badge ? <span className="dock-badge">{badge > 99 ? '99+' : badge}</span> : null}
            </button>
          );
        })}
        {minimized.length > 0 && (
          <div className="dock-minimized">
            {minimized.map(win => (
              <button key={win.id} type="button" title={`恢复 ${win.title}`} onClick={() => onRestore(win.id)}>
                <span aria-hidden="true">◱</span>
                {win.title.length > 10 ? `${win.title.slice(0, 10)}…` : win.title}
              </button>
            ))}
          </div>
        )}
      </nav>
      {context && <ContextMenu x={context.x} y={context.y} items={contextItems(context.appId)} onClose={() => setContext(null)} />}
    </>
  );
}
