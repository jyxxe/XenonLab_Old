import React, {useEffect, useMemo, useState} from 'react';
import {Badge, Button, Checkbox, ErrorState, Input, Modal, Spinner} from '../design-system/primitives';
import {SchemaEditor, type Column} from '../SchemaEditor';
import type {API, APIPage, APIRaw} from '../shell/types';
import './library.css';
import {LibrarySidebar, expandAncestors, type LibraryViewKind} from './Sidebar';
import {TableView, type SortState} from './TableView';
import {Inspector, type ConflictInfo, type InspectorTab} from './Inspector';
import {SearchView} from './SearchView';
import {QueryConsole} from './QueryConsole';
import {TheoremView} from './TheoremView';
import {NewTableDialog} from './NewTableDialog';
import {useCatalogue, useJobs, useLibraryPrefs, useQueryConsole, useScopes, useSearch, useTableSession, useTheorems} from './hooks';
import {ancestorPaths, classifySchemaChange, describeError} from './logic.js';
import type {RecentEntry, Row, SearchHit, Table} from './types';

export function LibraryApp({api, apiPage, apiRaw}: {api: API; apiPage: APIPage; apiRaw: APIRaw}) {
  const {scopes} = useScopes(api);
  const catalogue = useCatalogue(apiPage);
  const session = useTableSession(api, apiPage);
  const search = useSearch(apiPage);
  const query = useQueryConsole(apiPage);
  const theorems = useTheorems(apiPage);
  const prefs = useLibraryPrefs();

  const [view, setView] = useState<LibraryViewKind>('library');
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selection, setSelection] = useState<Set<string>>(new Set());
  const [activeRowId, setActiveRowId] = useState<string | null>(null);
  const [sort, setSort] = useState<SortState>(null);
  const [filter, setFilter] = useState('');
  const [inspectorOpen, setInspectorOpen] = useState(() => window.innerWidth > 1023);
  const [inspectorTab, setInspectorTab] = useState<InspectorTab>('overview');
  const [conflict, setConflict] = useState<ConflictInfo | null>(null);
  const [notice, setNotice] = useState<{tone: 'info' | 'danger' | 'success'; message: string} | null>(null);
  const [dialog, setDialog] = useState<null | 'new' | 'move' | 'schema' | 'delete'>(null);
  const [schemaDraft, setSchemaDraft] = useState<Column[]>([]);
  const [destructiveConfirmed, setDestructiveConfirmed] = useState(false);
  const [movePath, setMovePath] = useState('');
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<unknown>(null);

  const canWrite = scopes.includes('xelibrary:write');
  const canAdmin = scopes.includes('xelibrary:admin');
  const table = session.table;
  const {jobs, error: jobsError, reload: reloadJobs} = useJobs(apiPage, table?.id ?? '', canAdmin && view === 'library' && Boolean(table));

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

  function openTable(next: Table) {
    setView('library');
    setSelection(new Set());
    setActiveRowId(null);
    setFilter('');
    setSort(null);
    setConflict(null);
    setInspectorOpen(true);
    setExpanded(current => expandAncestors(current, next.path));
    prefs.remember({kind: 'table', id: next.id, title: next.name, at: Date.now()});
    void session.open(next, session.includeDeleted);
  }

  function selectView(next: LibraryViewKind) {
    setView(next);
    if (next === 'theorems' && !theorems.items.length && !theorems.loading) void theorems.load();
  }

  async function openRow(tableId: string, rowId: string, remember = true) {
    const known = catalogue.tables.find(item => item.id === tableId) || (await api(`/tables/${tableId}`).catch(() => null));
    if (!known) {
      setNotice({tone: 'danger', message: '无法定位该记录所属的知识表。'});
      return;
    }
    setView('library');
    await session.open(known, true);
    const row: Row | null = await api(`/tables/${tableId}/rows/${rowId}`).catch(() => null);
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
      setView('theorems');
      void theorems.open(entry.id);
    }
  }

  function onSelectRow(rowId: string, event: React.MouseEvent) {
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
    const row = session.rows.find(item => item._row_id === rowId);
    if (row) prefs.remember({kind: 'row', id: `${table?.id}:${rowId}`, title: `${table?.name} · ${rowId.slice(0, 8)}`, at: Date.now()});
  }

  const selectedRow = session.rows.find(row => row._row_id === activeRowId) || null;

  async function saveRow(values: Record<string, any>, revision: number) {
    if (!table || !selectedRow) return;
    try {
      const page = await apiPage(`/tables/${table.id}/rows/${selectedRow._row_id}`, {
        method: 'PATCH',
        headers: {'If-Match': `"revision:${revision}"`},
        body: JSON.stringify({values}),
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
    const page = await apiPage(`/tables/${table.id}/rows`, {method: 'POST', body: JSON.stringify({values})});
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
      body: JSON.stringify({revision}),
    });
    await session.refresh(true);
    setNotice({tone: 'success', message: `已恢复到 r${revision}；恢复创建了新的 revision。`});
  }

  async function createTable(payload: {path: string; name: string; columns: Column[]}) {
    const page = await apiPage('/tables', {method: 'POST', body: JSON.stringify(payload)});
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
        body: JSON.stringify({path: movePath}),
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
        body: JSON.stringify({columns: schemaDraft}),
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
      await apiPage('/index/reindex', {method: 'POST', body: JSON.stringify({table_id: table.id})});
      setNotice({tone: 'success', message: '索引任务已排队，等待 Embedding worker。'});
      await reloadJobs();
    } catch (cause) {
      setNotice({tone: 'danger', message: describeError(cause, '重建索引').message});
    } finally {
      setBusy(false);
    }
  }

  const catalogueError = catalogue.error ? describeError(catalogue.error, '读取目录') : null;

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
            {notice.message}
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
            onStructured={() => setView('query')}
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
            duration={query.duration}
            onRun={text => void query.run(text)}
            onValidate={text => void query.validate(text)}
            onOpenRow={(tableId, rowId) => void openRow(tableId, rowId)}
            tableName={tableName}
          />
        )}

        {view === 'theorems' && (
          <TheoremView
            items={theorems.items}
            searchResults={theorems.searchResults}
            selected={theorems.selected}
            relations={theorems.relations}
            loading={theorems.loading}
            loadingMore={theorems.loadingMore}
            error={theorems.error}
            cursor={theorems.cursor}
            onLoad={() => void theorems.load()}
            onLoadMore={() => void theorems.loadMore()}
            onSearch={value => void theorems.search(value)}
            onClearSearch={theorems.clearSearch}
            onOpen={id => {
              void theorems.open(id);
              prefs.remember({kind: 'theorem', id, title: '定理', at: Date.now()});
            }}
            onClose={theorems.close}
          />
        )}

        {view === 'library' && !table && (
          <div className="library-welcome">
            <h2>XeLibrary</h2>
            <p>从左侧路径树打开一张知识表，或使用搜索与查询控制台。</p>
            {catalogueError ? (
              <ErrorState title={catalogueError.title} message={catalogueError.message} requestId={catalogueError.code} onRetry={() => void catalogue.load()} />
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
              onActivateRow={rowId => { setActiveRowId(rowId); setInspectorOpen(true); setInspectorTab('overview'); }}
              onSelectionChange={setSelection}
              onLoadMore={() => void session.loadMore()}
              onRetry={() => void session.refresh()}
              onRefresh={() => void session.refresh()}
              onContextMenu={() => undefined}
              canWrite={canWrite}
            />
          </>
        )}
      </main>

      {view === 'library' && table && inspectorOpen && (
        <Inspector
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

      <Modal open={dialog === 'move'} title={`移动 ${table?.name || ''}`} onClose={() => setDialog(null)} footer={<><Button variant="ghost" onClick={() => setDialog(null)}>取消</Button><Button variant="primary" disabled={busy || !movePath.startsWith('/')} onClick={() => void moveTable()}>移动知识表</Button></>}>
        {actionError ? <ErrorState title={describeError(actionError, '移动表').title} message={describeError(actionError, '移动表').message} /> : null}
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
        {actionError ? <ErrorState title={describeError(actionError, '更新 schema').title} message={describeError(actionError, '更新 schema').message} /> : null}
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
            title={describeError(actionError, '删除表').title}
            message={`${describeError(actionError, '删除表').message} 该表当前无法删除；可以先用「查看内容」确认剩余行、附件与向量引用。平台没有提供表级计数 API，因此这里不显示推测数字。`}
          />
        ) : (
          <p>只有真正为空（没有行、附件、向量引用、活动索引任务或上传会话）的表可以删除。这不是 cascade delete；需要后台归档/清理时应创建独立 Core Issue。</p>
        )}
      </Modal>
    </div>
  );
}
