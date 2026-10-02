import assert from 'node:assert/strict';
import test from 'node:test';
import {alignEmbeddingResults, validEmbeddingInputs} from '../src/embedding-batch.js';

test('batch input validation matches 1–32 nonblank texts and per-item character limit', () => {
  assert.equal(validEmbeddingInputs(['one']),true);
  assert.equal(validEmbeddingInputs(Array(32).fill('x'.repeat(32768))),true);
  for (const texts of [[],Array(33).fill('x'),['a',' \n '],['x'.repeat(32769)]]) assert.equal(validEmbeddingInputs(texts),false);
});

test('batch responses align with captured inputs by index rather than response order', () => {
  assert.deepEqual(alignEmbeddingResults([{index:1,embedding:[2,3]},{index:0,embedding:[0,1]}],['first','second']),
    [{index:0,input:'first',embedding:[0,1]},{index:1,input:'second',embedding:[2,3]}]);
});

test('invalid/missing/duplicate indexes or malformed vectors reject the complete result', () => {
  const first={index:0,embedding:[1]};
  for (const data of [null,[],[first],[first,first],[first,{index:2,embedding:[2]}],[first,{index:'1',embedding:[2]}],[first,{embedding:[2]}],[first,{index:1,embedding:[]}],[first,{index:1,embedding:[NaN]}],[first,{index:1,embedding:['2']}]]) {
    assert.throws(()=>alignEmbeddingResults(data,['first','second']),/向量数据或输入索引/);
  }
});
