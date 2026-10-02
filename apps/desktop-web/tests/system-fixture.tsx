// Isolated component fixture: synthetic API data, no production account or services.
import React from 'react';
import {createRoot} from 'react-dom/client';
import {System} from '../src/shell/System';
import '../src/style.css';

const mode = new URLSearchParams(location.search).get('storageCase') || 'ok';

const api = async (path: string) => {
  await new Promise(resolve => setTimeout(resolve, 60));
  if (path === '/users/me') {
    return {subject: 'fixture-user', scopes: mode === 'forbidden' ? ['desktop:read'] : ['desktop:read', 'hf-cache:read']};
  }
  if (path === '/cache/storage') {
    if (mode === 'unavailable') {
      throw Object.assign(new Error('trusted storage telemetry unavailable'), {code: 'STORAGE_TELEMETRY_UNAVAILABLE', status: 503});
    }
    return {
      pool: 'aux-10t',
      sampled_at: new Date().toISOString(),
      pool_total_bytes: 10_000_000_000_000,
      pool_used_bytes: 7_280_000_000_000,
      pool_free_bytes: 2_720_000_000_000,
      guest_total_bytes: 2_199_023_255_552,
      guest_used_bytes: 1_100_000_000_000,
      guest_free_bytes: 1_099_023_255_552,
      total_bytes: 10_000_000_000_000,
      used_bytes: 7_280_000_000_000,
      free_bytes: 1_099_023_255_552,
      used_percent: 72.8,
      watermark: 'normal',
      cached_bytes: 512_000_000_000,
    };
  }
  return {};
};
const apiPage = async (path: string) => ({data: [], meta: {}});

createRoot(document.getElementById('root')!).render(
  <div className="window" id="system-fixture" style={{left: 8, top: 8, width: 1180, height: 820, resize: 'none'}}>
    <div className="bar"><button className="close"/><button className="min"/><button className="max"/><span>系统 · 存储水位本地验收</span></div>
    <div className="window-body"><System api={api} apiPage={apiPage} openApp={(id) => {document.body.dataset.openedApp = id}}/></div>
  </div>,
);
