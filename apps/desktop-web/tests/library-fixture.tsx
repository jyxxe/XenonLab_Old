import React, {useEffect, useState} from 'react';
import {createRoot} from 'react-dom/client';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/shell/shell.css';
import '../src/style.css';
import {APIRequestError} from '../src/api-error.js';
import {LibraryApp} from '../src/library/LibraryApp';

// Synthetic XeLibrary backend used by browser acceptance. It mirrors the real
// response envelope ({data, meta}), error codes and revision semantics so the
// application can be exercised without touching a real deployment.

type Call = {method: string; path: string; body: any};
const calls: Call[] = [];
const theoremTitle = (fallback: string) => new URLSearchParams(location.search).get('theoremTitle') ?? fallback;

const COLUMNS = [
  {name: 'title', type: 'string', nullable: false, default_value: null},
  {name: 'body', type: 'text', nullable: true, default_value: null},
  {name: 'type', type: 'enum', nullable: true, default_value: null},
  {name: 'confidence', type: 'float64', nullable: true, default_value: null},
  {name: 'files', type: 'attachment_list', nullable: true, default_value: null},
];

const ROWS = Array.from({length: 101}, (_, index) => ({
  _row_id: `row-${String(index + 1).padStart(3, '0')}`,
  _revision: index === 0 ? 3 : 1,
  _created_at: '2026-09-01T00:00:00Z',
  _updated_at: `2026-09-${String((index % 28) + 1).padStart(2, '0')}T00:00:00Z`,
  _deleted_at: index === 3 ? '2026-09-20T00:00:00Z' : null,
  values: {
    title: index === 0 ? '一致收敛定义' : index === 100 ? '!global-first' : `Synthetic row ${index + 1}`,
    body: `记录 ${index + 1} 的正文内容，用于浏览器验收。`,
    type: index % 2 ? 'note' : 'theorem',
    confidence: index % 2 ? null : 0.91,
    files: index === 0 ? [{attachment_id: 'att-1', filename: 'proof.pdf'}] : [],
  },
}));

const fixtureParams = new URLSearchParams(location.search);

const state = {
  subject: fixtureParams.get('subject') === '__none__' ? '' : (fixtureParams.get('subject') || 'fixture-user'),
  // Default fixture identity is fully authorized for every library feature;
  // `?noTheoremRead=1` simulates an account without xelibrary:theorem:read.
  scopes: (['xelibrary:read', 'xelibrary:write', 'xelibrary:attach', 'xelibrary:query', 'xelibrary:admin', 'xelibrary:theorem:read', 'xelibrary:theorem:propose', 'xelibrary:theorem:promote', 'dev:read'] as string[]).filter(
    scope => !(fixtureParams.get('noTheoremRead') === '1' && scope === 'xelibrary:theorem:read'),
  ),
  conflict: false,
  slow: false,
  denied: false,
  reindexFailure: '',
  delayTheoremId: '',
  denyTheoremRelations: false,
  delayJobsTable: '',
  slowRowLookup: false,
  rows: ROWS.map(row => ({...row})),
  calls,
  saved: [] as Array<{rowId: string; values: Record<string, any>; revision: number}>,
  moved: '',
  renamed: '',
  created: [] as any[],
  chatKind: 'iframe' as 'iframe' | 'native',
  openedApp: '',
  notices: [] as string[],
  searchPool: Number(new URLSearchParams(location.search).get('searchPool') || 0) || 0,
};

