import React, {useCallback, useEffect, useRef, useState} from 'react';
import {Badge, Button, EmptyState, ErrorState, Textarea} from './design-system/primitives';
import type {AppViewProps} from './shell/types';
import {alignEmbeddingResults, validEmbeddingInputs, MAX_EMBEDDING_INPUTS, MAX_EMBEDDING_CHARACTERS, type EmbeddingResult} from './embedding-batch.js';

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

export function Embedding({api, apiPage, apiRaw}: AppViewProps) {
  const [scopes, setScopes] = useState<string[]>([]);
  const [models, setModels] = useState<Model[]>([]);
  const [jobs, setJobs] = useState<QueueJob[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [texts, setTexts] = useState(['']);
  const [model, setModel] = useState('fast');
  const [results, setResults] = useState<EmbeddingResult[] | null>(null);
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
    setResults(null); setResultModel(''); setEncodeError('');
  }

  useEffect(() => {
    let active = true;
    setRefreshing(true);
    setIdentityError(''); setModelsError(''); setQueueError('');
    void api('/users/me').then(me => {
      if (!active) return;
      const verifiedScopes = Array.isArray(me.scopes) ? me.scopes.filter((scope: unknown): scope is string => typeof scope === 'string') : [];
      setScopes(verifiedScopes);
      if (!verifiedScopes.includes('embedding:write')) { setResults(null); setResultModel(''); }
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
    }).catch(error => { if (active) setIdentityError(errorText(error)); })
      .finally(() => { if (active) setRefreshing(false); });
    return () => { active = false; };
  }, [api, apiPage, apiRaw, tick]);

  const run = useCallback(async () => {
    const inputs = [...texts];
    if (!validEmbeddingInputs(inputs) || !scopes.includes('embedding:write')) return;
    const draft = draftVersion.current;
    const request = ++requestVersion.current;
    const isCurrent = () => request === requestVersion.current && draft === draftVersion.current;
    setBusy(true); setResults(null); setResultModel(''); setEncodeError('');
    try {
      const body = await jsonResponse(await apiRaw('/embed/text', {
        method: 'POST', body: JSON.stringify({model, input: inputs.length === 1 ? inputs[0] : inputs}),
      }));
      if (!isCurrent()) return;
      setResults(alignEmbeddingResults(body?.data, inputs));
      setResultModel(String(body.model || model));
    } catch (error) { if (isCurrent()) setEncodeError(errorText(error)); }
    finally { if (request === requestVersion.current) setBusy(false); }
  }, [apiRaw, model, scopes, texts]);

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
        <p className="embedding-hint">一次编码 1–32 段独立文本；每段最多 32,768 字符，服务端另行验证模型 token 限制。</p>
        {texts.map((text, index) => <div className="embedding-input-item" key={index}>
          <label className="embedding-input-label" htmlFor={index === 0 ? 'embedding-text' : `embedding-text-${index}`}>输入文本 {index + 1}</label>
          <Textarea id={index === 0 ? 'embedding-text' : `embedding-text-${index}`} maxLength={MAX_EMBEDDING_CHARACTERS} rows={texts.length === 1 ? 6 : 3} value={text} onChange={event => {invalidateResult();setTexts(current => current.map((value, item) => item === index ? event.target.value : value))}} placeholder="输入要编码的文本…"/>
          <div className="embedding-actions"><span>{text.length.toLocaleString()} / 32,768</span><Button size="sm" disabled={texts.length === 1} aria-label={`移除输入项 ${index + 1}`} onClick={() => {invalidateResult();setTexts(current => current.filter((_, item) => item !== index))}}>移除</Button></div>
        </div>)}
        <div className="embedding-actions"><Button disabled={texts.length >= MAX_EMBEDDING_INPUTS} onClick={() => {invalidateResult();setTexts(current => current.length < MAX_EMBEDDING_INPUTS ? [...current, ''] : current)}}>新增输入项（{texts.length} / 32）</Button><Button variant="primary" loading={busy} disabled={(canReadModels && !modelReady) || !validEmbeddingInputs(texts)} onClick={run}>生成编码</Button></div>
        {!validEmbeddingInputs(texts) && <p className="embedding-hint" role="status">每个输入项都需要非空文本。</p>}
        {canReadModels && !modelReady ? <p className="embedding-hint" role="status">服务端尚未报告可用模型；不会伪造编码结果。</p> : null}
        {encodeError && <p className="embedding-error" role="alert">{encodeError}</p>}
        {results && <section className="embedding-result" aria-label="Embedding 结果"><h4>真实服务响应 · {resultModel}</h4>{results.map(result => <article className="embedding-result-item" key={result.index} data-input-index={result.index}><h5>输入 {result.index + 1} · {result.embedding.length} 维</h5><pre className="embedding-result-input">{result.input}</pre><details><summary>查看向量</summary><pre>{JSON.stringify(result.embedding)}</pre></details></article>)}</section>}
      </> : <EmptyState title="需要生成权限" description="当前账户没有 embedding:write 权限。"/>}
    </section>
    <section className="embedding-panel" aria-labelledby="embedding-queue-title">
      <div className="embedding-panel-head"><div><h3 id="embedding-queue-title">知识库 Embedding 索引任务</h3><p>显示此账户最新 50 个索引作业；同步文本编码不会排队，这里也不代表全局模型 worker 队列。</p></div></div>
      {!canReadQueue ? <EmptyState title="需要 embedding:read 权限" description="当前账户没有读取本人索引任务的权限。"/> : queueError ? <ErrorState title="队列读取失败" message={queueError} onRetry={() => setTick(value => value + 1)}/> : <>
        <div className="embedding-counts" aria-label="本人索引任务计数">{['queued','running','completed','failed'].map(status => <span key={status}>{({queued:'排队',running:'运行中',completed:'完成',failed:'失败'} as Record<string,string>)[status]} <b>{counts[status] ?? 0}</b></span>)}</div>
        {jobs.length ? <div className="embedding-queue-scroll"><table className="ds-table"><thead><tr><th>任务</th><th>知识表</th><th>状态</th><th>更新时间</th></tr></thead><tbody>{jobs.map(job => <tr key={job.job_id}><td>{job.job_id}</td><td>{job.table_id}</td><td>{job.status}</td><td>{job.updated_at || job.created_at || '—'}{job.error ? <small className="embedding-job-error">{job.error}</small> : null}</td></tr>)}</tbody></table></div> : <EmptyState title="没有可显示的索引任务" description="队列 API 没有返回当前账户的任务。"/>}
      </>}
    </section>
  </main>;
}
