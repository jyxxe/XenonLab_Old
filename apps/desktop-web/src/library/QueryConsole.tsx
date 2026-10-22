import {stringifyExactJSON} from './json.js';
import React, {useState} from 'react';
import {Badge, Button, EmptyState, ErrorState, Skeleton, Textarea} from '../design-system/primitives';
import {describeError, formatNumber, withQueryPagination} from './logic.js';
import {QUERY_EXPRESSION_LIMIT, queryExpressionLength} from './query-drafts.js';
import type {QueryExplain} from './types';

const KNOWN_UNSUPPORTED = [
  'EXISTS(...)',
  'SEARCH TEXT ...',
  'HAS_ATTACHMENT(...)',
  '行上下文函数（附件数量、路径内聚合）',
  '正则表达式（MATCHES 只支持 LIKE 风格的 % 与 _）',
];

export function QueryConsole({
  expression,
  onExpression,
  rows,
  meta,
  plan,
  error,
  running,
  validating,
  duration,
  recent,
  draftStatus,
  draftMessage,
  queryEnabled,
  onRun,
  onValidate,
  onOpenRow,
  onLoadRecent,
  onDeleteRecent,
  onClearRecent,
  onRetryStorage,
  tableName,
}: {
  expression: string;
  onExpression: (value: string) => void;
  rows: any[];
  meta: Record<string, any>;
  plan: QueryExplain | null;
  error: unknown;
  running: boolean;
  validating: boolean;
  duration: number | null;
  recent: Array<{expression: string; updated_at: string}>;
  draftStatus: string;
  draftMessage: string;
  queryEnabled: boolean;
  onRun: (expression: string, remember?: boolean) => void;
  onValidate: (expression: string) => Promise<boolean> | void;
  onOpenRow: (tableId: string, rowId: string) => void;
  onLoadRecent: (item: {expression: string}) => void;
  onDeleteRecent: (expression: string) => void;
  onClearRecent: () => void;
  onRetryStorage: () => void;
  tableName: (tableId: string) => string;
}) {
  const [showUnsupported, setShowUnsupported] = useState(false);
  const [copyState, setCopyState] = useState('');
  const offset = plan?.offset ?? 0;
  const errorInfo = error ? describeError(error, '执行查询') : null;

  const pageQuery = (nextOffset: number) => withQueryPagination(expression, plan?.limit ?? 100, nextOffset);

  return (
    <div className="library-query">
      <header className="library-view-head">
        <div>
          <h2>查询控制台</h2>
          <p>Query DSL 在服务端按有界 AST 解释执行；未实现的语法会返回明确错误，不会被忽略。</p>
        </div>
        <Badge tone={meta.approximate ? 'warning' : 'success'}>{meta.approximate ? 'approximate' : 'exact'}</Badge>
      </header>

      <Textarea
        className="library-query-editor"
        aria-label="查询表达式"
        spellCheck={false}
        value={expression}
        onChange={event => onExpression(event.target.value)}
        onKeyDown={event => {
          if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') onRun(expression);
        }}
      />

      <div className="library-query-history" aria-label="查询草稿与历史">
        <p>当前表达式 {queryExpressionLength(expression)} / {QUERY_EXPRESSION_LIMIT} 个字符。草稿按已验证账号保存在本机；校验通过后加入最近查询，最多保留 20 条并按表达式去重。载入只恢复表达式，不会执行查询。</p>
        {draftStatus === 'unverified' && <p role="status">正在确认当前账号；确认前不会读取或保存查询记录。</p>}
        {draftStatus === 'loading' && <p role="status">正在读取当前账号的查询草稿…</p>}
        {!queryEnabled && <p role="status">身份正在验证；验证完成前查询操作已暂停。</p>}
        {draftStatus === 'read-error' || draftStatus === 'write-error' ? <div role="alert"><span>{draftMessage}</span> <Button size="sm" onClick={onRetryStorage}>重试本地存储</Button></div> : draftMessage && <p role="status">{draftMessage}</p>}
        <details>
          <summary>最近查询（{recent.length} / 20）</summary>
          {recent.length ? <>
            <ol>{recent.map(item => <li key={item.expression}>
              <code title={item.expression}>{item.expression.length > 180 ? `${item.expression.slice(0, 180)}…` : item.expression}</code>
              <time>{item.updated_at ? new Date(item.updated_at).toLocaleString() : ''}</time>
              <Button size="sm" onClick={() => onLoadRecent(item)}>载入草稿</Button>
              <Button size="sm" variant="ghost" aria-label="删除这条最近查询" onClick={() => onDeleteRecent(item.expression)}>删除</Button>
            </li>)}</ol>
            <Button size="sm" variant="ghost" onClick={onClearRecent}>清空最近查询</Button>
          </> : <p>没有最近查询。</p>}
        </details>
      </div>

      <div className="library-query-toolbar">
        <Button variant="primary" onClick={() => onRun(expression)} disabled={!queryEnabled || running}>{running ? '执行中…' : '运行'}</Button>
        <Button onClick={() => void onValidate(expression)} disabled={!queryEnabled || running || validating}>{validating ? '校验中…' : '校验语法'}</Button>
        <Button
          onClick={async () => {
            try {
              await navigator.clipboard?.writeText(expression);
              setCopyState('已复制');
            } catch {
              setCopyState('复制失败');
            }
            setTimeout(() => setCopyState(''), 1500);
          }}
        >
          复制查询
        </Button>
        <Button variant="ghost" onClick={() => setShowUnsupported(value => !value)}>已知未实现语法</Button>
        {copyState && <span className="muted" role="status">{copyState}</span>}
        {duration != null && <span className="muted">耗时 {duration} ms</span>}
        <span className="muted">结果 {formatNumber(rows.length)} 行</span>
      </div>

      {showUnsupported && (
        <div className="library-notice">
          <strong>Known unsupported features</strong>
          <ul>{KNOWN_UNSUPPORTED.map(item => <li key={item}><code>{item}</code></li>)}</ul>
          <p>需要这些能力时应创建独立 Core Issue，而不是在 UI 中伪装为可用。</p>
        </div>
      )}

      {plan && (
        <dl className="library-query-plan">
          <div><dt>路径</dt><dd className="mono">{plan.path}</dd></div>
          <div><dt>LIMIT</dt><dd>{plan.limit}</dd></div>
          <div><dt>OFFSET</dt><dd>{plan.offset}</dd></div>
          <div><dt>ORDER BY</dt><dd>{plan.order_by ? stringifyExactJSON(plan.order_by) : '无'}{plan.descending ? ' DESC' : ''}</dd></div>
          <div><dt>执行器</dt><dd>{plan.execution}</dd></div>
        </dl>
      )}

      {errorInfo ? (
        <ErrorState title={errorInfo.title} message={errorInfo.message} requestId={errorInfo.requestId} errorCode={errorInfo.code} onRetry={() => onRun(expression)} />
      ) : running ? (
        <div className="library-result-skeleton" role="status" aria-label="正在执行查询">
          {Array.from({length: 5}, (_, index) => <Skeleton key={index} height={30} />)}
        </div>
      ) : rows.length ? (
        <>
          <div className="table-scroll library-query-results">
            <table className="ds-table">
              <thead>
                <tr><th>表</th><th>路径</th><th>Row ID</th><th>Revision</th><th>值</th><th /></tr>
              </thead>
              <tbody>
                {rows.map(item => (
                  <tr key={`${item.table_id}:${item._row_id}`}>
                    <td title={item.table_id}>{tableName(item.table_id)}</td>
                    <td>{item.path}</td>
                    <td className="mono">{String(item._row_id).slice(0, 10)}…</td>
                    <td>r{item._revision}</td>
                    <td title={stringifyExactJSON(item.values)}>{stringifyExactJSON(item.values).slice(0, 160)}</td>
                    <td><Button size="sm" variant="ghost" onClick={() => onOpenRow(item.table_id, item._row_id)}>定位</Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="library-query-pagination">
            <Button size="sm" disabled={offset === 0 || running} onClick={() => { const next = Math.max(0, offset - (plan?.limit ?? 100)); onRun(pageQuery(next), false); }}>
              上一页
            </Button>
            <span className="muted">OFFSET {offset}</span>
            <Button
              size="sm"
              disabled={rows.length < (plan?.limit ?? 100) || running}
              onClick={() => { const next = offset + (plan?.limit ?? 100); onRun(pageQuery(next), false); }}
            >
              下一页
            </Button>
          </div>
        </>
      ) : plan ? (
        <EmptyState icon="{ }" title="查询没有返回记录" description="表达式语法有效，但没有匹配的行。" />
      ) : (
        <EmptyState icon="{ }" title="Query Console" description={'例如：FROM "/数学/**" WHERE type = "theorem" ORDER BY updated_at DESC LIMIT 100'} />
      )}
    </div>
  );
}
