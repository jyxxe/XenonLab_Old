import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './src/design-system/tokens.css';
import './src/design-system/primitives.css';
import './src/style.css';
import { TableView, type SortState } from './src/library/TableView';
import { Inspector } from './src/library/Inspector';
const control = { hold: true, pending: [] as Array<() => void>, settled: 0 };
(window as any).inspectorFixture = control;
const token = 'round6-local-test-token', tableId = new URLSearchParams(location.search).get('table')!;
async function request(path: string, init: RequestInit = {}) { const h = new Headers(init.headers); h.set('Authorization', `Bearer ${token}`); if (init.body)
    h.set('Content-Type', 'application/json'); const mutation = ['PATCH', 'DELETE', 'POST'].includes(init.method || ''); if (mutation && control.hold)
    await new Promise<void>(r => control.pending.push(r)); const res = await fetch(`/api/v1${path}`, { ...init, headers: h }), body = await res.json(); if (mutation)
    control.settled++; if (!res.ok)
    throw Object.assign(Error(body?.error?.message || `HTTP ${res.status}`), { status: res.status }); return body; }
const api = async (p: string, i: RequestInit = {}) => (await request(p, i)).data;
const apiPage = async (p: string, i: RequestInit = {}) => { const b = await request(p, i); return { data: b.data, meta: b.meta || {} }; };
const apiRaw = async (p: string, i: RequestInit = {}) => { const h = new Headers(i.headers); h.set('Authorization', `Bearer ${token}`); return fetch(`/api/v1${p}`, { ...i, headers: h }); };
function App() { const [table, setTable] = useState<any>(null), [rows, setRows] = useState<any[]>([]), [active, setActive] = useState<string | null>(null), [selection, setSelection] = useState(new Set<string>()), [sort, setSort] = useState<SortState>(null), [filter, setFilter] = useState(''), [cursor, setCursor] = useState<string | null>(null), [conflict, setConflict] = useState<any>(null), [notice, setNotice] = useState(''); useEffect(() => { void (async () => { const t = await api(`/tables/${tableId}`), p = await apiPage(`/tables/${tableId}/rows?limit=100`); setTable({ id: tableId, name: t.name, path: t.path, columns: t.columns, table_revision: t.table_revision, schema_revision: t.schema_revision }); setRows(p.data); setCursor(p.meta.next_cursor || null); })(); }, []); const row = rows.find(r => r._row_id === active) || null; async function save(values: any, revision: number) { if (!row)
    return; try {
    const b = await apiPage(`/tables/${tableId}/rows/${row._row_id}`, { method: 'PATCH', headers: { 'If-Match': `"revision:${revision}"` }, body: JSON.stringify({ values }) });
    setRows(old => old.map(r => r._row_id === b.data._row_id ? b.data : r));
    setConflict(null);
    setNotice(`已保存 r${b.data._revision}`);
}
catch (e: any) {
    if (e.status === 412) {
        const current = await api(`/tables/${tableId}/rows/${row._row_id}`);
        setConflict({ rowId: row._row_id, localRevision: revision, currentRevision: current._revision, message: e.message });
    }
    throw e;
} } async function reload() { if (!row)
    return; const fresh = await api(`/tables/${tableId}/rows/${row._row_id}`); setRows(old => old.map(r => r._row_id === fresh._row_id ? fresh : r)); setConflict(null); } return table ? <main><h1>本地 Inspector 切换检查</h1><p role="status">{notice}</p><section style={{ height: 480 }}><TableView table={table} columns={table.columns} rows={rows} cursor={cursor} loading={false} loadingMore={false} error={null} filter={filter} onFilter={setFilter} sort={sort} onSort={k => setSort(s => s?.key === k ? { key: k, direction: s.direction === 'asc' ? 'desc' : 'asc' } : { key: k, direction: 'asc' })} selection={selection} activeRowId={active} onSelectRow={id => { setActive(id); setSelection(new Set([id])); }} onActivateRow={id => { setActive(id); setSelection(new Set([id])); }} onSelectionChange={setSelection} onLoadMore={() => { }} onRetry={() => { }} onRefresh={() => { }} onContextMenu={() => { }} onAskAI={() => { }} canWrite/></section>{row && <Inspector table={table} columns={table.columns} row={row} selectionSize={selection.size} api={api} apiPage={apiPage} apiRaw={apiRaw} scopes={['xelibrary:read', 'xelibrary:write']} jobs={[]} jobsError={null} canAdmin={false} onReloadJobs={() => { }} onReindex={() => { }} conflict={conflict} onSave={save} onDelete={async (revision) => { if (row)
    await api(`/tables/${tableId}/rows/${row._row_id}`, { method: 'DELETE', headers: { 'If-Match': `"revision:${revision}"` } }); }} onRestore={async (revision, currentRevision) => { if (!row)
    return; const saved = await api(`/tables/${tableId}/rows/${row._row_id}/restore`, { method: 'POST', headers: { 'If-Match': `"revision:${currentRevision}"` }, body: JSON.stringify({ revision }) }); setRows(old => old.map(r => r._row_id === saved._row_id ? saved : r)); }} onCreate={async () => { }} onReloadRow={reload} onToggleFavorite={() => { }} onClose={() => setActive(null)}/>}</main> : <p>loading</p>; }
createRoot(document.getElementById('root')!).render(<App />);
