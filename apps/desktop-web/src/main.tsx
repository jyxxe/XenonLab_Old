import React, {useCallback, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './design-system/tokens.css';
import './design-system/primitives.css';
import './shell/shell.css';
import './style.css';
import {login, callback, sessionToken, logout, SessionFailure} from './auth';
import {initialSessionState, transitionSession, mayUseCachedToken} from './session-state.js';
import {ToastProvider} from './design-system/primitives';
import {DesktopShell} from './shell/DesktopShell';
import {Knowledge} from './Knowledge';
import {APIRequestError} from './api-error.js';

type APIPage<T = any> = {data: T; meta: {request_id?: string; next_cursor?: string | null; [key: string]: any}};
const libraryView = location.hostname === 'library.xenon.lan';

function App() {
  const [token, setToken] = useState('');
  const [persistent, setPersistent] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState('');
  const [sessionStatus, setSessionStatus] = useState(initialSessionState);

  const transition = (event: {type: string; message?: string}) => setSessionStatus(old => transitionSession(old, event));

  useEffect(() => {
    if (!libraryView) return;
    const handler = (event: MessageEvent) => {
      const trusted = ['https://xenon.lan', 'https://192.168.0.101:9443', 'https://xenon.jyxxe.com'];
      if (event.source !== parent || !trusted.includes(event.origin) || event.data?.type !== 'auth.session_changed' || typeof event.data.access_token !== 'string') return;
      setToken(event.data.access_token);
    };
    addEventListener('message', handler);
    return () => removeEventListener('message', handler);
  }, []);

  useEffect(() => {
    let active = true;
    callback()
      .then(async result => result || (await sessionToken()))
      .then(result => {
        if (!active) return;
        if (result) {
          setPersistent(true);
          setToken(result.token);
          transition({type: 'restored'});
        } else transition({type: 'no-session'});
      })
      .catch(e => {
        if (!active) return;
        const failure = e instanceof SessionFailure ? e : new SessionFailure('temporary', String(e));
        transition({type: failure.kind === 'reauthorize' ? 'reauthorization-required' : 'temporary-failure', message: failure.message});
        setError(failure.message);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!persistent && sessionStatus.phase !== 'retrying') return;
    let busy = false;
    const renew = async () => {
      if (busy) return;
      busy = true;
      try {
        const result = await sessionToken();
        if (result) {
          setPersistent(true);
          setToken(result.token);
          transition({type: 'restored'});
        } else if (persistent) transition({type: 'reauthorization-required', message: '登录凭据已失效，需要重新授权'});
        else transition({type: 'no-session'});
      } catch (e) {
        const failure = e instanceof SessionFailure ? e : new SessionFailure('temporary', String(e));
        transition({type: failure.kind === 'reauthorize' ? 'reauthorization-required' : 'temporary-failure', message: failure.message});
      } finally {
        busy = false;
      }
    };
    const timer = setInterval(renew, sessionStatus.phase === 'retrying' ? 12000 : 60000);
    const visible = () => {
      if (document.visibilityState === 'visible') void renew();
    };
    document.addEventListener('visibilitychange', visible);
    return () => {
      clearInterval(timer);
      document.removeEventListener('visibilitychange', visible);
    };
  }, [persistent, sessionStatus.phase]);

  useEffect(() => {
    if (token && !connected) setConnected(true);
  }, [token]);

  async function accessToken() {
    if (!persistent) return token ? {token} : null;
    try {
      const current = await sessionToken();
      if (current) {
        setToken(current.token);
        return current;
      }
      const failure = new SessionFailure('reauthorize', '登录凭据已失效，需要重新授权');
      transition({type: 'reauthorization-required', message: failure.message});
      throw failure;
    } catch (e) {
      const failure = e instanceof SessionFailure ? e : new SessionFailure('temporary', String(e));
      transition({type: failure.kind === 'reauthorize' ? 'reauthorization-required' : 'temporary-failure', message: failure.message});
      if (mayUseCachedToken(failure.kind, Boolean(token))) return {token};
      throw failure;
    }
  }

  async function apiPage<T = any>(path: string, init: RequestInit = {}): Promise<APIPage<T>> {
    const current = await accessToken();
    if (!current) throw Error('需要登录后访问工作空间');
    const headers = new Headers(init.headers);
    if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    headers.set('Authorization', `Bearer ${current.token}`);
    const response = await fetch(`/api/v1${path}`, {...init, headers});
    const result = await response.json();
    if (!response.ok) {
      if (response.status === 401) transition({type: 'reauthorization-required', message: '工作空间需要重新授权'});
      throw new APIRequestError(result.error?.message || `HTTP ${response.status}`, response.status, result.error?.code);
    }
    return {data: result.data, meta: result.meta || {}};
  }

  async function api<T = any>(path: string, init: RequestInit = {}): Promise<T> {
    return (await apiPage<T>(path, init)).data;
  }

  async function apiRaw(path: string, init: RequestInit = {}): Promise<Response> {
    const current = await accessToken();
    if (!current) throw Error('需要登录后访问工作空间');
    const headers = new Headers(init.headers);
    if (typeof FormData !== 'undefined' && init.body instanceof FormData) headers.delete('Content-Type');
    else if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
    headers.set('Authorization', `Bearer ${current.token}`);
    const response = await fetch(`/api/v1${path}`, {...init, headers});
    if (!response.ok) {
      const result = await response.clone().json().catch(() => null);
      if (response.status === 401) transition({type: 'reauthorization-required', message: '工作空间需要重新授权'});
      throw new APIRequestError(result?.error?.message || result?.detail?.[0]?.msg || `HTTP ${response.status}`, response.status, result?.error?.code);
    }
    return response;
  }

  async function handleLogout() {
    try {
      if (persistent) await logout();
    } catch {
      /* the local session is cleared even when the provider is unreachable */
    }
    setPersistent(false);
    setToken('');
    setConnected(false);
    transition({type: 'user-logout'});
  }

  // Session renewal and status banners re-render App. Preserve the API boundary
  // identity so child effects do not reconnect/reset an active workspace.
  // Requests still use the latest session/auth implementation.
  const transport = useRef({api, apiPage, apiRaw});
  transport.current = {api, apiPage, apiRaw};
  const stableApi = useCallback((path: string, init?: RequestInit) => transport.current.api(path, init), []);
  const stableApiPage = useCallback((path: string, init?: RequestInit) => transport.current.apiPage(path, init), []);
  const stableApiRaw = useCallback((path: string, init?: RequestInit) => transport.current.apiRaw(path, init), []);

  if (libraryView) return <Knowledge api={stableApi} apiPage={stableApiPage} apiRaw={stableApiRaw} />;

  const sessionBanner =
    connected && (sessionStatus.phase === 'retrying' || sessionStatus.phase === 'reauthorize') ? (
      <aside
        className="session-banner"
        data-testid="session-status-banner"
        data-phase={sessionStatus.phase}
        role={sessionStatus.phase === 'reauthorize' ? 'alert' : 'status'}
      >
        {sessionStatus.message}；当前工作区已保留。
        {sessionStatus.phase === 'retrying' ? (
          <button
            type="button"
            data-testid="session-retry"
            onClick={() =>
              sessionToken()
                .then(result => {
                  if (result) {
                    setToken(result.token);
                    transition({type: 'restored'});
                  }
                })
                .catch(e =>
                  transition({
                    type: e instanceof SessionFailure && e.kind === 'reauthorize' ? 'reauthorization-required' : 'temporary-failure',
                    message: String(e),
                  }),
                )
            }
          >
            重试
          </button>
        ) : (
          <button type="button" data-testid="session-reauthorize" onClick={() => login().catch(e => setError(String(e)))}>
            重新授权登录
          </button>
        )}
      </aside>
    ) : null;

  if (!connected) {
    return (
      <div className="login-page">
        <header className="menubar">
          <span className="menubar-brand"><b>✦</b> XeonoLab</span>
          <span className="menubar-spacer" />
          <span className="menubar-status">知识 · 模型 · 工作空间</span>
        </header>
        <section className="intro">
          <small>YOUR KNOWLEDGE WORKSPACE</small>
          <h1>让知识，有处可循。</h1>
          <p>统一桌面 · 可追溯数据 · 受控工具</p>
        </section>
        <section className="login">
          <h2>连接工作空间</h2>
          <p>通过统一身份服务登录。</p>
          {sessionStatus.phase === 'restoring' ? (
            <p data-testid="session-status" data-phase="restoring" role="status">正在恢复工作空间…</p>
          ) : sessionStatus.phase === 'retrying' ? (
            <>
              <p data-testid="session-status" data-phase="retrying" role="status">{sessionStatus.message}，工作区将保留。</p>
              <button
                type="button"
                data-testid="session-retry"
                onClick={() => {
                  transition({type: 'restore-started'});
                  sessionToken()
                    .then(result => {
                      if (result) {
                        setPersistent(true);
                        setToken(result.token);
                        transition({type: 'restored'});
                      } else transition({type: 'no-session'});
                    })
                    .catch(e =>
                      transition({
                        type: e instanceof SessionFailure && e.kind === 'reauthorize' ? 'reauthorization-required' : 'temporary-failure',
                        message: String(e),
                      }),
                    );
                }}
              >
                重试恢复
              </button>
              <button type="button" data-testid="session-reauthorize" onClick={() => login().catch(e => setError(String(e)))}>重新授权登录</button>
              <button type="button" data-testid="session-logout" onClick={() => void handleLogout()}>退出登录</button>
            </>
          ) : sessionStatus.phase === 'reauthorize' ? (
            <>
              <p data-testid="session-status" data-phase="reauthorize" role="alert">{sessionStatus.message}；当前工作区已保留。</p>
              <button type="button" data-testid="session-reauthorize" onClick={() => login().catch(e => setError(String(e)))}>重新授权登录</button>
              <button type="button" data-testid="session-logout" onClick={() => void handleLogout()}>退出登录</button>
            </>
          ) : (
            <>
              <button type="button" className="ds-button ds-button-primary" onClick={() => login().catch(e => setError(String(e)))}>登录 XeonoLab</button>
              <details>
                <summary>API 访问令牌</summary>
                <input
                  className="ds-input"
                  type="password"
                  aria-label="访问令牌"
                  value={token}
                  onChange={e => setToken(e.target.value)}
                  placeholder="OIDC access token"
                />
                <button type="button" className="ds-button" onClick={() => setConnected(true)}>连接</button>
              </details>
            </>
          )}
          <p className="error" role="alert">{error}</p>
        </section>
        <output hidden data-testid="session-status" data-phase={sessionStatus.phase} />
      </div>
    );
  }

  return (
    <ToastProvider>
      <DesktopShell api={stableApi} apiPage={stableApiPage} apiRaw={stableApiRaw} token={token} onLogout={handleLogout} />
      <output hidden data-testid="session-status" data-phase={sessionStatus.phase} />
      {sessionBanner}
    </ToastProvider>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
