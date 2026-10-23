import test from 'node:test';
import assert from 'node:assert/strict';
import {discoverDesktop} from '../src/capability-discovery.js';
const cap = overrides => ({enabled:true,runtime:'ui',permissions:['desktop:read'],...overrides});
const knowledge = cap({id:'builtin.platform.app.knowledge',kind:'app',source:'builtin'});
const widget = cap({id:'plugin.tools.widget',kind:'widget',source:'plugin',plugin_id:'tools',desktop_id:'tools-widget'});
const app = cap({id:'plugin.tools.app',kind:'app',source:'plugin',plugin_id:'tools',desktop_id:'tools-app'});
const external = cap({id:'registered.platform.app.chat',kind:'app',source:'registered',desktop_id:'chat'});
const registry = [{id:'tools-app',plugin_id:'tools',origin:'https://plugin.example'}, {id:'chat',origin:'https://chat.example'}];
const widgets = [{id:'tools-widget',plugin_id:'tools'}];
const scopes = ['desktop:read'];

test('unified discovery preserves descriptors and native adapter ids', () => {
  const result = discoverDesktop([knowledge,app,widget,external,cap({id:'builtin.platform.widget.clock',source:'builtin',kind:'widget'})],registry,widgets,scopes);
  assert.deepEqual([...result.builtinApps],['knowledge']);
  assert.deepEqual([...result.builtinWidgets],['clock']);
  assert.deepEqual(result.apps,registry);
  assert.equal(result.apps[0],registry[0]);
  assert.deepEqual(result.widgets,widgets);
});

test('disabled and removed capabilities cannot survive stale descriptors or widget preferences', () => {
  const result = discoverDesktop([knowledge,{...app,enabled:false}],registry,widgets,scopes);
  assert.deepEqual(result.apps,[]);
  assert.deepEqual(result.widgets,[]);
  assert.deepEqual([...result.builtinApps],['knowledge']);
});

test('permission, runtime and plugin ownership boundaries are checked', () => {
  for (const invalid of [
    {...app,permissions:['desktop:admin']}, {...app,runtime:'service'},
    {...app,plugin_id:'another'}, {...app,source:'registered'},
    {...app,desktop_id:'other-app'}, {...app,enabled:undefined}, {...app,permissions:'desktop:read'},
  ]) assert.deepEqual(discoverDesktop([invalid],registry,widgets,scopes).apps,[]);
});

test('malformed discovery fails closed rather than restoring hardcoded apps', () => {
  assert.deepEqual(discoverDesktop([cap({kind:'app',source:'registered'})],[{}],[],scopes).apps,[]);
  for(const input of [null,{},[null,{},false]]) {
    const result=discoverDesktop(input,registry,widgets,scopes);
    assert.equal(result.builtinApps.size,0);
    assert.deepEqual(result.apps,[]);
    assert.deepEqual(result.widgets,[]);
  }
});
