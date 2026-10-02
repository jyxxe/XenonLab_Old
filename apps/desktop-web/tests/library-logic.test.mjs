import assert from 'node:assert/strict';
import test from 'node:test';
import {
  ancestorPaths,
  buildPathTree,
  changedFields,
  classifySchemaChange,
  describeError,
  filterRows,
  filterTree,
  filteredTreeExpansion,
  flattenTree,
  formatBytes,
  indexStatusFromJobs,
  isKeywordResult,
  libraryPrefsStorageKey,
  mergeRecent,
  parseFieldFilters,
  rowHasAttachment,
  searchWarnings,
  sortRows,
  toggleFavorite,
  virtualWindow,
  withOffset,
  withQueryPagination,
} from '../src/library/logic.js';

const tables = [
  {id: 'a', name: 'analysis', path: '/数学/分析'},
  {id: 'b', name: 'algebra', path: '/数学/代数'},
  {id: 'c', name: 'notes', path: '/计算机'},
  {id: 'd', name: 'root', path: '/'},
];

test('buildPathTree groups tables by logical path and counts descendants', () => {
  const tree = buildPathTree(tables);
  const math = tree.children.find(child => child.name === '数学');
  assert.ok(math, '数学 node exists');
  assert.equal(math.count, 2, '数学 counts both analysis and algebra');
  const analysis = math.children.find(child => child.name === '分析');
  assert.deepEqual(analysis.tables.map(table => table.id), ['a']);
  assert.equal(tree.tables[0].id, 'd', 'root-level tables stay on the root');
});

test('flattenTree only expands open nodes and ancestorPaths expands parents', () => {
  const tree = buildPathTree(tables);
  const collapsed = flattenTree(tree, new Set());
  assert.ok(collapsed.every(item => item.kind === 'node' || item.path === '/'), 'collapsed tree shows nodes plus always-visible root tables');
  const open = flattenTree(tree, new Set(['/数学', '/数学/分析']));
  assert.ok(open.some(item => item.kind === 'table' && item.table.id === 'a'));
  assert.ok(!open.some(item => item.kind === 'table' && item.table.id === 'b'), 'closed sibling stays hidden');
  assert.deepEqual(ancestorPaths('/数学/分析/实分析'), ['/数学', '/数学/分析', '/数学/分析/实分析']);
});

test('filterTree keeps matching branches and drops the rest', () => {
  const tree = buildPathTree(tables);
  const filtered = filterTree(tree, 'analysis');
  assert.ok(filtered, 'matching subtree survives');
  assert.deepEqual(filtered.children.flatMap(child => child.children.flatMap(node => node.tables.map(t => t.id))), ['a']);
  assert.equal(filterTree(tree, 'missing'), null);
});

test('sortRows and filterRows work on schema and system fields', () => {
  const rows = [
    {_row_id: 'r1', _revision: 1, _updated_at: '2026-01-01T00:00:00Z', values: {title: 'beta', score: 2}},
    {_row_id: 'r2', _revision: 3, _updated_at: '2026-02-01T00:00:00Z', values: {title: 'alpha', score: 10}},
    {_row_id: 'r3', _revision: 2, _updated_at: '2026-03-01T00:00:00Z', values: {title: null, score: 1}},
  ];
  assert.deepEqual(sortRows(rows, 'title').map(row => row._row_id), ['r3', 'r2', 'r1'], 'null sorts first');
  assert.deepEqual(sortRows(rows, 'score', 'desc').map(row => row._row_id), ['r2', 'r1', 'r3']);
  assert.deepEqual(sortRows(rows, '_revision', 'desc').map(row => row._row_id), ['r2', 'r3', 'r1']);
  assert.deepEqual(filterRows(rows, 'alpha').map(row => row._row_id), ['r2']);
  assert.deepEqual(filterRows(rows, 'r3').map(row => row._row_id), ['r3']);
  assert.equal(filterRows(rows, '').length, 3);
});

test('virtualWindow keeps a bounded render window with padding', () => {
  const window = virtualWindow(3400, 400, 10000, 34, 4);
  assert.ok(window.end - window.start <= Math.ceil(400 / 34) + 8, 'bounded number of rendered rows');
  assert.equal(window.padTop, window.start * 34);
  assert.equal(window.padTop + (window.end - window.start) * 34 + window.padBottom, window.totalHeight);
  assert.deepEqual(virtualWindow(0, 400, 3), {start: 0, end: 3, padTop: 0, padBottom: 0, totalHeight: 102});
  const filtered = virtualWindow(3400, 400, 2, 34, 8);
  assert.deepEqual(filtered, {start: 0, end: 2, padTop: 0, padBottom: 0, totalHeight: 68});
});

