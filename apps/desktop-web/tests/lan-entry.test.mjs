import test from 'node:test';
import assert from 'node:assert/strict';
import {DEFAULT_LAN_ORIGIN, copyLanEntry, lanEntryOrigin, lanEntryState} from '../src/lan-entry.js';

test('the LAN origin defaults to the documented IP entry and is normalised', () => {
  assert.equal(DEFAULT_LAN_ORIGIN, 'https://192.168.0.101:9443');
  assert.equal(lanEntryOrigin(undefined), DEFAULT_LAN_ORIGIN);
  assert.equal(lanEntryOrigin(''), DEFAULT_LAN_ORIGIN);
  assert.equal(lanEntryOrigin('   '), DEFAULT_LAN_ORIGIN);
  assert.equal(lanEntryOrigin('https://192.168.0.101:9443/'), 'https://192.168.0.101:9443');
  assert.equal(lanEntryOrigin('https://192.168.0.101:9443/deep/path'), 'https://192.168.0.101:9443');
  for (const invalid of ['not a url', 'ftp://192.168.0.101:21', 42, {}, null]) {
    assert.equal(lanEntryOrigin(invalid), DEFAULT_LAN_ORIGIN, String(invalid));
  }
});

test('the network panel states the CA and issuer caveats instead of promising a silent switch', () => {
  const publicState = lanEntryState('https://xenon.jyxxe.com', undefined);
  assert.equal(publicState.origin, DEFAULT_LAN_ORIGIN);
  assert.equal(publicState.current, false);
  assert.match(publicState.hint, /LAN CA/);
  assert.match(publicState.hint, /OIDC issuer/);
  assert.match(publicState.hint, /重新登录/);

  const lanState = lanEntryState('https://192.168.0.101:9443', undefined);
  assert.equal(lanState.current, true);
  assert.match(lanState.hint, /当前已通过内网 IP 入口访问/);

  const configured = lanEntryState('https://192.168.0.101:9443', 'https://192.168.0.101:9443/');
  assert.equal(configured.current, true);
  assert.equal(lanEntryState(undefined, undefined).current, false);
});

test('copying the LAN entry reports clipboard success, failure and absence', async () => {
  const copied = [];
  assert.equal(await copyLanEntry(DEFAULT_LAN_ORIGIN, {writeText: async value => {copied.push(value)}}), true);
  assert.deepEqual(copied, [DEFAULT_LAN_ORIGIN]);
  assert.equal(await copyLanEntry(DEFAULT_LAN_ORIGIN, {writeText: async () => {throw new Error('denied')}}), false);
  assert.equal(await copyLanEntry(DEFAULT_LAN_ORIGIN, undefined), false);
  assert.equal(await copyLanEntry(DEFAULT_LAN_ORIGIN, {}), false);
});
