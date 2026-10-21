import test from 'node:test';
import assert from 'node:assert/strict';
import {launchpadSource} from '../src/shell/launchpad-source.js';

test('external source identifies canonical host and complete origin including a nondefault port', () => {
  assert.deepEqual(launchpadSource({kind: 'iframe', origin: 'https://Production.Example.test:9443'}), {
    host: 'production.example.test:9443', origin: 'https://production.example.test:9443',
  });
});

test('display source excludes URL credentials, paths, query strings and fragments', () => {
  assert.deepEqual(launchpadSource({kind: 'iframe', origin: 'https://synthetic-user:synthetic-password@host.example.test/entry?fixture=value#section'}), {
    host: 'host.example.test', origin: 'https://host.example.test',
  });
});

test('native, missing, malformed and non-web sources retain generic card metadata', () => {
  for (const app of [{kind: 'native', origin: 'https://example.test'}, {kind: 'iframe'},
    {kind: 'iframe', origin: 'bad origin'}, {kind: 'iframe', origin: 'data:text/plain,fixture'},
    {kind: 'iframe', origin: 'file:///tmp/fixture'}]) assert.equal(launchpadSource(app), null);
});
