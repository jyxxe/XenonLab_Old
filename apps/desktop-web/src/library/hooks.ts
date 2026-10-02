import {useCallback, useEffect, useRef, useState} from 'react';
import type {API, APIPage} from '../shell/types';
import {libraryPrefsStorageKey, mergeRecent, parseFieldFilters, toggleFavorite} from './logic.js';
import {addQueryRecent, QUERY_EXPRESSION_LIMIT, queryDraftsStorageKey, readQueryDrafts, writeQueryDrafts} from './query-drafts.js';
import type {Job, QueryExplain, RecentEntry, Row, SearchHit, SearchMeta, SearchMode, Table, Theorem} from './types';

export const SEARCH_LIMIT = 25;
export const DEFAULT_QUERY = 'FROM "/**" LIMIT 100';

export function useScopes(api: API, authEpoch = 0) {
  const [identity, setIdentity] = useState<{epoch: number; scopes: string[]; subject: string | null; loaded: boolean}>({epoch: authEpoch, scopes: [], subject: null, loaded: false});
  const current = identity.epoch === authEpoch ? identity : {epoch: authEpoch, scopes: [], subject: null, loaded: false};
  useEffect(() => {
    let active = true;
    setIdentity({epoch: authEpoch, scopes: [], subject: null, loaded: false});
    api('/users/me')
      .then(me => {
        if (active) {
          setIdentity({epoch: authEpoch, scopes: me.scopes || [], subject: typeof me.subject === 'string' && me.subject.trim() ? me.subject : null, loaded: true});
        }
      })
      .catch(() => active && setIdentity({epoch: authEpoch, scopes: [], subject: null, loaded: true}));
    return () => {
      active = false;
    };
  }, [api, authEpoch]);
  return current;
}

