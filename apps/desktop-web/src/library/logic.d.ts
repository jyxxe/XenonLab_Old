import type {Column, Job, Table, Theorem} from './types';

export function formatNumber(value: unknown): string;
export function formatBytes(value: unknown): string;
export function relativeTime(value: string | undefined, now?: number): string;
export function sortableValue(value: unknown): [number, unknown];
export const SYSTEM_FIELDS: Array<{name: string; label: string; type: string}>;
export function rowValue(row: any, key: string): unknown;
export function sortRows(rows: any[], key: string, direction?: 'asc' | 'desc'): any[];
export function filterRows(rows: any[], query: string): any[];

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
export function parseFieldFilters(text: string): {filters: Record<string, string>; errors: string[]};
export function searchWarnings(meta?: Record<string, unknown>): string[];
export function isKeywordResult(scoreType: unknown): boolean;
export const INDEX_STATES: Record<string, {label: string; tone: string}>;
export function indexStatusFromJobs(jobs?: Job[]): {state: string; detail: string; latest?: Job};
export function mergeRecent(list: any[], entry: any, limit?: number): any[];
export function recentEntryPresentation(entry: {kind: string; id: string; title?: string}, list?: Array<{kind: string; id: string; title?: string}>): {title: string; identifier: string; label: string};
export function toggleFavorite(list: any[], entry: any): any[];
export function isFavorite(list: any[], entry: any): boolean;
export function libraryPrefsStorageKey(subject: string | null | undefined, section: 'recent' | 'favorites'): string | null;
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
