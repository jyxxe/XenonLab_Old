/**
 * Pure helpers for the XeLibrary application. Kept as plain JS so the Node test
 * runner can exercise them without a bundler.
 */

export function formatNumber(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('zh-CN') : '—';
}

export function formatBytes(value) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return '—';
  if (value < 1024) return `${value} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let size = value / 1024;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return `${size.toFixed(size >= 10 ? 0 : 1)} ${units[index]}`;
}

export function relativeTime(value, now = Date.now()) {
  const stamp = value ? Date.parse(value) : NaN;
  if (Number.isNaN(stamp)) return '—';
  const seconds = Math.max(0, (now - stamp) / 1000);
  if (seconds < 60) return '刚刚';
  if (seconds < 3600) return `${Math.round(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)} 小时前`;
  if (seconds < 30 * 86400) return `${Math.round(seconds / 86400)} 天前`;
  return new Date(stamp).toLocaleDateString('zh-CN');
}

export function sortableValue(value) {
  if (value === null || value === undefined) return [0, ''];
  if (typeof value === 'boolean') return [1, value ? 1 : 0];
  if (typeof value === 'number') return [2, value];
  if (typeof value === 'string') return [3, value];
  return [4, JSON.stringify(value)];
}

function compare(left, right) {
  const a = sortableValue(left);
  const b = sortableValue(right);
  if (a[0] !== b[0]) return a[0] - b[0];
  if (a[1] === b[1]) return 0;
  return a[1] > b[1] ? 1 : -1;
}

export const SYSTEM_FIELDS = [
  {name: '_row_id', label: 'Row ID', type: 'string'},
  {name: '_revision', label: 'Revision', type: 'int64'},
  {name: '_updated_at', label: 'Updated', type: 'datetime'},
  {name: '_created_at', label: 'Created', type: 'datetime'},
];

/** Resolve a sort/filter key against either a system field or a schema column. */
export function rowValue(row, key) {
  if (!row) return null;
  if (key.startsWith('_')) return row[key];
  return row.values ? row.values[key] : null;
}

export function sortRows(rows, key, direction = 'asc') {
  const factor = direction === 'desc' ? -1 : 1;
  return [...rows].sort((left, right) => factor * compare(rowValue(left, key), rowValue(right, key)));
}

export function filterRows(rows, query) {
  const needle = String(query || '').trim().toLowerCase();
  if (!needle) return rows;
  return rows.filter(row => {
    if (row._row_id.toLowerCase().includes(needle)) return true;
    return Object.values(row.values || {}).some(value => {
      if (value === null || value === undefined) return false;
      return String(typeof value === 'object' ? JSON.stringify(value) : value).toLowerCase().includes(needle);
    });
  });
}

/** Build a logical path tree from catalogue rows. */
export function buildPathTree(tables) {
  const root = {path: '/', name: '/', tables: [], children: [], count: 0};
  const nodes = new Map([['/', root]]);
  const ensure = path => {
    if (nodes.has(path)) return nodes.get(path);
    const parts = path.split('/').filter(Boolean);
    const parentPath = '/' + parts.slice(0, -1).join('/');
    const parent = parentPath === '/' ? root : ensure(parentPath);
    const node = {path, name: parts[parts.length - 1], tables: [], children: [], count: 0};
    nodes.set(path, node);
    parent.children.push(node);
    return node;
  };
  for (const table of tables) {
    const path = table.path || '/';
    const node = path === '/' ? root : ensure(path);
    node.tables.push(table);
  }
  const finalize = node => {
    node.children.sort((left, right) => left.name.localeCompare(right.name, 'zh'));
    node.tables.sort((left, right) => left.name.localeCompare(right.name, 'zh'));
    node.count = node.tables.length + node.children.reduce((total, child) => total + finalize(child), 0);
    return node.count;
  };
  finalize(root);
  return root;
}

export function filterTree(node, needle) {
  const text = String(needle || '').trim().toLowerCase();
  if (!text) return node;
  const matchesSelf = node.name.toLowerCase().includes(text) || node.path.toLowerCase().includes(text);
  const tables = node.tables.filter(table => table.name.toLowerCase().includes(text));
  const children = node.children.map(child => filterTree(child, text)).filter(Boolean);
  if (!matchesSelf && !children.length && !tables.length) return null;
  const clone = {...node, children, tables: matchesSelf ? node.tables : tables};
  clone.count = clone.tables.length + clone.children.reduce((total, child) => total + child.count, 0);
  return clone;
}

/** Depth-first list of visible tree rows for rendering; tables render at their node. */
export function flattenTree(root, expanded) {
  const out = [];
  const walk = (node, depth) => {
    out.push({kind: 'node', path: node.path, name: node.name, depth, count: node.count});
    if (expanded.has(node.path)) {
      for (const child of node.children) walk(child, depth + 1);
      for (const table of node.tables) out.push({kind: 'table', path: table.path, name: table.name, depth: depth + 1, table});
    }
  };
  for (const child of root.children) walk(child, 0);
  for (const table of root.tables) out.push({kind: 'table', path: '/', name: table.name, depth: 0, table});
  return out;
}

/** Expand every ancestor path of the given path. */
export function ancestorPaths(path) {
  const parts = String(path || '/').split('/').filter(Boolean);
  const out = [];
  for (let index = 0; index < parts.length; index += 1) {
    out.push('/' + parts.slice(0, index + 1).join('/'));
  }
  return out;
}

/**
 * Windowed rendering window for a fixed-height virtual list. Returns the slice
 * bounds and the padding needed to preserve scroll height.
 */
export function virtualWindow(scrollTop, viewportHeight, total, rowHeight = 34, overscan = 8) {
  const size = Number.isFinite(rowHeight) && rowHeight > 0 ? rowHeight : 34;
  const count = Number.isFinite(total) ? Math.max(0, Math.floor(total)) : 0;
  const height = Math.max(size, Number.isFinite(viewportHeight) ? viewportHeight : size);
  const visible = Math.ceil(height / size);
  const requestedStart = Math.max(0, Math.floor(Math.max(0, scrollTop) / size) - overscan);
  const lastStart = Math.max(0, count - visible - overscan * 2);
  const start = Math.min(count, Math.min(requestedStart, lastStart));
  const end = Math.min(count, start + visible + overscan * 2);
  return {
    start,
    end,
    padTop: start * size,
    padBottom: Math.max(0, (count - end) * size),
    totalHeight: count * size,
  };
}

/** Compare a draft schema with the live one and classify migration risk. */
export function classifySchemaChange(current = [], draft = []) {
  const currentByName = new Map(current.map(column => [column.name, column]));
  const draftNames = new Set(draft.map(column => column.name));
  const added = draft.filter(column => !currentByName.has(column.name));
  const removed = current.filter(column => !draftNames.has(column.name));
  const typeChanged = draft.filter(column => {
    const before = currentByName.get(column.name);
    return before && before.type !== column.type;
  });
  const nullableRestricted = draft.filter(column => {
    const before = currentByName.get(column.name);
    return before && before.nullable !== false && column.nullable === false;
  });
  const requiredAdded = added.filter(column => column.nullable === false && (column.default_value === null || column.default_value === undefined));
  const risk = removed.length || typeChanged.length || requiredAdded.length || nullableRestricted.length ? 'destructive' : 'safe';
  return {
    added,
    removed,
    typeChanged,
    nullableRestricted,
    requiredAdded,
    risk,
    summary: [
      added.length ? `新增 ${added.length} 个字段` : '',
      removed.length ? `删除 ${removed.length} 个字段及其中数据` : '',
      typeChanged.length ? `${typeChanged.length} 个字段类型变更` : '',
      nullableRestricted.length ? `${nullableRestricted.length} 个字段从可空改为必填，现有空值可能无法通过校验` : '',
      requiredAdded.length ? `${requiredAdded.length} 个必填字段没有默认值` : '',
    ].filter(Boolean),
  };
}

/** Parse `field = value` lines used by search filters into an exact-match dict. */
export function parseFieldFilters(text) {
  const filters = {};
  const errors = [];
  for (const line of String(text || '').split(/\n+/)) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    const match = /^([^=\s]+)\s*=\s*(.+)$/.exec(trimmed);
    if (!match) {
      errors.push(`无法解析「${trimmed}」，请使用 字段 = 值`);
      continue;
    }
    filters[match[1]] = match[2];
  }
  return {filters, errors};
}

export function searchWarnings(meta = {}) {
  const warnings = [];
  if (meta.missing_embeddings) {
    warnings.push(`还有 ${meta.missing_embeddings} 条记录尚未建立可用的向量索引，结果可能不完整。`);
  }
  if (meta.partial) warnings.push('本次结果标记为部分结果（partial）。');
  if (meta.approximate) warnings.push('结果包含近似或回退匹配（approximate）。');
  if (meta.index_backend === 'sqlite_exact_bootstrap') {
    warnings.push('当前索引后端为开发用 SQLite 精确引导（sqlite_exact_bootstrap），不是生产向量库。');
  }
  return warnings;
}

export function isKeywordResult(scoreType) {
  return typeof scoreType === 'string' && (scoreType.startsWith('sqlite_fts') || scoreType === 'substring_scan');
}

export const INDEX_STATES = {
  not_indexed: {label: 'Not Indexed', tone: 'neutral'},
  queued: {label: 'Queued', tone: 'warning'},
  indexing: {label: 'Indexing', tone: 'accent'},
  ready: {label: 'Ready', tone: 'success'},
  partial: {label: 'Partial', tone: 'warning'},
  failed: {label: 'Failed', tone: 'danger'},
  stale: {label: 'Stale', tone: 'warning'},
  unavailable: {label: 'Unavailable', tone: 'neutral'},
};

/** Derive an index state from real job rows only; never invents progress data. */
export function indexStatusFromJobs(jobs = []) {
  if (!jobs.length) {
    return {state: 'not_indexed', detail: '没有该表的索引任务记录。'};
  }
  const sorted = [...jobs].sort((left, right) => String(right.updated_at || right.created_at || '').localeCompare(String(left.updated_at || left.created_at || '')));
  const latest = sorted[0];
  const state = {queued: 'queued', running: 'indexing', completed: 'ready', failed: 'failed'}[latest.status] || 'unavailable';
  const detail =
    state === 'queued'
      ? '索引任务已排队，等待 Embedding worker。'
      : state === 'indexing'
        ? '索引任务正在运行。'
        : state === 'ready'
          ? '最近一次索引任务已完成。'
          : latest.error
            ? `索引失败：${latest.error}`
            : '索引任务失败。';
  return {state, detail, latest};
}

export function libraryPrefsKey(entry) {
  return `${entry.kind}:${entry.id}`;
}

// Deletion markers travel with the favorites list (issue #72 review): a
// tombstone newer than an entry suppresses that entry's revival on any device
// that still has a stale cached copy.
export function mergeServerPrefs(local, server, kind, tombstones = []) {
  const newestDeletion = new Map();
  for (const tombstone of tombstones || []) {
    if (!tombstone || !tombstone.kind || !tombstone.id) continue;
    const key = libraryPrefsKey(tombstone);
    const at = Number(tombstone.at) || 0;
    if (at > (newestDeletion.get(key) ?? -Infinity)) newestDeletion.set(key, at);
  }
  const merged = new Map();
  for (const entry of server || []) {
    if (!entry || !entry.kind || !entry.id || entry.deleted) continue;
    const key = libraryPrefsKey(entry);
    if (Number(entry.at) <= (newestDeletion.get(key) ?? -Infinity)) continue;
    merged.set(key, {...entry});
  }
  for (const entry of local || []) {
    if (!entry || !entry.kind || !entry.id || entry.deleted) continue;
    const key = libraryPrefsKey(entry);
    if (Number(entry.at) <= (newestDeletion.get(key) ?? -Infinity)) continue;
    const existing = merged.get(key);
    if (!existing || Number(entry.at) > Number(existing.at)) merged.set(key, {...entry});
  }
  const list2 = [...merged.values()];
  if (kind === 'recent') return list2.sort((a, b) => Number(b.at) - Number(a.at)).slice(0, 8);
  return list2.slice(0, 50);
}

// Union of deletion markers keyed by kind+id, keeping the newest per key.
export function mergeTombstones(local, server) {
  const merged = new Map();
  for (const tombstone of [...(server || []), ...(local || [])]) {
    if (!tombstone || !tombstone.kind || !tombstone.id || !tombstone.deleted) continue;
    const key = libraryPrefsKey(tombstone);
    const existing = merged.get(key);
    if (!existing || Number(tombstone.at) > Number(existing.at)) merged.set(key, {...tombstone});
  }
  return [...merged.values()];
}

export function mergeRecent(list, entry, limit = 8) {
  const filtered = list.filter(item => !(item.kind === entry.kind && item.id === entry.id));
  return [entry, ...filtered].slice(0, limit);
}

// Toggling favorites also maintains deletion tombstones so the removal wins
// over stale cached copies on other devices (issue #72 review).
export function toggleFavorite(list, entry, tombstones = [], at = Date.now()) {
  const exists = list.some(item => item.kind === entry.kind && item.id === entry.id);
  const key = libraryPrefsKey(entry);
  if (exists) {
    return {
      favorites: list.filter(item => !(item.kind === entry.kind && item.id === entry.id)),
      tombstones: [
        ...tombstones.filter(tombstone => libraryPrefsKey(tombstone) !== key),
        {kind: entry.kind, id: entry.id, title: entry.title || '', at, deleted: true},
      ].slice(-200),
    };
  }
  return {
    favorites: [...list, {...entry, at}].slice(0, 50),
    tombstones: tombstones.filter(tombstone => libraryPrefsKey(tombstone) !== key),
  };
}

export function libraryPrefsStorageKey(subject, section) {
  const owner = subject ? encodeURIComponent(subject) : 'anonymous';
  return `xeonolab.library.${owner}.${section}`;
}

export function isFavorite(list, entry) {
  return list.some(item => item.kind === entry.kind && item.id === entry.id);
}

export function changedFields(before = {}, after = {}) {
  const names = new Set([...Object.keys(before), ...Object.keys(after)]);
  return [...names].filter(name => JSON.stringify(before[name] ?? null) !== JSON.stringify(after[name] ?? null));
}

export function rowHasAttachment(row, columns = []) {
  return columns.some(column => {
    if (column.type !== 'attachment_list') return false;
    const value = row?.values?.[column.name];
    return Array.isArray(value) && value.length > 0;
  });
}

/** Map API/HTTP errors to user-facing copy without hiding the error code. */
export function describeError(error, context = '') {
  const status = typeof error?.status === 'number' ? error.status : null;
  const code = error?.code || '';
  const requestId = typeof error?.requestId === 'string' ? error.requestId : undefined;
  const raw = String(error?.message || error || '');
  const known = {
    XL_FORBIDDEN: {title: '权限不足', message: '当前身份没有执行此操作的权限；数据没有被修改。', retryable: false},
    FORBIDDEN: {title: '权限不足', message: '当前身份没有执行此操作的权限。', retryable: false},
    XL_TABLE_NOT_FOUND: {title: '知识表不存在', message: '该表可能已被移动或删除，请刷新目录。', retryable: true},
    TABLE_NOT_EMPTY: {title: '表不为空，无法删除', message: '请先清除（purge）表中的全部行；历史 revision 也会保留数据。', retryable: false},
    TABLE_HAS_ATTACHMENTS: {title: '表仍有关联附件', message: '请先解除或清除全部附件引用。', retryable: false},
    TABLE_HAS_VECTORS: {title: '表仍有向量引用', message: '请先移除派生的向量索引引用。', retryable: false},
    TABLE_INDEX_ACTIVE: {title: '索引任务进行中', message: '请等待该表的索引任务结束后再删除。', retryable: true},
    TABLE_UPLOAD_ACTIVE: {title: '存在未完成上传', message: '请完成或取消该表的上传会话。', retryable: true},
    TABLE_BUSY: {title: '表当前被占用', message: '有读者或写者正在使用该表，请稍后重试。', retryable: true},
    TABLE_EXISTS: {title: '目标路径已存在同名表', message: '请更换表名或路径。', retryable: false},
    TABLE_HAS_VECTORS_: {title: '表仍有向量引用', message: '请先移除派生向量。', retryable: false},
    STALE_REVISION: {title: '数据已被他人修改', message: '本地 revision 已过期，本次修改未提交。', retryable: true, conflict: true},
    IDEMPOTENCY_CONFLICT: {title: '重复请求冲突', message: '相同请求键被用于不同内容。', retryable: false},
    NULL_NOT_ALLOWED: {title: '必填字段为空', message: '请填写所有非空字段。', retryable: false},
    UNKNOWN_COLUMN: {title: '未知字段', message: '提交的数据包含 schema 中不存在的字段。', retryable: false},
    INVALID_SCHEMA: {title: '字段定义无效', message: '字段名重复或类型不受支持。', retryable: false},
    SCHEMA_JOB_REQUIRED: {title: '需要后台迁移', message: '表超过 10000 行，schema 变更需要后台 worker，当前界面不会执行。', retryable: false},
    INVALID_QUERY: {title: '查询语法错误', message: 'DSL 只支持 FROM / WHERE / ORDER BY / LIMIT / OFFSET 及其已实现的运算符。', retryable: false},
    QUERY_TYPE_ERROR: {title: '查询类型不匹配', message: '比较的两个字段类型不一致。', retryable: false},
    QUERY_UNSUPPORTED: {title: '查询包含未实现的函数', message: '该函数不在已实现的函数列表中。', retryable: false},
    QWEN_UNAVAILABLE: {title: 'Quality 搜索不可用', message: 'Qwen quality worker 尚未部署；请使用 Fast 或 Structured。', retryable: false},
    HYBRID_NOT_CONFIGURED: {title: 'Hybrid 搜索不可用', message: '混合检索尚未实现。', retryable: false},
    SEARCH_NOT_CONFIGURED: {title: '搜索未配置', message: 'Embedding URL、服务令牌或 tokenizer 未配置。', retryable: false},
    EMBEDDING_UNAVAILABLE: {title: 'Embedding 服务不可用', message: '向量服务请求失败，请稍后重试。', retryable: true},
    QDRANT_NOT_CONFIGURED: {title: '向量后端未配置', message: '生产语义检索需要 Qdrant 配置。', retryable: false},
    EMPTY_QUERY: {title: '搜索内容为空', message: '请输入搜索词。', retryable: false},
    UPLOAD_TOO_LARGE: {title: '文件过大', message: '单请求上限 16 MiB；更大文件请使用分片上传。', retryable: false},
    ATTACHMENT_NOT_FOUND: {title: '附件不存在', message: '该附件可能已被解除关联。', retryable: true},
    REVISION_NOT_FOUND: {title: 'revision 不存在', message: '请刷新历史记录。', retryable: true},
  };
  if (known[code]) return {...known[code], code, status, raw, requestId};
  if (status === 401) return {title: '会话已过期', message: '请重新登录后重试。', retryable: false, code, status, raw, requestId};
  if (status === 403) return {title: '权限不足', message: '当前身份没有访问该资源的权限；权限不足不会伪装成“不存在”。', retryable: false, code, status, raw, requestId};
  if (status === 404) return {title: '资源不存在', message: '该对象可能已被移动或删除。', retryable: true, code, status, raw, requestId};
  if (status === 409) return {title: '状态冲突', message: '该操作与当前资源状态冲突，请刷新后重试。', retryable: true, code, status, raw, requestId};
  if (status === 412) return {title: '数据已被他人修改', message: '本地 revision 已过期，本次修改未提交。', retryable: true, conflict: true, code, status, raw, requestId};
  if (status === 413) return {title: '请求过大', message: '数据量超过服务端限制。', retryable: false, code, status, raw, requestId};
  if (status === 422) return {title: '请求无效', message: '服务端拒绝了该请求的字段或取值。', retryable: false, code, status, raw, requestId};
  if (status === 429) return {title: '请求过于频繁', message: '请稍候再试。', retryable: true, code, status, raw, requestId};
  if (status === 503) return {title: '服务暂不可用', message: '依赖服务未就绪，请稍后重试。', retryable: true, code, status, raw, requestId};
  return {title: context ? `${context}失败` : '操作失败', message: raw || '未知错误', retryable: true, code, status, raw, requestId};
}

export function isIndexActiveJob(job) {
  return job && (job.status === 'queued' || job.status === 'running');
}

function numericClauseSpans(expression) {
  const spans = [];
  let quoted = false;
  let escaped = false;
  for (let index = 0; index < expression.length;) {
    const character = expression[index];
    if (quoted) {
      if (escaped) escaped = false;
      else if (character === '\\') escaped = true;
      else if (character === '"') quoted = false;
      index += 1;
      continue;
    }
    if (character === '"') {
      quoted = true;
      index += 1;
      continue;
    }
    if (!/[A-Za-z_]/.test(character)) {
      index += 1;
      continue;
    }
    const start = index;
    while (index < expression.length && /[A-Za-z_]/.test(expression[index])) index += 1;
    const keyword = expression.slice(start, index).toUpperCase();
    if (keyword !== 'LIMIT' && keyword !== 'OFFSET') continue;
    let numberStart = index;
    while (numberStart < expression.length && /\s/.test(expression[numberStart])) numberStart += 1;
    let end = numberStart;
    while (end < expression.length && /\d/.test(expression[end])) end += 1;
    if (end > numberStart) spans.push({keyword, start, numberStart, end});
  }
  return spans;
}

/** Replace or append LIMIT/OFFSET clauses without rewriting quoted values. */
export function withOffset(expression, offset) {
  const text = String(expression || '').trim();
  const value = Math.max(0, Math.floor(Number(offset) || 0));
  const existing = numericClauseSpans(text).filter(clause => clause.keyword === 'OFFSET').at(-1);
  if (existing) return text.slice(0, existing.numberStart) + value + text.slice(existing.end);
  return /;\s*$/.test(text) ? text.replace(/;\s*$/, ` OFFSET ${value};`) : `${text} OFFSET ${value}`;
}

/** Build a deterministic page query, using the server's default limit when absent. */
export function withQueryPagination(expression, limit, offset) {
  const text = String(expression || '').trim();
  const safeLimit = Math.max(1, Math.min(1000, Math.floor(Number(limit) || 100)));
  const safeOffset = Math.max(0, Math.floor(Number(offset) || 0));
  const spans = numericClauseSpans(text).sort((left, right) => right.start - left.start);
  let base = text;
  for (const span of spans) base = base.slice(0, span.start) + base.slice(span.end);
  base = base.replace(/;\s*$/, '').trim();
  return `${base} LIMIT ${safeLimit} OFFSET ${safeOffset}`;
}
