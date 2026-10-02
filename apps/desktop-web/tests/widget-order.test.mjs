import test from 'node:test';
import assert from 'node:assert/strict';
import {moveWidget} from '../src/widget-order.js';
test('widget moves preserve all entries and leave the source unchanged',()=>{
 const initial=['clock','notes','search'];
 assert.deepEqual(moveWidget(initial,'search',-1),['clock','search','notes']);
 assert.deepEqual(moveWidget(initial,'clock',1),['notes','clock','search']);
 assert.deepEqual(initial,['clock','notes','search']);
});
test('first/last, missing and invalid moves are no-ops',()=>{
 const initial=['clock','notes'];
 for(const [id,direction] of [['clock',-1],['notes',1],['missing',1],['clock',0],['clock',2]]) assert.equal(moveWidget(initial,id,direction),initial);
 assert.deepEqual(moveWidget([],'clock',1),[]);
});
