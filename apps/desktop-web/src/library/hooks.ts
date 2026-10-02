import {useCallback, useEffect, useRef, useState} from 'react';
import type {API, APIPage, APIRaw} from '../shell/types';
import {libraryPrefsStorageKey, mergeRecent, mergeServerPrefs, mergeTombstones, parseFieldFilters, toggleFavorite} from './logic.js';
import type {Job, QueryExplain, RecentEntry, Row, SearchHit, SearchMeta, SearchMode, Table, Theorem} from './types';

export const SEARCH_LIMIT = 25;
export const DEFAULT_QUERY = 'FROM "/**" LIMIT 100';

export function useScopes(api: API) {
  const [scopes, setScopes] = useState<string[]>([]);
  const [subject, setSubject] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let active = true;
    api('/users/me')
      .then(me => {
        if (active) {
          setScopes(me.scopes || []);
          setSubject(typeof me.subject === 'string' ? me.subject : null);
          setLoaded(true);
        }
      })
      .catch(() => active && setLoaded(true));
    return () => {
      active = false;
    };
  }, [api]);
  return {scopes, subject, loaded};
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

  const run = useCallback(
    async (overrides: {query?: string; path?: string; filters?: Record<string, string>; kind?: 'semantic' | 'keyword'} = {}) => {
      const text = (overrides.query ?? query).trim();
      const scope = (overrides.path ?? path).trim() || '/**';
      const request = ++generation.current;
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
    setRunning(false);
    setHits([]);
    setMeta({});
    setError(null);
    setRan(false);
  }, []);

  const reset = useCallback(() => {
    ++generation.current;
    setRunning(false);
    setHits([]);
    setMeta({});
    setError(null);
    setRan(false);
  }, []);

  return {mode, setMode: changeMode, kind, setKind, query, setQuery, path, setPath, filterText, setFilterText, hits, meta, error, running, ran, run, reset};
}

