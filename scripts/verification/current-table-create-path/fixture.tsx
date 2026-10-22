import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/shell/shell.css';
import '../src/style.css';
import { APIRequestError } from '../src/api-error.js';
import { LibraryApp } from '../src/library/LibraryApp';
const token = 'create-path-local-admin';
const calls: any[] = [];
async function page(path: string, init: RequestInit = {}) { const method = (init.method || 'GET').toUpperCase(); const headers = new Headers(init.headers || {}); headers.set('Authorization', 'Bearer ' + token); if (init.body && !headers.has('Content-Type'))
    headers.set('Content-Type', 'application/json'); const trace = { method, path, headers: Object.fromEntries(headers.entries()), body: typeof init.body === 'string' ? JSON.parse(init.body) : undefined }; calls.push(trace); const response = await fetch(path === '/users/me' ? '/desktop-api/api/v1/users/me' : '/api/v1' + path, { ...init, headers }); const payload = await response.json().catch(() => ({})); trace.status = response.status; trace.responsePayload = payload; if (!response.ok) {
    const err = payload.error || {};
    throw new APIRequestError(err.message || response.statusText, response.status, err.code || 'HTTP_ERROR', err.details || {});
} return payload; }
const api = async (path: string, init?: RequestInit) => (await page(path, init)).data;
const apiPage = page;
const apiRaw = async (path: string, init: RequestInit = {}) => { const headers = new Headers(init.headers || {}); headers.set('Authorization', 'Bearer ' + token); return fetch(path === '/users/me' ? '/desktop-api/api/v1/users/me' : '/api/v1' + path, { ...init, headers }); };
(window as any).createPathAudit = { calls };
createRoot(document.getElementById('root')!).render(<LibraryApp api={api} apiPage={apiPage} apiRaw={apiRaw} authEpoch={0}/>);
