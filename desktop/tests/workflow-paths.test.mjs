import test from 'node:test';
import assert from 'node:assert/strict';
import { workflowPaths, shortPathText } from '../src/workflow-paths.js';
const loop = { workdir:'/Users/pc/Loops/a', revision:2, config:{resources:[{id:'repo',path:'/Users/pc/project'}],inputDefaults:{}}, definition:{inputs:[{id:'source',kind:'file'},{id:'text',kind:'text',label:'采购清单'}],steps:[{id:'parse',outputs:[{id:'json',path:'${workspace}/purchase.json'}]}]},runs:[{id:'old',revision:1,directory:'/old'},{id:'latest',revision:2,directory:'/Users/pc/Loops/a/runs/latest',inputs:{source:'/Users/pc/Loops/a/runs/latest/inputs/source.txt'}}]};
test('workflow files resolve consistently using the matching real run, preserving text inputs',()=>{
 const {resolve,workspace}=workflowPaths(loop,'old');
 assert.equal(workspace,'/Users/pc/Loops/a/runs/latest/workspace');
 assert.equal(resolve('${workspace}/purchase.json'),workspace+'/purchase.json');
 assert.equal(resolve('${steps.parse.outputs.json}'),workspace+'/purchase.json');
 assert.equal(resolve('purchase.json',true),workspace+'/purchase.json');
 assert.equal(resolve('${resources.repo}'),'/Users/pc/project');
 assert.equal(resolve('${inputs.source}'),'/Users/pc/Loops/a/runs/latest/inputs/source.txt');
 assert.equal(resolve('${inputs.text}'),'本次输入 · 采购清单');
 assert.equal(resolve('/Users/pc/external',true),'/Users/pc/external');
 assert.equal(shortPathText(workspace,'/Users/pc'),'~/Loops/a/runs/latest/workspace');
 assert.equal(shortPathText('/Users/pc-other/project','/Users/pc'),'/Users/pc-other/project');
});
test('unexecuted revisions never display a previous run as their actual directory',()=>{
 const {workspace}=workflowPaths({...loop,revision:3});
 assert.equal(workspace,'/Users/pc/Loops/a/runs/{runId}/workspace');
});
