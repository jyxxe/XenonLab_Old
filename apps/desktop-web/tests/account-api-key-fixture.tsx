import React from 'react';
import {createRoot} from 'react-dom/client';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/shell/shell.css';
import '../src/style.css';
import {Accounts} from '../src/Accounts';

// Synthetic account tree and one-time key issuance. No real credential writes.
const scopes = ['accounts:manage', 'desktop:admin', 'desktop:read'];
const root = {id: 'fixture-root', parent: null, name: 'Root Administrator', username: 'fixture-admin', enabled: true, inherit_all: false, grants: scopes, effective_scopes: scopes, kind: 'human'};
const child = {...root, id: 'fixture-child', parent: root.id, name: 'Child Account', username: 'fixture-child', grants: ['desktop:read'], effective_scopes: ['desktop:read']};
const rows = [root, child];
const calls: Array<{path: string; method: string}> = [];
(window as any).accountApiKeyFixture = {calls};
async function api(path: string, init: RequestInit = {}) {
  const method = init.method || 'GET';
  calls.push({path, method});
  if (path === '/users/me') return {subject: root.id, kind: 'human', scopes};
  if (path.startsWith('/auth/accounts?')) return rows;
  if (path === '/auth/accounts/root-scope-candidates') return {candidate_scopes: []};
  if (path.startsWith('/auth/api-keys?owner=')) {
    const owner = decodeURIComponent(path.split('owner=')[1]);
    return [{id: `key-${owner}`, name: `Key for ${owner}`, scopes: ['desktop:read'], revoked: false, created: 0}];
  }
  if (path === '/auth/api-keys' && method === 'POST') return {token: 'xek-ONLY-ONCE-SECRET', id: 'fixture-key'};
  throw Error(`Unexpected synthetic request: ${path}`);
}
createRoot(document.getElementById('root')!).render(<Accounts api={api} />);
