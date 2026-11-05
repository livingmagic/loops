import test from 'node:test';
import assert from 'node:assert/strict';
import { createPluginStore } from '../src/plugin-store.mjs';
const response = method => method === 'agent_plugins' ? { agents: [{id:'codex'}] } : [{id:method}];
test('startup loads page data and slash catalog together; subscribers reuse the same snapshot',async()=>{
 const calls=[];const store=createPluginStore(async method=>{calls.push(method);return response(method);});
 const first=store.refresh();assert.equal(store.refresh(),first);await first;
 assert.equal(calls.length,4);assert.equal(store.getSnapshot().loaded,true);
 let unsubscribe=store.subscribe(()=>{});unsubscribe();unsubscribe=store.subscribe(()=>{});
 assert.equal(calls.length,4);assert.equal(store.getSnapshot().agents[0].id,'codex');unsubscribe();
});
test('background refresh keeps loaded data and failures preserve last successful values',async()=>{
 let fail=false;const store=createPluginStore(async method=>{if(fail&&method==='connector_list')throw Error('offline');return response(method);});
 await store.refresh();const items=store.getSnapshot().items;fail=true;const pending=store.refresh();
 assert.equal(store.getSnapshot().loading,false);assert.equal(store.getSnapshot().items,items);await pending;
 assert.equal(store.getSnapshot().items,items);assert.equal(store.getSnapshot().pageError,'offline');
 fail=false;await store.refresh();assert.equal(store.getSnapshot().pageError,'');
});
test('a state change during loading triggers one follow-up so stale data is not retained',async()=>{
 let release,calls=0;const gate=new Promise(resolve=>{release=resolve;});
 const store=createPluginStore(async method=>{calls++;await gate;return response(method);});
 const pending=store.refresh();store.refresh({invalidate:true});store.refresh({invalidate:true});release();await pending;
 assert.equal(calls,8);assert.equal(store.getSnapshot().loaded,true);
});
