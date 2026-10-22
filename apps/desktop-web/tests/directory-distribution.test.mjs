import test from 'node:test';
import assert from 'node:assert/strict';
import {directoryDistribution} from '../src/directory-distribution.js';

const tables = (name, count = 1) => Array.from({length: count}, () => ({path: `/${name}/nested`}));

test('top six plus remainder cover all loaded tables with exact directory names/counts', () => {
  const rows = [...tables('B', 7), ...tables('A', 7), ...tables('C', 5), ...tables('D', 4),
    ...tables('E', 3), ...tables('F', 2), ...tables('目录完整长名称一', 1), ...tables('目录完整长名称二', 1)];
  const result = directoryDistribution(rows);
  assert.deepEqual(result.slice(0, 6).map(row => [row.name, row.value]), [
    ['A', 7], ['B', 7], ['C', 5], ['D', 4], ['E', 3], ['F', 2],
  ]);
  assert.equal(result[6].kind, 'other');
  assert.equal(result[6].value, 2);
  assert.deepEqual(result[6].directories, [{name: '目录完整长名称一', value: 1}, {name: '目录完整长名称二', value: 1}]);
  assert.equal(result.reduce((sum, row) => sum + row.value, 0), rows.length);
  assert.deepEqual(directoryDistribution([...rows].reverse()), result);
});

test('zero through six directories have no aggregate; first path component and root semantics stay intact', () => {
  assert.deepEqual(directoryDistribution([]), []);
  const result = directoryDistribution([{path: '/'}, {path: ''}, ...tables('alpha', 2), ...tables('beta')]);
  assert.deepEqual(result.map(row => [row.name, row.value]), [['/', 2], ['alpha', 2], ['beta', 1]]);
  assert.ok(result.every(row => row.kind === 'directory'));
  assert.equal(directoryDistribution(['a', 'b', 'c', 'd', 'e', 'f'].flatMap(name => tables(name))).length, 6);
});

test('arbitrary directory keys and genuine Other directories do not collide with an aggregate', () => {
  const names = ['__proto__', 'constructor', 'toString', '其他', '其他（汇总）', 'Other', 'x', 'y'];
  const result = directoryDistribution(names.flatMap(name => tables(name)));
  assert.equal(result.length, 7);
  const full = result.flatMap(row => row.directories);
  assert.deepEqual(full.map(row => row.name).sort(), [...names].sort());
  assert.ok(full.every(row => row.value === 1));
  const other = directoryDistribution([...tables('其他（汇总）', 20), ...names.flatMap(name => tables(name))]);
  assert.equal(other[0].name, '其他（汇总）');
  assert.equal(other[0].kind, 'directory');
  assert.equal(other[6].kind, 'other');
});
