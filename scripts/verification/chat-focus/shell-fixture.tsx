import * as React from 'react';
import {createRoot} from 'react-dom/client';
import '../../../apps/desktop-web/src/design-system/tokens.css';
import '../../../apps/desktop-web/src/design-system/primitives.css';
import '../../../apps/desktop-web/src/shell/shell.css';
import '../../../apps/desktop-web/src/style.css';
import {ToastProvider} from '../../../apps/desktop-web/src/design-system/primitives';
import {DesktopShell} from '../../../apps/desktop-web/src/shell/DesktopShell';

const TABLE_ID = '11111111-1111-4111-8111-111111111111';
const ROW_IDS = ['22222222-2222-4222-8222-222222222222', '33333333-3333-4333-8333-333333333333'];
const calls: Array<{method: string; path: string; body?: any}> = [];
const columns = [
  {name: 'title', type: 'string', nullable: false, default_value: null},
  {name: 'body', type: 'text', nullable: true, default_value: null},
  {name: 'type', type: 'enum', nullable: true, default_value: null},
];
const rows = [
  {_row_id: ROW_IDS[0], _revision: 1, _created_at: '2026-10-01T00:00:00Z', _updated_at: '2026-10-01T00:00:00Z', _deleted_at: null, values: {title: 'Synthetic convergence note', body: 'Fixture body only; never production data.', type: 'note'}},
  {_row_id: ROW_IDS[1], _revision: 1, _created_at: '2026-10-01T00:00:00Z', _updated_at: '2026-10-01T00:00:00Z', _deleted_at: null, values: {title: 'Synthetic comparison row', body: 'Second fixture row for the selection workflow.', type: 'note'}},
];
const preferences:any = {theme: 'dark', accent: 'blue', wallpaper: 'aurora', timezone: 'UTC', widgets: ['clock'], shortcut_apps: [], dock_size: 'medium', dock_autohide: false, restore_windows: true, animations: true, reduced_motion: false};
const layout:any = {dock: [], windows: [], dock_initialized: false};

async function api(path: string, init: RequestInit = {}): Promise<any> {
  const method = (init.method || 'GET').toUpperCase();
  const body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined;
  calls.push({method, path, body});
  if (path === '/users/me') return {subject: 'synthetic-human', scopes: ['xelibrary:read', 'xelibrary:write', 'xelibrary:query', 'xelibrary:admin', 'dev:read']};
  if (path === '/apps' || path === '/apps?include_unavailable=true') return [];
  if (path === '/users/me/preferences') {if (method === 'PATCH') Object.assign(preferences, body); return {...preferences};}
  if (path === '/users/me/layout') {if (method === 'PUT') Object.assign(layout, body); return {...layout};}
  if (path === '/tables?limit=100') return {data: [{id: TABLE_ID, name: 'research-notes', path: '/fixture/research'}], meta: {next_cursor: null}};
  if (path === `/tables/${TABLE_ID}/schema`) return {id: TABLE_ID, name: 'research-notes', path: '/fixture/research', table_revision: 1, schema_revision: 1, columns};
  if (path.startsWith(`/tables/${TABLE_ID}/rows?`)) return {data: rows, meta: {next_cursor: null}};
  if (path === `/jobs?table_id=${TABLE_ID}&limit=20`) return {data: [], meta: {counts: {queued: 0, running: 0, completed: 0, failed: 0}}};
  if (path.startsWith(`/tables/${TABLE_ID}/rows/`) && path.endsWith('/attachments')) return {data: [], meta: {}};
  if (path.startsWith(`/tables/${TABLE_ID}/rows/`)) {
    const id = path.split('/')[4];
    const row = rows.find(item => item._row_id === id);
    if (!row) throw new Error('fixture row missing');
    return {...row};
  }
  if (path.startsWith('/acl/')) return {data: {resource_type: 'table', resource_id: TABLE_ID, explicit: false, grants: {}, revision: 1}, meta: {}};
  throw new Error(`Unexpected synthetic API request: ${method} ${path}`);
}

const apiPage = async (path: string, init?: RequestInit) => {
  const result = await api(path, init);
  return result && Array.isArray(result.data) ? {data: result.data, meta: result.meta || {}} : {data: result, meta: {}};
};
const apiRaw = async (path: string, init?: RequestInit) => {
  const result = await api(path, init);
  return new Response(JSON.stringify(result), {status: 200, headers: {'Content-Type': 'application/json'}});
};
(window as any).round18Fixture = {calls, rows, TABLE_ID, ROW_IDS};
createRoot(document.getElementById('root')!).render(
  <ToastProvider>
    <DesktopShell api={api} apiPage={apiPage} apiRaw={apiRaw} onLogout={async () => {}} />
  </ToastProvider>,
);
