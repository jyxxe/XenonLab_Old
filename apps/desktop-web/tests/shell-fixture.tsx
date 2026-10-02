import React from 'react';
import {createRoot} from 'react-dom/client';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/shell/shell.css';
import '../src/style.css';
import {ToastProvider} from '../src/design-system/primitives';
import {DesktopShell} from '../src/shell/DesktopShell';

// The shell talks to the platform through injected functions; this fixture keeps the
// same surface with deterministic, synthetic data so browser acceptance runs offline.
type Call = {method: string; path: string; body: any};
const calls: Call[] = [];
const registry:any[]=[];
const tables=[
  {id:'example-table',name:'部署验收',path:'/示例/平台'},
];
const control={failLayout:false,failPreferences:false,failWork:false,delayTables:false,tablesInFlight:0};
const stored = (key: string, fallback: any) => {
  try {
    const value = sessionStorage.getItem(`shell-fixture:${key}`);
    return value ? JSON.parse(value) : fallback;
  } catch {
    return fallback;
  }
};
const preferences = stored('preferences', {
  theme: 'dark',
  accent: 'blue',
  wallpaper: 'aurora',
  custom_wallpaper_available: false,
  timezone: 'UTC',
  widgets: ['clock', 'status'],
  shortcut_apps: [],
  dock_size: 'medium',
  dock_autohide: false,
  restore_windows: true,
  animations: true,
  reduced_motion: false,
});
const layout: {dock: string[]; windows: any[];dock_initialized?:boolean} = stored('layout', {dock: [], windows: []});
const work = Array.from({length: 5}, (_, index) => ({
  id: `fixture-${index + 1}`,
  repository: 'xenon-admin/XeonoLab',
  issue_id: 70 + index,
  title: `Synthetic issue ${70 + index}`,
  status: ['claimed', 'queued', 'claimed', 'completed', 'blocked'][index],
  holder: index === 0 ? 'fixture-user-0001' : index === 2 ? 'expired-agent' : null,
  expires: 0,
  epoch: 1,
  claim_active: index === 0,
}));

async function api(path: string, init: RequestInit = {}): Promise<any> {
  const method = (init.method || 'GET').toUpperCase();
  const body = typeof init.body === 'string' ? JSON.parse(init.body) : undefined;
  calls.push({method, path, body});
  if (path === '/users/me') {
    return {subject: 'fixture-user-0001', kind: 'human', scopes: ['xelibrary:read', 'dev:read', 'desktop:read','desktop:admin']};
  }
  if(path==='/apps?include_unavailable=true')return [...registry];
  if (path === '/apps') {if(method==='POST'){const saved={...body,revision:1};registry.push(saved);return saved}return [...registry]}
  if (path === '/users/me/preferences') {
    if (method === 'PATCH' && body) {
      if(control.failPreferences)throw Error('synthetic preferences save failure');
      Object.assign(preferences, body);
      sessionStorage.setItem('shell-fixture:preferences', JSON.stringify(preferences));
    }
    return {...preferences};
  }
  if (path === '/users/me/layout') {
    if (method === 'PUT' && body) {
      if(control.failLayout)throw Error('synthetic layout save failure');
      layout.dock_initialized = body.dock_initialized;
      layout.dock = body.dock || [];
      layout.windows = body.windows || [];
      sessionStorage.setItem('shell-fixture:layout', JSON.stringify(layout));
    }
    return {dock_initialized:layout.dock_initialized,dock: [...layout.dock], windows: layout.windows.map(win => ({...win}))};
  }
  if (path.startsWith('/work')) {
    if (control.failWork) throw new Error('synthetic work read failure');
    const url = new URL(path, 'http://fixture.local');
    const cursor = url.searchParams.get('cursor');
    if (cursor === 'page-2') return {data: work.slice(3), meta: {next_cursor: null}};
    if (url.searchParams.get('limit') === '1000') return {data: work, meta: {next_cursor: null}};
    return {data: work.slice(0, 3), meta: {next_cursor: 'page-2'}};
  }
  if (path === '/jobs?limit=50') return {data: [], meta: {counts: {queued: 0, running: 0, completed: 0, failed: 0}}};
  if (path.startsWith('/tables?')) {
    control.tablesInFlight++;
    try {
      if (control.delayTables) await new Promise(resolve=>setTimeout(resolve,400));
      return {data:tables,meta:{next_cursor:null}};
    } finally {control.tablesInFlight--}
  }
  throw new Error(`unexpected fixture request ${method} ${path}`);
}

const apiPage = async (path: string, init?: RequestInit) => {
  const data = await api(path, init);
  if (data && Array.isArray(data.data)) return {data: data.data, meta: data.meta || {}};
  return {data, meta: {}};
};
const apiRaw = async (path: string, init?: RequestInit) => {
  if (path === '/users/me/wallpaper' && (!init?.method || init.method === 'GET')) {
    if (!preferences.custom_wallpaper_available) return new Response(null, {status: 404});
    const image = Uint8Array.from(atob('UklGRjQAAABXRUJQVlA4ICgAAACQAQCdASoCAAIAAMASJQBOl0AAcwAA/v5b3/87bUdT4y/1te28MKAA'), char => char.charCodeAt(0));
    return new Response(image, {status: 200, headers: {'Content-Type': 'image/webp'}});
  }
  if (path === '/users/me/wallpaper' && init?.method === 'PUT') {
    calls.push({method: 'PUT', path, body: {contentType: new Headers(init.headers).get('Content-Type')}});
    preferences.custom_wallpaper_available = true;
    sessionStorage.setItem('shell-fixture:preferences', JSON.stringify(preferences));
    return new Response(JSON.stringify({data: {wallpaper: 'custom'}}), {status: 200, headers: {'Content-Type': 'application/json'}});
  }
  if (path === '/users/me/wallpaper' && init?.method === 'DELETE') {
    calls.push({method: 'DELETE', path, body: null});
    preferences.custom_wallpaper_available = false;
    if (preferences.wallpaper === 'custom' && preferences.wallpaper_mode === 'image') preferences.wallpaper = 'aurora';
    sessionStorage.setItem('shell-fixture:preferences', JSON.stringify(preferences));
    return new Response(JSON.stringify({data: {deleted: true, wallpaper: 'aurora'}}), {status: 200, headers: {'Content-Type': 'application/json'}});
  }
  const data = await api(path, init);
  return new Response(JSON.stringify({data}), {status: 200, headers: {'Content-Type': 'application/json'}});
};

const browserFetch = window.fetch.bind(window);
window.fetch = (input, init) => String(input) === '/health/ready'
  ? Promise.resolve(new Response(JSON.stringify({service: 'desktop-web'}), {status: 200, headers: {'Content-Type': 'application/json'}}))
  : browserFetch(input, init);

(window as any).shellFixture = {
  calls,
  control,
  registry,
  preferences,
  layout,
  loggedOut: false,
  resetCalls: () => calls.splice(0, calls.length),
};

createRoot(document.getElementById('root')!).render(
  <ToastProvider>
    <DesktopShell
      api={api}
      apiPage={apiPage}
      apiRaw={apiRaw}
      token="fixture-token"
      onLogout={async () => {
        (window as any).shellFixture.loggedOut = true;
      }}
    />
  </ToastProvider>,
);
