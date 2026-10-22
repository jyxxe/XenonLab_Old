import React, {useMemo, useState} from 'react';
import {createRoot} from 'react-dom/client';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/style.css';
import {APIRequestError} from '../src/api-error.js';
import {LibraryApp} from '../src/library/LibraryApp';
const calls: any[] = [];
(window as any).recentAudit = {calls};
function Harness() {
  const [owner, setOwner] = useState('recent-human-a');
  const [epoch, setEpoch] = useState(0);
  const client = useMemo(() => {
    const apiPage = async (path: string, init: RequestInit = {}) => {
      if (path === '/users/me') return {data: {subject: owner, kind: 'human', scopes: ['xelibrary:read', 'xelibrary:write']}, meta: {}};
      const headers = new Headers(init.headers); headers.set('Authorization', `Bearer ${owner}`);
      if (init.body) headers.set('Content-Type', 'application/json');
      const response = await fetch('/api/v1' + path, {...init, headers});
      const payload = await response.json(); calls.push({owner, path, method: init.method || 'GET', status: response.status});
      if (!response.ok) {const e = payload.error; throw new APIRequestError(e.message, response.status, e.code, e.details || {});}
      return payload;
    };
    const api = async (path: string, init?: RequestInit) => (await apiPage(path, init)).data;
    const apiRaw = async (path: string, init: RequestInit = {}) => {const headers = new Headers(init.headers); headers.set('Authorization', `Bearer ${owner}`); return fetch('/api/v1' + path, {...init, headers});};
    return {api, apiPage, apiRaw};
  }, [owner]);
  return <><button onClick={() => {setOwner(owner === 'recent-human-a' ? 'recent-human-b' : 'recent-human-a'); setEpoch(value => value + 1);}}>Fixture switch owner ({owner})</button><LibraryApp {...client} authEpoch={epoch}/></>;
}
createRoot(document.getElementById('root')!).render(<Harness/>);