async function route(path: string, init: RequestInit = {}): Promise<{data: any; meta: Record<string, any>}> {
  const method = (init.method || 'GET').toUpperCase();
  const body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined;
  calls.push({method, path, body});
  const url = new URL(path, 'http://fixture.local');
  const clean = url.pathname;

  if (clean === '/users/me') {
    return {data: {subject: state.subject || null, scopes: state.scopes}, meta: {}};
  }
  if (clean === '/tables' && method === 'GET') {
    return {data: [
      {id: 't1', path: '/数学/分析', name: 'uniform-convergence', owner: 'fixture-user'},
      {id: 't2', path: '/计算机', name: 'notes', owner: 'fixture-user'},
    ], meta: {next_cursor: null}};
  }
  if (clean === '/tables' && method === 'POST') {
    state.created.push(body);
    return {data: {table_id: 't3', path: body.path, name: body.name, revision: 1}, meta: {}};
  }
  if ((clean === '/tables/t1/schema' || clean === '/tables/t1') && method === 'GET') {
    return {data: {id: 't1', path: '/数学/分析', name: 'uniform-convergence', table_revision: 12, schema_revision: 2, columns: COLUMNS}, meta: {}};
  }
  if ((clean === '/tables/t2/schema' || clean === '/tables/t2') && method === 'GET') {
    return {data: {id: 't2', path: '/计算机', name: 'notes', table_revision: 4, schema_revision: 1, columns: [{name: 'note', type: 'text', nullable: true, default_value: null}]}, meta: {}};
  }
  if (clean === '/tables/t1/rows' && method === 'GET') {
    if (state.slow) await new Promise(resolve => setTimeout(resolve, 450));
    if (state.denied) throw new APIRequestError('path permission denied', 403, 'XL_FORBIDDEN');
    const includeDeleted = url.searchParams.get('include_deleted') === 'true';
    const list = [...state.rows, {_row_id: 'row-extra', _revision: 1, _created_at: '2026-09-01T00:00:00Z', _updated_at: '2026-09-01T00:00:00Z', _deleted_at: null, values: {title: '追加页记录', body: 'page 2', type: 'note', confidence: null, files: []}}]
      .filter(row => includeDeleted || !row._deleted_at);
    const sortBy = url.searchParams.get('sort_by');
    const direction = url.searchParams.get('direction') === 'desc' ? -1 : 1;
    if (sortBy) {
      const value = (row: any) => sortBy.startsWith('_') ? row[sortBy] : row.values[sortBy];
      list.sort((left, right) => {
        const a = value(left);
        const b = value(right);
        const compare = a === b ? 0 : a === null || a === undefined ? -1 : b === null || b === undefined ? 1 : a > b ? 1 : -1;
        return compare ? direction * compare : left._row_id.localeCompare(right._row_id);
      });
    } else {
      list.sort((left, right) => left._row_id.localeCompare(right._row_id));
    }
    const limit = Number(url.searchParams.get('limit') || 100);
    const cursor = url.searchParams.get('cursor');
    const pageNumber = cursor ? Number(cursor.split('page-').at(-1) || 2) : 1;
    const offset = (pageNumber - 1) * limit;
    const page = list.slice(offset, offset + limit);
    return {data: page, meta: {next_cursor: offset + page.length < list.length ? `rows-page-${pageNumber + 1}` : null}};
  }
  if (clean === '/tables/t2/rows' && method === 'GET') {
    return {data: [{_row_id: 't2-row-1', _revision: 1, _created_at: '2026-09-01T00:00:00Z', _updated_at: '2026-09-01T00:00:00Z', _deleted_at: null, values: {note: '另一张表的记录'}}], meta: {next_cursor: null}};
  }
  if (clean === '/tables/t1/rows' && method === 'POST') {
    const created = {_row_id: 'row-new', _revision: 1, _created_at: '2026-10-01T00:00:00Z', _updated_at: '2026-10-01T00:00:00Z', _deleted_at: null, values: body.values};
    state.rows = [created, ...state.rows];
    return {data: created, meta: {request_id: 'req-create'}};
  }
  if (clean.startsWith('/tables/t1/rows/') && clean.endsWith('/revisions')) {
    const rowId = clean.split('/')[4];
    return {data: [
      {_row_id: rowId, _revision: 1, _created_at: '2026-09-01T00:00:00Z', _updated_at: '2026-09-01T00:00:00Z', _deleted_at: null, values: {title: '旧标题', body: '旧正文', type: 'note', confidence: null, files: []}},
      {_row_id: rowId, _revision: 2, _created_at: '2026-09-02T00:00:00Z', _updated_at: '2026-09-02T00:00:00Z', _deleted_at: null, values: {title: '新标题', body: '新正文', type: 'note', confidence: 0.5, files: []}},
    ], meta: {}};
  }
  if (clean.startsWith('/tables/t1/rows/') && clean.endsWith('/attachments')) {
    const attachmentRow = clean.split('/')[4];
    return {data: [
      {attachment_id: 'att-1', row_id: attachmentRow, filename: 'proof.pdf', media_type: 'application/pdf', size: 2048, created_at: '2026-09-10T00:00:00Z', metadata: {}},
      {attachment_id: 'att-2', row_id: attachmentRow, filename: 'image.png', media_type: 'image/png', size: 128, created_at: '2026-09-10T00:01:00Z', metadata: {}},
    ], meta: {}};
  }
  if (clean.startsWith('/tables/t1/rows/') && method === 'PATCH') {
    const rowId = clean.split('/')[4];
    if (state.conflict) {
      throw new APIRequestError('If-Match must equal the current revision', 412, 'STALE_REVISION');
    }
    state.saved.push({rowId, values: body.values, revision: 3});
    const row = state.rows.find(item => item._row_id === rowId);
    if (row) {
      row.values = {...row.values, ...body.values};
      row._revision += 1;
      return {data: {...row}, meta: {request_id: 'req-save'}};
    }
    return {data: {...ROWS[0], values: body.values, _revision: 4}, meta: {request_id: 'req-save'}};
  }
  if (clean.startsWith('/tables/t1/rows/') && method === 'DELETE') {
    const rowId = clean.split('/')[4];
    const row = state.rows.find(item => item._row_id === rowId);
    if (!row) throw new APIRequestError('row not found', 404, 'XL_ROW_NOT_FOUND');
    row._deleted_at = '2026-10-21T00:00:00Z';
    row._revision += 1;
    return {data: {...row}, meta: {request_id: 'req-delete'}};
  }
  if (clean.startsWith('/tables/t1/rows/') && method === 'GET') {
    const rowId = clean.split('/')[4];
    if (state.slowRowLookup && rowId === 'row-001') await new Promise(resolve => setTimeout(resolve, 450));
    const row = state.rows.find(item => item._row_id === rowId);
    if (!row) throw new APIRequestError('row not found', 404, 'XL_ROW_NOT_FOUND');
    return {data: {...row}, meta: {}};
  }
  if (clean === '/tables/t1/move') {
    state.moved = body.path;
    return {data: {table_id: 't1', path: body.path}, meta: {}};
  }
  if (clean === '/tables/t1' && method === 'PATCH') {
    state.renamed = body.name;
    return {data: {table_id: 't1', path: '/数学/分析', name: body.name, table_revision: 14}, meta: {}};
  }
  if (clean === '/tables/t1' && method === 'DELETE') {
    throw new APIRequestError('purge every row before deleting the table', 409, 'TABLE_NOT_EMPTY');
  }
  if (clean === '/tables/t1/schema' && method === 'PATCH') {
    return {data: {schema_revision: 3, table_revision: 13}, meta: {}};
  }
  if (/^\/tables\/[^/]+\/index-status$/.test(clean)) {
    const statusTableId = clean.split('/')[2] || 't1';
    if (state.delayJobsTable === statusTableId) await new Promise(resolve => setTimeout(resolve, 450));
    return {data: {table_id: statusTableId, state: 'ready', row_count: 100, embedded_rows: 100, missing_rows: 0, pending_rows: 0, chunk_count: 137, last_job: {job_id: statusTableId === 't2' ? 'job-t2' : 'job-9', owner: 'fixture-user', table_id: statusTableId, status: 'completed', created_at: '2026-09-30T00:00:00Z', updated_at: '2026-09-30T00:05:00Z'}}, meta: {}};
  }
  if (clean === '/jobs') {
    const jobTableId = url.searchParams.get('table_id') || 't1';
    if (state.delayJobsTable === jobTableId) await new Promise(resolve => setTimeout(resolve, 450));
    return {data: [{job_id: jobTableId === 't2' ? 'job-t2' : 'job-9', owner: 'fixture-user', table_id: jobTableId, status: 'completed', created_at: '2026-09-30T00:00:00Z', updated_at: '2026-09-30T00:05:00Z'}], meta: {counts: {completed: 1}}};
  }
  if (clean === '/acl/table/t1' || clean.startsWith('/acl/path/') || clean.startsWith('/acl/row/')) {
    return {data: {resource_type: 'table', resource_id: 't1', explicit: clean.startsWith('/acl/path/') ? false : true, grants: clean.startsWith('/acl/path/') ? {} : {'role:reader': ['read']}, revision: 1}, meta: {}};
  }
  if (clean === '/search/semantic') {
    if (state.delaySearch) await new Promise(resolve => setTimeout(resolve, state.delaySearch));
    if (body.mode === 'quality') throw new APIRequestError('quality worker not configured', 503, 'QWEN_UNAVAILABLE');
    const hit = (index: number) => ({
      table_id: 't1', row_id: `row-${String(index + 1).padStart(3, '0')}`, row_revision: 3, path: '/数学/分析',
      score: Number((0.87 - index * 0.001).toFixed(3)), score_type: 'd100m_cosine',
      snippet: `一致收敛定义：如果 fn 一致收敛于 f…（${index + 1}）`, approximate: true, indexed_revision: 2,
    });
    const data = state.searchPool > 0
      ? Array.from({length: Math.min(body.limit, state.searchPool)}, (_, index) => hit(index))
      : [hit(0)];
    return {
      data,
      meta: {partial: true, missing_embeddings: 2341, index_backend: 'qdrant_fast_text', model_revision: 'd100m-1'},
    };
  }
  if (clean === '/search/text') {
    return {data: [{table_id: 't1', row_id: 'row-002', row_revision: 1, path: '/数学/分析', score: 4.2, score_type: 'sqlite_fts_bm25', snippet: '关键词命中：一致收敛', approximate: false}], meta: {partial: false}};
  }
  if (clean === '/query/validate' || clean === '/query/explain') {
    if (/BOGUS/i.test(body.expression)) throw new APIRequestError('invalid or unsupported Query DSL', 422, 'INVALID_QUERY');
    return {data: {path: '/**', ast: null, limit: 100, offset: 0, order_by: null, descending: false, execution: 'bounded AST interpreter', approximate: false}, meta: {}};
  }
  if (clean === '/query') {
    if (/BOGUS/i.test(body.expression)) throw new APIRequestError('invalid or unsupported Query DSL', 422, 'INVALID_QUERY');
    return {data: [{table_id: 't1', path: '/数学/分析', _row_id: 'row-001', _revision: 3, values: {title: '一致收敛定义', type: 'theorem'}}], meta: {approximate: false}};
  }
  if (clean === '/theorems') {
    return {data: [
      {theorem_id: 'thm-1', path: '/theorems/analysis', title: theoremTitle('一致收敛定理'), statement_natural: '一致收敛蕴含逐点收敛。', formal_status: 'none', review_status: 'proposed', confidence: {evidence_score: 0.5, source_score: 0.4, consistency_score: 0.6}, revision: 2, updated_at: '2026-09-25T00:00:00Z'},
      {theorem_id: 'thm-2', path: '/theorems/analysis/uniqueness', title: theoremTitle('极限唯一性定理'), statement_natural: '收敛序列的极限唯一。', formal_status: 'none', review_status: 'proposed', revision: 1, updated_at: '2026-09-26T00:00:00Z'},
    ], meta: {next_cursor: null}};
  }
  if (clean === '/theorems/thm-1') {
    if (state.delayTheoremId === 'thm-1') await new Promise(resolve => setTimeout(resolve, 450));
    return {data: {theorem_id: 'thm-1', path: '/theorems/analysis', title: theoremTitle('一致收敛定理'), statement_natural: '一致收敛蕴含逐点收敛。', assumptions: ['f_n 连续'], proof_natural: '取 N 为一致收敛的阈值。', formal_status: 'none', review_status: 'proposed', confidence: {evidence_score: 0.5, source_score: 0.4, consistency_score: 0.6}, revision: 2, updated_at: '2026-09-25T00:00:00Z'}, meta: {}};
  }
  if (clean === '/theorems/thm-2') {
    return {data: {theorem_id: 'thm-2', path: '/theorems/analysis/uniqueness', title: theoremTitle('极限唯一性定理'), statement_natural: '收敛序列的极限唯一。', formal_status: 'none', review_status: 'proposed', revision: 1, updated_at: '2026-09-26T00:00:00Z'}, meta: {}};
  }
  if (clean === '/theorems/thm-1/dependencies' || clean === '/theorems/thm-1/dependents') {
    if (state.denyTheoremRelations) throw new APIRequestError('theorem relation permission denied', 403, 'XL_FORBIDDEN');
    return {data: [], meta: {}};
  }
  if (clean === '/theorems/thm-2/dependencies' || clean === '/theorems/thm-2/dependents') {
    if (state.denyTheoremRelations) throw new APIRequestError('theorem relation permission denied', 403, 'XL_FORBIDDEN');
    return {data: [], meta: {}};
  }
  if (clean === '/theorems/thm-1/evidence') {
    if (state.denyTheoremRelations) throw new APIRequestError('theorem relation permission denied', 403, 'XL_FORBIDDEN');
    return {data: {evidence_refs: ['row-001'], revision: 2}, meta: {}};
  }
  if (clean === '/theorems/thm-2/evidence') {
    if (state.denyTheoremRelations) throw new APIRequestError('theorem relation permission denied', 403, 'XL_FORBIDDEN');
    return {data: {evidence_refs: [], revision: 1}, meta: {}};
  }
  if (clean === '/theorems/search') {
    return {data: [{theorem_id: 'thm-1', path: '/theorems/analysis', title: theoremTitle('一致收敛定理'), statement_natural: '一致收敛蕴含逐点收敛。', formal_status: 'none', review_status: 'proposed', revision: 2}], meta: {partial: false}};
  }
  if (/^\/tables\/t1\/rows:bulkUpdate$/.test(clean)) {
    calls.push({method: 'POST', path: clean, body});
    return {data: {mode: 'sync', job_id: 'bulk-job-1', status: 'completed', total: (body?.rows ?? []).length, done: (body?.rows ?? []).length, failed: 0, results: (body?.rows ?? []).map((row: any) => ({row_id: row.row_id, revision: 4}))}, meta: {request_id: 'req-bulk'}};
  }
  if (clean === '/index/reindex') {
    if (state.reindexFailure) throw new APIRequestError('embedding worker unreachable', 503, 'EMBEDDING_UNAVAILABLE', {requestId: state.reindexFailure === 'correlated' ? 'req-reindex-failed' : undefined});
    return {data: {job_id: 'job-10'}, meta: {}};
  }
  throw new Error(`unexpected fixture request ${method} ${path}`);
}

