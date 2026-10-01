import React, {useEffect, useMemo, useRef, useState} from 'react';
import {Badge, Button, EmptyState, ErrorState, Input, Select, Skeleton, StatusDot, Tabs} from '../design-system/primitives';
import {forgejoBase} from './apps';
import type {AppViewProps} from './types';

type WorkItem = {
  id: string;
  repository: string;
  issue_id: number;
  title: string;
  status: string;
  holder: string | null;
  expires: number;
  epoch: number;
  claim_active: boolean;
};

const statusLabels: Record<string, string> = {queued: '排队', claimed: '进行中', completed: '已完成', blocked: '需处理'};
const statusTones: Record<string, 'neutral' | 'accent' | 'success' | 'danger'> = {
  queued: 'neutral',
  claimed: 'accent',
  completed: 'success',
  blocked: 'danger',
};
const filters = [
  {id: 'active', label: '进行中'},
  {id: 'queued', label: '排队'},
  {id: 'completed', label: '已完成'},
  {id: 'blocked', label: '需处理'},
  {id: 'all', label: '全部'},
];

function issueUrl(repository: string, issueId: number): string | null {
  const [owner, repo, ...rest] = repository.split('/');
  if (rest.length || !owner || !repo || owner === '.' || repo === '.') return null;
  if (!/^[-A-Za-z0-9_.]+$/.test(owner) || !/^[-A-Za-z0-9_.]+$/.test(repo)) return null;
  return `${forgejoBase()}/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/issues/${issueId}`;
}

