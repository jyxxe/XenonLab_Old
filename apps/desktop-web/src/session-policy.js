/**
 * Session/rate-limit policy for the desktop shell.
 *
 * The platform returns `expires` as a relative lifetime in seconds, access tokens
 * live only a few minutes, and every API request used to refresh the token first.
 * Keeping the access token client-side (until shortly before it expires) plus a
 * single-flight refresh removes that request amplification, and the failure
 * classification keeps transient 429/5xx responses from ending the session.
 */

/** Refresh slightly before expiry so an in-flight request never uses a stale token. */
export const REFRESH_SKEW_MS = 60000;
export const DEFAULT_RETRY_DELAY_MS = 12000;

/**
 * Only replay requests whose body can be sent again without consuming a stream
 * or duplicating a write. Current safe POST callers pass JSON strings.
 */
export function canReplayRequest(path, method, body) {
  if (method === 'GET') return true;
  if (method !== 'POST' || (body != null && typeof body !== 'string')) return false;
  const route = path.split('?')[0];
  return ['/query', '/query/validate', '/query/explain', '/search/text', '/search/semantic', '/search/hybrid'].includes(route)
    || /^\/tables\/[^/]+\/query$/.test(route);
}

/** Parse Retry-After as either delta seconds or an HTTP date. */
export function retryAfterDelayMs(value, now = Date.now()) {
  if (!value) return 0;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return Math.ceil(seconds * 1000);
  const at = Date.parse(value);
  return Number.isFinite(at) ? Math.max(0, at - now) : 0;
}

export function emptyAccess() {
  return {token: '', expiresAt: 0};
}

/**
 * Store a session-token response. `expires` is a relative lifetime (seconds);
 * opaque or non-numeric values keep the token usable until a 401 rejects it.
 */
export function rememberAccess(previous, result, now) {
  if (!result || typeof result.token !== 'string' || !result.token) return previous;
  const lifetime = Number(result.expires);
  const expiresAt = Number.isFinite(lifetime) && lifetime > 0 ? now + lifetime * 1000 : 0;
  return {token: result.token, expiresAt};
}

export function accessExpired(access, now) {
  return Boolean(access && access.token) && access.expiresAt > 0 && access.expiresAt <= now;
}

/** True when the cached token is missing or close enough to expiry to refresh. */
export function shouldRefresh(access, now, skew = REFRESH_SKEW_MS) {
  if (!access || typeof access.token !== 'string' || !access.token) return true;
  if (!(access.expiresAt > 0)) return false;
  return access.expiresAt - now <= skew;
}

/**
 * Classify a failed request or token refresh.
 * - `signed-out`: no session exists any more.
 * - `reauthorize`: the identity or desktop grant was revoked; user action required.
 * - `retry`: transient (rate limit, upstream 5xx, network); keep the workspace.
 */
export function classifyFailure(status, code) {
  if (status === 401) return code === 'NO_SESSION' ? 'signed-out' : 'reauthorize';
  if (status === 403) return code === 'DESKTOP_ACCESS_REVOKED' ? 'reauthorize' : 'retry';
  return 'retry';
}

/** Human message that tells the user what happened and whether it is automatic. */
export function failureMessage(kind, retryAfterSeconds, detail) {
  if (kind === 'reauthorize') return detail || '登录凭据已失效，需要重新授权';
  if (kind === 'signed-out') return '尚未登录，请重新登录';
  const wait = Number.isFinite(retryAfterSeconds) && retryAfterSeconds > 0 ? `（约 ${Math.ceil(retryAfterSeconds)} 秒后自动重试）` : '';
  return `${detail || '服务暂时不可用'}${wait}；工作区将保留，可稍后重试`;
}
