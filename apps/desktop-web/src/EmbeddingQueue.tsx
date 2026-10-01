import React, {useCallback, useEffect, useRef, useState} from 'react';
import {Badge, Button, EmptyState, ErrorState, Skeleton} from './design-system/primitives';
import {
  appendUnique,
  formatQueueDuration,
  formatQueueTime,
  queueStatusLabel,
  queueStatusTone,
  summarizeQueue,
} from './embedding-queue-logic.js';
import type {APIPage} from './shell/types';

type QueueRequest = {
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

type QueueSummary = {
  active: QueueRequest[];
  counts: Record<string, number>;
  worker: {configured: boolean; busy: boolean; revision: string | null};
};

const HISTORY_PAGE = 100;

/** Visible embedding queue (issue #74): unfinished requests plus the recent finished history. */
export function EmbeddingQueue({apiPage}: {apiPage: APIPage}) {
  const [summary, setSummary] = useState<QueueSummary | null>(null);
  const [rows, setRows] = useState<QueueRequest[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [message, setMessage] = useState('');
  const generation = useRef(0);

  const refresh = useCallback(async () => {
    const current = ++generation.current;
    setLoading(true);
    setLoadingMore(false);
    setMessage('');
    try {
      const [queuePage, historyPage] = await Promise.all([
        apiPage('/embedding/queue'),
        apiPage(`/embedding/requests?limit=${HISTORY_PAGE}`),
      ]);
      if (current !== generation.current) return;
      setSummary(queuePage.data as QueueSummary);
      setRows(historyPage.data as QueueRequest[]);
      setCursor(historyPage.meta.next_cursor || null);
    } catch (error) {
      if (current === generation.current) {
        setSummary(null);
        setRows([]);
        setCursor(null);
        setMessage(/403|scope required/.test(String(error)) ? '需要 dev:read 权限才能查看 Embedding 队列。' : String(error));
      }
    } finally {
      if (current === generation.current) setLoading(false);
    }
  }, [apiPage]);

  const loadMore = useCallback(async () => {
    if (!cursor || loadingMore) return;
    const current = generation.current;
    setLoadingMore(true);
    try {
      const page = await apiPage(`/embedding/requests?limit=${HISTORY_PAGE}&cursor=${encodeURIComponent(cursor)}`);
      if (current !== generation.current) return;
      setRows(old => appendUnique(old, page.data as QueueRequest[]));
      setCursor(page.meta.next_cursor || null);
    } catch (error) {
      if (current === generation.current) setMessage(String(error));
    } finally {
      if (current === generation.current) setLoadingMore(false);
    }
  }, [apiPage, cursor, loadingMore]);

  useEffect(() => {
    void refresh();
    const refreshListener = () => void refresh();
    addEventListener('xeonolab.app.refresh', refreshListener);
    const timer = setInterval(() => void refresh(), 10000);
    return () => {
      generation.current += 1;
      removeEventListener('xeonolab.app.refresh', refreshListener);
      clearInterval(timer);
    };
  }, [refresh]);

  const counts = summarizeQueue(summary?.counts);
  const worker = summary?.worker;
  const active = summary?.active ?? [];

  return (
    <div className="embedding-queue-app">
      <header className="app-head">
        <div>
          <h2>Embedding 队列</h2>
          <p>D100M worker 的实时请求与最近已完成记录；历史保留最近 1000 条。</p>
        </div>
        <div className="app-head-actions">
          <Button onClick={() => void refresh()} disabled={loading}>{loading ? '读取中…' : '刷新'}</Button>
        </div>
      </header>

      <section className="queue-stats" aria-label="队列概览">
        <div className="queue-stat" data-testid="queue-stat-running">
          <small>运行中</small>
          <strong>{counts.running}</strong>
        </div>
        <div className="queue-stat" data-testid="queue-stat-completed">
          <small>已完成</small>
          <strong>{counts.completed}</strong>
        </div>
        <div className="queue-stat" data-testid="queue-stat-failed">
          <small>失败</small>
          <strong>{counts.failed}</strong>
        </div>
        <div className="queue-stat" data-testid="queue-stat-worker">
          <small>Worker</small>
          <strong>
            {worker ? (
              worker.configured ? (
                <Badge tone={worker.busy ? 'warning' : 'success'}>{worker.busy ? '忙碌' : '就绪'}</Badge>
              ) : (
                <Badge tone="danger">未配置</Badge>
              )
            ) : (
              '—'
            )}
          </strong>
        </div>
      </section>

      {message && <ErrorState title="无法读取 Embedding 队列" message={message} onRetry={() => void refresh()} />}

      {!message && (
        <>
          <section aria-label="未完成请求">
            <h3 className="queue-section-title">未完成请求</h3>
            {loading ? (
              <Skeleton height={44} />
            ) : active.length ? (
              <ul className="job-list">
                {active.map(row => (
                  <li key={row.id} data-testid="queue-active-row">
                    <Badge tone="accent">{queueStatusLabel(row.status)}</Badge>
                    <span>
                      队列 #{row.id} · {row.model} · {row.batch_size} 条文本 / {row.input_chars} 字符
                    </span>
                    <small>{formatQueueTime(row.created_at)}</small>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">当前没有进行中的请求。</p>
            )}
          </section>

          <section aria-label="最近已完成请求" className="queue-history">
            <h3 className="queue-section-title">最近请求记录</h3>
            <div className="ds-table-wrap">
              <table className="ds-table queue-table" data-testid="queue-history-table">
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>状态</th>
                    <th>文本数</th>
                    <th>字符</th>
                    <th>Tokens</th>
                    <th>耗时</th>
                    <th>错误</th>
                    <th>调用方</th>
                  </tr>
                </thead>
                <tbody>
                  {loading
                    ? Array.from({length: 6}, (_, index) => (
                        <tr key={index}>
                          <td colSpan={8}>
                            <Skeleton height={16} />
                          </td>
                        </tr>
                      ))
                    : rows.map(row => (
                        <tr key={row.id} data-testid="queue-history-row">
                          <td>{formatQueueTime(row.created_at)}</td>
                          <td>
                            <Badge tone={queueStatusTone(row.status)}>{queueStatusLabel(row.status)}</Badge>
                          </td>
                          <td>{row.batch_size}</td>
                          <td>{row.input_chars}</td>
                          <td>{row.tokens ?? '—'}</td>
                          <td>{formatQueueDuration(row.duration_ms)}</td>
                          <td>{row.error_code ? <code>{row.error_code}</code> : '—'}</td>
                          <td title={row.caller || undefined}>{row.caller ? `${row.caller.slice(0, 8)}…` : '—'}</td>
                        </tr>
                      ))}
                </tbody>
              </table>
              {!loading && !rows.length && (
                <EmptyState
                  icon="≡"
                  title="还没有请求记录"
                  description="Embedding 服务收到第一个请求后，这里会显示最近 1000 条已完成或失败的记录。"
                />
              )}
            </div>
            {cursor && (
              <div className="issues-footer">
                <Button onClick={() => void loadMore()} disabled={loadingMore}>
                  {loadingMore ? '加载中…' : '加载更多'}
                </Button>
              </div>
            )}
          </section>
        </>
      )}
    </div>
  );
}
