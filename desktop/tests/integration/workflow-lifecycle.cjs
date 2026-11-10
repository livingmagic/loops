// Real Electron preload -> IPC -> Host -> Python -> persisted state.
// A hand-authored workflow replaces AI generation. Native Agent discovery is
// disabled to avoid reading machine accounts; workflow APIs use the real Host.
const { app, BrowserWindow, ipcMain } = require('electron');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'loops-lifecycle-ui-'));
app.setPath('userData', path.join(directory, 'electron'));
const watchdog = setTimeout(() => { console.error('Workflow UI timed out.'); app.exit(1); }, 90000);
watchdog.unref();
app.whenReady().then(async () => {
  const desktop = path.resolve(__dirname, '../..');
  const { Host } = await import(pathToFileURL(path.join(desktop, 'host/service.mjs')));
  const host = new Host({dataDirectory:path.join(directory,'data'),configDirectory:path.join(directory,'config'),workspaceRoot:path.join(directory,'workspaces')});
  const calls = [];
  ipcMain.handle('loops:request', (_, method, params) => {
    calls.push(method);
    if (method === 'agent_plugins') return {agents:[],agentPluginErrors:[]};
    return host.action(method, params);
  });
  const window = new BrowserWindow({show:false,width:1280,height:960,webPreferences:{preload:path.join(desktop,'electron/preload.cjs'),contextIsolation:true,sandbox:true}});
  host.on('event', event => { if (!window.isDestroyed()) window.webContents.send('loops:event',event); });
  window.webContents.on('console-message', event => { if (event.level >= 2) console.error(event.message); });
  const evaluate = async code => {
    try { return await window.webContents.executeJavaScript(code); }
    catch (error) { console.error('Renderer command failed: '+code); throw error; }
  };
  async function until(code) {
    const limit=Date.now()+15000;
    while (Date.now()<limit) {
      if (await evaluate(code)) return;
      await new Promise(resolve=>setTimeout(resolve,20));
    }
    throw Error('UI timeout: '+code+'; visible='+await evaluate('document.body.innerText'));
  }
  const click = (text,scope='document') => evaluate(`[...${scope}.querySelectorAll('button')].find(b=>b.textContent.trim()===${JSON.stringify(text)}).click()`);
  const fill = (selector,value) => evaluate(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});Object.getOwnPropertyDescriptor(e.tagName==='SELECT'?HTMLSelectElement.prototype:e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(e,${JSON.stringify(value)});e.dispatchEvent(new Event(e.tagName==='SELECT'?'change':'input',{bubbles:true}));})()`);
  let loop;
  const definition = marker => ({schemaVersion:2,title:'CI 生命周期',goal:'将输入转换为大写',completion:'返回实际结果',inputs:[{id:'text',label:'文本',kind:'text',required:true}],
    scripts:{'scripts/convert.py':`import json,os\n# ${marker}\ncontext=json.loads(os.environ['LOOPS_CONTEXT'])\nresult=context['inputs']['text'].upper()\n`},
    steps:[{id:'convert',label:'转换文本',instructions:'转换为大写',inputs:{text:'${inputs.text}'},execution:{tool:'python',script:'scripts/convert.py'},outputs:[{id:'reply',label:'结果',kind:'value',source:'result',dataType:'string'}],checks:[],onFailure:{action:'stop'}}],finalOutputs:['${steps.convert.outputs.reply}']});
  async function trialAndSave() {
    await click('试跑');
    await until('!!document.querySelector("[aria-label=本次试跑]")');
    await fill('#input-text','hello ci');
    await click('开始试跑');
    await until('!!document.querySelector(".run-card.success")');
    await until('[...document.querySelectorAll(".toolbar-actions button")].some(b=>b.textContent.trim()==="保存"&&!b.disabled)');
    await click('保存', 'document.querySelector(".toolbar-actions")');
    await until('!document.querySelector(".save-state.unsaved")');
  }
  try {
    loop = await host.action('new');
    await host.engine.call('definition',{loopId:loop.id,definition:definition('version-one')});
    await window.loadFile(path.join(desktop,'dist/index.html'));
    await until('!!document.querySelector(".settings-window")');
    await evaluate('document.querySelector("[aria-label=关闭设置]").click()');
    await click('CI 生命周期');
    await until('!!document.querySelector(".toolbar-actions")');
    assert.equal(await evaluate('[...document.querySelectorAll(".toolbar-actions button")].find(b=>b.textContent.trim()==="保存").disabled'),true);
    await trialAndSave();
    let saved=(await host.action('list')).find(item=>item.id===loop.id);
    assert.equal(saved.savedRevision,1);
    assert.equal(saved.runs.at(-1).artifacts[0].value,'HELLO CI');
    await evaluate('document.querySelector(".run-card.success .real-artifacts button").click()');
    await until('!!document.querySelector(".dialog")');
    assert.match(await evaluate('document.querySelector(".dialog").innerText'),/HELLO CI/);
    await evaluate('document.querySelector(".dialog [aria-label=关闭]").click()');
    await host.engine.call('definition',{loopId:loop.id,definition:definition('version-two')});
    await until('document.querySelector(".save-state.unsaved")?.textContent==="未保存"');
    await trialAndSave();
    saved=(await host.action('list')).find(item=>item.id===loop.id);
    assert.equal(saved.savedRevision,2);
    await fill('[aria-label=流程版本]','1');
    await until('!!document.querySelector(".flow-step")');
    await evaluate('document.querySelector(".flow-step").click()');
    await until('!!document.querySelector(".script-link")');
    await evaluate('document.querySelector(".script-link").click()');
    await until('!!document.querySelector(".dialog")');
    const script=await evaluate('document.querySelector(".dialog").innerText');
    assert.match(script,/version-one/);assert.ok(!script.includes('version-two'));
    await evaluate('document.querySelector(".dialog [aria-label=关闭]").click()');
    await evaluate(`document.getElementById(${JSON.stringify('loop-more-')}+${JSON.stringify(loop.id)}).click()`);
    await click('删除','document.querySelector(".loop-menu")');
    await until('document.querySelector(".dialog")?.textContent.includes("删除")');
    await click('删除','document.querySelector(".dialog")');
    await until('![...document.querySelectorAll(".loop-list .loop-item")].some(b=>b.textContent==="CI 生命周期")');
    await click('设置');await click('回收站');
    await until('[...document.querySelectorAll(".settings-row")].some(row=>row.textContent.includes("CI 生命周期"))');
    await click('恢复','[...document.querySelectorAll(".settings-row")].find(row=>row.textContent.includes("CI 生命周期"))');
    await evaluate('document.querySelector("[aria-label=关闭设置]").click()');
    await until('[...document.querySelectorAll(".loop-list .loop-item")].some(b=>b.textContent==="CI 生命周期")');
    await window.reload();
    await until('!!document.querySelector(".settings-window")');
    await evaluate('document.querySelector("[aria-label=关闭设置]").click()');
    await click('CI 生命周期');
    await until('document.querySelector(".save-state")?.textContent==="版本 2"');
    const restored=(await host.action('list')).find(item=>item.id===loop.id);
    assert.equal(restored.savedRevision,2);assert.equal(restored.runs.length,2);
    for (const method of ['start','save','version','artifact','delete','restore']) assert.ok(calls.includes(method),method+' must cross the real IPC/Host boundary');
    assert.ok(!calls.includes('chat'),'This test must never invoke a model.');
    console.log('Workflow lifecycle UI passed with real Host/Python and isolated data.');
  } finally {
    window.destroy();await host.close();fs.rmSync(directory,{recursive:true,force:true});clearTimeout(watchdog);app.quit();
  }
}).catch(error=>{console.error(error);app.exit(1);});
