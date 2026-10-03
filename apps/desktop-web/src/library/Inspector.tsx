import {normalizeRowValues, parseExactJSON, stringifyExactJSON} from './json.js';
import React, {useEffect, useMemo, useState} from 'react';
import {Badge, Button, ErrorState, IconButton, Input, Select, Skeleton, Spinner, StatusDot, Tabs, Textarea} from '../design-system/primitives';
import type {UploadStore} from '../upload-state.js';
import {Attachments} from '../Attachments';
import {changedFields, describeError, indexStatusFromJobs, relativeTime} from './logic.js';
import type {Column, Job, Row, Table} from './types';

export type InspectorTab = 'overview' | 'fields' | 'attachments' | 'history' | 'permissions' | 'index';

const TABS: Array<{id: InspectorTab; label: string}> = [
  {id: 'overview', label: '概览'},
  {id: 'fields', label: '字段'},
  {id: 'attachments', label: '附件'},
  {id: 'history', label: '历史'},
  {id: 'permissions', label: '权限'},
  {id: 'index', label: '索引'},
];

export type ConflictInfo = {rowId: string | null; localRevision: number; currentRevision: number; message: string};

export function Inspector({
  table,
  columns,
  row,
  selectionSize,
  api,
  apiPage,
  apiRaw,
  scopes,
  jobs,
  jobsError,
  canAdmin,
  onReloadJobs,
  onReindex,
  conflict,
  onSave,
  onDelete,
  onRestore,
  onCreate,
  onReloadRow,
  onToggleFavorite,
  onClose,
  uploadStore,
  identityKey = '',
}: {
  table: Table;
  columns: Column[];
  row: Row | null;
  selectionSize: number;
  api: (path: string, init?: RequestInit) => Promise<any>;
  apiPage: (path: string, init?: RequestInit) => Promise<{data: any; meta: Record<string, any>}>;
  apiRaw: (path: string, init?: RequestInit) => Promise<Response>;
  scopes: string[];
  jobs: Job[] | null;
  jobsError: unknown;
  canAdmin: boolean;
  onReloadJobs: () => void;
  onReindex: () => void;
  conflict: ConflictInfo | null;
  onSave: (values: Record<string, any>, revision: number) => Promise<void>;
  onDelete: (revision: number) => Promise<void>;
  onRestore: (revision: number, currentRevision: number) => Promise<void>;
  onCreate: (values: Record<string, any>) => Promise<void>;
  onReloadRow: () => void;
  onToggleFavorite: () => void;
  onClose: () => void;
  uploadStore?: UploadStore;
  identityKey?: string;
}) {
  const [tab, setTab] = useState<InspectorTab>('overview');
  const [draft, setDraft] = useState<Record<string, any>>({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [history, setHistory] = useState<Row[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [compare, setCompare] = useState<Row | null>(null);
  const [acl, setAcl] = useState<{table: any; path: any; row: any} | null>(null);
  const [aclError, setAclError] = useState<unknown>(null);

  const canWrite = scopes.includes('xelibrary:write');
  const canAttach = scopes.includes('xelibrary:attach');

  useEffect(() => {
    setError(null);
    setMessage('');
    setCompare(null);
    if (row) setDraft({...row.values});
    else setDraft(Object.fromEntries(columns.map(column => [column.name, column.default_value])));
  }, [row?._row_id, columns, row?.values, row]);

  useEffect(() => {
    if (tab !== 'history' || !row) return;
    let active = true;
    setHistoryLoading(true);
    api(`/tables/${table.id}/rows/${row._row_id}/revisions`)
      .then(rows => active && setHistory(rows as Row[]))
      .catch(cause => active && setError(cause))
      .finally(() => active && setHistoryLoading(false));
    return () => {
      active = false;
    };
  }, [tab, row?._row_id, api, table.id, row]);

  useEffect(() => {
    if (tab !== 'permissions' || !canAdmin) return;
    let active = true;
    setAclError(null);
    const requests: Array<Promise<any>> = [
      api(`/acl/table/${table.id}`).catch(cause => {
        throw cause;
      }),
      api(`/acl/path/${encodeURI(table.path)}`),
    ];
    if (row) requests.push(api(`/acl/row/${table.id}:${row._row_id}`));
    Promise.all(requests)
      .then(([tableAcl, pathAcl, rowAcl]) => active && setAcl({table: tableAcl, path: pathAcl, row: rowAcl || null}))
      .catch(cause => active && setAclError(cause));
    return () => {
      active = false;
    };
  }, [tab, canAdmin, api, table.id, table.path, row]);

  const changed = row ? changedFields(row.values, draft) : [];
  const indexStatus = useMemo(() => indexStatusFromJobs(jobs || []), [jobs]);

  function setValue(column: Column, raw: string) {
    let value: any = raw;
    if (column.type === 'int64') value = raw === '' ? null : raw;
    if (column.type === 'float64') value = raw === '' ? null : Number(raw);
    if (column.type === 'boolean') value = raw === '' ? null : raw === 'true';
    if (['json', 'attachment_list'].includes(column.type) && raw !== '') {
      try {
        value = parseExactJSON(raw);
      } catch {
        value = raw;
      }
    }
    setDraft(current => ({...current, [column.name]: value}));
  }

  async function submit() {
    setBusy(true);
    setError(null);
    setMessage('');
    try {
      const values = normalizeRowValues(columns, draft);
      if (row) await onSave(values, row._revision);
      else await onCreate(values);
      setMessage(row ? '已保存' : '记录已创建');
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (!row) return;
    setBusy(true);
    setError(null);
    try {
      await onDelete(row._revision);
      setMessage('记录已软删除，历史仍然保留');
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }

  async function restore(revision: number) {
    if (!row) return;
    setBusy(true);
    setError(null);
    try {
      await onRestore(revision, row._revision);
      setMessage(`已从 r${revision} 恢复；这会创建一个新的 revision，不会覆盖历史`);
    } catch (cause) {
      setError(cause);
    } finally {
      setBusy(false);
    }
  }

  const errorInfo = error ? describeError(error, row ? '保存记录' : '创建记录') : null;

  return (
    <aside className="knowledge-inspector record-editor" aria-label="对象 Inspector">
      <header className="inspector-head">
        <div>
          <strong>{row ? '记录 Inspector' : '新建记录'}</strong>
          <small>{row ? row._row_id.slice(0, 12) + '…' : `${table.path}/${table.name}`}</small>
        </div>
        <IconButton label="关闭 Inspector" onClick={onClose}>✕</IconButton>
      </header>

      <Tabs tabs={TABS.map(entry => ({id: entry.id, label: entry.label}))} value={tab} onChange={id => setTab(id as InspectorTab)} ariaLabel="Inspector 分类" className="inspector-tabs" />

      <div className="inspector-body">
        {conflict && (row ? conflict.rowId === row._row_id : true) && (
          <div className="inspector-conflict" role="alert">
            <strong>记录已被他人修改</strong>
            <p>本地 r{conflict.localRevision}，服务端 r{conflict.currentRevision}；本次未提交。</p>
            <div className="ds-button-row">
              <Button size="sm" onClick={onReloadRow}>重新加载</Button>
              <Button size="sm" variant="ghost" onClick={() => undefined} disabled title="diff 详情见历史标签">对比</Button>
            </div>
          </div>
        )}

        {errorInfo && <ErrorState title={errorInfo.title} message={errorInfo.message} requestId={errorInfo.requestId} errorCode={errorInfo.code} />}
        {message && <p role="status" className="inspector-status">{message}</p>}

        {tab === 'overview' && (
          <div className="inspector-overview">
            {!row ? (
              <>
                <p>未选择记录。填写字段后可创建一条新记录。</p>
                <p className="muted">已选择 {selectionSize} 行；批量操作暂未实现。</p>
              </>
            ) : (
              <dl className="inspector-meta">
                <div><dt>知识表</dt><dd>{table.path}/{table.name}</dd></div>
                <div><dt>Row ID</dt><dd className="mono">{row._row_id}</dd></div>
                <div><dt>Revision</dt><dd>r{row._revision}</dd></div>
                <div><dt>Created</dt><dd>{relativeTime(row._created_at)}</dd></div>
                <div><dt>Updated</dt><dd>{relativeTime(row._updated_at)}</dd></div>
                <div><dt>Deleted</dt><dd>{row._deleted_at ? <Badge tone="danger">已软删除</Badge> : '有效'}</dd></div>
              </dl>
            )}
            <Button size="sm" onClick={onToggleFavorite}>★ 收藏此表</Button>
            {row && (
              <Attachments
                uploadStore={uploadStore}
                key={`overview:${identityKey}:${table.id}:${row._row_id}`}
                tableId={table.id}
                rowId={row._row_id}
                api={api}
                apiPage={apiPage}
                apiRaw={apiRaw}
                canAttach={canAttach}
                canRead={scopes.includes('xelibrary:read')}
                onChanged={async () => undefined}
              />
            )}
          </div>
        )}

        {tab === 'fields' && (
          <div className="inspector-fields">
            {columns.map(column => {
              const value = draft[column.name];
              const text = value === null || value === undefined ? '' : typeof value === 'object' ? stringifyExactJSON(value) : String(value);
              return (
                <label key={column.name} className="inspector-field">
                  <span className="inspector-field-name">
                    {column.name}
                    <small>{column.type}{column.nullable ? ' · 可空' : ' · 必填'}</small>
                  </span>
                  {column.type === 'boolean' ? (
                    <Select value={text} disabled={!canWrite || Boolean(row?._deleted_at) || busy} onChange={event => setValue(column, event.target.value)}>
                      <option value="">空</option>
                      <option value="true">是</option>
                      <option value="false">否</option>
                    </Select>
                  ) : column.type === 'text' || ['json', 'attachment_list'].includes(column.type) ? (
                    <Textarea disabled={!canWrite || Boolean(row?._deleted_at) || busy} value={text} onChange={event => setValue(column, event.target.value)} />
                  ) : (
                    <Input
                      type={column.type === 'float64' ? 'number' : 'text'}
                      inputMode={column.type === 'int64' ? 'numeric' : undefined}
                      disabled={!canWrite || Boolean(row?._deleted_at) || busy}
                      value={text}
                      onChange={event => setValue(column, event.target.value)}
                    />
                  )}
                </label>
              );
            })}
            {row && changed.length > 0 && <p className="muted">将提交字段：{changed.join('、')}</p>}
            <div className="ds-button-row">
              <Button variant="primary" disabled={!canWrite || busy || Boolean(row?._deleted_at)} onClick={() => void submit()}>
                {busy ? '保存中…' : row ? '保存记录' : '创建记录'}
              </Button>
              {row && !row._deleted_at && (
                <Button variant="danger" disabled={!canWrite || busy} onClick={() => void remove()}>软删除记录</Button>
              )}
              {row?._deleted_at && canWrite && (
                <Button disabled={busy} onClick={() => void restore(row._revision)}>恢复为有效</Button>
              )}
            </div>
            {!canWrite && <p className="muted">当前身份没有 xelibrary:write，字段为只读。</p>}
          </div>
        )}

        {tab === 'attachments' && (
          row ? (
            <Attachments
              uploadStore={uploadStore}
              key={`${identityKey}:${table.id}:${row._row_id}`}
              tableId={table.id}
              rowId={row._row_id}
              api={api}
              apiPage={apiPage}
              apiRaw={apiRaw}
              canAttach={canAttach}
                canRead={scopes.includes('xelibrary:read')}
              onChanged={async () => undefined}
            />
          ) : (
            <p className="muted">选择一条记录后可以查看与管理附件。</p>
          )
        )}

        {tab === 'history' && (
          <div className="inspector-history">
            {!row ? (
              <p className="muted">选择一条记录后可以查看 revision 时间线。</p>
            ) : historyLoading ? (
              <Skeleton height={120} />
            ) : !history.length ? (
              <p className="muted">暂无历史记录。</p>
            ) : (
              <>
                <ol className="history-timeline">
                  {[...history].reverse().map(revision => (
                    <li key={revision._revision} className={revision._revision === row._revision ? 'current' : ''}>
                      <div>
                        <strong>r{revision._revision}</strong>
                        {revision._revision === row._revision ? <Badge tone="accent">current</Badge> : null}
                        {revision._deleted_at ? <Badge tone="danger">已删除</Badge> : null}
                      </div>
                      <small>{new Date(revision._updated_at).toLocaleString('zh-CN')}</small>
                      <div className="ds-button-row">
                        <Button size="sm" variant="ghost" onClick={() => setCompare(revision)}>查看</Button>
                        <Button
                          size="sm"
                          disabled={!canWrite || busy || revision._revision === row._revision}
                          onClick={() => void restore(revision._revision)}
                          title="恢复会创建新的 revision，不会覆盖历史"
                        >
                          恢复此版本
                        </Button>
                      </div>
                    </li>
                  ))}
                </ol>
                {compare && (
                  <div className="history-compare">
                    <h4>r{compare._revision} 与当前 r{row._revision} 的差异</h4>
                    {changedFields(compare.values, row.values).length ? (
                      <ul>
                        {changedFields(compare.values, row.values).map(name => (
                          <li key={name}>
                            <strong>{name}</strong>
                            <div className="history-diff">
                              <pre>{stringifyExactJSON(compare.values[name] ?? null, 2)}</pre>
                              <pre>{stringifyExactJSON(row.values[name] ?? null, 2)}</pre>
                            </div>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="muted">字段内容与当前 revision 相同（revision 变更可能来自删除/恢复）。</p>
                    )}
                    <p className="muted">恢复操作会新增一个 revision，并完整保留当前历史。</p>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {tab === 'permissions' && (
          <div className="inspector-permissions">
            <h4>当前身份能力</h4>
            <dl className="inspector-meta">
              <div><dt>读取 scope</dt><dd><StatusDot tone={scopes.includes('xelibrary:read') ? 'success' : 'neutral'} />{scopes.includes('xelibrary:read') ? '身份具备 xelibrary:read' : '身份未声明 xelibrary:read'}</dd></div>
              <div><dt>写入 scope</dt><dd>{canWrite ? '身份具备 xelibrary:write' : '身份未声明 xelibrary:write'}</dd></div>
              <div><dt>查询 scope</dt><dd>{scopes.includes('xelibrary:query') ? '身份具备 xelibrary:query' : '身份未声明 xelibrary:query'}</dd></div>
              <div><dt>附件 scope</dt><dd>{canAttach ? '身份具备 xelibrary:attach' : '身份未声明 xelibrary:attach'}</dd></div>
              <div><dt>管理 scope</dt><dd>{canAdmin ? '身份具备 xelibrary:admin' : '身份未声明 xelibrary:admin'}</dd></div>
            </dl>
            {canAdmin ? (
              aclError ? (
                <ErrorState title="无法读取 ACL" message={describeError(aclError).message} requestId={describeError(aclError).requestId} errorCode={describeError(aclError).code} onRetry={() => setTab('permissions')} />
              ) : acl ? (
                <div className="acl-effective">
                  <h4>ACL 来源</h4>
                  <AclSource label={`路径 ${table.path}`} entry={acl.path} inherited />
                  <AclSource label={`表 ${table.name}`} entry={acl.table} />
                  {row && <AclSource label={`记录 ${row._row_id.slice(0, 8)}…`} entry={acl.row} />}
                  <p className="muted">优先级：记录 override → 表 override → 路径继承。未显式设置的层级显示为“继承”。</p>
                </div>
              ) : (
                <Spinner label="正在读取 ACL" />
              )
            ) : (
              <p className="muted">
                这里显示登录身份的 scopes，不代表某张表、某条路径或某行一定可访问。资源 ACL 与身份 scope 会由服务端在每次请求时共同复核；403 表示该资源操作仍被拒绝。
              </p>
            )}
          </div>
        )}

        {tab === 'index' && (
          <div className="inspector-index">
            {!canAdmin ? (
              <div className="library-notice" role="status">
                <strong>索引状态不可用</strong>
                <p>GET /api/v1/jobs 需要 xelibrary:admin 权限；当前身份无法读取该表的索引任务。这里不会显示推测的索引状态。</p>
              </div>
            ) : jobsError ? (
              <ErrorState
                title="索引状态不可用"
                requestId={describeError(jobsError).requestId} errorCode={describeError(jobsError).code}
                message={describeError(jobsError).message + '（普通用户无 xelibrary:admin 时无法读取索引任务。）'}
                onRetry={onReloadJobs}
              />
            ) : (
              <>
                <dl className="inspector-meta">
                  <div><dt>状态</dt><dd><Badge tone={indexStatus.state === 'ready' ? 'success' : indexStatus.state === 'failed' ? 'danger' : indexStatus.state === 'not_indexed' ? 'neutral' : 'warning'}>{indexStatus.state}</Badge></dd></div>
                  <div><dt>说明</dt><dd>{indexStatus.detail}</dd></div>
                  {indexStatus.latest && <div><dt>最近任务</dt><dd className="mono">{indexStatus.latest.job_id}</dd></div>}
                  {indexStatus.latest && <div><dt>更新时间</dt><dd>{relativeTime(indexStatus.latest.updated_at || indexStatus.latest.created_at)}</dd></div>}
                </dl>
                <p className="muted">
                  当前平台只暴露任务级索引信息（GET /api/v1/jobs?table_id=...），没有每表 chunk 进度与 indexed_revision API；
                  这里不会显示推测的进度数字。细粒度 Index Status 需要独立 Core Issue。
                </p>
                <div className="ds-button-row">
                  <Button size="sm" onClick={onReloadJobs}>刷新索引任务</Button>
                  {canAdmin && <Button size="sm" variant="primary" onClick={onReindex} disabled={Boolean(indexStatus.latest && (indexStatus.latest.status === 'queued' || indexStatus.latest.status === 'running'))}>重建 Embedding 索引</Button>}
                </div>
              </>
            )}
          </div>
        )}
      </div>
    </aside>
  );
}

function AclSource({label, entry, inherited = false}: {label: string; entry: any; inherited?: boolean}) {
  const grants = entry?.grants || {};
  const subjects = Object.entries(grants).filter(([, actions]) => Array.isArray(actions) && actions.length);
  return (
    <div className="acl-source">
      <strong>{label}</strong>
      {entry?.explicit ? (
        subjects.length ? (
          <ul>
            {subjects.map(([subject, actions]) => (
              <li key={subject}>
                <code>{subject}</code> <Badge>{(actions as string[]).join(' · ')}</Badge>
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted">显式设置但没有授予任何主体。</p>
        )
      ) : (
        <p className="muted">{inherited ? '继承自上一层级' : '无显式规则，继承上一层级'}</p>
      )}
    </div>
  );
}
