import { modelHarnessOptions, closeModelHarness } from './model-harness.mjs';
const modelOptions = await modelHarnessOptions();
import fs from 'node:fs/promises';
import path from 'node:path';
import assert from 'node:assert/strict';
import { Host } from '../host/service.mjs';
const desktop=path.resolve(new URL('..',import.meta.url).pathname),qa=path.join(desktop,'qa/artifacts/live-automation-chat');
const previous=JSON.parse(await fs.readFile(path.join(qa,'live-chat-results.json'),'utf8'));
const root=previous.dataDirectory;
const host=new Host({...modelOptions,dataDirectory:root,workspaceRoot:path.join(root,'workspaces'),configDirectory:desktop,fallbackConfigDirectory:path.dirname(desktop)});
try {
 const text=`每天上午9点读取固定文件 ${path.join(root,'source.txt')}，依次运行“内容整理”和“报告编写”，结果保存到 ${path.join(root,'scope-check')}。先搭建，不保存或启用。`;
 const accepted=await host.action('automation_chat',{text});assert.equal(accepted.accepted,true);
 const task=await new Promise((resolve,reject)=>{let busy=false,again=false;const done=()=>{clearTimeout(timer);host.off('event',event)};const timer=setTimeout(()=>{done();reject(new Error('timeout'))},180000);const event=e=>{if(['automation_agent_done','automation_state'].includes(e.event))void tick()};async function tick(){if(busy){again=true;return}busy=true;try{const t=await host.action('automation_get',{taskId:accepted.taskId});if(!t.chatBusy){done();resolve(t)}}catch(e){done();reject(e)}finally{busy=false;if(again){again=false;void tick()}}}host.on('event',event);void tick()});
 assert.ok(task.draftSpec,JSON.stringify(task.messages));assert.equal(task.revision,0);assert.equal(task.enabled,false);assert.equal(task.runs.length,0);
 await fs.writeFile(path.join(qa,'trigger-scope.json'),JSON.stringify({taskId:task.id,prompt:text,spec:task.draftSpec,messages:task.messages},null,2));
 console.log(task.draftSpec.source.script);
 console.log(task.messages.filter(m=>m.role==='assistant').at(-1)?.text);
}finally{await host.close();closeModelHarness()}
