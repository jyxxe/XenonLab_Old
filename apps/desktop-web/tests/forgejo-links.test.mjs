import test from 'node:test';
import assert from 'node:assert/strict';
import {forgejoBase, forgejoIssueUrl} from '../src/shell/forgejo-links.js';

test('Forgejo links share the existing public and LAN desktop origin rules', () => {
  assert.equal(forgejoBase('xenon.jyxxe.com'), 'https://xenon.jyxxe.com/dev');
  assert.equal(forgejoBase('127.0.0.1'), 'https://192.168.0.101:9445');
  assert.equal(forgejoIssueUrl('xenon-admin/XeonoLab', 71, 'xenon.jyxxe.com'), 'https://xenon.jyxxe.com/dev/xenon-admin/XeonoLab/issues/71');
  assert.equal(forgejoIssueUrl('team.project/repo_name-1', 12, '127.0.0.1'), 'https://192.168.0.101:9445/team.project/repo_name-1/issues/12');
});

test('missing or malformed source metadata remains plain text', () => {
  for (const repository of [undefined, null, 17, '', 'owner', '/repo', 'owner/', 'owner/repo/extra', 'owner name/repo', './repo', 'owner/..']) {
    assert.equal(forgejoIssueUrl(repository, 71, '127.0.0.1'), null);
  }
  for (const issueId of [undefined, null, '71', 0, -1, 1.5, Number.NaN, Number.MAX_SAFE_INTEGER + 1]) {
    assert.equal(forgejoIssueUrl('owner/repo', issueId, '127.0.0.1'), null);
  }
});
