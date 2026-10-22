import test from 'node:test';
import assert from 'node:assert/strict';
import {auditDisplayFields, filterAuditRows} from '../src/shell/audit-filter.js';

const rows = [
  {id:'new',at:1790990000,event:'API_Key.Created',actor:'Alice',resource:'key-Alpha',target:'hidden-target'},
  {id:'older',at:'2026-10-02T01:02:03Z',event:'table.updated',actor:'BOB',target:'table-Fallback'},
  {id:'empty'},
];
test('audit matching uses visible locale time, case-insensitive fields and object fallback', () => {
  for (const query of [' api_KEY ', 'ALIce','KEY-alpha',auditDisplayFields(rows[0])[0]]) {
    assert.deepEqual(filterAuditRows(rows,query),[rows[0]]);
  }
  assert.deepEqual(filterAuditRows(rows,'table-fallback'),[rows[1]]);
  assert.deepEqual(filterAuditRows(rows,'hidden-target'),[]);
  assert.deepEqual(auditDisplayFields(rows[0]),[new Date(rows[0].at*1000).toLocaleString(),'API_Key.Created','Alice','key-Alpha']);
  assert.equal(auditDisplayFields(rows[1])[0],new Date(rows[1].at).toLocaleString());
  assert.deepEqual(auditDisplayFields(rows[2]),['—','','','']);
});
test('audit filtering preserves loaded order/identity, ignores blank search and handles no match', () => {
  assert.equal(filterAuditRows(rows,'  '),rows);
  const matching=filterAuditRows(rows,'a');
  assert.deepEqual(matching,[rows[0],rows[1]]);
  assert.equal(matching[0],rows[0]);
  assert.deepEqual(filterAuditRows(rows,'missing'),[]);
  assert.deepEqual(filterAuditRows([],'missing'),[]);
  // Separate columns must not create a synthetic cross-field match.
  assert.deepEqual(filterAuditRows(rows,'CreatedAlice'),[]);
});
