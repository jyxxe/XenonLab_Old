import React from 'react';
import {createRoot} from 'react-dom/client';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/shell/shell.css';
import '../src/style.css';
import {Accounts} from '../src/Accounts';

// Only the currently authorized synthetic subtree is returned. No real account,
// credential or write endpoint is used by this navigation fixture.
const scopes = ['accounts:manage', 'desktop:admin', 'desktop:read'];
const root = {id: 'fixture-root', parent: null, name: 'Root Administrator', username: 'fixture-admin', enabled: true, inherit_all: false, grants: scopes, effective_scopes: scopes, kind: 'human'};
const rows = [root, ...Array.from({length: 60}, (_, index) => ({
  ...root, id: `fixture-child-${index + 1}`, parent: root.id,
  name: index === 59 ? 'Research TEAM 最后账号' : `团队 ${index + 1}`,
  username: `Scientist-${String(index + 1).padStart(2, '0')}`,
  grants: ['desktop:read'], effective_scopes: ['desktop:read'],
}))];
const calls: Array<{path: string; method: string}> = [];
(window as any).accountFilterFixture = {calls, rows};
async function api(path: string, init: RequestInit = {}) {
  const method = init.method || 'GET';
  calls.push({path, method});
  if (method !== 'GET') throw Error('Navigation fixture never accepts writes');
  if (path === '/users/me') return {subject: root.id, kind: 'human', scopes};
  if (path.startsWith('/auth/accounts?')) return rows;
  if (path === '/auth/accounts/root-scope-candidates') return {candidate_scopes: []};
  if (path.startsWith('/auth/api-keys?owner=')) {
    const owner = decodeURIComponent(path.split('owner=')[1]);
    return [{id: `key-${owner}`, name: `Key for ${owner}`, scopes: ['desktop:read'], revoked: false, created: 0}];
  }
  throw Error(`Unexpected synthetic request: ${path}`);
}
createRoot(document.getElementById('root')!).render(<Accounts api={api} />);
