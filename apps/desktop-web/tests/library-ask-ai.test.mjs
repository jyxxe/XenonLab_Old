import assert from 'node:assert/strict';
import test from 'node:test';
import {
  SELECTION_AI_ACTIONS,
  buildSelectionPrompt,
  selectionContextSummary,
} from '../src/library/logic.js';

const columns = [
  {name: 'title', type: 'string', nullable: false, default_value: null},
  {name: 'type', type: 'enum', nullable: true, default_value: null},
];

const rows = Array.from({length: 25}, (_, index) => ({
  _row_id: `row-${String(index + 1).padStart(2, '0')}`,
  _revision: index + 1,
  values: {title: `记录 ${index + 1}`, type: index % 2 ? 'note' : 'theorem'},
}));

const table = {id: 't1', path: '/数学/分析', name: 'uniform-convergence'};

test('selection context summary describes exactly what will be shared', () => {
  const summary = selectionContextSummary(table, rows, columns);
  assert.equal(summary.tablePath, '/数学/分析/uniform-convergence');
  assert.equal(summary.tableId, 't1');
  assert.equal(summary.rowCount, 25);
  assert.equal(summary.sharedRows, 20);
  assert.equal(summary.truncated, true);
  assert.deepEqual(summary.fields, ['title', 'type']);
  assert.equal(summary.rowIds.length, 20);
  assert.equal(summary.rowIds[0], 'row-01');
});

test('the prompt is bounded and declares truncation', () => {
  const prompt = buildSelectionPrompt('summarize', table, rows, columns);
  const lines = prompt.split('\n');
  assert.ok(lines.some(line => line.includes('选中记录：25 行')), 'total selection size is stated');
  assert.ok(prompt.includes('仅包含前 20 行'), 'truncation is explicit');
  const rowLines = lines.filter(line => line.startsWith('- row-'));
  assert.equal(rowLines.length, 20, 'at most 20 rows are embedded');
  assert.ok(prompt.includes('知识表：/数学/分析/uniform-convergence'));
  assert.ok(prompt.includes('字段：title, type'));
});

test('ask composes the question and flags a missing one', () => {
  const withQuestion = buildSelectionPrompt('ask', table, rows.slice(0, 2), columns, '这些记录的共同点是什么？');
  assert.ok(withQuestion.includes('问题：这些记录的共同点是什么？'));
  const without = buildSelectionPrompt('ask', table, rows.slice(0, 2), columns, '');
  assert.ok(without.includes('（请在发送前补充你的问题）'), 'an empty question is called out instead of silently sent');
});

test('each action gets its own instruction line', () => {
  const ask = buildSelectionPrompt('ask', table, rows.slice(0, 1), columns);
  const summarize = buildSelectionPrompt('summarize', table, rows.slice(0, 1), columns);
  const related = buildSelectionPrompt('related', table, rows.slice(0, 1), columns);
  assert.ok(ask.includes('回答问题'));
  assert.ok(summarize.includes('总结'));
  assert.ok(related.includes('查找可能相关的知识点'));
});

test('long values are truncated per cell, not passed through unbounded', () => {
  const longRow = [{_row_id: 'row-x', _revision: 1, values: {title: 'x'.repeat(5000), type: 'note'}}];
  const prompt = buildSelectionPrompt('summarize', table, longRow, columns);
  const embedded = [...prompt.matchAll(/x+/g)].map(match => match[0].length);
  assert.ok(Math.max(...embedded) <= 201, `value is cut at the character limit (max run ${Math.max(...embedded)})`);
});

test('object values serialise instead of rendering as [object Object]', () => {
  const row = [{_row_id: 'row-y', _revision: 1, values: {title: {nested: 'value'}, type: null}}];
  const prompt = buildSelectionPrompt('ask', table, row, columns);
  assert.ok(prompt.includes('title={"nested":"value"}'));
  assert.ok(/type=;|type=$/.test(prompt.split('\n').find(line => line.includes('row-y'))), 'null values render empty');
});

test('degenerate inputs produce an honest draft instead of throwing', () => {
  const empty = buildSelectionPrompt('summarize', table, [], columns);
  assert.ok(empty.includes('选中记录：0 行'));
  const noColumns = buildSelectionPrompt('ask', table, rows.slice(0, 1), []);
  assert.ok(noColumns.includes('row-01'), 'rows are still listed without schema columns');
  const unknown = buildSelectionPrompt('nonsense', table, rows.slice(0, 1), columns);
  assert.ok(unknown.includes('回答问题'), 'an unknown action falls back to the ask template');
  assert.deepEqual(SELECTION_AI_ACTIONS.map(entry => entry.id), ['ask', 'summarize', 'related']);
  assert.ok(SELECTION_AI_ACTIONS.every(entry => entry.label && entry.hint));
});