test('classifySchemaChange separates safe additions from destructive edits', () => {
  const current = [
    {name: 'title', type: 'string', nullable: false, default_value: null},
    {name: 'body', type: 'text', nullable: true, default_value: null},
  ];
  const safe = classifySchemaChange(current, [...current, {name: 'tags', type: 'string', nullable: true, default_value: null}]);
  assert.equal(safe.risk, 'safe');
  assert.equal(safe.added.length, 1);

  const destructive = classifySchemaChange(current, [
    {name: 'title', type: 'text', nullable: false, default_value: null},
    {name: 'extra', type: 'int64', nullable: false, default_value: null},
  ]);
  assert.equal(destructive.risk, 'destructive');
  assert.deepEqual(destructive.removed.map(column => column.name), ['body']);
  assert.deepEqual(destructive.typeChanged.map(column => column.name), ['title']);
  assert.deepEqual(destructive.requiredAdded.map(column => column.name), ['extra']);
  const tightened = classifySchemaChange(
    [{name: 'optional', type: 'string', nullable: true, default_value: null}],
    [{name: 'optional', type: 'string', nullable: false, default_value: null}],
  );
  assert.equal(tightened.risk, 'destructive');
  assert.deepEqual(tightened.nullableRestricted.map(column => column.name), ['optional']);
  assert.match(tightened.summary.join(' '), /现有空值/);
  assert.equal(destructive.summary.length, 4);
  assert.ok(destructive.summary.some(item => item.includes('类型变更')));
});

test('parseFieldFilters reads exact-match lines and reports malformed input', () => {
  const parsed = parseFieldFilters('type = theorem\nconfidence = 0.91\n\nbroken');
  assert.deepEqual(parsed.filters, {type: 'theorem', confidence: '0.91'});
  assert.equal(parsed.errors.length, 1);
  assert.deepEqual(parseFieldFilters('').filters, {});
});

test('searchWarnings never hide partial or approximate results', () => {
  assert.deepEqual(searchWarnings({partial: false}), []);
  const warnings = searchWarnings({partial: true, missing_embeddings: 2341, approximate: true, index_backend: 'sqlite_exact_bootstrap'});
  assert.equal(warnings.length, 4);
  assert.ok(warnings.some(warning => warning.includes('2341')));
});

test('search result type groups both CJK indexing and legacy substring hits as keywords', () => {
  assert.equal(isKeywordResult('sqlite_fts_bm25'), true);
  assert.equal(isKeywordResult('sqlite_fts_cjk'), true);
  assert.equal(isKeywordResult('substring_scan'), true);
  assert.equal(isKeywordResult('d100m_cosine'), false);
});

test('indexStatusFromJobs maps real job states without inventing progress', () => {
  assert.equal(indexStatusFromJobs([]).state, 'not_indexed');
  assert.equal(indexStatusFromJobs([{status: 'queued'}]).state, 'queued');
  assert.equal(indexStatusFromJobs([{status: 'running'}]).state, 'indexing');
  assert.equal(indexStatusFromJobs([{status: 'completed'}]).state, 'ready');
  const failed = indexStatusFromJobs([{status: 'failed', error: 'embedding down'}]);
  assert.equal(failed.state, 'failed');
  assert.match(failed.detail, /embedding down/);
  const latest = indexStatusFromJobs([
    {status: 'completed', updated_at: '2026-01-01T00:00:00Z'},
    {status: 'failed', updated_at: '2026-02-01T00:00:00Z'},
  ]);
  assert.equal(latest.state, 'failed', 'newest job wins');
});

test('describeError maps known codes and never disguises permission errors', () => {
  const forbidden = describeError({status: 403, code: 'XL_FORBIDDEN', message: 'nope'});
  assert.equal(forbidden.title, '权限不足');
  assert.equal(forbidden.retryable, false);
  const conflict = describeError({status: 412, code: 'STALE_REVISION', message: 'stale'});
  assert.equal(conflict.conflict, true);
  const notEmpty = describeError({status: 409, code: 'TABLE_NOT_EMPTY', message: 'rows'});
  assert.match(notEmpty.message, /purge/);
  const unknown = describeError(new Error('boom'), '保存记录');
  assert.equal(unknown.title, '保存记录失败');
});

test('recent/favorites helpers stay bounded and deduplicate', () => {
  const recent = mergeRecent([], {kind: 'table', id: 'a', title: 'A', at: 1});
  assert.equal(recent.length, 1);
  const updated = mergeRecent(recent, {kind: 'table', id: 'a', title: 'A2', at: 2});
  assert.equal(updated.length, 1);
  assert.equal(updated[0].title, 'A2');
  const favorites = toggleFavorite([], {kind: 'table', id: 'a', title: 'A', at: 1});
  assert.equal(favorites.length, 1);
  assert.deepEqual(toggleFavorite(favorites, {kind: 'table', id: 'a', title: 'A', at: 1}), []);
  assert.notEqual(libraryPrefsStorageKey('subject:one', 'recent'), libraryPrefsStorageKey('subject:two', 'recent'));
  assert.equal(libraryPrefsStorageKey(null, 'favorites'), null);
});

