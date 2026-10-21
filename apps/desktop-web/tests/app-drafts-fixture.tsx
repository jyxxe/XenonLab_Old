import React from 'react';
import {createRoot} from 'react-dom/client';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/shell/shell.css';
import '../src/style.css';
import {AppRegistry} from '../src/AppRegistry';
import {apiErrorFromResponse} from '../src/api-error.js';

const api = async (path: string, init: RequestInit = {}) => {
  const headers = new Headers(init.headers);
  headers.set('Authorization', 'Bearer issue244-synthetic-token');
  if (init.body) headers.set('Content-Type', 'application/json');
  const response = await fetch(`/api/v1${path}`, {...init, headers});
  const body = await response.json();
  if (!response.ok) throw apiErrorFromResponse(response, body);
  return body.data;
};
createRoot(document.getElementById('root')!).render(<AppRegistry api={api} changed={async () => {}} />);
