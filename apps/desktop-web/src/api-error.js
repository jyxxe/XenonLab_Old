export class APIRequestError extends Error {
  constructor(message, status, code, metadata = {}) {
    super(message);
    this.name = 'APIRequestError';
    this.status = status;
    this.code = code;
    this.requestId = nonemptyString(metadata.requestId);
  }
}

function nonemptyString(value) {
  return typeof value === 'string' && value.trim() ? value.trim() : undefined;
}

/** Retain server correlation IDs for both JSON APIs and raw attachment requests. */
export function apiErrorFromResponse(response, payload) {
  const error = payload?.error;
  return new APIRequestError(
    nonemptyString(error?.message) || nonemptyString(payload?.detail?.[0]?.msg) || `HTTP ${response.status}`,
    response.status,
    nonemptyString(error?.code),
    {requestId: nonemptyString(error?.request_id) || nonemptyString(response.headers.get('X-Request-ID'))},
  );
}

/**
 * Human-readable status text for UI messages.
 *
 * `String(error)` renders `APIRequestError: <message>`, leaking an internal class
 * name into user-facing status lines. Prefer the server-provided message, then a
 * generic fallback that never exposes the class name.
 */
export function readableErrorMessage(error) {
  const message = typeof error?.message === 'string' ? error.message.trim() : '';
  if (message) return message;
  const text = typeof error === 'string' ? error.trim() : '';
  return text || '请稍后重试';
}

export function isUnregisteredAccountError(error) {
  return error instanceof APIRequestError && error.status === 404 && error.code === 'ACCOUNT_NOT_FOUND';
}