test('changedFields, rowHasAttachment, withOffset, and formatters behave', () => {
  assert.deepEqual(changedFields({a: 1, b: 2}, {a: 1, b: 3}), ['b']);
  assert.equal(rowHasAttachment({values: {files: [{id: 1}]}}, [{name: 'files', type: 'attachment_list'}]), true);
  assert.equal(rowHasAttachment({values: {files: []}}, [{name: 'files', type: 'attachment_list'}]), false);
  assert.equal(withOffset('FROM "/**" LIMIT 10', 20), 'FROM "/**" LIMIT 10 OFFSET 20');
  assert.equal(withOffset('FROM "/**" LIMIT 10 OFFSET 5', 25), 'FROM "/**" LIMIT 10 OFFSET 25');
  assert.equal(
    withOffset('FROM "/**" WHERE title = "OFFSET 10" LIMIT 25', 25),
    'FROM "/**" WHERE title = "OFFSET 10" LIMIT 25 OFFSET 25',
  );
  assert.equal(
    withQueryPagination('FROM "/**" WHERE note = "LIMIT 10 OFFSET 5" LIMIT 25 OFFSET 50', 10, 0),
    'FROM "/**" WHERE note = "LIMIT 10 OFFSET 5" LIMIT 10 OFFSET 0',
  );
  assert.equal(withQueryPagination('FROM "/**"', 100, 20), 'FROM "/**" LIMIT 100 OFFSET 20');
  assert.equal(formatBytes(2048), '2.0 KB');
  assert.equal(formatBytes(-1), '—');
});


test('filtered tree reveals matching deep tables without changing saved expansion', () => {
  const tree = buildPathTree(tables);
  const saved = new Set(['/计算机']);
  const filtered = filterTree(tree, '  ANALYSIS  ');
  assert.deepEqual(flattenTree(filtered, filteredTreeExpansion(filtered)).filter(item => item.kind === 'table').map(item => item.table.id), ['a']);
  assert.deepEqual([...saved], ['/计算机']);
  assert.ok(!flattenTree(tree, saved).some(item => item.kind === 'table' && item.table.id === 'a'));
  assert.deepEqual([...filteredTreeExpansion(filterTree(tree, 'missing'))], []);
});

test('filtered path matches reveal descendants and support transient explicit collapse', () => {
  const filtered = filterTree(buildPathTree(tables), '/数学');
  const collapsed = new Set(['/数学']);
  assert.deepEqual(flattenTree(filtered, filteredTreeExpansion(filtered, collapsed)).filter(item => item.kind === 'table'), []);
  assert.deepEqual(flattenTree(filtered, filteredTreeExpansion(filtered)).filter(item => item.kind === 'table').map(item => item.table.id).sort(), ['a', 'b']);
  assert.deepEqual([...collapsed], ['/数学']);
});

test('field display order binds original schema objects, appends new fields and ignores removed/duplicate names', async () => {
  const {orderedColumns} = await import('../src/library/logic.js');
  const columns = [{name: 'title', type: 'string'}, {name: 'files', type: 'json'}, {name: 'new', type: 'bool'}];
  const result = orderedColumns(columns, ['files', 'removed', 'files', 3, '_row_id', 'title']);
  assert.deepEqual(result.map(column => column.name), ['files', 'title', 'new']);
  assert.equal(result[0], columns[1]);
  for (const invalid of [null, {}, 'files']) assert.deepEqual(orderedColumns(columns, invalid), columns);
  assert.deepEqual(orderedColumns(columns, []), columns);
});

test('moving a field preserves other fields, handles both boundaries and leaves schema unchanged', async () => {
  const {moveColumn} = await import('../src/library/logic.js');
  const columns = [{name: 'a'}, {name: 'b'}, {name: 'c'}];
  assert.deepEqual(moveColumn(columns, [], 'c', -1), ['a', 'c', 'b']);
  assert.deepEqual(moveColumn(columns, ['c', 'a', 'b'], 'c', 1), ['a', 'c', 'b']);
  for (const [name, direction] of [['a', -1], ['c', 1], ['missing', 1], ['b', 2]]) assert.deepEqual(moveColumn(columns, [], name, direction), ['a', 'b', 'c']);
  assert.deepEqual(columns, [{name: 'a'}, {name: 'b'}, {name: 'c'}]);
});
