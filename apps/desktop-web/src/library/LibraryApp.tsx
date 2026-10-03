import {normalizeColumnDefaults, stringifyExactJSON} from './json.js';
import React, {useEffect, useMemo, useRef, useState} from 'react';
import {Badge, Button, Checkbox, ErrorState, Input, Modal, Select, Spinner} from '../design-system/primitives';
import {SchemaEditor, type Column} from '../SchemaEditor';
import type {API, APIPage, APIRaw, AppIntent, DesktopApp} from '../shell/types';
import './library.css';
import {LibrarySidebar, expandAncestors, type LibraryViewKind} from './Sidebar';
import {TableView, type SortState} from './TableView';
import {createUploadStore} from '../upload-state.js';
import {Inspector, type ConflictInfo, type InspectorTab} from './Inspector';
import {SearchView} from './SearchView';
import {QueryConsole} from './QueryConsole';
import {TheoremView} from './TheoremView';
import {NewTableDialog} from './NewTableDialog';
import {AskAIDialog} from './AskAIDialog';
import {useCatalogue, useJobs, useLibraryPrefs, useQueryConsole, useScopes, useSearch, useTableSession, useTheorems} from './hooks';
import {ancestorPaths, changedFields, classifySchemaChange, describeError} from './logic.js';
import type {RecentEntry, Row, SearchHit, SelectionAIAction, Table} from './types';

