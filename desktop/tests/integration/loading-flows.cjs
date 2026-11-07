// UI regression harness. All backend responses below are explicit test doubles.
// Run with Electron after building desktop/dist. Never uses installed Loops data.
const { app, BrowserWindow, ipcMain } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'loops-loading-ui-'));
app.setPath('userData', directory);
const preload = path.join(directory, 'preload.cjs');
fs.writeFileSync(preload, `
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('loops', {
  request: (method, params) => ipcRenderer.invoke('test:request', method, params),
  subscribe: callback => {
    const handler = (_, value) => callback(value);
    ipcRenderer.on('test:event', handler);
    return () => ipcRenderer.removeListener('test:event', handler);
  },
});
`);
const calls = [];
let failList = true, finishAgents, finishCatalog, holdAgents = true;
const agents = { agents: [{id:'codex',name:'Codex',installed:true,ready:true,detail:'Test Agent',models:[]}], agentPluginErrors:[] };
const task = {id:'task',title:'Loading test task',revision:0,enabled:false,latestRun:null};
ipcMain.handle('test:request', async (_, method, params) => {
  calls.push({method,params});
  if (method === 'list') {
    if (failList) { failList = false; throw Error('Test initial list failure'); }
    return [];
  }
  if (method === 'settings') return {defaultModelId:'configured',models:[{id:'configured',name:'Test model',configured:true,thinkingLevel:'off',thinkingLevels:['off']}],providers:[],services:[]};
  if (method === 'connector_list') return [{manifest:{id:'example',name:'Available MCP',type:'mcp'},enabled:true,status:'connected'}];
  if (method === 'connector_presets') return [];
  if (method === 'agent_plugins') {
    if (holdAgents) return new Promise(resolve => {finishAgents = resolve;});
    return agents;
  }
  if (method === 'automation_list') return [task];
  if (method === 'automation_catalog') return new Promise(resolve => {finishCatalog = resolve;});
  if (method === 'automation_get') return {...task,spec:null,draftSpec:null,runs:[],messages:[],logs:[],chatBusy:false};
  throw Error('Unexpected request: '+method);
});
app.whenReady().then(async () => {
  const window = new BrowserWindow({show:false,width:1200,height:900,webPreferences:{preload,contextIsolation:true,sandbox:true}});
  window.webContents.on('console-message', event => { if (event.level >= 2) console.error(event.message); });
  const evaluate = source => window.webContents.executeJavaScript(source);
  async function until(source) {
    const limit = Date.now()+5000;
    while(Date.now()<limit) {
      if(await evaluate(source)) return;
      await new Promise(resolve=>setTimeout(resolve,20));
    }
    throw Error('Timed out: '+source+'; body='+await evaluate('document.body.innerText')+'; calls='+JSON.stringify(calls));
  }
  function click(text) {
    return evaluate(`[...document.querySelectorAll('button')].find(button => button.textContent.trim() === ${JSON.stringify(text)}).click()`);
  }
  try {
    await window.loadFile(path.resolve(__dirname,'../../dist/index.html'));
    await until('!!document.querySelector(".loop-load-error")');
    assert.equal(calls.some(call=>call.method==='agent_plugins'),false);
    await click('重新加载流程');
    await until('!document.querySelector(".loop-load-error")');
    await until('document.querySelector(".model-trigger")?.textContent.includes("Test model")');
    await click('插件');
    await until('document.body.textContent.includes("Available MCP")');
    assert.ok(finishAgents);
    assert.equal(await evaluate('document.querySelectorAll(".connector-card").length'),1);
    holdAgents=false; finishAgents(agents);
    await until('document.querySelectorAll(".connector-card").length === 2');
    const before=calls.filter(call=>call.method==='agent_plugins').length;
    window.webContents.send('test:event',{event:'connector_state'});
    // A visible render after the event confirms the connection refresh settled.
    await evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))');
    assert.equal(calls.filter(call=>call.method==='agent_plugins').length,before);
    await click('自动化任务');
    await until('!!document.querySelector(".auto-task-row")');
    assert.ok(finishCatalog, 'catalog request is still pending');
    await evaluate('document.querySelector(".auto-task-row").click()');
    await until('document.querySelector(".auto-title h1")?.textContent === "Loading test task"');
    finishCatalog([]);
    // Independently verify that a live state event recovers the failed startup.
    failList=true;
    calls.length=0;
    await window.loadFile(path.resolve(__dirname,'../../dist/index.html'));
    await until('!!document.querySelector(".loop-load-error")');
    window.webContents.send('test:event',{event:'state',loops:[]});
    await until('!document.querySelector(".loop-load-error")');
    await until('document.querySelector(".model-trigger")?.textContent.includes("Test model")');
    await evaluate('new Promise(resolve=>requestIdleCallback(()=>setTimeout(resolve,0),{timeout:1000}))');
    assert.ok(calls.some(call=>call.method==='agent_plugins'), 'live recovery restarts background warmup');
    console.log('Loading UI checks passed (explicit backend doubles).');
  } finally {
    window.destroy();
    fs.rmSync(directory,{recursive:true,force:true});
    app.quit();
  }
}).catch(error => { console.error(error); app.exit(1); });
