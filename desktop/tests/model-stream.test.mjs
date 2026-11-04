import test from 'node:test';
import assert from 'node:assert/strict';
import { createAssistantMessageEventStream } from '@earendil-works/pi-ai';
import { modelStream, MODEL_IDLE_MESSAGE } from '../host/model-stream.mjs';
const model={provider:'test',id:'test-model',api:'test'};
const message=()=>({role:'assistant',provider:'test',model:'test-model',api:'test',content:[{type:'text',text:'ok'}],stopReason:'stop',timestamp:Date.now()});
function stalled(_model,_context,{signal}) {
 const stream=createAssistantMessageEventStream();
 signal.addEventListener('abort',()=>{const m={...message(),stopReason:'aborted',errorMessage:'SDK abort'};stream.push({type:'error',reason:'aborted',error:m});stream.end(m);},{once:true});
 return stream;
}
test('a stalled request reports inactivity, distinguishes cancellation and records no secrets',async()=>{
 const traces=[];
 const result=await modelStream(stalled,{idleMs:30,retries:0,record:t=>traces.push(t)})(model,{messages:[{secret:'private'}]},{apiKey:'secret-key'}).result();
 assert.equal(result.errorMessage,MODEL_IDLE_MESSAGE); assert.equal(traces.length,1);assert.equal(traces[0].outcome,'idle_timeout');
 assert(!JSON.stringify(traces).includes('private'));assert(!JSON.stringify(traces).includes('secret-key'));
 const controller=new AbortController();const cancelled=[];
 const pending=modelStream(stalled,{idleMs:1000,record:t=>cancelled.push(t)})(model,{}, {signal:controller.signal}).result();
 controller.abort();assert.notEqual((await pending).errorMessage,MODEL_IDLE_MESSAGE);assert.equal(cancelled[0].outcome,'cancelled');
});
test('real progress renews the per-request deadline across a longer generation',async()=>{
 const traces=[];
 const send=()=>{const stream=createAssistantMessageEventStream();let count=0;
  const timer=setInterval(()=>{stream.push({type:'thinking_delta',delta:'x',partial:message(),contentIndex:0});
   if(++count===8){clearInterval(timer);const m=message();stream.push({type:'done',reason:'stop',message:m});stream.end(m);}},15);return stream;};
 const result=await modelStream(send,{idleMs:70,record:t=>traces.push(t)})(model,{},{}).result();
 assert.equal(result.stopReason,'stop');await new Promise(resolve=>setImmediate(resolve));
 assert.equal(traces[0].deltaCount,8);assert(traces[0].durationMs>70);assert.equal(traces[0].outcome,'stop');
});
test('a pause after generation shows waiting feedback and progress clears it',async()=>{
 const source=createAssistantMessageEventStream(), phases=[];
 let paused; const pause=new Promise(resolve=>{paused=resolve;});
 const result=modelStream(()=>source,{idleMs:1000,slowMs:15,status:(_text,phase)=>{
  phases.push(phase);if(phase==='slow')paused();
 }})(model,{},{}).result();
 source.push({type:'thinking_delta',delta:'first',partial:message(),contentIndex:0});
 await pause;
 source.push({type:'thinking_delta',delta:'next',partial:message(),contentIndex:0});
 const m=message();source.push({type:'done',reason:'stop',message:m});source.end(m);
 await result;
 assert.deepEqual(phases,['waiting','generating','slow','generating']);
});

test('five retries are six attempts, and cancellation during backoff prevents another request',async()=>{
 let calls=0; const statuses=[];
 const fail=()=>{calls++;const s=createAssistantMessageEventStream();const m={...message(),stopReason:'error',errorMessage:'503: overloaded'};s.push({type:'error',reason:'error',error:m});s.end(m);return s;};
 const result=await modelStream(fail,{retryDelayMs:1,status:(_t,p)=>statuses.push(p)})(model,{},{}).result();
 assert.equal(calls,6);assert.equal(statuses.filter(p=>p==='retrying').length,5);assert.equal(result.stopReason,'error');
 const control=new AbortController();calls=0;
 const stopped=await modelStream(fail,{status:(_t,p)=>{if(p==='retrying')control.abort();}})(model,{}, {signal:control.signal}).result();
 assert.equal(calls,1);assert.equal(stopped.stopReason,'aborted');
});

test('retrying after a partial tool call never replays a completed tool or retains abandoned messages',async()=>{
 const {Agent}=await import('@earendil-works/pi-agent-core');
 let requests=0,writes=0;const contexts=[];
 const toolMessage=()=>({...message(),stopReason:'toolUse',content:[{type:'toolCall',id:'write-once',name:'write_record',arguments:{}}]});
 const send=(_m,context,{signal})=>{
  requests++;contexts.push(structuredClone(context.messages));const s=createAssistantMessageEventStream();
  const m=requests<=2?toolMessage():message();s.push({type:'start',partial:m});
  if(requests===2){
   s.push({type:'toolcall_delta',delta:'{}',contentIndex:0,partial:m});
   signal.addEventListener('abort',()=>{const failed={...m,stopReason:'aborted',errorMessage:'timeout'};s.push({type:'error',reason:'aborted',error:failed});s.end(failed);},{once:true});
  }else{s.push({type:'done',reason:m.stopReason,message:m});s.end(m);}return s;
 };
 const agent=new Agent({initialState:{model,tools:[{name:'write_record',label:'write',description:'write',parameters:{type:'object',properties:{}},execute:async()=>{writes++;return {content:[{type:'text',text:'saved'}],details:{}};}}]},streamFn:modelStream(send,{idleMs:25,retryDelayMs:1})});
 await agent.prompt('write then summarize');
 assert.equal(writes,1);assert.equal(requests,3);assert.deepEqual(contexts[1],contexts[2]);
 assert.equal(agent.state.messages.filter(m=>m.role==='toolResult').length,1);
 assert.equal(agent.state.messages.filter(m=>m.role==='assistant').length,2);
 assert.equal(agent.state.messages.at(-1).content[0].text,'ok');
});
