// UI regression test with explicit backend doubles and isolated app data.
const { app, BrowserWindow, ipcMain } = require('electron');
setTimeout(() => { console.error('Electron UI regression timed out.'); app.exit(1); }, 60000).unref();
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'loops-model-ui-'));
app.setPath('userData', directory);
const preload = path.join(directory, 'preload.cjs');
fs.writeFileSync(preload, `const {contextBridge,ipcRenderer}=require('electron');contextBridge.exposeInMainWorld('loops',{request:(method,params)=>ipcRenderer.invoke('test:request',method,params),subscribe:()=>()=>{}});`);
let finishTest, fail = false;
const calls = [];
ipcMain.handle('test:request', async (_, method, params) => {
  calls.push({ method, params });
  if (method === 'settings') return { models: [], services: [], providers: [], defaultModelId: null };
  if (method === 'sharing_state') return { clients: [] };
  if (method === 'agent_plugins') return { agents: [], agentPluginErrors: [] };
  if (method === 'model_test') { await new Promise(resolve => { finishTest = resolve; }); if (fail) throw Error('测试服务拒绝请求'); return { success: true }; }
  if (['list','connector_list','connector_presets','automation_list'].includes(method)) return [];
  throw Error('Unexpected request: '+method);
});
app.whenReady().then(async () => {
  const window = new BrowserWindow({ show: false, width: 1200, height: 900, webPreferences: { preload, contextIsolation: true, sandbox: true } });
  const evaluate = code => window.webContents.executeJavaScript(code);
  const until = async code => { const limit=Date.now()+5000; while (!(await evaluate(code))) { if(Date.now()>limit) throw Error('UI timeout: '+code); await new Promise(resolve=>setTimeout(resolve,20)); } };
  const click = text => evaluate(`[...document.querySelectorAll('button')].find(button=>button.textContent.trim()===${JSON.stringify(text)}).click()`);
  const fill = (selector,value) => evaluate(`(()=>{const element=document.querySelector(${JSON.stringify(selector)});Object.getOwnPropertyDescriptor(element.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype,'value').set.call(element,${JSON.stringify(value)});element.dispatchEvent(new Event(element.tagName==='SELECT'?'change':'input',{bubbles:true}));})()`);
  try {
    await window.loadFile(path.resolve(__dirname, '../../dist/index.html'));
    await until('!!document.querySelector(".model-form")');
    await fill('.model-form select', 'compatible');
    await fill('[name=model]', 'test-model'); await fill('[name=baseUrl]', 'https://example.com/v1'); await fill('[name=apiKey]', 'fake-ui-key');
    await click('测试连接');
    await until('document.body.textContent.includes("测试中…")');
    assert.equal(await evaluate('document.querySelector(".model-form fieldset").disabled'), true);
    assert.equal(calls.filter(call=>call.method==='model_test').length,1);
    assert.equal(calls.find(call=>call.method==='model_test').params.apiKey,'fake-ui-key');
    assert.equal(calls.some(call=>call.method==='model_save'),false);
    finishTest(); await until('document.querySelector("[role=status]")?.textContent === "连接成功"');
    assert.equal(await evaluate('document.querySelector("[name=apiKey]").value'),'fake-ui-key');
    await fill('[name=model]', 'changed-model');
    await until('!document.querySelector(".model-form [role=status]")');
    fail = true; await click('测试连接'); await until('document.body.textContent.includes("测试中…")'); finishTest();
    await until('document.querySelector(".model-form [role=alert]")?.textContent.includes("测试服务拒绝请求")');
    assert.equal(await evaluate('document.querySelector(".model-form fieldset").disabled'), false);
    await evaluate('document.querySelector(".settings-window").click()');
    assert.equal(await evaluate('!!document.querySelector(".settings-window")'), true);
    await evaluate('document.querySelector(".dialog-backdrop").click()');
    await until('!document.querySelector(".settings-window")');
    console.log('Model connection and settings dismissal UI checks passed (explicit backend doubles).');
  } finally { window.destroy(); fs.rmSync(directory,{recursive:true,force:true}); app.quit(); }
}).catch(error => { console.error(error); app.exit(1); });
