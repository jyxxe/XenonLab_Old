import {useCallback, useEffect, useRef, useState} from 'react';
import type {API, APIPage} from '../shell/types';
import {mergeRecent, parseFieldFilters, toggleFavorite} from './logic.js';
import type {Job, QueryExplain, RecentEntry, Row, SearchHit, SearchMeta, SearchMode, Table, Theorem} from './types';

export const SEARCH_LIMIT = 25;
export const DEFAULT_QUERY = 'FROM "/**" LIMIT 100';

export function useScopes(api: API) {
  const [scopes, setScopes] = useState<string[]>([]);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let active = true;
    api('/users/me')
      .then(me => {
        if (active) {
          setScopes(me.scopes || []);
          setLoaded(true);
        }
      })
      .catch(() => active && setLoaded(true));
    return () => {
      active = false;
    };
  }, [api]);
  return {scopes, loaded};
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
      setError(cause);
    } finally {
      loadingRef.current = false;
      setLoadingMore(false);
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
      setLoading(true);
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
      if (view.current === viewRequest) setError(cause);
    } finally {
      loadingMoreRef.current = false;
      setLoadingMore(false);
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
        const endpoint = (overrides.kind ?? kind) === 'keyword' ? '/search/text' : '/search/semantic';
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
    [apiPage, kind, mode, path, query],
  );

  const reset = useCallback(() => {
    ++generation.current;
    setHits([]);
    setMeta({});
    setError(null);
    setRan(false);
  }, []);

  return {mode, setMode, kind, setKind, query, setQuery, path, setPath, filterText, setFilterText, hits, meta, error, running, ran, run, reset};
}

export function useQueryConsole(apiPage: APIPage) {
  const [expression, setExpression] = useState(DEFAULT_QUERY);
  const [rows, setRows] = useState<any[]>([]);
  const [meta, setMeta] = useState<Record<string, any>>({});
  const [plan, setPlan] = useState<QueryExplain | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [running, setRunning] = useState(false);
  const [duration, setDuration] = useState<number | null>(null);
  const generation = useRef(0);

  const validate = useCallback(
    async (text: string) => {
      try {
        const page = await apiPage('/query/validate', {method: 'POST', body: JSON.stringify({expression: text})});
        setPlan(page.data as QueryExplain);
        return true;
      } catch {
        setPlan(null);
        return false;
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
        await validate(text);
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

  return {expression, setExpression, rows, meta, plan, error, running, duration, run, validate};
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
  const generation = useRef(0);

  const load = useCallback(async () => {
    const request = ++generation.current;
    setLoading(true);
    setError(null);
    try {
      const page = await apiPage('/theorems?limit=50');
      if (request !== generation.current) return;
      setItems(page.data as Theorem[]);
      setCursor((page.meta.next_cursor as string) || null);
    } catch (cause) {
      if (request === generation.current) setError(cause);
    } finally {
      if (request === generation.current) setLoading(false);
    }
  }, [apiPage]);

  const loadMore = useCallback(async () => {
    if (!cursor) return;
    setLoadingMore(true);
    try {
      const page = await apiPage(`/theorems?limit=50&cursor=${encodeURIComponent(cursor)}`);
      setItems(old => [...old, ...(page.data as Theorem[])]);
      setCursor((page.meta.next_cursor as string) || null);
    } catch (cause) {
      setError(cause);
    } finally {
      setLoadingMore(false);
    }
  }, [apiPage, cursor]);

  const search = useCallback(
    async (query: string) => {
      setLoading(true);
      setError(null);
      try {
        const page = await apiPage('/theorems/search', {method: 'POST', body: JSON.stringify({query, limit: 50})});
        setSearchResults(page.data as Theorem[]);
      } catch (cause) {
        setError(cause);
      } finally {
        setLoading(false);
      }
    },
    [apiPage],
  );

  const open = useCallback(
    async (id: string) => {
      setError(null);
      try {
        const theorem = (await apiPage(`/theorems/${id}`)).data as Theorem;
        setSelected(theorem);
        const [dependencies, dependents, evidence] = await Promise.all([
          apiPage(`/theorems/${id}/dependencies`).then(page => page.data).catch(() => []),
          apiPage(`/theorems/${id}/dependents`).then(page => page.data).catch(() => []),
          apiPage(`/theorems/${id}/evidence`).then(page => page.data).catch(() => null),
        ]);
        setRelations({dependencies, dependents, evidence: evidence?.evidence_refs || []});
      } catch (cause) {
        setError(cause);
      }
    },
    [apiPage],
  );

  return {
    items,
    searchResults,
    selected,
    relations,
    cursor,
    loading,
    loadingMore,
    error,
    load,
    loadMore,
    search,
    clearSearch: () => setSearchResults(null),
    open,
    close: () => {
      setSelected(null);
      setRelations(null);
    },
  };
}

export function useLibraryPrefs() {
  const [recent, setRecent] = useState<RecentEntry[]>(() => read('xeonolab.library.recent'));
  const [favorites, setFavorites] = useState<RecentEntry[]>(() => read('xeonolab.library.favorites'));

  const remember = useCallback((entry: RecentEntry) => {
    setRecent(current => {
      const next = mergeRecent(current, entry);
      write('xeonolab.library.recent', next);
      return next;
    });
  }, []);

  const toggle = useCallback((entry: RecentEntry) => {
    setFavorites(current => {
      const next = toggleFavorite(current, entry);
      write('xeonolab.library.favorites', next);
      return next;
    });
  }, []);

  return {recent, favorites, remember, toggle};
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
  const [error, setError] = useState<unknown>(null);
  const generation = useRef(0);
  const load = useCallback(async () => {
    if (!enabled || !tableId) return;
    const request = ++generation.current;
    setError(null);
    try {
      const page = await apiPage(`/jobs?table_id=${encodeURIComponent(tableId)}&limit=20`);
      if (request === generation.current) setJobs(page.data as Job[]);
    } catch (cause) {
      if (request === generation.current) {
        setJobs(null);
        setError(cause);
      }
    }
  }, [apiPage, enabled, tableId]);
  useEffect(() => {
    void load();
  }, [load]);
  return {jobs, error, reload: load};
}