export function Issues({apiPage, notify, windowId}: AppViewProps) {
  const [items, setItems] = useState<WorkItem[]>([]);
  const [filter, setFilter] = useState('active');
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState('status');
  const [message, setMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [cursor, setCursor] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const generation = useRef(0);

  async function refresh() {
    const current = ++generation.current;
    setLoading(true);
    setLoadingMore(false);
    setMessage('');
    try {
      const page = await apiPage('/work?limit=100');
      if (current !== generation.current) return;
      setItems(page.data as WorkItem[]);
      setCursor(page.meta.next_cursor || null);
    } catch (error) {
      if (current === generation.current) setMessage(String(error));
    } finally {
      if (current === generation.current) setLoading(false);
    }
  }

  async function loadMore() {
    if (!cursor || loadingMore) return;
    const current = generation.current;
    setLoadingMore(true);
    try {
      const page = await apiPage(`/work?limit=100&cursor=${encodeURIComponent(cursor)}`);
      if (current !== generation.current) return;
      const incoming = page.data as WorkItem[];
      setItems(old => {
        const seen = new Set(old.map(item => item.id));
        return [...old, ...incoming.filter(item => !seen.has(item.id))];
      });
      setCursor(page.meta.next_cursor || null);
    } catch (error) {
      if (current === generation.current) setMessage(String(error));
    } finally {
      if (current === generation.current) setLoadingMore(false);
    }
  }

  useEffect(() => {
    void refresh();
    const refreshListener = (event: Event) => {if (!(event as CustomEvent).detail?.windowId || (event as CustomEvent).detail.windowId === windowId) void refresh()};
    addEventListener('xeonolab.app.refresh', refreshListener);
    return () => {generation.current += 1; removeEventListener('xeonolab.app.refresh', refreshListener)};
  }, []);

  const visible = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return items
      .filter(item => {
        if (filter === 'active') return item.status === 'queued' || item.status === 'claimed';
        if (filter === 'all') return true;
        return item.status === filter;
      })
      .filter(item => !needle || `${item.issue_id} ${item.title} ${item.repository} ${item.holder || ''}`.toLowerCase().includes(needle))
      .sort((a, b) => {
        const rank = (item: WorkItem) => (item.status === 'claimed' ? 0 : item.status === 'queued' ? 1 : item.status === 'blocked' ? 2 : 3);
        if (sort === 'newest') return b.issue_id - a.issue_id;
        if (sort === 'title') return a.title.localeCompare(b.title, 'zh-CN') || a.issue_id - b.issue_id;
        if (sort === 'issue') return a.issue_id - b.issue_id;
        return rank(a) - rank(b) || a.issue_id - b.issue_id;
      });
  }, [items, filter, query, sort]);

  const counts = useMemo(() => {
    const result: Record<string, number> = {active: 0, queued: 0, completed: 0, blocked: 0, all: items.length};
    for (const item of items) {
      if (item.status === 'queued' || item.status === 'claimed') result.active += 1;
      if (result[item.status] != null) result[item.status] += 1;
    }
    return result;
  }, [items]);

  return (
    <div className="issues-app">
      <header className="app-head">
        <div>
          <h2>任务</h2>
          <p>Harness 任务与 Issue 状态；完整标签与分配在 Forgejo 中维护。</p>
        </div>
        <div className="app-head-actions">
          <Button onClick={() => void refresh()} disabled={loading}>{loading ? '读取中…' : '刷新'}</Button>
          <Button variant="primary" onClick={() => window.open(`${forgejoBase()}/xenon-admin/XeonoLab/issues`, '_blank', 'noopener')}>
            打开 Forgejo Issues
          </Button>
        </div>
      </header>

      <div className="issues-toolbar">
        <Tabs
          tabs={filters.map(entry => ({id: entry.id, label: entry.label, badge: counts[entry.id]}))}
          value={filter}
          onChange={setFilter}
          ariaLabel="任务状态筛选"
        />
        <Input
          type="search"
          aria-label="搜索任务"
          placeholder="搜索 Issue 编号、标题、身份"
          value={query}
          onChange={event => setQuery(event.target.value)}
        />
        <Select aria-label="任务排序" value={sort} onChange={event => setSort(event.target.value)}>
          <option value="status">状态优先</option><option value="issue">编号升序</option><option value="newest">编号降序</option><option value="title">标题排序</option>
        </Select>
      </div>
      {cursor && <p className="muted" role="status">已加载 {items.length} 项；搜索、筛选与排序仅覆盖这些任务，加载更多后会更新结果。</p>}

      {message && <ErrorState title="无法读取任务" message={message} onRetry={() => void refresh()} />}

      <div className="ds-table-wrap">
        <table className="ds-table issues-table">
          <thead>
            <tr>
              <th>Issue</th>
              <th>标题</th>
              <th>状态</th>
              <th>领取身份</th>
              <th>领取有效期</th>
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({length: 6}, (_, index) => (
                  <tr key={index}>
                    <td colSpan={5}><Skeleton height={16} /></td>
                  </tr>
                ))
              : visible.map(item => {
                  const href = issueUrl(item.repository, item.issue_id);
                  return (
                    <tr key={item.id} onDoubleClick={() => href && window.open(href, '_blank', 'noopener')}>
                      <td>{href ? <a href={href} target="_blank" rel="noopener noreferrer">#{item.issue_id}</a> : `#${item.issue_id}`}</td>
                      <td title={item.title}>{item.title}</td>
                      <td>
                        <Badge tone={statusTones[item.status] ?? 'neutral'}>
                          <StatusDot tone={statusTones[item.status] ?? 'neutral'} label={undefined} />
                          {statusLabels[item.status] ?? item.status}
                        </Badge>
                      </td>
                      <td>{item.holder ? `${item.holder.slice(0, 8)}…` : <span className="ds-muted">未领取</span>}</td>
                      <td>{item.claim_active ? new Date(item.expires * 1000).toLocaleTimeString('zh-CN') : <span className="ds-muted">—</span>}</td>
                    </tr>
                  );
                })}
          </tbody>
        </table>
        {!loading && !visible.length && !message && (
          <EmptyState icon="⚑" title="没有匹配的任务" description={items.length ? '调整筛选或搜索条件。' : '任务队列为空。'} />
        )}
      </div>

      {cursor && (
        <div className="issues-footer">
          <Button onClick={() => void loadMore()} disabled={loadingMore}>{loadingMore ? '加载中…' : '加载更多'}</Button>
        </div>
      )}
    </div>
  );
}
