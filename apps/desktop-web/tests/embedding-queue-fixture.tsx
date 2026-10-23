import React from 'react';
import '../src/design-system/tokens.css';
import '../src/design-system/primitives.css';
import '../src/shell/shell.css';
import '../src/style.css';
import {createRoot} from 'react-dom/client';
import {EmbeddingQueue} from '../src/EmbeddingQueue';

type FixtureRow = {
  id: number;
  created_at: number;
  finished_at: number | null;
  status: string;
  model: string;
  batch_size: number;
  input_chars: number;
  truncation: string;
  tokens: number | null;
  duration_ms: number | null;
  error_code: string | null;
  caller: string | null;
};

const baseTime = 1790822400;

const makeRow = (n: number): FixtureRow => {
  const failed = n % 17 === 0;
  return {
    id: n,
    created_at: baseTime - (300 - n) * 3,
    finished_at: baseTime - (300 - n) * 3 + 2,
    status: failed ? 'failed' : 'completed',
    model: 'd100m',
    batch_size: 1 + (n % 8),
    input_chars: 40 + n,
    truncation: 'reject',
    tokens: 40 + n,
    duration_ms: failed ? 12 : 250 + (n % 30) * 10,
    error_code: failed ? 'TOKEN_LIMIT' : null,
    caller: 'fixture-caller-0001',
  };
};

let refreshCount = 0;

const firstPage = Array.from({length: 100}, (_, i) => makeRow(200 - i));
const secondPage = Array.from({length: 50}, (_, i) => makeRow(100 - i));

const apiPage = async (path: string) => {
  const url = new URL(path, 'http://fixture.local');
  const cursor = url.searchParams.get('cursor');
  if (url.pathname === '/embedding/queue') {
    return {
      data: {
        active: [
          {id: 301, created_at: baseTime - 2, finished_at: null, status: 'running', model: 'd100m',
            batch_size: 6, input_chars: 900, truncation: 'reject', tokens: null, duration_ms: null,
            error_code: null, caller: 'fixture-caller-0001'},
        ],
        counts: {running: 1, completed: 118, failed: 7},
        worker: {configured: true, busy: true, revision: 'synthetic-revision-0001'},
      },
      meta: {},
    };
  }
  if (!cursor) {
    refreshCount += 1;
    return {data: firstPage.map(row => ({...row})), meta: {next_cursor: 'fixture-page-2'}};
  }
  if (cursor === 'fixture-page-2') return {data: secondPage.map(row => ({...row})), meta: {next_cursor: null}};
  return {data: [], meta: {next_cursor: null}};
};

(window as any).queueFixture = {
  get refreshCount() {return refreshCount},
};

createRoot(document.getElementById('root')!).render(
  <div className="window" id="embedding-queue-fixture" style={{left: 8, top: 8, width: 1020, height: 700, resize: 'none'}}>
    <div className="bar"><button className="close"/><button className="min"/><button className="max"/><span>Embedding 队列 · 本地合成数据验收</span></div>
    <div className="window-body"><EmbeddingQueue apiPage={apiPage as any}/></div>
  </div>,
);
