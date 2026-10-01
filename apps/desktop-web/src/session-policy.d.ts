export const REFRESH_SKEW_MS: number;
export const DEFAULT_RETRY_DELAY_MS: number;
export function canReplayRequest(path: string, method: string, body?: BodyInit | null): boolean;

export type AccessToken = {token: string; expiresAt: number};
export type TokenResult = {token: string; expires: number};

export function emptyAccess(): AccessToken;
export function rememberAccess(previous: AccessToken, result: TokenResult | null | undefined, now: number): AccessToken;
export function accessExpired(access: AccessToken, now: number): boolean;
export function shouldRefresh(access: AccessToken, now: number, skew?: number): boolean;
export function classifyFailure(status: number, code?: string): 'signed-out' | 'reauthorize' | 'retry';
export function failureMessage(kind: string, retryAfterSeconds?: number, detail?: string): string;
export function retryAfterDelayMs(value?: string | null, now?: number): number;
