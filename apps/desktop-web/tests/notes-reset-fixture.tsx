import React from 'react';
import {createRoot} from 'react-dom/client';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/shell/shell.css';
import '../src/style.css';
import {ToastProvider} from '../src/design-system/primitives';
import {DesktopShell} from '../src/shell/DesktopShell';
import {apiErrorFromResponse} from '../src/api-error.js';

// This token belongs only to the owned loopback test API, never production OIDC.
const apiRaw = async (path: string, init: RequestInit = {}) => {
  const headers = new Headers(init.headers);
  headers.set('Authorization', 'Bearer issue242-synthetic-token');
  if (init.body) headers.set('Content-Type', 'application/json');
  return fetch(`/api/v1${path}`, {...init, headers});
};
const apiPage = async (path: string, init: RequestInit = {}) => {
  const response = await apiRaw(path, init);
  const body = await response.json();
  if (!response.ok) throw apiErrorFromResponse(response, body);
  return body;
};
const api = async (path: string, init?: RequestInit) => (await apiPage(path, init)).data;
createRoot(document.getElementById('root')!).render(
  <ToastProvider><DesktopShell api={api} apiPage={apiPage} apiRaw={apiRaw} onLogout={async () => {}} /></ToastProvider>,
);
