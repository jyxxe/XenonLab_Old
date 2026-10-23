import React, {useState} from 'react';
import {createRoot} from 'react-dom/client';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/style.css';
import '../src/library/library.css';
import {APIRequestError} from '../src/api-error.js';
import {parseAPIJSON} from '../src/library/json.js';
import {LibraryApp} from '../src/library/LibraryApp';
import {QueryConsole} from '../src/library/QueryConsole';

// Synthetic localhost acceptance only. No production identity or data.
const calls: Array<{path: string; method: string; body?: string}> = [];
async function apiPage(path: string, init: RequestInit = {}) {
  calls.push({path, method: init.method || 'GET', body: init.body as string | undefined});
  const response = await fetch('/api/v1' + path, {...init, headers: {'Authorization': 'Bearer query-copy-synthetic', 'Content-Type': 'application/json', ...init.headers}});
  const body = parseAPIJSON(path, await response.text());
  if (!response.ok) throw new APIRequestError(body.error.message, response.status, body.error.code);
  if (path === '/query') (window as any).queryCopyFixture.lastQuery = body;
  return body;
}
const api = async (path: string, init?: RequestInit) => (await apiPage(path, init)).data;
const firstRows = Array.from({length: 3}, (_, index) => ({table_id: 'unknown-table-complete-123456789', path: '/synthetic', _row_id: `complete-first-page-row-${index}`, _revision: 1, values: {page: 'first', index}}));
const nextRows = [{table_id: 'second-table', path: '/synthetic/next', _row_id: 'complete-second-page-row', _revision: 2, values: {page: 'next'}}];
const initial = {expression: 'FROM "/synthetic/**" LIMIT 3', rows: firstRows, running: false, validating: false, error: null as unknown, queryEnabled: true, mounted: true, plan: {path: '/synthetic/**', ast: null, limit: 3, offset: 0, order_by: null, descending: false, execution: 'synthetic', approximate: false}};

function Fixture() {
  const [state, setState] = useState(initial);
  (window as any).queryCopyFixture = {calls, set: (change: Partial<typeof initial>) => setState(current => ({...current, ...change})), reset: () => setState({...initial}), state};
  if (new URLSearchParams(location.search).get('mode') !== 'direct') return <LibraryApp api={api} apiPage={apiPage} />;
  return <main style={{padding: 16}}><p>Synthetic Query Console fixture</p>{state.mounted && <QueryConsole
    {...state} meta={{approximate: false}} duration={1} recent={[]} draftStatus="ready" draftMessage=""
    onExpression={expression => setState(current => ({...current, expression, rows: []}))}
    onRun={() => setState(current => ({...current, rows: nextRows, plan: {...current.plan, offset: 3}}))}
    onValidate={() => undefined} onOpenRow={() => undefined} onLoadRecent={() => undefined}
    onDeleteRecent={() => undefined} onClearRecent={() => undefined} onRetryStorage={() => undefined}
    tableName={id => id.slice(0, 8)} tableNameForCopy={id => id}
  />}</main>;
}
createRoot(document.getElementById('root')!).render(<Fixture />);
