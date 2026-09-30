import React, {useState} from 'react';
import {Badge, Button, EmptyState, ErrorState, Skeleton, Textarea} from '../design-system/primitives';
import {describeError, formatNumber} from './logic.js';
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
  duration,
  onRun,
  onValidate,
  onOpenRow,
  tableName,
}: {
  expression: string;
  onExpression: (value: string) => void;
  rows: any[];
  meta: Record<string, any>;
  plan: QueryExplain | null;
  error: unknown;
  running: boolean;
  duration: number | null;
  onRun: (expression: string) => void;
  onValidate: (expression: string) => Promise<boolean> | void;
  onOpenRow: (tableId: string, rowId: string) => void;
  tableName: (tableId: string) => string;
}) {
  const [showUnsupported, setShowUnsupported] = useState(false);
  const [copyState, setCopyState] = useState('');
  const [offset, setOffset] = useState(0);
  const errorInfo = error ? describeError(error, '执行查询') : null;

  const page = () => {
    const limit = plan?.limit || 100;
    return expression.replace(/\bLIMIT\s+\d+/i, `LIMIT ${limit}`).replace(/\bOFFSET\s+\d+/i, '');
  };
  const withOffset = (next: number) => `${page().trim()} OFFSET ${next}`;

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

      <div className="library-query-toolbar">
        <Button variant="primary" onClick={() => onRun(expression)} disabled={running}>{running ? '执行中…' : '运行'}</Button>
        <Button onClick={() => void onValidate(expression)} disabled={running}>校验语法</Button>
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
          <div><dt>ORDER BY</dt><dd>{plan.order_by ? JSON.stringify(plan.order_by) : '无'}{plan.descending ? ' DESC' : ''}</dd></div>
          <div><dt>执行器</dt><dd>{plan.execution}</dd></div>
        </dl>
      )}

      {errorInfo ? (
        <ErrorState title={errorInfo.title} message={errorInfo.message} requestId={errorInfo.code} onRetry={() => onRun(expression)} />
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
                    <td title={JSON.stringify(item.values)}>{JSON.stringify(item.values).slice(0, 160)}</td>
                    <td><Button size="sm" variant="ghost" onClick={() => onOpenRow(item.table_id, item._row_id)}>定位</Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="library-query-pagination">
            <Button size="sm" disabled={offset === 0 || running} onClick={() => { const next = Math.max(0, offset - (plan?.limit || 100)); setOffset(next); onRun(withOffset(next)); }}>
              上一页
            </Button>
            <span className="muted">OFFSET {offset}</span>
            <Button
              size="sm"
              disabled={rows.length < (plan?.limit || 100) || running}
              onClick={() => { const next = offset + (plan?.limit || 100); setOffset(next); onRun(withOffset(next)); }}
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