const api = async (path: string, init?: RequestInit) => (await route(path, init)).data;
const apiPage = (path: string, init?: RequestInit) => route(path, init);
const PREVIEW_PNG = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=='), ch => ch.charCodeAt(0));
const apiRaw = async (path: string, init?: RequestInit) => {
  calls.push({method: (init?.method || 'GET').toUpperCase(), path, body: undefined, headers: (init?.headers || {}) as Record<string, string>});
  const theoremMatch = path.match(/^\/theorems\/(thm-[0-9]+)$/);
  if (theoremMatch) return new Response(JSON.stringify({data: {theorem_id: theoremMatch[1], title: '一致收敛定理', path: '/theorems/analysis', review_status: 'proposed', formal_status: 'none', revision: 2}, meta: {}}), {status: 200, headers: {'Content-Type': 'application/json', 'ETag': '"revision:2"'}});
  const decisionMatch = path.match(/^\/theorems\/(thm-[0-9]+)\/(promote|reject)$/);
  if (decisionMatch) {
    const status = decisionMatch[2] === 'promote' ? 'promoted' : 'rejected';
    return new Response(JSON.stringify({data: {theorem_id: decisionMatch[1], title: '一致收敛定理', path: '/theorems/analysis', review_status: status, formal_status: 'none', revision: 3}, meta: {}}), {status: 200, headers: {'Content-Type': 'application/json', 'ETag': '"revision:3"'}});
  }
  if (path.includes('/preview')) {
    return new Response(PREVIEW_PNG, {status: 200, headers: {'Content-Type': 'image/png', 'X-Content-Type-Options': 'nosniff'}});
  }
  return new Response('{}', {status: 200, headers: {'Content-Type': 'application/json'}});
};

