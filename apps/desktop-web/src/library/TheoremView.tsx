import React, {useState} from 'react';
import {Badge, Button, EmptyState, ErrorState, Input, Select, Skeleton} from '../design-system/primitives';
import {describeError, relativeTime} from './logic.js';
import type {Theorem} from './types';

function confidenceAverage(theorem: Theorem): number | null {
  const confidence = theorem.confidence;
  if (!confidence) return null;
  const values = [confidence.evidence_score, confidence.source_score, confidence.consistency_score].filter(
    value => typeof value === 'number',
  ) as number[];
  if (!values.length) return null;
  return values.reduce((total, value) => total + value, 0) / values.length;
}

export function TheoremView({
  items,
  searchResults,
  searchPartial = false,
  selected,
  relations,
  detailLoading,
  detailError,
  loading,
  loadingMore,
  error,
  cursor,
  onLoad,
  onLoadMore,
  onSearch,
  onClearSearch,
  onOpen,
  onClose,
}: {
  items: Theorem[];
  searchResults: Theorem[] | null;
  searchPartial?: boolean;
  selected: Theorem | null;
  relations: {dependencies: any[]; dependents: any[]; evidence: any[]} | null;
  detailLoading: boolean;
  detailError: unknown;
  loading: boolean;
  loadingMore: boolean;
  error: unknown;
  cursor: string | null;
  onLoad: () => void;
  onLoadMore: () => void;
  onSearch: (query: string, reviewStatus?: string) => void;
  onClearSearch: () => void;
  onOpen: (id: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState('');
  const [reviewStatus, setReviewStatus] = useState('');
  const list = searchResults ?? items;
  const errorInfo = error ? describeError(error, '读取定理') : null;
  const detailErrorInfo = detailError ? describeError(detailError, '读取定理关联信息') : null;

  return (
    <div className="library-theorems">
      <header className="library-view-head">
        <div>
          <h2>定理</h2>
          <p>定理对象存储在 XeLibrary catalogue 中；形式验证与候选生成在 Library Agent / Lean 就绪前不可用。</p>
        </div>
        <Button size="sm" onClick={() => { setQuery(''); setReviewStatus(''); onLoad(); }} disabled={loading}>{loading ? '刷新中…' : '刷新'}</Button>
      </header>

      <div className="library-search-bar">
        <Input
          type="search"
          aria-label="搜索定理"
          placeholder="按标题或陈述搜索定理"
          value={query}
          onChange={event => setQuery(event.target.value)}
          onKeyDown={event => event.key === 'Enter' && query.trim() && onSearch(query, reviewStatus)}
        />
        <Select aria-label="定理审查状态" value={reviewStatus} disabled={!query.trim()} onChange={event => { const status = event.target.value; setReviewStatus(status); if (query.trim()) onSearch(query, status); }}>
          <option value="">全部审查状态</option><option value="draft">草稿</option><option value="proposed">待审</option><option value="promoted">已晋升</option><option value="rejected">已拒绝</option>
        </Select>
        <Button onClick={() => onSearch(query, reviewStatus)} disabled={loading || !query.trim()}>搜索</Button>
        {(searchResults !== null || query || reviewStatus) && <Button variant="ghost" onClick={() => { setQuery(''); setReviewStatus(''); onClearSearch(); }}>清除搜索</Button>}
      </div>

      {searchResults !== null && searchPartial && <p className="library-notice" role="status">仅显示部分匹配结果；这不是完整审阅队列。请缩小关键词范围或调整审查状态。</p>}

      {errorInfo ? (
        <ErrorState title={errorInfo.title} message={errorInfo.message} requestId={errorInfo.requestId} errorCode={errorInfo.code} onRetry={() => query.trim() ? onSearch(query, reviewStatus) : onLoad()} />
      ) : loading && !list.length ? (
        <div className="library-result-skeleton" role="status" aria-label="正在加载定理">
          {Array.from({length: 4}, (_, index) => <Skeleton key={index} height={54} />)}
        </div>
      ) : !list.length ? (
        <EmptyState icon="⊢" title={searchResults ? '没有匹配的定理' : '还没有定理'} description="定理由 API 创建；Library Agent 与 Lean verifier 尚未部署。" />
      ) : (
        <>
          <ul className="library-theorem-list">
            {list.map(theorem => {
              const average = confidenceAverage(theorem);
              return (
                <li key={theorem.theorem_id}>
                  <button type="button" className={selected?.theorem_id === theorem.theorem_id ? 'active' : ''} onClick={() => onOpen(theorem.theorem_id)}>
                    <strong>{theorem.title}</strong>
                    <span className="library-theorem-meta">
                      <code>{theorem.path}</code>
                      <Badge tone={theorem.review_status === 'promoted' ? 'success' : theorem.review_status === 'rejected' ? 'danger' : 'neutral'}>
                        {theorem.review_status || 'unknown'}
                      </Badge>
                      <Badge tone={theorem.formal_status === 'verified' ? 'success' : theorem.formal_status === 'failed' ? 'danger' : 'neutral'}>
                        formal: {theorem.formal_status || 'none'}
                      </Badge>
                      {average != null && <span>confidence {average.toFixed(2)}</span>}
                      <small>{relativeTime(theorem.updated_at)}</small>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
          {!searchResults && cursor && (
            <Button onClick={onLoadMore} disabled={loadingMore}>{loadingMore ? '加载中…' : '加载更多定理'}</Button>
          )}
        </>
      )}

      {selected && (
        <section className="library-theorem-detail" aria-label="定理详情">
          <header>
            <h3>{selected.title}</h3>
            <Button size="sm" variant="ghost" onClick={onClose}>关闭</Button>
          </header>
          <div className="library-theorem-badges">
            <Badge tone={selected.formal_status === 'verified' ? 'success' : 'neutral'}>formal_status: {selected.formal_status || 'none'}</Badge>
            <Badge tone={selected.review_status === 'promoted' ? 'success' : 'neutral'}>review_status: {selected.review_status || 'unknown'}</Badge>
            {selected.revision != null && <Badge>r{selected.revision}</Badge>}
          </div>
          <dl className="inspector-meta">
            <div><dt>路径</dt><dd className="mono">{selected.path}</dd></div>
            <div><dt>更新</dt><dd>{relativeTime(selected.updated_at)}</dd></div>
          </dl>
          {selected.statement_natural && <section><h4>Statement</h4><p className="library-prose">{selected.statement_natural}</p></section>}
          {selected.assumptions?.length ? <section><h4>Assumptions</h4><ul>{selected.assumptions.map(item => <li key={item}>{item}</li>)}</ul></section> : null}
          {selected.proof_natural && <section><h4>Natural Proof</h4><pre>{selected.proof_natural}</pre></section>}
          {selected.formal_statement && <section><h4>Formal Statement</h4><pre>{selected.formal_statement}</pre></section>}
          {selected.lean_source && <section><h4>Lean Proof</h4><pre>{selected.lean_source}</pre></section>}
          {detailErrorInfo ? (
            <ErrorState title={detailErrorInfo.title} message={detailErrorInfo.message} requestId={detailErrorInfo.requestId} errorCode={detailErrorInfo.code} onRetry={() => onOpen(selected.theorem_id)} />
          ) : detailLoading ? (
            <p className="muted" role="status">正在读取 evidence 与定理关系…</p>
          ) : (
            <>
              <section>
                <h4>Evidence</h4>
                {relations?.evidence?.length ? <ul>{relations.evidence.map((item, index) => <li key={index} className="mono">{typeof item === 'string' ? item : JSON.stringify(item)}</li>)}</ul> : <p className="muted">没有 evidence_refs。</p>}
              </section>
              <section>
                <h4>Dependencies</h4>
                {relations?.dependencies?.length ? <ul>{relations.dependencies.map((item, index) => <li key={index} className="mono">{typeof item === 'string' ? item : JSON.stringify(item)}</li>)}</ul> : <p className="muted">没有记录依赖。</p>}
              </section>
              <section>
                <h4>Dependents</h4>
                {relations?.dependents?.length ? <ul>{relations.dependents.map((item, index) => <li key={index} className="mono">{typeof item === 'string' ? item : JSON.stringify(item)}</li>)}</ul> : <p className="muted">没有记录被依赖项。</p>}
              </section>
            </>
          )}
          <section className="library-notice">
            <strong>不可用能力</strong>
            <p>Agent derivation unavailable — Library Agent 尚未部署，POST /theorems/{'{id}'}/candidates 会返回 503。</p>
            <p>Lean verification unavailable — Lean 沙箱尚未部署，POST /theorems/{'{id}'}/verify 会返回 503。界面不会显示伪造的 Verified 状态。</p>
          </section>
        </section>
      )}
    </div>
  );
}
