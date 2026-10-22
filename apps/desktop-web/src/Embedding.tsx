import React, {useCallback, useEffect, useRef, useState} from 'react';
import {Badge, Button, EmptyState, ErrorState, Textarea} from './design-system/primitives';
import type {AppViewProps} from './shell/types';

type Model = {id: string; alias?: string; status: string; dimensions?: number | string; max_tokens?: number};
type QueueJob = {job_id: string; table_id: string; status: string; created_at?: string; updated_at?: string; error?: string};

function errorText(error: unknown): string {
  if (error instanceof Error) return error.message;
  return typeof error === 'string' ? error : '读取失败，请重试。';
}

async function jsonResponse(response: Response): Promise<any> {
  let body: any = null;
  try { body = await response.json(); } catch { /* reported below */ }
  if (!response.ok) throw new Error(body?.error?.message || `请求失败（HTTP ${response.status}）`);
  return body;
}

export function Embedding({api, apiPage, apiRaw, openApp}: AppViewProps) {
  const [scopes, setScopes] = useState<string[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [jobs, setJobs] = useState<QueueJob[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [text, setText] = useState('');
  const [model, setModel] = useState('fast');
  const [vector, setVector] = useState<number[] | null>(null);
  const [resultModel, setResultModel] = useState('');
  const [busy, setBusy] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [identityError, setIdentityError] = useState('');
  const [modelsError, setModelsError] = useState('');
  const [encodeError, setEncodeError] = useState('');
  const [queueError, setQueueError] = useState('');
  const [tick, setTick] = useState(0);
  // Draft edits invalidate responses even if the user later restores the same text.
  const draftVersion = useRef(0);
  const requestVersion = useRef(0);

  useEffect(() => () => { requestVersion.current += 1; }, []);

  function invalidateResult() {
    draftVersion.current += 1;
    setVector(null); setResultModel(''); setEncodeError('');
  }

  useEffect(() => {
    let active = true;
    setRefreshing(true);
    setIdentityError(''); setModelsError(''); setQueueError('');
    void api('/users/me').then(me => {
      if (!active) return;
      const verifiedScopes = Array.isArray(me.scopes) ? me.scopes.filter((scope: unknown): scope is string => typeof scope === 'string') : [];
      setScopes(verifiedScopes);
      if (!verifiedScopes.includes('embedding:write')) { setVector(null); setResultModel(''); }
      if (verifiedScopes.includes('embedding:read')) {
        void apiRaw('/embed/models').then(jsonResponse).then(body => {
          if (active) setModels(Array.isArray(body?.data) ? body.data : []);
        }).catch(error => { if (active) setModelsError(errorText(error)); });
      } else setModels([]);
      if (verifiedScopes.includes('embedding:read')) {
        void apiPage('/embedding/jobs?limit=50').then(page => {
          if (!active) return;
          setJobs(Array.isArray(page.data) ? page.data : []);
          setCounts(page.meta.counts && typeof page.meta.counts === 'object' ? page.meta.counts : {});
        }).catch(error => { if (active) setQueueError(errorText(error)); });
      } else { setJobs([]); setCounts({}); }
    }).catch(error => { if (active) { setScopes([]); setIdentityError(errorText(error)); } })
      .finally(() => { if (active) setRefreshing(false); });
    return () => { active = false; };
  }, [api, apiPage, apiRaw, tick]);

  const run = useCallback(async () => {
    const input = text;
    if (!input.trim() || !scopes.includes('embedding:write')) return;
    const draft = draftVersion.current;
    const request = ++requestVersion.current;
    const isCurrent = () => request === requestVersion.current && draft === draftVersion.current;
    setBusy(true); setVector(null); setResultModel(''); setEncodeError('');
    try {
      const body = await jsonResponse(await apiRaw('/embed/text', {
        method: 'POST', body: JSON.stringify({model, input}),
      }));
      if (!isCurrent()) return;
      const first = Array.isArray(body?.data) ? body.data[0] : null;
      if (!Array.isArray(first?.embedding) || !first.embedding.every((value: unknown) => typeof value === 'number' && Number.isFinite(value))) {
        throw new Error('Embedding 服务返回了无法识别的向量数据。');
      }
      setVector(first.embedding);
      setResultModel(String(body.model || model));
    } catch (error) { if (isCurrent()) setEncodeError(errorText(error)); }
    finally { if (request === requestVersion.current) setBusy(false); }
  }, [apiRaw, model, scopes, text]);

  const availableModel = models.find(item => item.alias === model || item.id === model);
  const modelReady = availableModel?.status === 'ready';
  const canReadModels = scopes.includes('embedding:read');
  const canWrite = scopes.includes('embedding:write');
  const canReadQueue = scopes.includes('embedding:read');

  return <main className="embedding-app">
    <header className="app-head">
      <div><h2>Embedding</h2><p>文本编码与当前账号有权查看的知识库索引任务。</p></div>
      <Button onClick={() => setTick(value => value + 1)} disabled={refreshing} loading={refreshing}>刷新</Button>
    </header>
    {identityError ? <ErrorState title="无法验证当前权限" message={identityError} onRetry={() => setTick(value => value + 1)}/> : null}
    <section className="embedding-panel" aria-labelledby="embedding-text-title">
      <div className="embedding-panel-head"><div><h3 id="embedding-text-title">文本编码</h3><p>由当前账户的 embedding:write 权限控制；同步生成，worker 忙时返回 429，不进入等待队列。</p></div>
        {!canReadModels ? <Badge>状态不可读</Badge> : modelReady ? <Badge tone="success">{availableModel?.id} · 可用</Badge> : availableModel ? <Badge tone="warning">模型不可用</Badge> : <Badge>正在读取</Badge>}
      </div>
      {canReadModels && modelsError ? <ErrorState title="模型状态读取失败" message={modelsError} onRetry={() => setTick(value => value + 1)}/> : null}
      {canReadModels && !modelsError && <div className="embedding-model-row"><label htmlFor="embedding-model">模型</label><select id="embedding-model" value={model} onChange={event => {invalidateResult();setModel(event.target.value)}}>
          {models.map(item => <option key={item.id} value={item.alias || item.id} disabled={item.status !== 'ready'}>{item.id} · {item.status === 'ready' ? `${item.dimensions ?? '维度未知'} 维` : '未配置'}</option>)}
        </select></div>}
      {!canReadModels && <p className="embedding-hint" role="status">当前账户没有 embedding:read 权限；服务端仍会验证模型并返回真实执行结果。</p>}
      {canWrite ? <>
        <label className="embedding-input-label" htmlFor="embedding-text">输入文本</label>
        <Textarea id="embedding-text" maxLength={32768} rows={6} value={text} onChange={event => {invalidateResult();setText(event.target.value)}} placeholder="输入要编码的文本…" disabled={!canWrite}/>
        <div className="embedding-actions"><span>{text.length.toLocaleString()} / 32,768</span><Button variant="primary" loading={busy} disabled={(canReadModels && !modelReady) || !text.trim()} onClick={run}>生成编码</Button></div>
        {canReadModels && !modelReady ? <p className="embedding-hint" role="status">服务端尚未报告可用模型；不会伪造编码结果。</p> : null}
        {encodeError && <p className="embedding-error" role="alert">{encodeError}</p>}
        {vector && <section className="embedding-result" aria-label="Embedding 结果"><h4>真实服务响应 · {resultModel}</h4><p>{vector.length} 维</p><details><summary>查看向量</summary><pre>{JSON.stringify(vector)}</pre></details></section>}
      </> : <EmptyState title="需要生成权限" description="当前账户没有 embedding:write 权限。"/>}
    </section>
    <section className="embedding-panel" aria-labelledby="embedding-queue-title">
      <div className="embedding-panel-head"><div><h3 id="embedding-queue-title">知识库 Embedding 索引任务</h3><p>显示此账户最新 50 个索引作业；同步文本编码不会排队，这里也不代表全局模型 worker 队列。</p></div></div>
      {!canReadQueue ? <EmptyState title="需要 embedding:read 权限" description="当前账户没有读取本人索引任务的权限。"/> : queueError ? <ErrorState title="队列读取失败" message={queueError} onRetry={() => setTick(value => value + 1)}/> : <>
        <div className="embedding-counts" aria-label="本人索引任务计数">{['queued','running','completed','failed'].map(status => <span key={status}>{({queued:'排队',running:'运行中',completed:'完成',failed:'失败'} as Record<string,string>)[status]} <b>{counts[status] ?? 0}</b></span>)}</div>
        {jobs.length ? <div className="embedding-queue-scroll"><table className="ds-table">
          <thead><tr><th>任务</th><th>知识表</th><th>状态</th><th>更新时间</th></tr></thead>
          <tbody>{jobs.map(job => <tr key={job.job_id}>
            <td>{job.job_id}</td>
            <td><div className="embedding-table-target">
              <span title={job.table_id}>{job.table_id}</span>
              {scopes.includes('xelibrary:read') && <Button size="sm" variant="ghost" onClick={() => openApp('knowledge', {action: 'openTable', table_id: job.table_id})}>在知识库中打开</Button>}
            </div></td>
            <td>{job.status}</td>
            <td>{job.updated_at || job.created_at || '—'}{job.error ? <small className="embedding-job-error">{job.error}</small> : null}</td>
          </tr>)}</tbody>
        </table></div> : <EmptyState title="没有可显示的索引任务" description="队列 API 没有返回当前账户的任务。"/>}
      </>}
    </section>
  </main>;
}
