import type {Column, IndexStatus, IndexStatusLastJob, Job, Row, Table, Theorem} from './types';

export function formatNumber(value: unknown): string;
export function formatBytes(value: unknown): string;
export function relativeTime(value: string | undefined, now?: number): string;
export function sortableValue(value: unknown): [number, unknown];
export const SYSTEM_FIELDS: Array<{name: string; label: string; type: string}>;
export function rowValue(row: any, key: string): unknown;
export function sortRows(rows: any[], key: string, direction?: 'asc' | 'desc'): any[];
export function filterRows(rows: any[], query: string): any[];
export function rowSelectionRange(visibleIds: string[], anchorId: string | null, targetId: string): string[];

export type TreeNode = {path: string; name: string; tables: Table[]; children: TreeNode[]; count: number};
export function buildPathTree(tables: Table[]): TreeNode;
export function filterTree(node: TreeNode, needle: string): TreeNode | null;
export function filteredTreeExpansion(root: TreeNode | null, collapsed?: Set<string>): Set<string>;
export function flattenTree(
  root: TreeNode,
  expanded: Set<string>,
): Array<{kind: 'node' | 'table'; path: string; name: string; depth: number; count?: number; table?: Table}>;
export function ancestorPaths(path: string): string[];

export function virtualWindow(
  scrollTop: number,
  viewportHeight: number,
  total: number,
  rowHeight?: number,
  overscan?: number,
): {start: number; end: number; padTop: number; padBottom: number; totalHeight: number};

export type SchemaChange = {
  added: Column[];
  removed: Column[];
  typeChanged: Column[];
  nullableRestricted: Column[];
  requiredAdded: Column[];
  risk: 'safe' | 'destructive';
  summary: string[];
};
export function classifySchemaChange(current?: Column[], draft?: Column[]): SchemaChange;
export function parseFieldFilters(text: string): {filters: Record<string, unknown>; errors: string[]};
export const SEARCH_LIMIT_OPTIONS: number[];
export function searchResultSummary(limit: unknown, hitCount: unknown): string | null;
export function searchWarnings(meta?: Record<string, unknown>): string[];
export function isKeywordResult(scoreType: unknown): boolean;
export const INDEX_STATES: Record<string, {label: string; tone: string}>;
export function indexStatusFromJobs(jobs?: Job[]): {state: string; detail: string; latest?: Job};
export function indexStatusFromServer(status: IndexStatus | null | undefined): {
  state: string;
  tone: 'neutral' | 'accent' | 'success' | 'warning' | 'danger';
  label: string;
  detail: string;
  rows: number;
  embedded: number;
  pending: number;
  last_job: IndexStatusLastJob | null;
};
export const RECENT_VISIBLE_LIMIT: number;
export function recentListDisplay(recent: unknown, showAll?: boolean): {visible: any[]; expanded: boolean; toggle: boolean; hidden: number; label: string};
export function mergeRecent(list: any[], entry: any, limit?: number): any[];
export function libraryPrefsKey(entry: any): string;
export function mergeServerPrefs(local: any[] | null | undefined, server: any[] | null | undefined, kind: 'recent' | 'favorites', tombstones?: any[]): any[];
export function mergeTombstones(local: any[] | null | undefined, server: any[] | null | undefined): any[];
export function recentEntryPresentation(entry: {kind: string; id: string; title?: string}, list?: Array<{kind: string; id: string; title?: string}>): {title: string; identifier: string; label: string};
export function toggleFavorite(list: any[], entry: any, tombstones?: any[], at?: number): {favorites: any[]; tombstones: any[]};
export function isFavorite(list: any[], entry: any): boolean;
export function libraryPrefsStorageKey(subject: string | null | undefined, section: 'recent' | 'favorites' | 'tombstones'): string | null;
export function changedFields(before?: Record<string, unknown>, after?: Record<string, unknown>): string[];
export function rowHasAttachment(row: any, columns?: Column[]): boolean;
export function isIndexActiveJob(job: Job | undefined): boolean;
export function withOffset(expression: string, offset: number): string;
export function withQueryPagination(expression: string, limit: number, offset: number): string;
export type ErrorInfo = {
  title: string;
  message: string;
  retryable: boolean;
  conflict?: boolean;
  code?: string;
  requestId?: string;
  status?: number | null;
  raw?: string;
};
export function describeError(error: unknown, context?: string): ErrorInfo;

export type {Theorem};
export type SelectionAIActionId = 'ask' | 'summarize' | 'related';
export const SELECTION_AI_ACTIONS: Array<{id: SelectionAIActionId; label: string; hint: string}>;
export type SelectionContextSummary = {
  tablePath: string;
  tableId: string;
  rowCount: number;
  sharedRows: number;
  truncated: boolean;
  fields: string[];
  rowIds: string[];
};
export function selectionContextSummary(table: Table | null, rows: any[], columns?: Column[]): SelectionContextSummary;
export function buildSelectionPrompt(
  action: SelectionAIActionId,
  table: Table | null,
  rows: any[],
  columns?: Column[],
  question?: string,
): string;

export function loadedDeletedRow(rows: Row[] | undefined, rowId: string): Row | null;
export function retainRefreshedRow(rows: Row[], retainedRow?: Row): Row[];
export function orderedColumns(columns: Column[], preference: unknown): Column[];
export function moveColumn(columns: Column[], preference: unknown, name: string, direction: number): string[];
export function clampColumnWidth(width: number): number;
export function keyboardColumnWidth(width: number, key: string, shift?: boolean): number | null;
