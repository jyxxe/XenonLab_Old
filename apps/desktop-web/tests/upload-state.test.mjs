import test from 'node:test';
import assert from 'node:assert/strict';
import {createUploadStore} from '../src/upload-state.js';
test('upload memory isolates table/row keys and survives unsubscribed recoverable work',()=>{
 const store=createUploadStore(),a=JSON.stringify(['table-a','same-row']),b=JSON.stringify(['table-b','same-row']);
 const file={name:'ordinary.bin'};let notifications=0;const off=store.subscribe(a,()=>notifications++);
 store.set(a,{resume:{file,uploadId:'session-a',nextPart:2,partCount:3,partSize:8},busy:false,message:'retry'});
 off();assert.equal(store.get(a).resume.file,file);assert.equal(store.get(b).resume,null);
 const offAgain=store.subscribe(a,()=>notifications++);store.set(a,{busy:true});assert.equal(store.get(a).busy,true);assert.equal(notifications,2);
 store.set(a,{resume:null,busy:false,message:'complete'});assert.equal(store.get(a).resume,null);offAgain();assert.equal(store.get(a).message,'');
});
test('disposal releases old File references and prevents late writes without changing a new owner',()=>{
 const old=createUploadStore(),fresh=createUploadStore();old.set('a',{resume:{file:{name:'file'},uploadId:'old'},busy:true});old.dispose();
 old.set('a',{busy:false,resume:{file:{name:'late'},uploadId:'late'}});assert.equal(old.alive,false);assert.equal(old.get('a').resume,null);assert.equal(fresh.get('a').resume,null);
 fresh.set('a',{busy:true});assert.equal(fresh.alive,true);assert.equal(fresh.get('a').busy,true);
});
