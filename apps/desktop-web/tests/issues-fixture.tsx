import React from 'react';
import {createRoot} from 'react-dom/client';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/shell/shell.css';
import '../src/style.css';
import {Issues} from '../src/shell/Issues';

// Synthetic rows covering queued / live claim / expired claim / completed / blocked (#80 semantics).
const rows = [
  {id: 'w1', repository: 'xenon-admin/XeonoLab', issue_id: 71, title: 'Synthetic queued task', status: 'queued', holder: null, expires: 0, epoch: 0, claim_active: false},
  {id: 'w2', repository: 'xenon-admin/XeonoLab', issue_id: 72, title: 'Synthetic live claim', status: 'claimed', holder: 'fixture-agent', expires: 1790824000, epoch: 1, claim_active: true},
  {id: 'w3', repository: 'xenon-admin/XeonoLab', issue_id: 73, title: 'Synthetic expired claim', status: 'claimed', holder: 'stale-agent', expires: 1790820000, epoch: 2, claim_active: false},
  {id: 'w4', repository: 'xenon-admin/XeonoLab', issue_id: 74, title: 'Synthetic completed task', status: 'completed', holder: null, expires: 0, epoch: 1, claim_active: false},
  {id: 'w5', repository: 'xenon-admin/XeonoLab', issue_id: 75, title: 'Synthetic blocked task', status: 'blocked', holder: null, expires: 0, epoch: 0, claim_active: false},
];

const apiPage = async (path: string) => {
  if (path.startsWith('/work')) return {data: rows, meta: {}};
  throw new Error(`unexpected fixture path: ${path}`);
};

createRoot(document.getElementById('root')!).render(
  <div className="window" id="issues-fixture" style={{left: 0, top: 0, width: 390, height: 600, resize: 'none'}}>
    <div className="bar"><button className="close"/><button className="min"/><button className="max"/><span>任务 · 窄屏夹具</span></div>
    <div className="window-body"><Issues apiPage={apiPage as any} api={async () => ({}) as any} apiRaw={null as any} app={{} as any} apps={[]} openApp={() => {}} preferences={{} as any} applyPreferences={async () => {}} notify={() => {}} windowId="issues" closeWindow={() => {}} /></div>
  </div>,
);
