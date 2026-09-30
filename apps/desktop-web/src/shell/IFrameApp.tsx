import React, {useEffect, useRef, useState} from 'react';
import {Button, Spinner} from '../design-system/primitives';
import {appEntryUrl, sandboxValue} from './apps';
import type {DesktopApp} from './types';

type FramePhase = 'loading' | 'ready' | 'timeout';

/**
 * Wraps every external application in consistent loading / timeout / failure states.
 * A cross-origin frame cannot report HTTP errors, so a 20s deadline drives the
 * "unreachable" state instead of leaving a blank area.
 */
export function IFrameApp({
  app,
  windowId,
  theme,
  active,
  timeoutMs = (globalThis as {__XEONOLAB_IFRAME_TIMEOUT__?: number}).__XEONOLAB_IFRAME_TIMEOUT__ ?? 20000,
}: {
  app: DesktopApp;
  windowId: string;
  theme: string;
  active: boolean;
  timeoutMs?: number;
}) {
  const [phase, setPhase] = useState<FramePhase>('loading');
  const [attempt, setAttempt] = useState(0);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    setPhase('loading');
    timer.current = window.setTimeout(() => setPhase(current => (current === 'loading' ? 'timeout' : current)), timeoutMs);
    return () => {
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [attempt, timeoutMs]);

  useEffect(() => {
    const frame = document.getElementById(`frame-${windowId}`) as HTMLIFrameElement | null;
    if (app.origin) frame?.contentWindow?.postMessage({type: 'theme.changed', theme}, app.origin);
  }, [theme, windowId, app.origin, attempt]);

  const url = appEntryUrl(app);
  return (
    <div className={`iframe-app${phase === 'ready' ? ' ds-loaded' : ''}`}>
      {phase === 'loading' && <div className="iframe-loading-bar" aria-hidden="true" />}
      <iframe
        id={`frame-${windowId}`}
        title={app.title}
        src={attempt ? `${url}${url.includes('?') ? '&' : '?'}reload=${attempt}` : url}
        sandbox={sandboxValue(app)}
        referrerPolicy="no-referrer"
        onLoad={() => {
          if (timer.current) window.clearTimeout(timer.current);
          setPhase('ready');
        }}
      />
      {phase === 'loading' && (
        <div className="iframe-app-status" role="status">
          <Spinner size="lg" label={`正在连接 ${app.title}`} />
          <strong>正在连接 {app.title}</strong>
          <p>{app.origin}</p>
        </div>
      )}
      {phase === 'timeout' && (
        <div className="iframe-app-status" role="alert">
          <b aria-hidden="true">⚠</b>
          <strong>{app.title} 无法加载</strong>
          <p>
            在 {timeoutMs / 1000} 秒内没有收到应用响应。可能是服务未启动、网络不可达，或认证尚未完成。
            当前窗口会保留打开状态。
          </p>
          <div className="ds-button-row">
            <Button onClick={() => setAttempt(value => value + 1)}>重新加载</Button>
            <Button variant="primary" onClick={() => window.open(url, '_blank', 'noopener')}>在浏览器中打开</Button>
          </div>
          {!active && <p>该窗口当前不是活跃窗口。</p>}
        </div>
      )}
    </div>
  );
}
