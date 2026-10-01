import React, {useEffect, useState} from 'react';
import {Badge, Button, EmptyState, ErrorState, Panel, Skeleton} from '../design-system/primitives';
import {forgejoBase} from '../shell/apps';
import type {AppViewProps} from '../shell/types';

type WorkItem = {
  id: string;
  repository: string;
  issue_id: number;
  title: string;
  status: string;
  holder: string | null;
  epoch: number;
  claim_active: boolean;
};

const REPOSITORY = 'xenon-admin/XeonoLab';
const statusLabels: Record<string, string> = {queued: '排队', claimed: '进行中', completed: '已完成', blocked: '阻塞'};
const statusTones: Record<string, 'neutral' | 'accent' | 'success' | 'danger' | 'warning'> = {
  queued: 'neutral',
  claimed: 'accent',
  completed: 'success',
  blocked: 'danger',
};

export function Development({apiPage, openApp, notify, windowId}: AppViewProps) {
  const base = forgejoBase();
  const [work, setWork] = useState<WorkItem[] | null>(null);
  const [workError, setWorkError] = useState('');
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const [truncated, setTruncated] = useState(false);

  useEffect(() => {
    let active = true;
    setLoading(true);
    apiPage('/work?limit=1000')
      .then(page => {
        if (!active) return;
        setWork(page.data);
        setTruncated(Boolean(page.meta.next_cursor));
        setWorkError('');
      })
      .catch(error => {
        if (!active) return;
        setWork([]);
        setWorkError(/403|scope/.test(String(error)) ? '需要 dev:read 权限才能查看 Harness 任务' : String(error));
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [tick, apiPage]);

  useEffect(() => {const refresh = (event: Event) => {if (!(event as CustomEvent).detail?.windowId || (event as CustomEvent).detail.windowId === windowId) setTick(value => value+1)}; addEventListener('xeonolab.app.refresh',refresh); return () => removeEventListener('xeonolab.app.refresh',refresh)},[]);

  const activeWork = work?.filter(item => item.status === 'queued' || item.status === 'claimed') ?? [];
  const recent = [...(work ?? [])].sort((a,b) => b.issue_id-a.issue_id).slice(0, 6);

  return (
    <div className="development-app">
      <header className="app-head">
        <div>
          <h2>开发</h2>
          <p>仓库、任务与协作入口</p>
        </div>
        <div className="app-head-actions"><Button disabled={loading} onClick={() => setTick(value => value+1)}>刷新</Button>

          <Button variant="primary" onClick={() => window.open(base + '/' + REPOSITORY, '_blank', 'noopener')}>
            打开仓库
          </Button>
        </div>
      </header>

      <div className="app-grid">
        <Panel title="仓库" className="dev-card">
          <dl className="dev-meta">
            <div><dt>仓库</dt><dd>{REPOSITORY}</dd></div>
            <div><dt>入口</dt><dd><a href={base + '/' + REPOSITORY} target="_blank" rel="noopener noreferrer">Forgejo</a></dd></div>
          </dl>
          <div className="ds-button-row">
            <Button size="sm" onClick={() => window.open(base + '/' + REPOSITORY + '/issues', '_blank', 'noopener')}>Issues</Button>
            <Button size="sm" onClick={() => window.open(base + '/' + REPOSITORY + '/pulls', '_blank', 'noopener')}>Pull Requests</Button>
            <Button size="sm" onClick={() => window.open(base + '/' + REPOSITORY + '/actions', '_blank', 'noopener')}>Actions</Button>
          </div>
        </Panel>

        <Panel title="Harness 任务" actions={<Badge tone={activeWork.length ? 'accent' : 'neutral'}>{activeWork.length}{truncated ? '+' : ''} 进行中</Badge>} className="dev-card">
          {loading ? (
            <div className="dev-lines"><Skeleton height={16} /><Skeleton height={16} width="70%" /><Skeleton height={16} width="84%" /></div>
          ) : workError ? (
            <ErrorState title="任务队列不可用" message={workError} onRetry={() => setTick(value => value+1)} />
          ) : activeWork.length ? (
            <ul className="dev-list">
              {activeWork.slice(0, 5).map(item => (
                <li key={item.id}>
                  <Badge tone={statusTones[item.status] ?? 'neutral'}>{statusLabels[item.status] ?? item.status}</Badge>
                  <span title={item.title}>#{item.issue_id} {item.title}</span>
                  <small>{item.holder ? `由 ${item.holder.slice(0, 8)}… 领取` : '待领取'}</small>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState icon="✓" title="队列为空" description="当前没有排队或进行中的 Harness 任务。" />
          )}
          {truncated && <p className="muted">状态仅覆盖已载入的 {work?.length ?? 0} 项；完整列表请打开任务中心。</p>}
          <Button size="sm" onClick={() => openApp('issues')}>打开任务中心</Button>
        </Panel>

        <Panel title="最近同步的任务" className="dev-card">
          {loading ? (
            <Skeleton height={16} />
          ) : recent.length ? (
            <ul className="dev-list">
              {recent.map(item => (
                <li key={item.id}>
                  <a href={`${base}/${REPOSITORY}/issues/${item.issue_id}`} target="_blank" rel="noopener noreferrer">#{item.issue_id}</a>
                  <span title={item.title}>{item.title}</span>
                </li>
              ))}
            </ul>
          ) : (
            <EmptyState icon="⌘" title="尚无活动" description="任务同步后会显示在这里。" />
          )}
        </Panel>

        <Panel title="发布" className="dev-card">
          <dl className="dev-meta">
            <div><dt>发布流程</dt><dd>由 Codex 协调器执行（release → approval → exclusive lease）</dd></div>
            <div><dt>桌面版本</dt><dd>{document.documentElement.dataset.release || '未标记'}</dd></div>
          </dl>
          <Button size="sm" onClick={() => openApp('system')}>查看系统状态</Button>
          <Button size="sm" onClick={() => notify({title: '提示', description: '发布状态未通过桌面 API 暴露；此处不显示推测数据。'})}>发布状态</Button>
        </Panel>
      </div>
    </div>
  );
}