const appIntents: Array<Record<string, unknown>> = [];

(window as any).libraryFixture = {
  calls,
  state,
  appIntents,
  resetCalls: () => calls.splice(0, calls.length),
};

// Mirrors the desktop shell wiring: a registered chat iframe app and an
// openApp that records intents instead of opening windows. Toggling
// window.libraryFixture.state.chatKind forces React to re-read the list.
function ShellFixture() {
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    let last = state.chatKind;
    const timer = setInterval(() => {
      if (state.chatKind !== last) {
        last = state.chatKind;
        setRevision(value => value + 1);
      }
    }, 100);
    return () => clearInterval(timer);
  }, []);
  const apps = revision >= 0 ? [
    {id: 'chat', title: 'AI 对话', icon: '✦', kind: state.chatKind, origin: 'https://chat.fixture.local', entry: '/', permissions: [], defaultSize: [1080, 780], minSize: [720, 520], singleton: true, dock: true},
    {id: 'knowledge', title: '知识库', icon: '▤', kind: 'native', permissions: [], defaultSize: [1180, 780], minSize: [820, 520], singleton: true, dock: true},
  ] : [];
  return (
    <LibraryApp
      api={api}
      apiPage={apiPage}
      apiRaw={apiRaw}
      apps={apps as any}
      openApp={(id: string, intent?: Record<string, unknown>) => {
        appIntents.push({id, ...(intent || {})});
        state.openedApp = id;
      }}
      notify={(toast: {title: string}) => {
        state.notices = [...(state.notices || []), toast.title];
      }}
    />
  );
}

createRoot(document.getElementById('root')!).render(<ShellFixture />);
