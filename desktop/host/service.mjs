import { EventEmitter } from 'node:events';
import { Engine } from './engine.mjs';
import { converse } from './agent.mjs';
import { readConfig, publicConfig } from './config.mjs';
import os from 'node:os';
import { draftAutomation, converseAutomation } from './automation-agent.mjs';
import { modelFailureMessage } from './model-errors.mjs';

export class Host extends EventEmitter {
  constructor(options) {
    super();
    this.options = options; this.engine = new Engine(options); this.busy = new Map(); this.deleting = new Set(); this.automationJobs = new Map(); this.automationStreams = new Map();
    this.engine.on('event', (event) => this.emit('event', event));
  }
  async action(method, params = {}) {
    const allowed = new Set(['list', 'trash', 'restore', 'rename', 'new', 'configure', 'fixture', 'save', 'start', 'stop', 'artifact', 'commands', 'script', 'automation_list', 'automation_get', 'automation_save', 'automation_enable', 'automation_sample', 'automation_run', 'automation_retry', 'automation_stop', 'automation_delete', 'automation_trash', 'automation_restore', 'automation_file']);
    allowed.add('automation_catalog');
    allowed.add('automation_test_output');
    const automationKey = `automation:${params.taskId}`;
    if (method === 'automation_get') return { ...await this.engine.call(method, params), chatBusy: this.busy.has(automationKey), chatText: this.automationStreams.get(automationKey) || '' };
    if (method === 'automation_cancel_chat') { this.busy.get(automationKey)?.abort(); return true; }
    if (['automation_save', 'automation_enable', 'automation_delete'].includes(method) && this.busy.has(automationKey)) throw new Error('自动化 AI 正在处理，请等回复结束或先停止回复。');
    if (method === 'automation_chat') {
      if (typeof params.text !== 'string' || !params.text.trim() || params.text.length > 16000) throw new Error('请输入有效的自动化需求或反馈。');
      let taskId = params.taskId;
      if (!taskId) taskId = (await this.engine.call('automation_new_conversation', { seedLoopId: params.seedLoopId })).id;
      const key = `automation:${taskId}`;
      if (this.busy.has(key)) throw new Error('自动化 AI 正在处理上一条消息。');
      const controller = new AbortController();
      this.busy.set(key, controller);
      const config = readConfig(this.options.configDirectory, this.options.fallbackConfigDirectory);
      try {
        await this.engine.call('automation_message', { taskId, role: 'user', text: params.text.trim() });
        if (!config.key) {
          await this.engine.call('automation_message', { taskId, role: 'error', text: 'DeepSeek 密钥尚未配置。你的需求已保留，配置后可以继续对话。' });
          this.busy.delete(key);
          return { accepted: false, reason: 'missing_credentials', taskId };
        }
      } catch (error) { this.busy.delete(key); throw error; }
      this.automationStreams.set(key, '');
      this.emit('event', { event: 'automation_agent_start', taskId });
      const timeout = setTimeout(() => controller.abort(), 180000);
      const job = converseAutomation({ engine: this.engine, taskId, text: params.text.trim(), editedSpec: params.editedSpec, config, signal: controller.signal, emit: event => {
        if (event.event === 'automation_agent_delta') this.automationStreams.set(key, (this.automationStreams.get(key) || '') + event.text);
        this.emit('event', event);
      } }).catch(async error => {
        await this.engine.call('automation_message', { taskId, role: 'error', text: controller.signal.aborted ? '本次回复已停止，对话和已完成的修改已保留。' : modelFailureMessage(error) });
      }).finally(() => {
        clearTimeout(timeout); this.busy.delete(key); this.automationJobs.delete(key); this.automationStreams.delete(key);
        this.emit('event', { event: 'automation_agent_done', taskId });
      });
      this.automationJobs.set(key, job);
      job.catch(() => {}); // An engine-exit event already reports transport failures.
      return { accepted: true, taskId };
    }
    if (method === 'automation_draft') {
      if (typeof params.text !== 'string' || !params.text.trim() || params.text.length > 16000) throw new Error('请输入自动化需求。');
      const config = readConfig(this.options.configDirectory, this.options.fallbackConfigDirectory);
      if (!config.key) throw new Error('DeepSeek 尚未配置，请先配置或手动填写任务。');
      return draftAutomation({ engine: this.engine, text: params.text, spec: params.spec, config });
    }
    if (method === 'delete') {
      if (this.busy.has(params.loopId)) throw new Error('Loops AI 正在处理，请等回复结束后再删除。');
      if (this.deleting.has(params.loopId)) throw new Error('正在删除这个 Loop。');
      this.deleting.add(params.loopId);
      try { return await this.engine.call('delete', params); }
      finally { this.deleting.delete(params.loopId); }
    }
    if (method === 'settings') return { ...publicConfig(readConfig(this.options.configDirectory, this.options.fallbackConfigDirectory)), homeDirectory: os.homedir(), ...await this.engine.call('environment') };
    if (method === 'chat') {
      if (typeof params.text !== 'string' || !params.text.trim() || params.text.length > 16000) throw new Error('请输入有效的需求或反馈。');
      if (!params.loopId) params = { ...params, loopId: (await this.engine.call('new')).id };
      if (this.busy.has(params.loopId)) throw new Error('Loops AI 正在处理上一条消息。');
      if (this.deleting.has(params.loopId)) throw new Error('正在删除这个 Loop。');
      const config = readConfig(this.options.configDirectory, this.options.fallbackConfigDirectory);
      const controller = new AbortController();
      this.busy.set(params.loopId, controller);
      try {
        await this.engine.call('message', { loopId: params.loopId, role: 'user', text: params.text.trim() });
        if (!config.key) {
          const message = 'DeepSeek 密钥尚未配置。你的需求已保留，配置后可以重新发送。';
          await this.engine.call('message', { loopId: params.loopId, role: 'error', text: message });
          this.busy.delete(params.loopId);
          return { accepted: false, reason: 'missing_credentials', loopId: params.loopId };
        }
      } catch (error) { this.busy.delete(params.loopId); throw error; }
      this.emit('event', { event: 'agent_start', loopId: params.loopId });
      const timeout = setTimeout(() => controller.abort(), 180000);
      converse({ engine: this.engine, loopId: params.loopId, text: params.text.trim(), config, signal: controller.signal, emit: (event) => this.emit('event', event) })
        .catch(async (error) => {
          const safe = controller.signal.aborted ? '本次回复已停止，需求与已完成的修改已保留。' : 'Loops AI 请求未完成。请检查 DeepSeek 的配置、网络和账户额度，需求与已完成的修改已保留。';
          await this.engine.call('message', { loopId: params.loopId, role: 'error', text: safe });
        }).finally(() => { clearTimeout(timeout); this.busy.delete(params.loopId); this.emit('event', { event: 'agent_done', loopId: params.loopId }); });
      return { accepted: true, loopId: params.loopId };
    }
    if (method === 'cancel_chat') { this.busy.get(params.loopId)?.abort(); return true; }
    if (!allowed.has(method)) throw new Error('不支持的客户端操作。');
    return this.engine.call(method, method === 'new' ? {} : params);
  }
  async close() { for (const controller of this.busy.values()) controller.abort(); await Promise.allSettled([...this.automationJobs.values()]); await this.engine.close(); }
}
