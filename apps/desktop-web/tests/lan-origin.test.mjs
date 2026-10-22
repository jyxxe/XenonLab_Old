import assert from 'node:assert/strict';
import test from 'node:test';
import {isLanOrigin} from '../src/shell/lan-origin.js';

test('LAN origins are detected for the lab hostnames and private address ranges', () => {
  for (const host of ['localhost', '127.0.0.1', '::1', '10.1.2.3', '172.16.0.10', '172.31.255.254', '192.168.0.101', 'xenon.lan', 'library.xenon.lan', 'XENON.LAN']) {
    assert.equal(isLanOrigin(host), true, `${host} must be a LAN origin`);
  }
});

test('public and unrelated origins are not treated as the LAN', () => {
  for (const host of ['', null, undefined, 'xenon.jyxxe.com', 'dev.xenon.jyxxe.com', 'chat.xenon.jyxxe.com', 'example.com', 'xenon.lan.example.com', '192.168.0.101.example.com', '172.32.0.1', '11.0.0.1', '192.169.1.1', '999.1.1.1', '192.168.0']) {
    assert.equal(isLanOrigin(host), false, `${host} must not be a LAN origin`);
  }
});