export function LibraryApp({
  api,
  apiPage,
  apiRaw,
  intent,
  apps,
  openApp,
  notify,
  authEpoch = 0,
}: {
  api: API;
  apiPage: APIPage;
  apiRaw: APIRaw;
  intent?: AppIntent;
  apps?: DesktopApp[];
  openApp?: (id: string, intent?: Omit<AppIntent, 'id'>) => void;
  notify?: (toast: {title: string; tone?: 'info' | 'success' | 'danger'}) => void;
  authEpoch?: number;
}) {
  const {scopes, subject, loaded: identityLoaded} = useScopes(api, authEpoch);
  const catalogue = useCatalogue(apiPage);
  const session = useTableSession(api, apiPage);
  const search = useSearch(apiPage);
  const query = useQueryConsole(apiPage, subject, identityLoaded && Boolean(subject), authEpoch);
  const theorems = useTheorems(apiPage);
  const prefs = useLibraryPrefs(subject);
  const uploadStore = useMemo(()=>createUploadStore(),[subject,authEpoch]);
  useEffect(()=>()=>uploadStore.dispose(),[uploadStore]);

  const [view, setView] = useState<LibraryViewKind>('library');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selection, setSelection] = useState<Set<string>>(new Set());
  const [activeRowId, setActiveRowId] = useState<string | null>(null);
  const [sort, setSort] = useState<SortState>(null);
  const [filter, setFilter] = useState('');
  const [inspectorOpen, setInspectorOpen] = useState(() => window.innerWidth > 1023);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>('overview');
  const [conflict, setConflict] = useState<ConflictInfo | null>(null);
  const [notice, setNotice] = useState<{tone: 'info' | 'danger' | 'success'; message: string; code?: string; requestId?: string} | null>(null);
  const [dialog, setDialog] = useState<null | 'new' | 'move' | 'schema' | 'delete'>(null);
  const [askAI, setAskAI] = useState<SelectionAIAction | null>(null);
  const [schemaDraft, setSchemaDraft] = useState<Column[]>([]);
  const [destructiveConfirmed, setDestructiveConfirmed] = useState(false);
  const [movePath, setMovePath] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);
  const rowNavigation = useRef(0);
  const [tableTarget, setTableTarget] = useState<{id: string; loading: boolean; error: unknown} | null>(null);

  const canWrite = scopes.includes('xelibrary:write');
  const canAdmin = scopes.includes('xelibrary:admin');
  const table = session.table;
  const {jobs, error: jobsError, reload: reloadJobs} = useJobs(apiPage, table?.id ?? '', canAdmin && view === 'library' && Boolean(table));

  const movePaths = useMemo(() => [...new Set(['/', ...catalogue.tables.flatMap(item => ancestorPaths(item.path))])].sort((left, right) => left.localeCompare(right, 'zh')), [catalogue.tables]);

  const tableNames = useMemo(() => new Map(catalogue.tables.map(item => [item.id, item.name])), [catalogue.tables]);
  const tableName = (id: string) => tableNames.get(id) || id.slice(0, 8);

  // First load reveals the paths that actually contain tables so the catalogue is
  // reachable without clicks; the user's later collapse/expand state is preserved.
  useEffect(() => {
    if (!catalogue.tables.length) return;
    setExpanded(current => {
      if (current.size) return current;
      const next = new Set<string>();
      for (const item of catalogue.tables) for (const ancestor of ancestorPaths(item.path)) next.add(ancestor);
      return next;
    });
  }, [catalogue.tables]);

  useEffect(() => {
    if (!notice) return;
    const timer = setTimeout(() => setNotice(null), 6000);
    return () => clearTimeout(timer);
  }, [notice]);

  function openTable(next: Table, expandPath = true) {
    ++rowNavigation.current;
    setTableTarget(null);
    setView('library');
    setSelection(new Set());
    setActiveRowId(null);
    setFilter('');
    setSort(null);
    setConflict(null);
    setInspectorOpen(true);
    if (expandPath) setExpanded(current => expandAncestors(current, next.path));
    prefs.remember({kind: 'table', id: next.id, title: next.name, at: Date.now()});
    void session.open(next, session.includeDeleted);
  }

  async function openTableTarget(id: string) {
    const request = ++rowNavigation.current;
    setView('library');
    setTableTarget({id, loading: true, error: null});
    setSelection(new Set());
    setActiveRowId(null);
    setConflict(null);
    setDialog(null);
    setAskAI(null);
    session.clear();
    try {
      // Revalidate the current table ACL even when its metadata is cached in
      // the catalogue. The table may also be beyond the loaded directory page.
      const next = await api(`/tables/${encodeURIComponent(id)}`) as Table;
      if (request !== rowNavigation.current) return;
      openTable(next);
    } catch (error) {
      if (request === rowNavigation.current) setTableTarget({id, loading: false, error});
    }
  }

  function selectView(next: LibraryViewKind) {
    ++rowNavigation.current;
    setTableTarget(null);
    setView(next);
    if (next === 'theorems' && !theorems.items.length && !theorems.loading) void theorems.load();
  }

  async function openRow(tableId: string, rowId: string, remember = true) {
    const request = ++rowNavigation.current;
    setTableTarget(null);
    const known = catalogue.tables.find(item => item.id === tableId) || (await api(`/tables/${tableId}`).catch(() => null));
    if (request !== rowNavigation.current) return;
    if (!known) {
      setNotice({tone: 'danger', message: '无法定位该记录所属的知识表。'});
      return;
    }
    setView('library');
    await session.open(known, true);
    if (request !== rowNavigation.current) return;
    const row: Row | null = await api(`/tables/${tableId}/rows/${rowId}`).catch(() => null);
    if (request !== rowNavigation.current) return;
    if (!row) {
      setNotice({tone: 'danger', message: '记录不存在，或当前身份没有读取权限。'});
      return;
    }
    session.setRows(old => (old.some(item => item._row_id === rowId) ? old : [row, ...old]));
    setSelection(new Set([rowId]));
    setActiveRowId(rowId);
    setInspectorOpen(true);
    setInspectorTab('overview');
    setExpanded(current => expandAncestors(current, known.path));
    if (remember) prefs.remember({kind: 'row', id: `${tableId}:${rowId}`, title: `${known.name} · ${rowId.slice(0, 8)}`, at: Date.now()});
  }

  async function openHit(hit: SearchHit) {
    await openRow(hit.table_id, hit.row_id);
  }

  function openEntry(entry: RecentEntry) {
    if (entry.kind === 'table') {
      const known = catalogue.tables.find(item => item.id === entry.id);
      if (known) openTable(known);
      else setNotice({tone: 'danger', message: '该表已不在当前目录中。'});
    } else if (entry.kind === 'row') {
      const [tableId, rowId] = entry.id.split(':');
      void openRow(tableId, rowId);
    } else if (entry.kind === 'theorem') {
      ++rowNavigation.current;
      setTableTarget(null);
      setView('theorems');
      void theorems.open(entry.id);
    }
  }

  function rememberLoadedRow(rowId: string) {
    if (table && session.rows.some(row => row._row_id === rowId)) {
      prefs.remember({kind: 'row', id: `${table.id}:${rowId}`, title: `${table.name} · ${rowId.slice(0, 8)}`, at: Date.now()});
    }
  }

  function activateLoadedRow(rowId: string) {
    setActiveRowId(rowId);
    setInspectorOpen(true);
    setInspectorTab('overview');
    rememberLoadedRow(rowId);
  }

  function onSelectRow(rowId: string, event: React.MouseEvent) {
    ++rowNavigation.current;
    setActiveRowId(rowId);
    setInspectorOpen(true);
    setSelection(current => {
      if (event.shiftKey && activeRowId) {
        const ids = session.rows.map(row => row._row_id);
        const from = ids.indexOf(activeRowId);
        const to = ids.indexOf(rowId);
        if (from >= 0 && to >= 0) {
          const [start, end] = from < to ? [from, to] : [to, from];
          return new Set(ids.slice(start, end + 1));
        }
      }
      if (event.metaKey || event.ctrlKey) {
        const next = new Set(current);
        if (next.has(rowId)) next.delete(rowId);
        else next.add(rowId);
        return next;
      }
      return new Set([rowId]);
    });
    rememberLoadedRow(rowId);
  }

  const selectedRow = session.rows.find(row => row._row_id === activeRowId) || null;

  async function saveRow(values: Record<string, any>, revision: number) {
    if (!table || !selectedRow) return;
    try {
      const page = await apiPage(`/tables/${table.id}/rows/${selectedRow._row_id}`, {
        method: 'PATCH',
        headers: {'If-Match': `"revision:${revision}"`},
        body: stringifyExactJSON({values: Object.fromEntries(changedFields(selectedRow.values, values).map(name => [name, values[name]]))}),
      });
      const saved = page.data as Row;
      session.setRows(old => old.map(row => (row._row_id === saved._row_id ? saved : row)));
      setConflict(null);
      setNotice({tone: 'success', message: `记录已保存（r${saved._revision}）。索引状态以上方 Index 标签的真实任务为准。`});
      await session.refreshMetadata();
    } catch (cause) {
      const info = describeError(cause, '保存记录');
      if (info.conflict) {
        const current = await api(`/tables/${table.id}/rows/${selectedRow._row_id}`).catch(() => null);
        setConflict({
          rowId: selectedRow._row_id,
          localRevision: revision,
          currentRevision: current?._revision ?? revision,
          message: info.message,
        });
      }
      throw cause;
    }
  }

  async function createRow(values: Record<string, any>) {
    if (!table) return;
    const page = await apiPage(`/tables/${table.id}/rows`, {method: 'POST', body: stringifyExactJSON({values})});
    const created = page.data as Row;
    session.setRows(old => [created, ...old]);
    setActiveRowId(created._row_id);
    setSelection(new Set([created._row_id]));
    await session.refresh();
    setActiveRowId(created._row_id);
    setNotice({tone: 'success', message: '记录已创建。当前平台没有自动 indexing worker；需要时请手动重建索引。'});
  }

  async function deleteRow(revision: number) {
    if (!table || !selectedRow) return;
    await apiPage(`/tables/${table.id}/rows/${selectedRow._row_id}`, {
      method: 'DELETE',
      headers: {'If-Match': `"revision:${revision}"`},
    });
    await session.refresh(true);
    setNotice({tone: 'success', message: '记录已软删除；历史与 revision 仍然保留。'});
  }

  async function restoreRow(revision: number, currentRevision: number) {
    if (!table || !selectedRow) return;
    await apiPage(`/tables/${table.id}/rows/${selectedRow._row_id}/restore`, {
      method: 'POST',
      headers: {'If-Match': `"revision:${currentRevision}"`},
      body: stringifyExactJSON({revision}),
    });
    await session.refresh(true);
    setNotice({tone: 'success', message: `已恢复到 r${revision}；恢复创建了新的 revision。`});
  }

  async function createTable(payload: {path: string; name: string; columns: Column[]}) {
    const page = await apiPage('/tables', {method: 'POST', body: stringifyExactJSON({...payload, columns: normalizeColumnDefaults(payload.columns)})});
    await catalogue.load();
    openTable({id: page.data.table_id, path: page.data.path, name: page.data.name});
  }

  async function moveTable() {
    if (!table || !Number.isInteger(table.table_revision)) return;
    setBusy(true);
    setActionError(null);
    try {
      await apiPage(`/tables/${table.id}/move`, {
        method: 'POST',
        headers: {'If-Match': `"revision:${table.table_revision}"`},
        body: stringifyExactJSON({path: movePath}),
      });
      await catalogue.load();
      await session.refreshMetadata();
      setDialog(null);
      setNotice({tone: 'success', message: '知识表已移动。'});
    } catch (cause) {
      setActionError(cause);
    } finally {
      setBusy(false);
    }
  }

  async function saveSchema() {
    if (!table || !Number.isInteger(table.table_revision)) return;
    const change = classifySchemaChange(table.columns || [], schemaDraft);
    if (change.risk === 'destructive' && !destructiveConfirmed) {
      setActionError(new Error('破坏性 schema 变更需要先勾选确认。'));
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      await apiPage(`/tables/${table.id}/schema`, {
        method: 'PATCH',
        headers: {'If-Match': `"revision:${table.table_revision}"`},
        body: stringifyExactJSON({columns: normalizeColumnDefaults(schemaDraft)}),
      });
      setDialog(null);
      setDestructiveConfirmed(false);
      await session.refresh(true);
      setNotice({tone: 'success', message: '字段定义已更新。'});
    } catch (cause) {
      setActionError(cause);
    } finally {
      setBusy(false);
    }
  }

  async function deleteTable() {
    if (!table || !Number.isInteger(table.table_revision)) return;
    setBusy(true);
    setActionError(null);
    try {
      await apiPage(`/tables/${table.id}`, {method: 'DELETE', headers: {'If-Match': `"revision:${table.table_revision}"`}});
      setDialog(null);
      session.clear();
      await catalogue.load();
      setNotice({tone: 'success', message: '空知识表已删除。'});
    } catch (cause) {
      setActionError(cause);
    } finally {
      setBusy(false);
    }
  }

  async function reindex() {
    if (!table) return;
    setBusy(true);
    try {
      await apiPage('/index/reindex', {method: 'POST', body: stringifyExactJSON({table_id: table.id})});
      setNotice({tone: 'success', message: '索引任务已排队，等待 Embedding worker。'});
      await reloadJobs();
    } catch (cause) {
      const info = describeError(cause, '重建索引');
      setNotice({tone: 'danger', message: info.message, code: info.code, requestId: info.requestId});
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!intent) return;
    if (intent.action === 'openTable' && intent.table_id) {
      void openTableTarget(intent.table_id);
      return () => { ++rowNavigation.current; };
    }
    setTableTarget(null);
    if (intent.action === 'refresh') {
      void catalogue.load();
      if (session.table) void session.refresh();
      return;
    }
    if (intent.action === 'askAI') {
      // Drafts only make sense with a concrete selection; without one the
      // effect is a no-op so the dialog never pretends to have context.
      if (session.table) setAskAI('ask');
      return;
    }
    selectView(intent.action === 'query' ? 'query' : intent.action === 'theorems' ? 'theorems' : 'search');
    if (intent.action === 'search') {
      search.reset();
      search.setMode('fast');
      if (intent.query !== undefined) search.setQuery(intent.query);
      // User reviews the query before execution; providers never perform hidden
      // model/API requests while the command palette is being typed into.
      const timer = setTimeout(() => document.querySelector<HTMLInputElement>('.library-search input[type="search"]')?.focus(), 0);
      return () => clearTimeout(timer);
    }
  }, [intent]);

  // Chat availability comes from the shell's merged registry; XeLibrary itself
  // never hardcodes a chat origin. When no chat app exists the draft can still
  // be composed and copied.
  const chatApp = (apps || []).find(app => app.id === 'chat' && app.kind === 'iframe') || null;
  const selectedRows = useMemo(
    () => (selection.size ? session.rows.filter(row => selection.has(row._row_id)) : activeRowId ? session.rows.filter(row => row._row_id === activeRowId) : []),
    [selection, activeRowId, session.rows],
  );

  function confirmAskAI(prompt: string) {
    setAskAI(null);
    if (!chatApp) return;
    if (openApp) openApp('chat', {action: 'askAI', prompt});
    if (notify) notify({title: 'AI 草稿已投递到对话窗口，请核对后发送。', tone: 'info'});
  }

  const catalogueError = catalogue.error ? describeError(catalogue.error, '读取目录') : null;
  const tableTargetError = tableTarget?.error ? describeError(tableTarget.error, '打开知识表') : null;

  return (
    <div className="knowledge">
      <LibrarySidebar
        tables={catalogue.tables}
        loading={catalogue.loading}
        loadingMore={catalogue.loadingMore}
        error={catalogue.error}
        cursor={catalogue.cursor}
        view={view}
        tableId={table?.id ?? null}
        expanded={expanded}
        onToggleExpand={path =>
          setExpanded(current => {
            const next = new Set(current);
            if (next.has(path)) next.delete(path);
            else next.add(path);
            return next;
          })
        }
        onSetView={selectView}
        onOpenTable={openTable}
        onLoadMore={() => void catalogue.loadMore()}
        onRetry={() => void catalogue.load()}
        onCreateTable={() => setDialog('new')}
        recent={prefs.recent}
        favorites={prefs.favorites}
        onOpenEntry={openEntry}
        onToggleFavorite={entry => prefs.toggle(entry)}
        theoremCount={theorems.items.length || null}
      />

      <main className="knowledge-content" aria-label="知识库主区域">
        {notice && (
          <div className={`library-toast library-toast-${notice.tone}`} role={notice.tone === 'danger' ? 'alert' : 'status'}>
            <div className="library-toast-content">
              {notice.message}
              {notice.code && <small className="ds-error-code">错误代码 {notice.code}</small>}
              {notice.requestId && <small className="ds-request-id">请求编号 {notice.requestId}</small>}
            </div>
            <button type="button" className="library-link" onClick={() => setNotice(null)}>关闭</button>
          </div>
        )}

        {view === 'search' && (
          <SearchView
            mode={search.mode}
            onMode={search.setMode}
            kind={search.kind}
            onKind={search.setKind}
            query={search.query}
            onQuery={search.setQuery}
            path={search.path}
            onPath={search.setPath}
            filterText={search.filterText}
            onFilterText={search.setFilterText}
            hits={search.hits}
            meta={search.meta}
            error={search.error}
            running={search.running}
            ran={search.ran}
            onRun={() => void search.run()}
            onStructured={() => selectView('query')}
            onOpenHit={hit => void openHit(hit)}
            tableName={tableName}
          />
        )}

        {view === 'query' && (
          <QueryConsole
            expression={query.expression}
            onExpression={query.setExpression}
            rows={query.rows}
            meta={query.meta}
            plan={query.plan}
            error={query.error}
            running={query.running}
            validating={query.validating}
            duration={query.duration}
            recent={query.recent}
            draftStatus={query.draftStatus}
            draftMessage={query.draftMessage}
            queryEnabled={identityLoaded && Boolean(subject)}
            onLoadRecent={query.loadRecent}
            onDeleteRecent={query.deleteRecent}
            onClearRecent={query.clearRecent}
            onRetryStorage={query.retryStorage}
            onRun={(text, remember) => void query.run(text, remember)}
            onValidate={text => void query.validate(text)}
            onOpenRow={(tableId, rowId) => void openRow(tableId, rowId)}
            tableName={tableName}
            tableNameForCopy={id => tableNames.get(id) ?? id}
          />
        )}

        {view === 'theorems' && (
          <TheoremView
            items={theorems.items}
            searchResults={theorems.searchResults}
            searchPartial={theorems.searchPartial}
            selected={theorems.selected}
            relations={theorems.relations}
            detailLoading={theorems.detailLoading}
            detailError={theorems.detailError}
            loading={theorems.loading}
            loadingMore={theorems.loadingMore}
            error={theorems.error}
            cursor={theorems.cursor}
            onLoad={() => void theorems.load()}
            onLoadMore={() => void theorems.loadMore()}
            onSearch={(value, status) => void theorems.search(value, status)}
            onClearSearch={theorems.clearSearch}
            onOpen={id => {
              void theorems.open(id);
              prefs.remember({kind: 'theorem', id, title: '定理', at: Date.now()});
            }}
            onClose={theorems.close}
          />
        )}

        {view === 'library' && tableTarget && (
          tableTarget.loading ? <p role="status"><Spinner label="正在打开知识表" /> 正在打开知识表…</p> : tableTargetError && <ErrorState title={tableTargetError.title} message={tableTargetError.message} requestId={tableTargetError.requestId} errorCode={tableTargetError.code} onRetry={() => void openTableTarget(tableTarget.id)} />
        )}

        {view === 'library' && !table && !tableTarget && (
          <div className="library-welcome">
            <h2>XeLibrary</h2>
            <p>从左侧路径树打开一张知识表，或使用搜索与查询控制台。</p>
            {catalogueError ? (
              <ErrorState title={catalogueError.title} message={catalogueError.message} requestId={catalogueError.requestId} errorCode={catalogueError.code} onRetry={() => void catalogue.load()} />
            ) : catalogue.loading ? (
              <p role="status"><Spinner label="正在加载知识表" /> 正在加载知识表目录…</p>
            ) : catalogue.tables.length ? (
              <ul className="library-welcome-list">
                {catalogue.tables.slice(0, 8).map(item => (
                  <li key={item.id}><button type="button" onClick={() => openTable(item)}>{item.path}/{item.name}</button></li>
                ))}
              </ul>
            ) : (
              <p>当前身份没有可见的知识表。创建一张新表，或联系管理员授予路径权限。</p>
            )}
          </div>
        )}

        {view === 'library' && table && (
          <>
            <header className="library-table-head">
              <div>
                <h2>{table.path}/{table.name}</h2>
                <p className="muted">
                  {table.columns?.length ? `${table.columns.length} 个字段` : ''}
                  {typeof table.table_revision === 'number' ? ` · table r${table.table_revision}` : ''}
                  {typeof table.schema_revision === 'number' ? ` · schema r${table.schema_revision}` : ''}
                </p>
              </div>
              <div className="library-table-actions">
                <Button size="sm" disabled={!canWrite} onClick={() => { setInspectorOpen(true); setInspectorTab('fields'); setActiveRowId(null); setSelection(new Set()); }}>新增记录</Button>
                <Button size="sm" onClick={() => setDialog('move')} disabled={!canWrite}>移动</Button>
                <Button size="sm" onClick={() => { setSchemaDraft((table.columns || []).map(column => ({...column}))); setDestructiveConfirmed(false); setDialog('schema'); }} disabled={!canAdmin}>字段定义</Button>
                <Button size="sm" variant="danger" onClick={() => setDialog('delete')} disabled={!canAdmin}>删除表</Button>
                <Button size="sm" variant="ghost" onClick={() => setInspectorOpen(value => !value)}>{inspectorOpen ? '隐藏 Inspector' : '显示 Inspector'}</Button>
                <Button size="sm" variant="ghost" onClick={() => setAskAI('ask')} title="就当前选择的记录向 AI 提问">{selection.size > 1 ? `Ask AI（${selection.size} 行）` : 'Ask AI'}</Button>
              </div>
            </header>
            <TableView
              table={table}
              columns={table.columns || []}
              rows={session.rows}
              cursor={session.cursor}
              loading={session.loading}
              loadingMore={session.loadingMore}
              error={session.error}
              filter={filter}
              onFilter={setFilter}
              sort={sort}
              onSort={key =>
                setSort(current => (current && current.key === key ? {key, direction: current.direction === 'asc' ? 'desc' : 'asc'} : {key, direction: 'asc'}))
              }
              selection={selection}
              activeRowId={activeRowId}
              onSelectRow={onSelectRow}
              onActivateRow={activateLoadedRow}
              onSelectionChange={setSelection}
              onLoadMore={() => void session.loadMore()}
              onRetry={() => void session.refresh()}
              onRefresh={() => void session.refresh()}
              onContextMenu={() => undefined}
              onAskAI={rows => {
                setSelection(new Set(rows.map(row => row._row_id)));
                if (rows.length === 1) setActiveRowId(rows[0]._row_id);
                setAskAI('ask');
              }}
              canWrite={canWrite}
            />
          </>
        )}
      </main>

      {view === 'library' && table && inspectorOpen && (
        <Inspector
          uploadStore={uploadStore}
          table={table}
          columns={table.columns || []}
          row={selectedRow}
          selectionSize={selection.size}
          api={api}
          apiPage={apiPage}
          apiRaw={apiRaw}
          scopes={scopes}
          jobs={jobs}
          jobsError={jobsError}
          canAdmin={canAdmin}
          onReloadJobs={() => void reloadJobs()}
          onReindex={() => void reindex()}
          conflict={conflict}
          onSave={saveRow}
          onDelete={deleteRow}
          onRestore={restoreRow}
          onCreate={createRow}
          onReloadRow={async () => {
            if (!table || !selectedRow) return;
            const row = await api(`/tables/${table.id}/rows/${selectedRow._row_id}`);
            session.setRows(old => old.map(item => (item._row_id === row._row_id ? row : item)));
            setConflict(null);
          }}
          onToggleFavorite={() => prefs.toggle({kind: 'table', id: table.id, title: table.name, at: Date.now()})}
          onClose={() => setInspectorOpen(false)}
        />
      )}

      <NewTableDialog open={dialog === 'new'} onClose={() => setDialog(null)} onCreate={createTable} />

      {askAI && table && (
        <AskAIDialog
          open
          action={askAI}
          onAction={setAskAI}
          table={table}
          rows={selectedRows}
          columns={table.columns || []}
          chatAvailable={Boolean(chatApp)}
          onConfirm={confirmAskAI}
          onClose={() => setAskAI(null)}
        />
      )}

      <Modal open={dialog === 'move'} title={`移动 ${table?.name || ''}`} onClose={() => setDialog(null)} footer={<><Button variant="ghost" onClick={() => setDialog(null)}>取消</Button><Button variant="primary" disabled={busy || !movePath.startsWith('/')} onClick={() => void moveTable()}>移动知识表</Button></>}>
        {actionError ? <ErrorState title={describeError(actionError, '移动表').title} requestId={describeError(actionError).requestId} errorCode={describeError(actionError).code} message={describeError(actionError, '移动表').message} /> : null}
        <label style={{display: 'grid', gap: 4, marginBottom: 12}}>
          已加载目录（可选）
          <Select value={movePaths.includes(movePath) ? movePath : ''} onChange={event => { if (event.target.value) setMovePath(event.target.value); }}>
            <option value="">选择已有路径</option>
            {movePaths.map(path => <option key={path} value={path}>{path}</option>)}
          </Select>
          <small className="muted">候选仅来自当前已加载的可见目录，不代表完整目录或目标写权限；也可在下方输入新路径。</small>
        </label>
        <label>
          目标路径
          <Input value={movePath} onChange={event => setMovePath(event.target.value)} placeholder="/数学/分析" />
          <small className="muted">移动需要目标路径的写权限；会清除该表已发布的向量点，需要重新索引。</small>
        </label>
      </Modal>

      <Modal
        open={dialog === 'schema'}
        title="字段定义"
        size="lg"
        onClose={() => setDialog(null)}
        footer={<><Button variant="ghost" onClick={() => setDialog(null)}>取消</Button><Button variant="primary" disabled={busy} onClick={() => void saveSchema()}>保存字段定义</Button></>}
      >
        {actionError ? <ErrorState title={describeError(actionError, '更新 schema').title} requestId={describeError(actionError).requestId} errorCode={describeError(actionError).code} message={describeError(actionError, '更新 schema').message} /> : null}
        {(() => {
          const change = classifySchemaChange(table?.columns || [], schemaDraft);
          return (
            <div className={`schema-risk schema-risk-${change.risk}`}>
              <strong>{change.risk === 'destructive' ? 'Potentially destructive / Unsupported migration' : 'Safe change'}</strong>
              {change.summary.length ? <ul>{change.summary.map(item => <li key={item}>{item}</li>)}</ul> : <p>没有字段增删或类型变更。</p>}
              {change.risk === 'destructive' && (
                <>
                  <p>删除字段或改变类型会影响全部现有行，并且当前后端不支持自动迁移（超过 10000 行会被拒绝）。</p>
                  <Checkbox
                    label="我确认这是破坏性变更，并理解现有数据可能被重写或拒绝。"
                    checked={destructiveConfirmed}
                    onChange={event => setDestructiveConfirmed(event.target.checked)}
                  />
                </>
              )}
            </div>
          );
        })()}
        <SchemaEditor columns={schemaDraft} change={setSchemaDraft} disabled={busy} />
      </Modal>

      <Modal open={dialog === 'delete'} title={`删除 ${table?.name || ''}`} onClose={() => setDialog(null)} footer={<><Button variant="ghost" onClick={() => setDialog(null)}>取消</Button><Button variant="danger" disabled={busy} onClick={() => void deleteTable()}>删除空表</Button></>}>
        {actionError ? (
          <ErrorState
            title={describeError(actionError, '删除表').title} requestId={describeError(actionError).requestId} errorCode={describeError(actionError).code}
            message={`${describeError(actionError, '删除表').message} 该表当前无法删除；可以先用「查看内容」确认剩余行、附件与向量引用。平台没有提供表级计数 API，因此这里不显示推测数字。`}
          />
        ) : (
          <p>只有真正为空（没有行、附件、向量引用、活动索引任务或上传会话）的表可以删除。这不是 cascade delete；需要后台归档/清理时应创建独立 Core Issue。</p>
        )}
      </Modal>
    </div>
  );
}
