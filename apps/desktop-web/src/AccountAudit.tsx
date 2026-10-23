import React, {useLayoutEffect, useRef, useState} from 'react';
import type {APIPage} from './shell/types';

type AuditEvent = {id: string; actor: string; target: string; event: string; at: number};

export function AccountAudit({apiPage, identityKey, accountLabel}: {
  apiPage: APIPage<AuditEvent[]>;
  identityKey: string;
  accountLabel: (id: string) => string;
}) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const pendingCursor = useRef<string | null>(null);
  const flight = useRef(false);
  const generation = useRef(0);
  const context = useRef({apiPage, identityKey});
  context.current = {apiPage, identityKey};

  useLayoutEffect(() => {
    ++generation.current;
    flight.current = false;
    setEvents([]);
    setCursor(null);
    setLoaded(false);
    setLoading(false);
    setError('');
    return () => { ++generation.current; };
  }, [apiPage, identityKey]);

  async function read(next: string | null) {
    if (flight.current) return;
    flight.current = true;
    pendingCursor.current = next;
    setLoading(true);
    setError('');
    const version = generation.current;
    const current = () => version === generation.current &&
      context.current.apiPage === apiPage && context.current.identityKey === identityKey;
    try {
      const page = await apiPage('/auth/accounts/audit/events' +
        (next ? '?cursor=' + encodeURIComponent(next) : ''));
      if (!current()) return;
      setEvents(previous => {
        const rows = next ? [...previous] : [];
        const ids = new Set(rows.map(row => row.id));
        for (const row of page.data) {
          if (!ids.has(row.id)) { rows.push(row); ids.add(row.id); }
        }
        return rows;
      });
      setCursor(page.meta.next_cursor || null);
      setLoaded(true);
    } catch (cause) {
      if (current()) setError(cause instanceof Error && cause.message.trim() ? cause.message : '读取账号审计失败');
    } finally {
      if (current()) { flight.current = false; setLoading(false); }
    }
  }

  return <details className="account-audit">
    <summary>账号审计</summary>
    <button disabled={loading} onClick={() => void read(null)}>读取最近记录</button>
    <p className="muted">名称与登录名来自当前可见账号树；原始 ID 保留供核对。</p>
    <ul>{events.map(event => <li key={event.id} data-event-id={event.id}>
      {new Date(event.at * 1000).toLocaleString()} · {event.event}
      <div>操作人：{accountLabel(event.actor)}</div>
      <div>目标：{accountLabel(event.target)}</div>
    </li>)}</ul>
    {loading && <p role="status">正在读取账号审计…</p>}
    {error && <div role="alert"><p>{error}；已加载记录保留，可重试。</p>
      <button disabled={loading} onClick={() => void read(pendingCursor.current)}>重试读取</button>
    </div>}
    {cursor && !error && <button disabled={loading} onClick={() => void read(cursor)}>加载更早记录</button>}
    {loaded && !loading && !error && <p role="status">
      已加载 {events.length} 条记录{cursor ? '，还有更早记录。' : '，历史已读完。'}
    </p>}
  </details>;
}
