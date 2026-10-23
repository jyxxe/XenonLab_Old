export const QUERY_EXPRESSION_LIMIT: number;
export const QUERY_RECENT_LIMIT: number;
export function queryDraftsStorageKey(subject: string | null | undefined): string | null;
export function queryExpressionLength(value: unknown): number;
export function readQueryDrafts(storage: Storage, subject: string | null | undefined): {
  draft: {expression: string; updated_at: string} | null;
  recent: Array<{expression: string; updated_at: string}>;
  error: unknown;
  ready: boolean;
};
export function writeQueryDrafts(storage: Storage, subject: string | null | undefined, draft: {expression: string; updated_at: string}, recent: Array<{expression: string; updated_at: string}>): {error: unknown};
export function addQueryRecent(recent: Array<{expression: string; updated_at: string}>, expression: string, updatedAt: string): Array<{expression: string; updated_at: string}>;
