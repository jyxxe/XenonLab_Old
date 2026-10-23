import React from 'react';
import { createRoot } from 'react-dom/client';
import './src/design-system/tokens.css';
import './src/design-system/primitives.css';
import './src/shell/shell.css';
import './src/style.css';
import { LibraryApp } from './src/library/LibraryApp';
import { apiErrorFromResponse } from './src/api-error.js';
const token = 'round6-local-test-token';
const control={holdStatus:'',failStatus:'',started:0,settled:0,pending:[] as Array<()=>void>,calls:[] as any[]};
(window as any).theoremFilterFixture=control;
async function raw(path: string, init: RequestInit = {}) { const headers = new Headers(init.headers); headers.set('Authorization', `Bearer ${token}`); if (init.body)
    headers.set('Content-Type', 'application/json'); return fetch(`/api/v1${path}`, { ...init, headers }); }
async function parse(path: string, init: RequestInit = {}) { const body=init.body?JSON.parse(String(init.body)):null;control.calls.push({path,body,method:init.method||'GET'});if(path==='/theorems/search'&&body.review_status===control.failStatus&&control.failStatus)throw Error('synthetic search unavailable');const response = await raw(path, init); const payload = await response.json(); if (!response.ok)
    throw apiErrorFromResponse(response, payload); if(path==='/theorems/search'&&body.review_status===control.holdStatus&&control.holdStatus){control.started++;await new Promise<void>(r=>control.pending.push(r));control.settled++;}return payload; }
const api = async (path: string, init?: RequestInit) => (await parse(path, init)).data;
const apiPage = async (path: string, init?: RequestInit) => parse(path, init);
function App() { return <div className="knowledge"><LibraryApp api={async (path, init) => path === '/users/me' ? { subject: 'round12-synthetic-user', scopes: ['xelibrary:read','xelibrary:theorem:read'] } : api(path, init)} apiPage={apiPage} apiRaw={raw}/></div>; }
createRoot(document.getElementById('root')!).render(<App />);
