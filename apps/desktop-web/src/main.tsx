import {Icon} from './design-system/Icon';
import React, {useCallback, useEffect, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import './design-system/tokens.css';
import './design-system/primitives.css';
import './shell/shell.css';
import './style.css';
import {login, callback, sessionToken, logout, SessionFailure} from './auth';
import {initialSessionState, transitionSession, mayUseCachedToken} from './session-state.js';
import {emptyAccess, accessExpired, canReplayRequest, classifyFailure, DEFAULT_RETRY_DELAY_MS, failureMessage, rememberAccess, retryAfterDelayMs, shouldRefresh} from './session-policy.js';
import {ToastProvider} from './design-system/primitives';
import {DesktopShell} from './shell/DesktopShell';
import {Knowledge} from './Knowledge';
import {LibraryJSONError, parseAPIJSON} from './library/json.js';
import {APIRequestError, apiErrorFromResponse} from './api-error.js';

type APIPage<T = any> = {data: T; meta: {request_id?: string; next_cursor?: string | null; [key: string]: any}};
type APIOptions = {soft?: boolean};
type PendingRequest = {generation:number; replay:()=>Promise<unknown>; resolve:(value:unknown)=>void; reject:(reason:unknown)=>void};
const libraryView = location.hostname === 'library.xenon.lan';
class LibraryResponseError extends APIRequestError {}

function isRetryable(error:unknown){
  if(error instanceof LibraryResponseError)return false;
  if(error instanceof SessionFailure)return error.kind==='temporary';
  if(error instanceof APIRequestError)return error.status===0||error.status===429||error.status>=500;
  return false;
}

function App() {
  const [token, setToken] = useState('');
  const [persistent, setPersistent] = useState(false);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState('');
  const [sessionStatus, setSessionStatus] = useState(initialSessionState);
  const [authEpoch, setAuthEpoch] = useState(0);
  const sessionStatusRef = useRef(initialSessionState);
  const accessRef = useRef(emptyAccess());
  const refreshRef = useRef<Promise<{token: string}> | null>(null);
  const sessionGenerationRef = useRef(0);
  const pendingRequestsRef = useRef<PendingRequest[]>([]);
  const retryRunningRef = useRef(false);
  const retryNotBeforeRef = useRef(0);
  const retryPendingRef = useRef<()=>void>(()=>{});
  const [retryNotBefore, setRetryNotBefore] = useState(0);
  const [retrySignal, setRetrySignal] = useState(0);
  // Stable identities for the API functions: a token refresh must not recreate them,
  // otherwise every refresh would re-run the desktop bootstrap and reload windows.
  const persistentRef = useRef(false);
  const tokenRef = useRef('');
  useEffect(() => {
    persistentRef.current = persistent;
  }, [persistent]);
  useEffect(() => {
    tokenRef.current = token;
  }, [token]);

  const transition = (event: {type: string; message?: string}) => {
    const next=transitionSession(sessionStatusRef.current,event);
    sessionStatusRef.current=next;
    setSessionStatus(next);
  };

  useEffect(() => {
    if (!libraryView) return;
    const handler = (event: MessageEvent) => {
      const trusted = ['https://xenon.lan', 'https://192.168.0.101:9443', 'https://xenon.jyxxe.com'];
      if (event.source !== parent || !trusted.includes(event.origin) || event.data?.type !== 'auth.session_changed' || typeof event.data.access_token !== 'string') return;
      sessionGenerationRef.current+=1;
      refreshRef.current=null;
      retryNotBeforeRef.current=0;
      setRetryNotBefore(0);
      for(const pending of pendingRequestsRef.current)pending.reject(new APIRequestError('会话已更改',0,'SESSION_CHANGED'));
      pendingRequestsRef.current=[];
      persistentRef.current=false;
      accessRef.current=emptyAccess();
      tokenRef.current=event.data.access_token;
      setPersistent(false);
      setToken(event.data.access_token);
      setAuthEpoch(value=>value+1);
    };
    addEventListener('message', handler);
    return () => removeEventListener('message', handler);
  }, []);

  useEffect(() => {
    let active = true;
    const generation=sessionGenerationRef.current;
    (async () => {
      try {
        const loginResult = await callback();
        const result = loginResult || (await sessionToken());
        if (!active||generation!==sessionGenerationRef.current) return;
        if (result) {
          const next = rememberAccess(emptyAccess(), result, Date.now());
          accessRef.current = next;
          persistentRef.current = true;
          tokenRef.current = next.token;
          setPersistent(true);
          setToken(next.token);
          setAuthEpoch(value=>value+1);
          transition({type: 'restored'});
        } else transition({type: 'no-session'});
      } catch (e) {
        if (!active||generation!==sessionGenerationRef.current) return;
        const failure = e instanceof SessionFailure ? e : new SessionFailure('temporary', String(e));
        transition({type: failure.kind === 'reauthorize' ? 'reauthorization-required' : 'temporary-failure', message: failure.message});
        setError(failure.message);
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if(sessionStatus.phase!=='retrying')return;
    const remaining=retryNotBefore-Date.now();
    const delay=remaining>0?Math.min(remaining,2_147_000_000):DEFAULT_RETRY_DELAY_MS;
    const timer=setTimeout(()=>{
      if(retryNotBeforeRef.current>Date.now())setRetrySignal(value=>value+1);
      else retryPendingRef.current();
    },delay);
    const visible=()=>{if(document.visibilityState==='visible')retryPendingRef.current()};
    document.addEventListener('visibilitychange',visible);
    return()=>{clearTimeout(timer);document.removeEventListener('visibilitychange',visible)};
  },[sessionStatus.phase,retryNotBefore,retrySignal]);

  useEffect(() => {
    if (token && !connected) setConnected(true);
  }, [token]);

  /**
   * Single-flight access-token refresh. Parallel API calls share one refresh so a
   * burst of requests cannot amplify the identity service load or the rate limiter.
   */
  const applyRetryState = useCallback((generation:number,detail:string,retryAfterMs=0) => {
    if(generation!==sessionGenerationRef.current)return;
    const now=Date.now();
    const notBefore=Math.max(retryNotBeforeRef.current,retryAfterMs>0?now+retryAfterMs:0);
    retryNotBeforeRef.current=notBefore;
    setRetryNotBefore(notBefore);
    setRetrySignal(value=>value+1);
    const waitSeconds=Math.max(0,(notBefore-now)/1000);
    transition({type:'temporary-failure',message:failureMessage('retry',waitSeconds,detail)});
  },[]);

  const refreshAccess = useCallback(async (): Promise<{token: string}> => {
    const generation=sessionGenerationRef.current;
    if (refreshRef.current) return refreshRef.current;
    const pending = (async () => {
      let result: {token: string; expires: number} | null;
      try {
        result = await sessionToken();
      } catch (e) {
        const failure = e instanceof SessionFailure ? e : new SessionFailure('temporary', String(e));
        if(generation===sessionGenerationRef.current){
          if(failure.kind==='reauthorize')transition({type:'reauthorization-required',message:failure.message});
          else applyRetryState(generation,failure.message,failure.retryAfterMs);
        }
        throw failure;
      }
      if(generation!==sessionGenerationRef.current)throw new SessionFailure('temporary','会话已结束');
      if (!result) {
        const failure = new SessionFailure('reauthorize', '登录凭据已失效，需要重新授权');
        transition({type: 'reauthorization-required', message: failure.message});
        throw failure;
      }
      const next = rememberAccess(accessRef.current, result, Date.now());
      accessRef.current = next;
      persistentRef.current = true;
      tokenRef.current = next.token;
      setPersistent(true);
      setToken(next.token);
      return {token: next.token};
    })();
    let wrapped:Promise<{token:string}>;
    wrapped = pending.finally(() => {if(refreshRef.current===wrapped)refreshRef.current=null});
    refreshRef.current = wrapped;
    return wrapped;
  }, [applyRetryState]);

  const accessToken = useCallback(async (): Promise<{token: string} | null> => {
    const generation=sessionGenerationRef.current;
    if (!persistentRef.current) return tokenRef.current ? {token: tokenRef.current} : null;
    const access = accessRef.current;
    if (!shouldRefresh(access, Date.now())) return {token: access.token};
    try {
      const result=await refreshAccess();
      if(generation!==sessionGenerationRef.current)throw new SessionFailure('temporary','会话已结束');
      return result;
    } catch (e) {
      const failure = e instanceof SessionFailure ? e : new SessionFailure('temporary', String(e));
      // A transient refresh failure keeps a still-valid cached token; an expired
      // token must fail as temporary instead of producing a spurious 401 that
      // would force the user back to the login page.
      if (generation===sessionGenerationRef.current&&failure.kind === 'temporary' && mayUseCachedToken(failure.kind, Boolean(access.token)) && !accessExpired(access, Date.now())) {
        return {token: access.token};
      }
      throw failure;
    }
  }, [refreshAccess]);

  /**
   * Only an explicit 401 moves the user to reauthorization. Rate limits and
   * upstream failures keep the workspace and surface a retryable banner.
   */
  const applyFailureState = useCallback((status:number, headers:Headers|undefined, error: {code?: string; message?: string} | undefined,generation:number) => {
    if(generation!==sessionGenerationRef.current)return;
    const outcome = classifyFailure(status, error?.code);
    if (outcome === 'reauthorize') {
      transition({type: 'reauthorization-required', message: '工作空间需要重新授权'});
      return;
    }
    if (status===0||status===429||status>=500) {
      const retryAfter=retryAfterDelayMs(headers?.get('Retry-After'));
      applyRetryState(generation,error?.message|| (status===0?'网络连接暂时中断':`HTTP ${status}`),retryAfter);
    }
  }, [applyRetryState]);

  const apiPage = useCallback(async <T = any>(path: string, init: RequestInit = {}, options: APIOptions = {}): Promise<APIPage<T>> => {
    const generation=sessionGenerationRef.current;
    const method=(init.method||'GET').toUpperCase();
    const perform=async():Promise<APIPage<T>>=>{
      const current = await accessToken();
      if (!current) throw Error('需要登录后访问工作空间');
      if(generation!==sessionGenerationRef.current)throw new APIRequestError('会话已结束',0,'SESSION_ENDED');
      const headers = new Headers(init.headers);
      if (!headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
      headers.set('Authorization', `Bearer ${current.token}`);
      let response:Response;
      try{response=await fetch(`/api/v1${path}`, {...init, headers})}
      catch(cause){
        const failure=new APIRequestError('网络连接暂时中断，请稍后重试',0,'NETWORK_ERROR');
        if(!options.soft)applyFailureState(0,undefined,{message:failure.message},generation);
        throw failure;
      }
      let raw:string;
      try{raw=await response.text()}
      catch(cause){
        const failure=new APIRequestError('网络连接暂时中断，请稍后重试',0,'NETWORK_ERROR');
        applyFailureState(0,undefined,{message:failure.message},generation);
        throw failure;
      }
      let result:any=null;
      let decodingError:unknown;
      try{result=raw?parseAPIJSON(path,raw):null}catch(cause){decodingError=cause}
      if(generation!==sessionGenerationRef.current)throw new APIRequestError('会话已结束',0,'SESSION_ENDED');
      if (!response.ok) {
        if(!options.soft)applyFailureState(response.status,response.headers,result?.error,generation);
        throw apiErrorFromResponse(response, result);
      }
      if(decodingError instanceof LibraryJSONError)throw new LibraryResponseError(decodingError.message,502,'INVALID_RESPONSE',{requestId:response.headers.get('X-Request-ID')||undefined});
      if(!result||typeof result!=='object'){
        const failure=new APIRequestError('服务返回了无法读取的数据',502,'INVALID_RESPONSE',{requestId:response.headers.get('X-Request-ID')||undefined});
        applyFailureState(502,response.headers,{message:failure.message,code:failure.code},generation);
        throw failure;
      }
      if(sessionStatusRef.current.phase==='retrying'&&pendingRequestsRef.current.length===0)transition({type:'restored'});
      return {data: result.data, meta: result.meta || {}};
    };
    const defer=()=>new Promise<APIPage<T>>((resolve,reject)=>pendingRequestsRef.current.push({generation,replay:perform as ()=>Promise<unknown>,resolve:resolve as (value:unknown)=>void,reject}));
    if(generation===sessionGenerationRef.current&&sessionStatusRef.current.phase==='retrying'&&Date.now()<retryNotBeforeRef.current){
      if(canReplayRequest(path,method,init.body))return await defer();
      throw new APIRequestError('服务要求稍后重试',429,'RETRY_AFTER');
    }
    try{return await perform()}
    catch(cause){
      if(!options.soft&&generation===sessionGenerationRef.current&&canReplayRequest(path,method,init.body)&&isRetryable(cause))return await defer();
      throw cause;
    }
  }, [accessToken, applyFailureState]);

  const api = useCallback(async <T = any>(path: string, init: RequestInit = {}, options: APIOptions = {}): Promise<T> => {
    return (await apiPage<T>(path, init, options)).data;
  }, [apiPage]);

  const apiRaw = useCallback(async (path: string, init: RequestInit = {}): Promise<Response> => {
    const generation=sessionGenerationRef.current;
    const method=(init.method||'GET').toUpperCase();
    const perform=async():Promise<Response>=>{
      if(generation!==sessionGenerationRef.current)throw new APIRequestError('会话已结束',0,'SESSION_ENDED');
      const current = await accessToken();
      if (!current) throw Error('需要登录后访问工作空间');
      if(generation!==sessionGenerationRef.current)throw new APIRequestError('会话已结束',0,'SESSION_ENDED');
      const headers = new Headers(init.headers);
      if (typeof FormData !== 'undefined' && init.body instanceof FormData) headers.delete('Content-Type');
      else if (init.body && !headers.has('Content-Type')) headers.set('Content-Type', 'application/json');
      headers.set('Authorization', `Bearer ${current.token}`);
      let response:Response;
      try{response=await fetch(`/api/v1${path}`, {...init, headers})}
      catch(cause){
        if(generation!==sessionGenerationRef.current)throw new APIRequestError('会话已结束',0,'SESSION_ENDED');
        const failure=new APIRequestError('网络连接暂时中断，请稍后重试',0,'NETWORK_ERROR');
        applyFailureState(0,undefined,{message:failure.message},generation);
        throw failure;
      }
      if(generation!==sessionGenerationRef.current)throw new APIRequestError('会话已结束',0,'SESSION_ENDED');
      if (!response.ok) {
        const raw=await response.clone().text().catch(()=> '');
        if(generation!==sessionGenerationRef.current)throw new APIRequestError('会话已结束',0,'SESSION_ENDED');
        let result:any=null;try{result=raw?JSON.parse(raw):null}catch{}
        applyFailureState(response.status,response.headers,result?.error,generation);
        throw apiErrorFromResponse(response, result);
      }
      if(generation!==sessionGenerationRef.current)throw new APIRequestError('会话已结束',0,'SESSION_ENDED');
      if(sessionStatusRef.current.phase==='retrying'&&pendingRequestsRef.current.length===0)transition({type:'restored'});
      return response;
    };
    const defer=()=>new Promise<Response>((resolve,reject)=>pendingRequestsRef.current.push({generation,replay:perform as ()=>Promise<unknown>,resolve:resolve as (value:unknown)=>void,reject}));
    if(generation===sessionGenerationRef.current&&sessionStatusRef.current.phase==='retrying'&&Date.now()<retryNotBeforeRef.current){
      if(canReplayRequest(path,method,init.body))return await defer();
      throw new APIRequestError('服务要求稍后重试',429,'RETRY_AFTER');
    }
    try{return await perform()}
    catch(cause){
      if(generation===sessionGenerationRef.current&&canReplayRequest(path,method,init.body)&&isRetryable(cause))return await defer();
      throw cause;
    }
  }, [accessToken, applyFailureState]);

  const retryPendingRequests=useCallback(async()=>{
    const now=Date.now();
    if(retryRunningRef.current||now<retryNotBeforeRef.current)return;
    retryRunningRef.current=true;
    if(retryNotBeforeRef.current){retryNotBeforeRef.current=0;setRetryNotBefore(0)}
    const generation=sessionGenerationRef.current;
    try{
      let current:{token:string}|null;
      try{current=await accessToken()}
      catch(cause){
        if(isRetryable(cause))return;
        const queued=pendingRequestsRef.current;
        pendingRequestsRef.current=[];
        for(const pending of queued)pending.reject(cause);
        return;
      }
      if(!current){
        const failure=new APIRequestError('登录凭据已失效，需要重新授权',401,'NO_SESSION');
        transition({type:'reauthorization-required',message:failure.message});
        const queued=pendingRequestsRef.current;
        pendingRequestsRef.current=[];
        for(const pending of queued)pending.reject(failure);
        return;
      }
      if(generation!==sessionGenerationRef.current)return;
      for(const pending of [...pendingRequestsRef.current]){
        if(pending.generation!==generation){
          pendingRequestsRef.current=pendingRequestsRef.current.filter(item=>item!==pending);
          pending.reject(new APIRequestError('会话已结束',0,'SESSION_ENDED'));
          continue;
        }
        try{
          const result=await pending.replay();
          if(generation!==sessionGenerationRef.current)return;
          pendingRequestsRef.current=pendingRequestsRef.current.filter(item=>item!==pending);
          pending.resolve(result);
        }catch(cause){
          if(!isRetryable(cause)){
            pendingRequestsRef.current=pendingRequestsRef.current.filter(item=>item!==pending);
            pending.reject(cause);
          }
          const authorizationFailed=(cause instanceof APIRequestError&&cause.status===401)||sessionStatusRef.current.phase==='reauthorize';
          if(authorizationFailed){
            const remaining=pendingRequestsRef.current;
            pendingRequestsRef.current=[];
            for(const queued of remaining)queued.reject(cause);
            break;
          }
          if(retryNotBeforeRef.current>Date.now()||isRetryable(cause))break;
        }
      }
      if(generation===sessionGenerationRef.current&&pendingRequestsRef.current.length===0){
        retryNotBeforeRef.current=0;setRetryNotBefore(0);
        if(sessionStatusRef.current.phase==='retrying')transition({type:'restored'});
      }
    }finally{retryRunningRef.current=false}
  },[accessToken]);
  retryPendingRef.current=()=>{void retryPendingRequests()};

  async function handleLogout() {
    const wasPersistent=persistentRef.current;
    sessionGenerationRef.current+=1;
    persistentRef.current=false;
    tokenRef.current='';
    accessRef.current=emptyAccess();
    refreshRef.current=null;
    retryNotBeforeRef.current=0;
    setRetryNotBefore(0);
    for(const pending of pendingRequestsRef.current)pending.reject(new APIRequestError('会话已结束',0,'SESSION_ENDED'));
    pendingRequestsRef.current=[];
    setPersistent(false);
    setToken('');
    setConnected(false);
    setAuthEpoch(value=>value+1);
    transition({type: 'user-logout'});
    try {if(wasPersistent)await logout()}
    catch {/* the local session is cleared even when the provider is unreachable */}
  }

  // The standalone library page has no shell around it, so no chat app can be
  // registered: Ask AI still composes/copy-exports drafts but cannot deliver.
  if (libraryView) return (
    <Knowledge
      api={api}
      apiPage={apiPage}
      apiRaw={apiRaw}
      authEpoch={authEpoch}
      apps={[]}
      openApp={() => undefined}
      notify={() => undefined}
    />
  );

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
            disabled={retryNotBefore>Date.now()}
            onClick={() => retryPendingRef.current()}
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
          <span className="menubar-brand"><b><Icon value="sparkle" size={18} /></b> XeonoLab</span>
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
                disabled={retryNotBefore>Date.now()}
                onClick={() => retryPendingRef.current()}
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
      <DesktopShell api={api} apiPage={apiPage} apiRaw={apiRaw} onLogout={handleLogout} />
      <output hidden data-testid="session-status" data-phase={sessionStatus.phase} />
      {sessionBanner}
    </ToastProvider>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
