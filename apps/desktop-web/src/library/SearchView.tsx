import {Icon} from '../design-system/Icon';
import React, {useMemo} from 'react';
import {Badge, Button, EmptyState, ErrorState, Input, Select, Skeleton, Tabs, Textarea} from '../design-system/primitives';
import {SEARCH_LIMIT_OPTIONS, describeError, isKeywordResult, parseFieldFilters, searchResultSummary, searchWarnings} from './logic.js';
import type {SearchHit, SearchMeta, SearchMode} from './types';

export function SearchView({
  showFilters,
  onShowFilters,
  mode,
  onMode,
  kind,
  onKind,
  query,
  onQuery,
  path,
  onPath,
  filterText,
  onFilterText,
  limit,
  onLimit,
  resultLimit,
  hits,
  meta,
  error,
  running,
  ran,
  onRun,
  onStructured,
  onOpenHit,
  tableName,
}: {
  showFilters: boolean;
  onShowFilters: (value: boolean) => void;
  mode: SearchMode;
  onMode: (mode: SearchMode) => void;
  kind: 'semantic' | 'keyword';
  onKind: (kind: 'semantic' | 'keyword') => void;
  query: string;
  onQuery: (value: string) => void;
  path: string;
  onPath: (value: string) => void;
  filterText: string;
  onFilterText: (value: string) => void;
  limit: number;
  onLimit: (value: number) => void;
  resultLimit: number | null;
  hits: SearchHit[];
  meta: SearchMeta;
  error: unknown;
  running: boolean;
  ran: boolean;
  onRun: () => void;
  onStructured: () => void;
  onOpenHit: (hit: SearchHit) => void;
  tableName: (tableId: string) => string;
}) {
  const parsed = useMemo(() => parseFieldFilters(filterText), [filterText]);
  const warnings = useMemo(() => searchWarnings(meta), [meta]);
  const errorInfo = error ? describeError(error, '搜索') : null;

  return (
    <div className="library-search">
      <header className="library-view-head">
        <div>
          <h2>搜索 XeLibrary</h2>
          <p>Fast 使用 D100M-Embedding 语义检索；Quality 与 Structured 走各自后端能力。</p>
        </div>
      </header>

      <Tabs
        tabs={[
          {id: 'fast', label: 'Fast'},
          {id: 'quality', label: 'Quality'},
          {id: 'structured', label: 'Structured'},
        ]}
        value={mode}
        onChange={id => {
          if (id === 'structured') onStructured();
          else onMode(id as SearchMode);
        }}
        ariaLabel="搜索模式"
      />

      <div className="library-search-bar">
        <Input
          type="search"
          aria-label="搜索 XeLibrary"
          placeholder="搜索知识库内容…"
          value={query}
          onChange={event => onQuery(event.target.value)}
          onKeyDown={event => event.key === 'Enter' && onRun()}
        />
        <label className="library-search-limit">
          结果条数
          <Select
            aria-label="结果条数上限"
            value={String(limit)}
            onChange={event => onLimit(Number(event.target.value))}
          >
            {SEARCH_LIMIT_OPTIONS.map(option => <option key={option} value={String(option)}>{option}</option>)}
          </Select>
        </label>
        <Button variant="primary" onClick={onRun} disabled={running}>{running ? '搜索中…' : '搜索'}</Button>
      </div>

      {mode === 'fast' && (
        <label className="library-search-kind">
          <input type="checkbox" checked={kind === 'keyword'} onChange={event => onKind(event.target.checked ? 'keyword' : 'semantic')} />
          关键词优先（SQLite FTS / bm25），关闭则使用 D100M 语义检索
        </label>
      )}
      {mode === 'quality' && (
        <p className="library-notice" role="status">
          Quality 对候选记录进一步精排，可能需要更长时间；服务不可用时会显示原因，可改用 Fast 或 Structured。
        </p>
      )}

      <div className="library-search-filters">
        <button type="button" className="library-link" onClick={() => onShowFilters(!showFilters)}>
          {showFilters ? '收起过滤条件' : '过滤条件'}
        </button>
        {showFilters && (
          <div className="library-search-filter-body">
            <label>
              路径模式
              <Input value={path} onChange={event => onPath(event.target.value)} placeholder="/**" aria-label="搜索路径模式" />
              <small>支持 <code>/**</code> 跨层级、<code>*</code> 单层通配；默认 / ** 表示全库。</small>
            </label>
            <label>
              字段精确匹配
              <Textarea
                aria-label="字段过滤"
                placeholder={'type = theorem\nconfidence = 0.91\nlabel = "0.91"'}
                value={filterText}
                onChange={event => onFilterText(event.target.value)}
              />
              <small>每行一条 <code>字段 = 值</code>。数字、true / false、null、数组和对象按 JSON 类型精确匹配；普通文本是字符串；以数字开头的文本及数字形态字符串须加双引号，例如 <code>"0.91"</code>。JSON 字符串使用双引号；非法 JSON、非有限数和不安全整数会阻止整次搜索。</small>
            </label>
            {parsed.errors.map((message, index) => <p key={index} className="library-error" role="alert">{message}</p>)}
          </div>
        )}
      </div>

      {warnings.length > 0 && (
        <div className="library-warning" role="alert">
          <strong><Icon value="warning" size={16} /> 结果可能不完整</strong>
          <ul>{warnings.map((warning, index) => <li key={index}>{warning}</li>)}</ul>
        </div>
      )}

      {errorInfo ? (
        <ErrorState title={errorInfo.title} message={errorInfo.message} requestId={errorInfo.requestId} errorCode={errorInfo.code} onRetry={onRun} />
      ) : running ? (
        <div className="library-result-skeleton" role="status" aria-label="正在搜索">
          {Array.from({length: 5}, (_, index) => <Skeleton key={index} height={58} />)}
        </div>
      ) : ran && !hits.length ? (
        <EmptyState
          icon="⌕"
          title={warnings.length ? '当前可见结果为空，但索引并不完整' : '没有搜索结果'}
          description={
            warnings.length
              ? '部分记录尚未建立可用索引，因此“没有搜索到”不等于“知识库中没有”。'
              : '尝试更短的关键词、更大的路径范围，或改用 Structured 查询。'
          }
        />
      ) : hits.length ? (
        <>
          <p className="library-result-count" role="status">
            <span className="library-result-count-main">{searchResultSummary(resultLimit ?? limit, hits.length)}</span>
            <small>指本次请求的上限与返回数，不代表知识库中匹配的总数。</small>
          </p>
          <ol className="library-results">
          {hits.map(hit => (
            <li key={`${hit.table_id}:${hit.row_id}`}>
              <button type="button" onClick={() => onOpenHit(hit)}>
                <div className="library-result-head">
                  <strong>{hit.snippet?.split('\n')[0]?.slice(0, 80) || hit.row_id}</strong>
                  <span className="library-result-score" title={`score ${hit.score}`}>
                    {isKeywordResult(hit.score_type) ? 'Keyword' : 'Semantic'} · {hit.score.toFixed(3)}
                  </span>
                </div>
                <div className="library-result-meta">
                  <span>{hit.path}</span>
                  <span>{tableName(hit.table_id)}</span>
                  <span className="mono">{hit.row_id.slice(0, 8)}…</span>
                  {hit.approximate ? <Badge tone="warning">approximate</Badge> : null}
                  {typeof hit.indexed_revision === 'number' && hit.indexed_revision !== hit.row_revision ? (
                    <Badge tone="warning">索引 revision 过期</Badge>
                  ) : null}
                </div>
                <p className="library-result-snippet">{hit.snippet}</p>
              </button>
            </li>
          ))}
          </ol>
        </>
      ) : (
        <EmptyState icon="⌕" title="统一搜索" description="在 Fast 中输入关键词，或在 Structured 中编写 Query DSL。" />
      )}
    </div>
  );
}
