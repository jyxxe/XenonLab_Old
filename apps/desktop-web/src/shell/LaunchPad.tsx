import React, {useEffect, useMemo, useRef, useState} from 'react';
import {Input, useFocusTrap} from '../design-system/primitives';
import type {DesktopApp} from './types';

export function LaunchPad({
  apps,
  recentIds,
  onLaunch,
  onClose,
}: {
  apps: DesktopApp[];
  recentIds: string[];
  onLaunch: (id: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const trap = useFocusTrap(true, onClose);
  const searchRef = useRef<HTMLInputElement>(null);

  const ordered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (needle) {
      return apps.filter(app => `${app.title} ${app.id} ${app.description || ''}`.toLowerCase().includes(needle));
    }
    const recent = recentIds.map(id => apps.find(app => app.id === id)).filter((app): app is DesktopApp => Boolean(app));
    const recentSet = new Set(recent.map(app => app.id));
    return [...recent, ...apps.filter(app => !recentSet.has(app.id))];
  }, [apps, query, recentIds]);

  useEffect(() => {
    setActive(0);
  }, [query]);

  useEffect(() => {
    searchRef.current?.focus();
  }, []);

  useEffect(() => {
    if (active >= ordered.length) setActive(Math.max(0, ordered.length - 1));
  }, [ordered.length, active]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    addEventListener('keydown', onKey);
    return () => removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div ref={trap} className="launchpad" role="dialog" aria-modal="true" aria-label="LaunchPad" onPointerDown={event => event.target === event.currentTarget && onClose()}>
      <Input
        ref={searchRef}
        className="launchpad-search"
        type="search"
        aria-label="搜索应用"
        placeholder="搜索应用"
        value={query}
        onChange={event => setQuery(event.target.value)}
        onKeyDown={event => {
          if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
            event.preventDefault();
            setActive(value => Math.min(value + 1, ordered.length - 1));
          } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
            event.preventDefault();
            setActive(value => Math.max(value - 1, 0));
          } else if (event.key === 'Enter' && ordered[active]) {
            event.preventDefault();
            onLaunch(ordered[active].id);
          }
        }}
      />
      {ordered.length ? (
        <div className="launchpad-grid" role="listbox" aria-label="应用程序列表">
          {ordered.map((app, index) => (
            <button
              key={app.id}
              type="button"
              role="option"
              aria-selected={index === active}
              className={`launchpad-app${index === active ? ' ds-active' : ''}`}
              onMouseEnter={() => setActive(index)}
              onClick={() => onLaunch(app.id)}
            >
              <span className="launchpad-icon" aria-hidden="true">{app.icon}</span>
              <strong>{app.title}</strong>
              <small>{recentIds.includes(app.id) && !query.trim() ? '最近使用' : app.kind === 'native' ? '桌面应用' : '外部应用'}</small>
            </button>
          ))}
        </div>
      ) : (
        <p className="launchpad-empty">没有匹配的应用</p>
      )}
      <p className="launchpad-hint">方向键选择 · Enter 打开 · Esc 关闭</p>
    </div>
  );
}