export function useCatalogue(apiPage: APIPage) {
  const [tables, setTables] = useState<Table[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const generation = useRef(0);
  const loadingRef = useRef(false);

  const load = useCallback(async () => {
    const current = ++generation.current;
    loadingRef.current = true;
    setLoading(true);
    setLoadingMore(false);
    setError(null);
    try {
      const page = await apiPage('/tables?limit=100');
      if (current !== generation.current) return;
      setTables(page.data as Table[]);
      setCursor((page.meta.next_cursor as string) || null);
    } catch (cause) {
      if (current === generation.current) setError(cause);
    } finally {
      if (current === generation.current) {
        loadingRef.current = false;
        setLoading(false);
      }
    }
  }, [apiPage]);

  const loadMore = useCallback(async () => {
    if (!cursor || loadingRef.current) return;
    const current = generation.current;
    loadingRef.current = true;
    setLoadingMore(true);
    try {
      const page = await apiPage(`/tables?limit=100&cursor=${encodeURIComponent(cursor)}`);
      if (current !== generation.current) return;
      setTables(old => {
        const seen = new Set(old.map(table => table.id));
        return [...old, ...(page.data as Table[]).filter(table => !seen.has(table.id))];
      });
      setCursor((page.meta.next_cursor as string) || null);
    } catch (cause) {
      if (current === generation.current) setError(cause);
    } finally {
      if (current === generation.current) {
        loadingRef.current = false;
        setLoadingMore(false);
      }
    }
  }, [apiPage, cursor]);

  useEffect(() => {
    void load();
  }, [load]);

  return {tables, cursor, loading, loadingMore, error, load, loadMore};
}

export function useTableSession(api: API, apiPage: APIPage) {
  const [table, setTable] = useState<Table | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [includeDeleted, setIncludeDeleted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const view = useRef(0);
  const rowsRequest = useRef(0);
  const loadingMoreRef = useRef(false);

  const fetchRows = useCallback(
    (tableId: string, cursorValue = '', showDeleted = false) => {
      const query = new URLSearchParams({limit: '100'});
      if (cursorValue) query.set('cursor', cursorValue);
      if (showDeleted) query.set('include_deleted', 'true');
      return apiPage(`/tables/${tableId}/rows?${query.toString()}`);
    },
    [apiPage],
  );

  const open = useCallback(
    async (next: Table, showDeleted = false) => {
      const request = ++view.current;
      setTable({...next});
      setRows([]);
      setCursor(null);
      setError(null);
      setLoading(true);
      setLoadingMore(false);
      ++rowsRequest.current;
      loadingMoreRef.current = false;
      setIncludeDeleted(showDeleted);
      try {
        const [page, schema] = await Promise.all([fetchRows(next.id, '', showDeleted), api(`/tables/${next.id}/schema`)]);
        if (view.current !== request) return;
        setRows(page.data as Row[]);
        setCursor((page.meta.next_cursor as string) || null);
        setTable({...next, ...schema});
      } catch (cause) {
        if (view.current === request) setError(cause);
      } finally {
        if (view.current === request) setLoading(false);
      }
    },
    [api, fetchRows],
  );

  const refresh = useCallback(
    async (showDeleted = includeDeleted) => {
      if (!table) return;
      const tableId = table.id;
      const request = ++view.current;
      ++rowsRequest.current;
      loadingMoreRef.current = false;
      setLoading(true);
      setLoadingMore(false);
      setError(null);
      try {
        const [page, schema] = await Promise.all([fetchRows(tableId, '', showDeleted), api(`/tables/${tableId}/schema`)]);
        if (view.current !== request) return;
        setRows(page.data as Row[]);
        setCursor((page.meta.next_cursor as string) || null);
        setTable(current => (current && current.id === tableId ? {...current, ...schema} : current));
      } catch (cause) {
        if (view.current === request) setError(cause);
      } finally {
        if (view.current === request) setLoading(false);
      }
    },
    [api, fetchRows, includeDeleted, table],
  );

  const loadMore = useCallback(async () => {
    if (!table || !cursor || loadingMoreRef.current) return;
    const tableId = table.id;
    const request = ++rowsRequest.current;
    const viewRequest = view.current;
    loadingMoreRef.current = true;
    setLoadingMore(true);
    try {
      const page = await fetchRows(tableId, cursor, includeDeleted);
      if (view.current !== viewRequest || rowsRequest.current !== request) return;
      setRows(old => {
        const seen = new Set(old.map(row => row._row_id));
        return [...old, ...(page.data as Row[]).filter(row => !seen.has(row._row_id))];
      });
      setCursor((page.meta.next_cursor as string) || null);
    } catch (cause) {
      if (view.current === viewRequest && rowsRequest.current === request) setError(cause);
    } finally {
      if (view.current === viewRequest && rowsRequest.current === request) {
        loadingMoreRef.current = false;
        setLoadingMore(false);
      }
    }
  }, [cursor, fetchRows, includeDeleted, table]);

  const refreshMetadata = useCallback(async () => {
    if (!table) return;
    const tableId = table.id;
    const request = view.current;
    try {
      const schema = await api(`/tables/${tableId}/schema`);
      if (view.current !== request) return;
      setTable(current => (current && current.id === tableId ? {...current, ...schema} : current));
    } catch (cause) {
      if (view.current === request) setError(cause);
    }
  }, [api, table]);

  const clear = useCallback(() => {
    ++view.current;
    ++rowsRequest.current;
    loadingMoreRef.current = false;
    setLoadingMore(false);
    setTable(null);
    setRows([]);
    setCursor(null);
    setError(null);
  }, []);

  return {
    table,
    rows,
    setRows,
    cursor,
    includeDeleted,
    loading,
    loadingMore,
    error,
    open,
    refresh,
    loadMore,
    refreshMetadata,
    clear,
  };
}

export function useSearch(apiPage: APIPage) {
  const [mode, setMode] = useState<SearchMode>('fast');
  const [kind, setKind] = useState<'semantic' | 'keyword'>('semantic');
  const [query, setQuery] = useState('');
  const [path, setPath] = useState('/**');
  const [filterText, setFilterText] = useState('');
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [meta, setMeta] = useState<SearchMeta>({});
  const [error, setError] = useState<unknown>(null);
  const [running, setRunning] = useState(false);
  const [ran, setRan] = useState(false);
  const generation = useRef(0);
  const [resultVersion, setResultVersion] = useState(0);

  const run = useCallback(
    async (overrides: {query?: string; path?: string; filters?: Record<string, string>; kind?: 'semantic' | 'keyword'} = {}) => {
      const text = (overrides.query ?? query).trim();
      const scope = (overrides.path ?? path).trim() || '/**';
      const request = ++generation.current;
      setResultVersion(version => version + 1);
      if (!text) {
        setError(new Error('请输入搜索内容'));
        setRan(true);
        return;
      }
      setRunning(true);
      setError(null);
      setRan(true);
      try {
        const keywordOnly = mode === 'fast' && (overrides.kind ?? kind) === 'keyword';
        const endpoint = keywordOnly ? '/search/text' : '/search/semantic';
        const parsed = parseFieldFilters(filterText);
        const body: Record<string, unknown> = {
          query: text,
          path: scope,
          limit: SEARCH_LIMIT,
          filters: {...parsed.filters, ...(overrides.filters || {})},
        };
        if (endpoint === '/search/semantic') body.mode = mode === 'quality' ? 'quality' : 'fast';
        const page = await apiPage(endpoint, {method: 'POST', body: JSON.stringify(body)});
        if (request !== generation.current) return;
        setHits(page.data as SearchHit[]);
        setMeta(page.meta as SearchMeta);
      } catch (cause) {
        if (request === generation.current) {
          setHits([]);
          setMeta({});
          setError(cause);
        }
      } finally {
        if (request === generation.current) setRunning(false);
      }
    },
    [apiPage, filterText, kind, mode, path, query],
  );

  const changeMode = useCallback((next: SearchMode) => {
    setMode(next);
    if (next === 'quality') setKind('semantic');
    ++generation.current;
    setResultVersion(version => version + 1);
    setRunning(false);
    setHits([]);
    setMeta({});
    setError(null);
    setRan(false);
  }, []);

  const reset = useCallback(() => {
    ++generation.current;
    setResultVersion(version => version + 1);
    setRunning(false);
    setHits([]);
    setMeta({});
    setError(null);
    setRan(false);
  }, []);

  return {resultVersion, mode, setMode: changeMode, kind, setKind, query, setQuery, path, setPath, filterText, setFilterText, hits, meta, error, running, ran, run, reset};
}

export function useQueryConsole(apiPage: APIPage, subject: string | null, identityVerified = Boolean(subject), authEpoch = 0) {
  const [draftState, setDraftState] = useState({owner: null as string | null, expression: DEFAULT_QUERY, recent: [] as Array<{expression: string; updated_at: string}>, status: 'unverified', message: ''});
  const ownerKey = queryDraftsStorageKey(subject);
  const currentDraftState = draftState.owner === subject ? draftState : null;
  const expression = currentDraftState?.expression ?? DEFAULT_QUERY;
  const recent = currentDraftState?.recent ?? [];
  const draftStatus = currentDraftState?.status ?? (ownerKey ? 'loading' : 'unverified');
  const draftMessage = currentDraftState?.message ?? '';
  const [rows, setRows] = useState<any[]>([]);
  const [meta, setMeta] = useState<Record<string, any>>({});
  const [plan, setPlan] = useState<QueryExplain | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [running, setRunning] = useState(false);
  const [validating, setValidating] = useState(false);
  const [duration, setDuration] = useState<number | null>(null);
  const generation = useRef(0);
  const validationGeneration = useRef(0);
  const observedAuthEpoch = useRef(authEpoch);
  if (observedAuthEpoch.current !== authEpoch) {
    observedAuthEpoch.current = authEpoch;
    generation.current += 1;
    validationGeneration.current += 1;
    setRows([]);
    setMeta({});
    setPlan(null);
    setError(null);
    setRunning(false);
    setValidating(false);
    setDuration(null);
  }

  useEffect(() => {
    if (!ownerKey || !subject) {
      setDraftState({owner: null, expression: DEFAULT_QUERY, recent: [], status: 'unverified', message: ''});
      return;
    }
    let result;
    try {
      result = readQueryDrafts(window.localStorage, subject);
    } catch (cause) {
      result = {draft: null, recent: [], error: cause, ready: false};
    }
    const failed = !result.ready;
    setDraftState({
      owner: subject,
      expression: result.draft?.expression ?? DEFAULT_QUERY,
      recent: result.recent,
      status: failed ? 'read-error' : 'ready',
      message: failed ? '本地查询记录无法读取；当前编辑只保存在本页内存中。可重试读取。' : '',
    });
  }, [ownerKey, subject]);

  const persistDraftState = useCallback((next: typeof draftState) => {
    if (!subject || observedAuthEpoch.current !== authEpoch || next.owner !== subject || next.status !== 'ready') return next;
    let result;
    try {
      result = writeQueryDrafts(window.localStorage, subject, {expression: next.expression, updated_at: new Date().toISOString()}, next.recent);
    } catch (error) {
      result = {error};
    }
    return result.error
      ? {...next, status: 'write-error', message: '本地查询记录无法保存；修改仍保留在当前页面。可重试保存。'}
      : next;
  }, [authEpoch, subject]);

  const setExpression = useCallback((value: string) => {
    if (Array.from(value).length > QUERY_EXPRESSION_LIMIT) {
      setDraftState(current => current.owner === subject ? {...current, message: `表达式最多 ${QUERY_EXPRESSION_LIMIT} 个 Unicode 字符；超出部分未接受。`} : current);
      return;
    }
    ++generation.current;
    ++validationGeneration.current;
    setDraftState(current => {
      const base = current.owner === subject ? current : {owner: subject, expression, recent: [], status: ownerKey ? 'loading' : 'unverified', message: ''};
      const next = {...base, expression: value, message: base.status === 'ready' ? '' : base.message};
      return ownerKey ? persistDraftState(next) : next;
    });
    setRows([]);
    setMeta({});
    setPlan(null);
    setError(null);
    setRunning(false);
    setValidating(false);
    setDuration(null);
  }, [expression, ownerKey, persistDraftState, subject]);

  const validate = useCallback(
    async (text: string) => {
      if (!identityVerified || observedAuthEpoch.current !== authEpoch) return false;
      const request = ++validationGeneration.current;
      setValidating(true);
      setError(null);
      try {
        const page = await apiPage('/query/validate', {method: 'POST', body: JSON.stringify({expression: text})});
        if (request === validationGeneration.current) setPlan(page.data as QueryExplain);
        return true;
      } catch (cause) {
        if (request === validationGeneration.current) {
          setPlan(null);
          setError(cause);
        }
        return false;
      } finally {
        if (request === validationGeneration.current) setValidating(false);
      }
    },
    [apiPage, authEpoch, identityVerified],
  );

  const run = useCallback(
    async (text: string, remember = true) => {
      if (!identityVerified || observedAuthEpoch.current !== authEpoch) return;
      const request = ++generation.current;
      setRunning(true);
      setError(null);
      const started = performance.now();
      try {
        if (!(await validate(text)) || request !== generation.current) return;
        if (remember && ownerKey && Array.from(text).length <= QUERY_EXPRESSION_LIMIT) {
          setDraftState(current => {
            const base = current.owner === subject ? current : {owner: subject, expression: text, recent: [], status: ownerKey ? 'loading' : 'unverified', message: ''};
            const next = {...base, expression: text, recent: addQueryRecent(base.recent, text, new Date().toISOString())};
            return persistDraftState(next);
          });
        }
        const page = await apiPage('/query', {method: 'POST', body: JSON.stringify({expression: text})});
        if (request !== generation.current) return;
        setRows(Array.isArray(page.data) ? page.data : []);
        setMeta(page.meta as Record<string, any>);
      } catch (cause) {
        if (request === generation.current) {
          setRows([]);
          setMeta({});
          setError(cause);
        }
      } finally {
        if (request === generation.current) {
          setDuration(Math.round(performance.now() - started));
          setRunning(false);
        }
      }
    },
    [apiPage, authEpoch, identityVerified, ownerKey, persistDraftState, subject, validate],
  );

  const loadRecent = useCallback((item: {expression: string}) => setExpression(item.expression), [setExpression]);
  const updateRecent = useCallback((nextRecent: Array<{expression: string; updated_at: string}>) => {
    setDraftState(current => {
      if (!subject || current.owner !== subject || current.status !== 'ready') return current;
      return persistDraftState({...current, recent: nextRecent, message: ''});
    });
  }, [persistDraftState, subject]);
  const deleteRecent = useCallback((expressionToDelete: string) => updateRecent(recent.filter(item => item.expression !== expressionToDelete)), [recent, updateRecent]);
  const clearRecent = useCallback(() => updateRecent([]), [updateRecent]);
  const retryStorage = useCallback(() => {
    if (!subject || observedAuthEpoch.current !== authEpoch) return;
    let storage;
    try { storage = window.localStorage; } catch (error) {
      setDraftState(current => current.owner === subject ? {...current, status: 'read-error', message: '浏览器仍拒绝访问本地存储；可继续使用查询，但不会保存。'} : current);
      return;
    }
    if (draftStatus === 'write-error' && currentDraftState) {
      const result = writeQueryDrafts(storage, subject, {expression: currentDraftState.expression, updated_at: new Date().toISOString()}, currentDraftState.recent);
      setDraftState({...currentDraftState, status: result.error ? 'write-error' : 'ready', message: result.error ? '本地查询记录仍无法保存；当前编辑保留在本页。' : '本地查询记录已保存。'});
    } else {
      const result = readQueryDrafts(storage, subject);
      setDraftState({owner: subject, expression: result.draft?.expression ?? DEFAULT_QUERY, recent: result.recent, status: result.ready ? 'ready' : 'read-error', message: result.ready ? '本地查询记录已重新读取。' : '本地查询记录仍无法读取；当前编辑只保存在本页。'});
    }
  }, [authEpoch, currentDraftState, draftStatus, subject]);

  return {expression, setExpression, rows: identityVerified ? rows : [], meta: identityVerified ? meta : {}, plan: identityVerified ? plan : null, error: identityVerified ? error : null, running: identityVerified && running, validating: identityVerified && validating, duration: identityVerified ? duration : null, run, validate, recent, draftStatus, draftMessage, loadRecent, deleteRecent, clearRecent, retryStorage};
}

export function useTheorems(apiPage: APIPage) {
  const [items, setItems] = useState<Theorem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [searchResults, setSearchResults] = useState<Theorem[] | null>(null);
  const [selected, setSelected] = useState<Theorem | null>(null);
  const [relations, setRelations] = useState<{dependencies: any[]; dependents: any[]; evidence: any[]} | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState<unknown>(null);
  const listGeneration = useRef(0);
  const detailGeneration = useRef(0);

  const load = useCallback(async () => {
    const request = ++listGeneration.current;
    setLoading(true);
    setLoadingMore(false);
    setError(null);
    try {
      const page = await apiPage('/theorems?limit=50');
      if (request !== listGeneration.current) return;
      setSearchResults(null);
      setItems(page.data as Theorem[]);
      setCursor((page.meta.next_cursor as string) || null);
    } catch (cause) {
      if (request === listGeneration.current) setError(cause);
    } finally {
      if (request === listGeneration.current) setLoading(false);
    }
  }, [apiPage]);

  const loadMore = useCallback(async () => {
    if (!cursor) return;
    const request = ++listGeneration.current;
    setLoadingMore(true);
    try {
      const page = await apiPage(`/theorems?limit=50&cursor=${encodeURIComponent(cursor)}`);
      if (request !== listGeneration.current) return;
      setItems(old => [...old, ...(page.data as Theorem[])]);
      setCursor((page.meta.next_cursor as string) || null);
    } catch (cause) {
      if (request === listGeneration.current) setError(cause);
    } finally {
      if (request === listGeneration.current) setLoadingMore(false);
    }
  }, [apiPage, cursor]);

  const search = useCallback(
    async (query: string) => {
      const request = ++listGeneration.current;
      setLoading(true);
      setLoadingMore(false);
      setError(null);
      try {
        const page = await apiPage('/theorems/search', {method: 'POST', body: JSON.stringify({query, limit: 50})});
        if (request !== listGeneration.current) return;
        setSearchResults(page.data as Theorem[]);
      } catch (cause) {
        if (request === listGeneration.current) setError(cause);
      } finally {
        if (request === listGeneration.current) setLoading(false);
      }
    },
    [apiPage],
  );

  const open = useCallback(
    async (id: string) => {
      const request = ++detailGeneration.current;
      const listed = items.find(item => item.theorem_id === id);
      setSelected(listed || null);
      setRelations(null);
      setDetailError(null);
      setDetailLoading(true);
      try {
        const theorem = (await apiPage(`/theorems/${id}`)).data as Theorem;
        if (request !== detailGeneration.current) return;
        setSelected(theorem);
        const [dependencies, dependents, evidence] = await Promise.all([
          apiPage(`/theorems/${id}/dependencies`).then(page => page.data),
          apiPage(`/theorems/${id}/dependents`).then(page => page.data),
          apiPage(`/theorems/${id}/evidence`).then(page => page.data),
        ]);
        if (request !== detailGeneration.current) return;
        setRelations({dependencies, dependents, evidence: evidence?.evidence_refs || []});
      } catch (cause) {
        if (request === detailGeneration.current) setDetailError(cause);
      } finally {
        if (request === detailGeneration.current) setDetailLoading(false);
      }
    },
    [apiPage, items],
  );

  const clearSearch = useCallback(() => {
    ++listGeneration.current;
    setSearchResults(null);
    setError(null);
    setLoading(false);
    setLoadingMore(false);
  }, []);

  const close = useCallback(() => {
    ++detailGeneration.current;
    setSelected(null);
    setRelations(null);
    setDetailError(null);
    setDetailLoading(false);
  }, []);

  return {
    items,
    searchResults,
    selected,
    relations,
    detailLoading,
    detailError,
    cursor,
    loading,
    loadingMore,
    error,
    load,
    loadMore,
    search,
    clearSearch,
    open,
    close,
  };
}

export function useLibraryPrefs(subject: string | null | undefined) {
  const owner = subject || null;
  const recentKey = libraryPrefsStorageKey(owner, 'recent');
  const favoritesKey = libraryPrefsStorageKey(owner, 'favorites');
  const [cache, setCache] = useState(() => ({
    owner,
    recent: read(recentKey),
    favorites: read(favoritesKey),
  }));
  const cacheOwner = owner;
  if (cache.owner !== cacheOwner) {
    setCache({owner: cacheOwner, recent: read(recentKey), favorites: read(favoritesKey)});
  }
  const recent = cache.owner === cacheOwner ? cache.recent : read(recentKey);
  const favorites = cache.owner === cacheOwner ? cache.favorites : read(favoritesKey);

  const remember = useCallback((entry: RecentEntry) => {
    setCache(current => {
      const previous = current.owner === cacheOwner ? current.recent : read(recentKey);
      const next = mergeRecent(previous, entry);
      write(recentKey, next);
      return {owner: cacheOwner, recent: next, favorites: current.owner === cacheOwner ? current.favorites : read(favoritesKey)};
    });
  }, [cacheOwner, favoritesKey, recentKey]);

  const toggle = useCallback((entry: RecentEntry) => {
    setCache(current => {
      const previous = current.owner === cacheOwner ? current.favorites : read(favoritesKey);
      const next = toggleFavorite(previous, entry);
      write(favoritesKey, next);
      return {owner: cacheOwner, recent: current.owner === cacheOwner ? current.recent : read(recentKey), favorites: next};
    });
  }, [cacheOwner, favoritesKey, recentKey]);

  return {recent, favorites, remember, toggle};
}

function read(key: string | null): RecentEntry[] {
  if (!key) return [];
  try {
    const value = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function write(key: string | null, value: RecentEntry[]) {
  if (!key) return;
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* recents and favorites are cosmetic; private mode may deny storage */
  }
}

export function useJobs(apiPage: APIPage, tableId: string, enabled: boolean) {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [jobsTableId, setJobsTableId] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [errorTableId, setErrorTableId] = useState('');
  const generation = useRef(0);
  const load = useCallback(async () => {
    if (!enabled || !tableId) {
      generation.current += 1;
      setJobs(null);
      setJobsTableId(tableId);
      setError(null);
      setErrorTableId(tableId);
      return;
    }
    const request = ++generation.current;
    setError(null);
    try {
      const page = await apiPage(`/jobs?table_id=${encodeURIComponent(tableId)}&limit=20`);
      if (request === generation.current) {
        setJobs(page.data as Job[]);
        setJobsTableId(tableId);
      }
    } catch (cause) {
      if (request === generation.current) {
        setJobs(null);
        setJobsTableId(tableId);
        setError(cause);
        setErrorTableId(tableId);
      }
    }
  }, [apiPage, enabled, tableId]);
  useEffect(() => {
    setJobs(null);
    setJobsTableId('');
    setError(null);
    setErrorTableId('');
    void load();
    return () => {
      generation.current += 1;
    };
  }, [load]);
  return {
    jobs: enabled && jobsTableId === tableId ? jobs : null,
    error: enabled && errorTableId === tableId ? error : null,
    reload: load,
  };
}
