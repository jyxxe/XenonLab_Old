import React, {useRef, useState} from 'react';
import {resizeRect} from './window-manager.js';
import type {DesktopApp, Rect, WindowState} from './types';

const HANDLES = ['n', 's', 'e', 'w', 'se', 'sw'] as const;

export function WindowFrame({
  win,
  app,
  active,
  accentIndex,
  onFocus,
  onMove,
  onResize,
  onMinimize,
  onToggleMaximize,
  onClose,
  actions,
  children,
}: {
  win: WindowState;
  app: DesktopApp;
  active: boolean;
  accentIndex: number;
  onFocus: () => void;
  onMove: (rect: Rect) => void;
  onResize: (rect: Rect) => void;
  onMinimize: () => void;
  onToggleMaximize: () => void;
  onClose: () => void;
  actions?: React.ReactNode;
  children: React.ReactNode;
}) {
  const [dragging, setDragging] = useState(false);
  const layerRef = useRef<HTMLElement>(null);

  function drag(event: React.PointerEvent) {
    if ((event.target as HTMLElement).closest('button,a,input,select')) return;
    if (win.maximized) return;
    event.preventDefault();
    const startX = event.clientX;
    const startY = event.clientY;
    const origin = {x: win.x, y: win.y};
    setDragging(true);
    const move = (pointer: PointerEvent) => {
      onMove({x: origin.x + pointer.clientX - startX, y: origin.y + pointer.clientY - startY, width: win.width, height: win.height});
    };
    const up = () => {
      setDragging(false);
      removeEventListener('pointermove', move);
      removeEventListener('pointerup', up);
    };
    addEventListener('pointermove', move);
    addEventListener('pointerup', up);
  }

  function resize(event: React.PointerEvent, handle: string) {
    if (win.maximized || event.button !== 0) return;
    event.preventDefault();
    const startX = event.clientX;
    const startY = event.clientY;
    const origin = {x: win.x, y: win.y, width: win.width, height: win.height};
    const move = (pointer: PointerEvent) => {
      onResize(
        resizeRect(origin, {width: app.minSize[0], height: app.minSize[1]}, handle, pointer.clientX - startX, pointer.clientY - startY, {
          width: innerWidth,
          height: innerHeight,
        }),
      );
    };
    const up = () => {
      removeEventListener('pointermove', move);
      removeEventListener('pointerup', up);
    };
    addEventListener('pointermove', move);
    addEventListener('pointerup', up);
  }

  return (
    <section
      ref={layerRef}
      className={[
        'window',
        active ? 'ds-window-active' : 'ds-window-inactive',
        win.maximized ? 'ds-window-maximized' : '',
        dragging ? 'ds-window-dragging' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      data-window-id={win.id}
      data-app-id={win.app_id}
      data-window-active={active ? 'true' : 'false'}
      style={{
        left: win.x,
        top: win.y,
        width: win.width,
        height: win.height,
        zIndex: 100 + accentIndex,
        visibility: win.minimized ? 'hidden' : undefined,
        pointerEvents: win.minimized ? 'none' : undefined,
      }}
      onPointerDown={onFocus}
      onFocusCapture={onFocus}
      aria-label={`${win.title} 窗口`}
    >
      <header className="bar" tabIndex={0} role="group"
        aria-label={`${win.title} 窗口布局：方向键移动，Shift 加方向键调整大小`}
        onKeyDown={event => {
          if (event.target !== event.currentTarget || win.maximized || innerWidth < 1024) return;
          const direction: Record<string, [number, number]> = {ArrowLeft: [-10, 0], ArrowRight: [10, 0], ArrowUp: [0, -10], ArrowDown: [0, 10]};
          const delta = direction[event.key];
          if (!delta) return;
          event.preventDefault();
          if (event.shiftKey) onResize(resizeRect(win, {width: app.minSize[0], height: app.minSize[1]}, 'se', delta[0], delta[1], {width: innerWidth, height: innerHeight}));
          else onMove({...win, x: win.x + delta[0], y: win.y + delta[1]});
        }}
        onPointerDown={drag} onDoubleClick={event => !(event.target as HTMLElement).closest('button') && onToggleMaximize()}>
        <div className="traffic">
          <button className="close" aria-label={`关闭 ${win.title}`} title="关闭" onClick={onClose} />
          <button className="min" aria-label={`最小化 ${win.title}`} title="最小化" onClick={onMinimize} />
          <button className="max" aria-label={`${win.maximized ? '还原' : '最大化'} ${win.title}`} title={win.maximized ? '还原' : '最大化'} onClick={onToggleMaximize} />
        </div>
        <span className="window-title">{win.title}</span>
        <div className="window-actions">{actions}</div>
      </header>
      <div className={`window-body${app.kind === 'native' ? ' ds-native-body' : ''}`}>{children}</div>
      {!win.maximized &&
        HANDLES.map(handle => (
          <span key={handle} className={`window-resize-handle rs-${handle}`} onPointerDown={event => resize(event, handle)} aria-hidden="true" />
        ))}
    </section>
  );
}
