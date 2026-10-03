import test from 'node:test';
import assert from 'node:assert/strict';
import {int64Value, LibraryJSONError, normalizeColumnDefaults, normalizeRowValues, parseAPIJSON, parseExactJSON, stringifyExactJSON} from '../src/library/json.js';
import {buildSelectionPrompt, changedFields, filterRows, formatNumber, sortRows} from '../src/library/logic.js';

test('library transport retains integer lexemes while safe numbers, float/exponent and strings keep their types', () => {
  const parsed = parseExactJSON('{"min":-9223372036854775808,"max":9223372036854775807,"unsafe":9007199254740993,"negative":-9007199254740993,"safe":9007199254740991,"float":1.25,"exp":1.2e3,"numericString":"9007199254740993","nested":[{"value":9223372036854775807}],"bool":true,"nil":null}');
  assert.equal(parsed.min, -9223372036854775808n);
  assert.equal(parsed.max, 9223372036854775807n);
  assert.equal(parsed.unsafe, 9007199254740993n);
  assert.equal(parsed.negative, -9007199254740993n);
  assert.equal(parsed.safe, 9007199254740991);
  assert.equal(parsed.float, 1.25);
  assert.equal(parsed.exp, 1200);
  assert.equal(parsed.numericString, '9007199254740993');
  assert.equal(parsed.nested[0].value, parsed.max);
  assert.equal(parsed.bool, true);
  assert.equal(parsed.nil, null);
  const wire = stringifyExactJSON(parsed);
  assert.match(wire, /"max":9223372036854775807/);
  assert.match(wire, /"numericString":"9007199254740993"/);
  assert.deepEqual(parseExactJSON(wire), parsed);
  for (const path of ['/tables?limit=50', '/tables/t/rows/r/revisions', '/query', '/search/text', '/theorems/t/evidence']) assert.equal(parseAPIJSON(path, '{"id":9007199254740993}').id, 9007199254740993n);
  assert.equal(typeof parseAPIJSON('/work', '{"id":9007199254740993}').id, 'number', 'other applications keep their existing representation');
  assert.throws(() => parseAPIJSON('/tables', '{"x":1e999}'), LibraryJSONError);
});

test('JSON grammar, escaped strings, prototype keys and internal marker collisions are preserved', () => {
  for (const raw of ['01', '+1', '1.', '{"v":9007199254740993,}', '{bad:1}', '"unterminated9007199254740993', '[1,,2]', 'NaN', 'Infinity', '{"x":1e999}']) assert.throws(() => parseExactJSON(raw), SyntaxError, raw);
  const raw = '{"__proto__":{"value":9007199254740993},"constructor":9223372036854775807,"quote":"\\\"9007199254740993\\\\","marker":"\\u005f_xel_exact_integer_9007199254740993","__xel_exact_integer_123":"__xel_exact_integer__123","list":[-9223372036854775808]}';
  const parsed = parseExactJSON(raw);
  assert.equal(Object.getPrototypeOf(parsed), Object.prototype);
  assert.equal(Object.hasOwn(parsed, '__proto__'), true);
  assert.equal(parsed.__proto__.value, 9007199254740993n);
  assert.equal(parsed.marker, '__xel_exact_integer_9007199254740993');
  assert.equal(parsed.constructor, 9223372036854775807n);
  assert.equal(parsed.quote, '"9007199254740993\\');
  assert.deepEqual(parseExactJSON(stringifyExactJSON(parsed, 2)), parsed);
  assert.equal({}.value, undefined, 'no prototype mutation');
  const cycle = {}; cycle.self = cycle;
  assert.throws(() => stringifyExactJSON(cycle), TypeError);
});

test('int64 accepts the full signed range, rejects fractions/exponents/out of range and already-rounded numbers', () => {
  for (const text of ['9007199254740993', '-9007199254740993', '-9223372036854775808', '9223372036854775807']) assert.equal(String(int64Value(text)), text);
  for (const [input, expected] of [[' +00012 ', 12], ['-0', 0], [9007199254740991, 9007199254740991], [7n, 7]]) assert.equal(int64Value(input), expected);
  for (const value of ['9223372036854775808', '-9223372036854775809', '1.5', '1e3', '0x10', '', 'NaN', Infinity, 9007199254740992, null, true]) assert.throws(() => int64Value(value, 'external_id'), /字段 external_id/);
});

test('row values and schema defaults normalize exact integers without changing other fields or prototypes', () => {
  const columns = [{name: 'id', type: 'int64', nullable: false}, {name: 'optional', type: 'int64', nullable: true}, {name: 'title', type: 'string', nullable: false}];
  const values = {id: '9223372036854775807', optional: '', title: 'title'};
  assert.deepEqual(normalizeRowValues(columns, values), {id: 9223372036854775807n, optional: null, title: 'title'});
  assert.equal(values.id, '9223372036854775807');
  assert.throws(() => normalizeRowValues(columns, {...values, id: null}), /id.*不能为空/);
  const defaults = [{name: 'id', type: 'int64', default_value: '-9223372036854775808'}, {name: 'required', type: 'int64', default_value: null}];
  assert.equal(normalizeColumnDefaults(defaults)[0].default_value, -9223372036854775808n);
  assert.equal(normalizeColumnDefaults(defaults)[1].default_value, null);
  const protoValues = parseExactJSON('{"__proto__":"9223372036854775807"}');
  const normalized = normalizeRowValues([{name: '__proto__', type: 'int64', nullable: false}], protoValues);
  assert.equal(normalized.__proto__, 9223372036854775807n);
  assert.equal(Object.getPrototypeOf(normalized), Object.prototype);
});

test('BigInt row consumers compare, filter, diff and compose context without rounding or throwing', () => {
  assert.equal(formatNumber(9007199254740993n), '9,007,199,254,740,993');
  const rows = [{_row_id: 'one', values: {n: 9007199254740993n, json: {n: 9223372036854775807n}}}, {_row_id: 'two', values: {n: 9007199254740992n}}, {_row_id: 'three', values: {n: -9223372036854775808n}}, {_row_id: 'four', values: {n: 1}}];
  assert.deepEqual(sortRows(rows, 'n').map(row => row._row_id), ['three', 'four', 'two', 'one']);
  assert.deepEqual(filterRows(rows, '9223372036854775807').map(row => row._row_id), ['one']);
  assert.deepEqual(changedFields(rows[0].values, {...rows[0].values, title: 'new'}), ['title']);
  assert.deepEqual(changedFields(rows[0].values, {...rows[0].values, n: 9007199254740994n}), ['n']);
  const prompt = buildSelectionPrompt('ask', {id: 't', path: '/', name: 'precision'}, rows.slice(0, 1), [{name: 'n'}, {name: 'json'}]);
  assert.match(prompt, /9007199254740993/);
  assert.match(prompt, /9223372036854775807/);
});
