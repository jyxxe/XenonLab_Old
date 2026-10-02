import React, {useEffect, useRef, useState} from 'react';
import {Button, Spinner} from '../design-system/primitives';
import {appEntryUrl, sandboxValue} from './apps';
import type {AppIntent, DesktopApp} from './types';

type FramePhase = 'loading' | 'loaded' | 'ready' | 'timeout' | 'auth-error' | 'network-error';

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
  intent,
  timeoutMs = (globalThis as {__XEONOLAB_IFRAME_TIMEOUT__?: number}).__XEONOLAB_IFRAME_TIMEOUT__ ?? 20000,
}: {
  app: DesktopApp;
  windowId: string;
  theme: string;
  active: boolean;
  intent?: AppIntent;
  timeoutMs?: number;
}) {
  const [phase, setPhase] = useState<FramePhase>('loading');
  const [attempt, setAttempt] = useState(0);
  const timer = useRef<number | null>(null);
  const [handoff, setHandoff] = useState<{prompt: string; sent: boolean} | null>(null);
  const deliveredDraft = useRef<string | null>(null);

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

  useEffect(() => {
    const handle = (event: MessageEvent) => {
      const frame = document.getElementById(`frame-${windowId}`) as HTMLIFrameElement | null;
      if (event.source !== frame?.contentWindow || event.origin !== app.origin || !event.data || typeof event.data !== 'object') return;
      if (event.data.type === 'app.ready') setPhase('ready');
      if (event.data.type === 'app.status' && ['auth-error', 'network-error'].includes(event.data.status)) setPhase(event.data.status);
    };
    const reload = (event: Event) => {if (active && (!(event as CustomEvent).detail?.windowId || (event as CustomEvent).detail.windowId === windowId)) setAttempt(value => value+1)};
    addEventListener('message',handle);
    addEventListener('xeonolab.app.reload',reload);
    return () => {removeEventListener('message',handle);removeEventListener('xeonolab.app.reload',reload)};
  },[windowId,app.origin,active]);

  // An askAI intent carries an editable XeLibrary draft. It is offered as a
  // reviewable handoff card; the user pastes it into the frame or copies it,
  // because the sandboxed cross-origin frame cannot be scripted by us.
  useEffect(() => {
    if (!intent || intent.action !== 'askAI' || !intent.prompt) return;
    if (deliveredDraft.current === intent.id) return;
    deliveredDraft.current = intent.id;
    setHandoff({prompt: intent.prompt, sent: false});
  }, [intent]);

  const url = appEntryUrl(app);
  const frameUrl = new URL(url);
  if (attempt) frameUrl.searchParams.set('reload',String(attempt));
  return (
    <div className={`iframe-app${phase === 'ready' || phase === 'loaded' ? ' ds-loaded' : ''}`}>
      {handoff && (
        <div className="iframe-handoff" role="region" aria-label="待发送的 AI 草稿">
          <strong>来自知识库的 AI 草稿</strong>
          <p>下面的草稿来自 XeLibrary 的记录选择，尚未发送到 {app.title}。跨域应用无法自动输入，请复制后粘贴到对话输入框。</p>
          <textarea
            className="iframe-handoff-text"
            aria-label="AI 草稿内容"
            spellCheck={false}
            readOnly
            value={handoff.prompt}
          />
          <div className="ds-button-row">
            <Button size="sm" variant="primary" onClick={async () => {
              try { await navigator.clipboard?.writeText(handoff.prompt); setHandoff({...handoff, sent: true}); } catch { /* clipboard unavailable */ }
            }}>{handoff.sent ? '已复制草稿' : '复制草稿'}</Button>
            <Button size="sm" variant="ghost" onClick={() => setHandoff(null)}>收起</Button>
          </div>
        </div>
      )}
      {phase === 'loading' && <div className="iframe-loading-bar" aria-hidden="true" />}
      <iframe
        key={attempt}
        id={`frame-${windowId}`}
        title={app.title}
        src={frameUrl.href}
        sandbox={sandboxValue(app)}
        referrerPolicy="no-referrer"
        onError={() => setPhase('network-error')}
        onLoad={() => {
          if (timer.current) window.clearTimeout(timer.current);
          setPhase(current => ['ready', 'auth-error', 'network-error'].includes(current) ? current : 'loaded');
        }}
      />
      <div className="iframe-toolbar" role="status"><span>{phase === 'ready' ? '应用已连接' : phase === 'loaded' ? '页面已载入；认证和业务连接尚未确认。' : phase === 'auth-error' ? '应用需要登录或重新授权' : phase === 'network-error' || phase === 'timeout' ? '应用连接失败' : '正在连接外部应用'}</span><Button size="sm" onClick={() => setAttempt(value => value+1)}>重新加载</Button><Button size="sm" onClick={() => window.open(url,'_blank','noopener')}>在浏览器中打开</Button></div>
      {phase === 'loading' && (
        <div className="iframe-app-status" role="status">
          <Spinner size="lg" label={`正在连接 ${app.title}`} />
          <strong>正在连接 {app.title}</strong>
          <p>{app.origin}</p>
        </div>
      )}
      {['timeout', 'auth-error', 'network-error'].includes(phase) && (
        <div className="iframe-app-status" role="alert">
          <b aria-hidden="true">⚠</b>
          <strong>{app.title}{phase === 'auth-error' ? ' 需要重新授权' : ' 无法加载'}</strong>
          <p>
            {phase === 'auth-error' ? '应用报告登录失效或没有访问权限。请在浏览器中登录，然后重新加载；桌面窗口和平台登录保持不变。' : phase === 'network-error' ? '应用报告网络连接失败。请检查服务与网络后重试；当前窗口会保留打开状态。' : `在 ${timeoutMs / 1000} 秒内页面没有完成载入。可能是服务未启动、网络不可达，或 frame policy 阻止访问；当前窗口会保留打开状态。`}
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
