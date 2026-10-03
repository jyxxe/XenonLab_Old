import assert from 'node:assert/strict';
import test from 'node:test';
import {parseExactJSON} from '../src/library/json.js';
import {serializeQueryResultsTSV} from '../src/library/query-results-tsv.js';
import {serializeTSV} from '../src/library/tsv.js';

test('query TSV keeps five columns, cross-table order, full IDs and full JSON', () => {
  const rows = [
    {table_id: 'table-b', path: '/B', _row_id: 'complete-row-b-123456789', _revision: 3, values: {title: '中文', body: 'x'.repeat(220), null: null, array: [0, false, {text: 'tab\tCR\rLF\n"quote"'}]}},
    {table_id: 'table-a', path: '/A', _row_id: 'complete-row-a-123456789', _revision: 0, values: {other_schema: {nested: true}}},
  ];
  const before = structuredClone(rows);
  const names = new Map([['table-b', 'B'], ['table-a', 'A']]);
  assert.equal(serializeQueryResultsTSV(rows, id => names.get(id) ?? id), serializeTSV(
    ['表', '路径', 'Row ID', 'Revision', '值'],
    [['B', '/B', rows[0]._row_id, 3, JSON.stringify(rows[0].values)], ['A', '/A', rows[1]._row_id, 0, JSON.stringify(rows[1].values)]],
  ));
  assert.deepEqual(rows, before, 'copy does not mutate rows, values or order');
});

test('null provenance stays empty while JSON null and unknown full table IDs are retained', () => {
  const calls = [];
  assert.equal(serializeQueryResultsTSV([
    {table_id: null, path: null, _row_id: null, _revision: null, values: {null: null}},
    {table_id: 'unknown-full-table-123456789', path: '/path', _row_id: 'row', _revision: 1, values: null},
  ], id => { calls.push(id); return id; }),
  '表\t路径\tRow ID\tRevision\t值\r\n\t\t\t\t"{""null"":null}"\r\nunknown-full-table-123456789\t/path\trow\t1\tnull');
  assert.deepEqual(calls, ['unknown-full-table-123456789'], 'null metadata is never passed to the name resolver');
});


test('query TSV preserves exact integer tokens parsed from API JSON', () => {
  const valuesJSON = '{"max":9223372036854775807,"min":-9223372036854775808,"safe":9007199254740991,"unsafe":9007199254740993,"nested":[null,{"min":-9223372036854775808,"numericString":"9223372036854775807"}]}';
  const values = parseExactJSON(valuesJSON);
  const rows = [{table_id: 'table', path: '/Int64', _row_id: 'row-full-id', _revision: 1, values}];
  const before = structuredClone(rows);
  assert.equal(typeof values.max, 'bigint');
  assert.equal(serializeQueryResultsTSV(rows, () => 'Integers'), serializeTSV(
    ['表', '路径', 'Row ID', 'Revision', '值'],
    [['Integers', '/Int64', 'row-full-id', 1, valuesJSON]],
  ));
  assert.deepEqual(rows, before);
});


test('query protects free provenance while preserving exact JSON tokens', () => {
  const json = '{"integer":-9223372036854775808,"label":"=synthetic label","nested":[9223372036854775807]}';
  const rows = [{table_id:'table', path:'@synthetic path', _row_id:'-synthetic row', _revision:2, values:parseExactJSON(json)}];
  assert.equal(serializeQueryResultsTSV(rows, () => '+synthetic table'), serializeTSV(
    ['表','路径','Row ID','Revision','值'], [["'+synthetic table", "'@synthetic path", "'-synthetic row", 2, json]],
  ));
  assert.equal(serializeQueryResultsTSV([{...rows[0], values:-9223372036854775808n}], () => 'ordinary table'), serializeTSV(
    ['表','路径','Row ID','Revision','值'], [['ordinary table', "'@synthetic path", "'-synthetic row", 2, '-9223372036854775808']],
  ), 'standalone JSON number token is not modified by the text-column policy');
});
