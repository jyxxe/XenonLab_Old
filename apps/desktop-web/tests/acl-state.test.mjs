import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeManagedGrants, unknownSubjectsForTree } from '../src/acl-state.js';

test('ACL edits are limited to account-tree subjects and preserve existing external subjects', () => {
  const existing = {
    'account-in-tree': ['read'],
    'account-outside-tree': ['read', 'attach'],
    '*': ['read'],
  };

  const result = mergeManagedGrants(existing, {
    'account-in-tree': ['write', 'write'],
    'account-outside-tree': ['write'],
    'invented-subject': ['read'],
    '*': [],
  }, [{ id: 'account-in-tree' }]);

  assert.deepEqual(result, {
    'account-in-tree': ['write'],
    'account-outside-tree': ['read', 'attach'],
  });
});

test('removing a managed subject grant does not alter unrelated ACL entries', () => {
  const result = mergeManagedGrants({
    'account-in-tree': ['read'],
    'other-known-entry': ['attach'],
  }, {}, [{ id: 'account-in-tree' }]);

  assert.deepEqual(result, { 'other-known-entry': ['attach'] });
});

test('subjects are classified as outside the account tree only after the tree loads', () => {
  const grants = { 'account-in-tree': ['read'], 'external-subject': ['attach'], '*': ['read'] };
  const accounts = [{ id: 'account-in-tree' }];

  assert.deepEqual(unknownSubjectsForTree(grants, [], false), []);
  assert.deepEqual(unknownSubjectsForTree(grants, accounts, true), ['external-subject']);
});