export function useQueryConsole(apiPage: APIPage) {
  const [expression, setExpressionState] = useState(DEFAULT_QUERY);
  const [rows, setRows] = useState<any[]>([]);
  const [meta, setMeta] = useState<Record<string, any>>({});
  const [plan, setPlan] = useState<QueryExplain | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [running, setRunning] = useState(false);
  const [validating, setValidating] = useState(false);
  const [duration, setDuration] = useState<number | null>(null);
  const generation = useRef(0);
  const validationGeneration = useRef(0);

  const setExpression = useCallback((value: string) => {
    ++generation.current;
    ++validationGeneration.current;
    setExpressionState(value);
    setRows([]);
    setMeta({});
    setPlan(null);
    setError(null);
    setRunning(false);
    setValidating(false);
    setDuration(null);
  }, []);

  const validate = useCallback(
    async (text: string) => {
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
    [apiPage],
  );

  const run = useCallback(
    async (text: string) => {
      const request = ++generation.current;
      setRunning(true);
      setError(null);
      const started = performance.now();
      try {
        if (!(await validate(text)) || request !== generation.current) return;
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
    [apiPage, validate],
  );

  return {expression, setExpression, rows, meta, plan, error, running, validating, duration, run, validate};
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

export function useLibraryPrefs(subject: string | null | undefined, api?: API, apiRaw?: APIRaw) {
  const owner = subject || null;
  const recentKey = libraryPrefsStorageKey(owner, 'recent');
  const favoritesKey = libraryPrefsStorageKey(owner, 'favorites');
  const tombstonesKey = libraryPrefsStorageKey(owner, 'tombstones');
  const [cache, setCache] = useState(() => ({
    owner: owner || 'anonymous',
    recent: read(recentKey),
    favorites: read(favoritesKey),
    tombstones: readTombstones(tombstonesKey),
  }));
  const cacheOwner = owner || 'anonymous';
  // ETag of the last server read; guards PATCHes against lost updates.
  const revisionRef = useRef<string | null>(null);

  const refreshFromServer = useCallback(async () => {
    if (!apiRaw || !owner) return null;
    const response = await apiRaw('/users/me/preferences');
    revisionRef.current = response.headers.get('ETag');
    const body = await response.json();
    return (body?.data ?? null) as {
      library_recents?: RecentEntry[];
      library_favorites?: RecentEntry[];
      library_favorite_tombstones?: Array<{kind: string; id: string; at: number; deleted: boolean}>;
    } | null;
  }, [apiRaw, owner]);

  // Issue #72: recents/favorites follow the account across devices. Writes are
  // guarded with If-Match; on a lost update the fresh server copy is merged and
  // the write retried once (issue #72 review: multi-device merge protocol).
  const pushServer = useCallback(
    async (payload: Record<string, unknown>) => {
      if (!api) return;
      const attempt = () =>
        api('/users/me/preferences', {
          method: 'PATCH',
          headers: {
            'Content-Type': 'application/json',
            ...(revisionRef.current ? {'If-Match': revisionRef.current} : {}),
          },
          body: JSON.stringify(payload),
        });
      try {
        await attempt();
      } catch (error) {
        if (!apiRaw || !/STALE_REVISION|412/.test(String(error))) return;
        const prefs = await refreshFromServer().catch(() => null);
        if (!prefs) return;
        const tombstones = mergeTombstones(readTombstones(tombstonesKey), prefs.library_favorite_tombstones);
        const mergedFavorites = mergeServerPrefs(read(favoritesKey), prefs.library_favorites, 'favorites', tombstones);
        const mergedRecent = mergeServerPrefs(read(recentKey), prefs.library_recents, 'recent');
        write(favoritesKey, mergedFavorites);
        write(recentKey, mergedRecent);
        writeTombstones(tombstonesKey, tombstones);
        setCache({owner: cacheOwner, recent: mergedRecent, favorites: mergedFavorites, tombstones});
        const retry: Record<string, unknown> = {};
        if ('library_favorites' in payload) {
          retry.library_favorites = mergedFavorites;
          retry.library_favorite_tombstones = tombstones;
        }
        if ('library_recents' in payload) retry.library_recents = mergedRecent;
        try {
          await attempt();
        } catch {
          /* best-effort sync; the next load re-merges from the server */
        }
      }
    },
    [api, apiRaw, cacheOwner, favoritesKey, recentKey, refreshFromServer, tombstonesKey],
  );

  useEffect(() => {
    if (!api || !owner) return;
    let active = true;
    refreshFromServer()
      .then(prefs => {
        if (!active || !prefs) return;
        const tombstones = mergeTombstones(readTombstones(tombstonesKey), prefs.library_favorite_tombstones);
        const mergedRecent = mergeServerPrefs(read(recentKey), prefs.library_recents, 'recent');
        const mergedFavorites = mergeServerPrefs(read(favoritesKey), prefs.library_favorites, 'favorites', tombstones);
        write(recentKey, mergedRecent);
        write(favoritesKey, mergedFavorites);
        writeTombstones(tombstonesKey, tombstones);
        setCache({owner: cacheOwner, recent: mergedRecent, favorites: mergedFavorites, tombstones});
        // Push back what the server is missing (local-only entries or newer
        // deletion markers) so a third device converges as well.
        const favoritesDrift = JSON.stringify(mergedFavorites) !== JSON.stringify(prefs.library_favorites || []);
        const tombstonesDrift =
          JSON.stringify(tombstones) !== JSON.stringify(prefs.library_favorite_tombstones || []);
        if (favoritesDrift || tombstonesDrift) {
          void pushServer({library_favorites: mergedFavorites, library_favorite_tombstones: tombstones});
        }
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [api, cacheOwner, favoritesKey, owner, pushServer, recentKey, refreshFromServer, tombstonesKey]);

  if (cache.owner !== cacheOwner) {
    setCache({owner: cacheOwner, recent: read(recentKey), favorites: read(favoritesKey), tombstones: readTombstones(tombstonesKey)});
  }
  const recent = cache.owner === cacheOwner ? cache.recent : read(recentKey);
  const favorites = cache.owner === cacheOwner ? cache.favorites : read(favoritesKey);

  const remember = useCallback((entry: RecentEntry) => {
    setCache(current => {
      const previous = current.owner === cacheOwner ? current.recent : read(recentKey);
      const next = mergeRecent(previous, entry);
      write(recentKey, next);
      void pushServer({library_recents: next});
      return {owner: cacheOwner, recent: next, favorites: current.owner === cacheOwner ? current.favorites : read(favoritesKey), tombstones: current.owner === cacheOwner ? current.tombstones : readTombstones(tombstonesKey)};
    });
  }, [cacheOwner, favoritesKey, pushServer, recentKey, tombstonesKey]);

  const toggle = useCallback((entry: RecentEntry) => {
    const at = Date.now();
    setCache(current => {
      const previous = current.owner === cacheOwner ? current.favorites : read(favoritesKey);
      const previousTombstones = current.owner === cacheOwner ? current.tombstones : readTombstones(tombstonesKey);
      const next = toggleFavorite(previous, entry, previousTombstones, at);
      write(favoritesKey, next.favorites);
      writeTombstones(tombstonesKey, next.tombstones);
      void pushServer({library_favorites: next.favorites, library_favorite_tombstones: next.tombstones});
      return {owner: cacheOwner, recent: current.owner === cacheOwner ? current.recent : read(recentKey), favorites: next.favorites, tombstones: next.tombstones};
    });
  }, [cacheOwner, favoritesKey, pushServer, recentKey, tombstonesKey]);

  return {recent, favorites, remember, toggle};
}

function readTombstones(key: string): Array<{kind: string; id: string; at: number; deleted: boolean}> {
  try {
    const value = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(value) ? value.filter(item => item && item.kind && item.id && item.deleted) : [];
  } catch {
    return [];
  }
}

function writeTombstones(key: string, tombstones: Array<{kind: string; id: string; at: number; deleted: boolean}>) {
  try {
    localStorage.setItem(key, JSON.stringify(tombstones));
  } catch {
    /* storage can be unavailable; sync is best-effort */
  }
}

function read(key: string): RecentEntry[] {
  try {
    const value = JSON.parse(localStorage.getItem(key) || '[]');
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function write(key: string, value: RecentEntry[]) {
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
