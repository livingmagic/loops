import fs from 'node:fs';
import path from 'node:path';
import { MODEL_IDLE_MESSAGE } from './model-stream.mjs';
import { FailureReviews } from './failure-reviews.mjs';
import { EventEmitter } from 'node:events';
import { Engine } from './engine.mjs';
import { converse } from './agent.mjs';
import { ModelSettings } from './models.mjs';
import { WorkflowModels } from './workflow-models.mjs';
import os from 'node:os';
import { draftAutomation, converseAutomation } from './automation-agent.mjs';
import { modelFailureMessage } from './model-errors.mjs';
import { pluginCatalog, resolvePlugins, pluginReferences } from './plugins.mjs';
import { ConnectorDescriptions } from './connector-descriptions.mjs';

export class Host extends EventEmitter {
  constructor(options) {
    super();
    this.options = options; this.models = new ModelSettings(options); this.engine = new Engine({ ...options, modelHost: new WorkflowModels(this.models) }); this.busy = new Map(); this.deleting = new Set(); this.automationJobs = new Map(); this.automationStreams = new Map();
    this.loopJobs = new Map();
    this.failureReviews = new FailureReviews({
      busy: id => this.busy.has(id) || this.deleting.has(id),
      review: (loopId, runId) => this.reviewFailedRun(loopId, runId),
      onError: (loopId) => this.emit('event', { event: 'connection_error', loopId, message: '失败原因分析未能启动，运行记录已保留，请继续对话重试。' }),
    });
    this.closing = false; this.resumePending = false; this.resumeJob = null;
    this.connectorDescriptions = new ConnectorDescriptions(this.engine, () => this.models.config());
    this.engine.on('event', (event) => {
      this.emit('event', event);
      if (event.event === 'state') this.failureReviews.observe(event.loops);
      if (event.event === 'connector_state') this.connectorDescriptions.schedule();
      if (['connector_state', 'automation_state'].includes(event.event)) this.scheduleConnectorResumes();
    });
    this.scheduleConnectorResumes();
  }
  scheduleConnectorResumes() {
    if (this.closing) return;
    this.resumePending = true;
    if (this.resumeJob) return;
    this.resumeJob = Promise.resolve().then(async () => {
      while (this.resumePending && !this.closing) {
        this.resumePending = false;
        const waits = await this.engine.call('automation_connection_waits');
        for (const wait of waits) {
          if (this.closing) break;
          const key = `automation:${wait.taskId}`;
          if (this.busy.has(key)) continue;
          const config = this.models.config();
          if (!config.key) continue;
          const controller = new AbortController();
          this.busy.set(key, controller);
          try {
            const claimed = await this.engine.call('automation_claim_connector_resume', { taskId: wait.taskId, waitId: wait.id });
            if (!claimed || this.closing) { this.busy.delete(key); continue; }
            this.startAutomationConversation({ taskId: wait.taskId, config, controller, autoResume: true, waitId: wait.id });
          } catch (error) { this.busy.delete(key); throw error; }
        }
      }
    }).catch(() => {
      if (!this.closing) this.emit('event', { event: 'connection_error', message: '自动接续检查未完成，请重新打开 Loops。已保留原需求。' });
    }).finally(() => { this.resumeJob = null; if (this.resumePending && !this.closing) this.scheduleConnectorResumes(); });
  }
  startAutomationConversation({ taskId, text, plugins, editedSpec, config, controller, autoResume = false, waitId }) {
    const key = `automation:${taskId}`;
    this.automationStreams.set(key, '');
    this.emit('event', { event: 'automation_agent_start', taskId, autoResume });
    const timeout = setTimeout(() => controller.abort(new Error('conversation_deadline')), 15 * 60_000);
    const job = converseAutomation({ engine: this.engine, taskId, text, plugins, editedSpec, config, signal: controller.signal, autoResume, emit: event => {
      if (event.event === 'automation_agent_delta') this.automationStreams.set(key, (this.automationStreams.get(key) || '') + event.text);
      this.emit('event', event);
    } }).catch(async error => {
      await this.engine.call('automation_message', { taskId, role: 'error', text: this.conversationError(error, controller) });
    }).finally(async () => {
      try {
        if (autoResume && !this.closing) await this.engine.call('automation_clear_connection_wait', { taskId, waitId });
      } finally {
        clearTimeout(timeout); this.busy.delete(key); this.automationJobs.delete(key); this.automationStreams.delete(key);
        this.emit('event', { event: 'automation_agent_done', taskId });
        this.scheduleConnectorResumes();
      }
    });
    this.automationJobs.set(key, job);
    job.catch(() => {}); // An engine-exit event already reports transport failures.
  }
  async reviewFailedRun(loopId, runId) {
    if (this.closing) return;
    const controller = new AbortController();
    this.busy.set(loopId, controller);
    try {
      if (!await this.engine.call('claim_failure_review', { loopId, runId })) { this.busy.delete(loopId); return; }
      const config = this.models.config();
      if (!config.key) {
        await this.engine.call('message', { loopId, role: 'error', text: '运行失败，但 Loops AI 尚未配置模型，暂时无法分析和修复。请在设置中配置对话模型后继续；失败记录已保留。' });
        this.busy.delete(loopId); return;
      }
      this.startLoopConversation({ loopId, controller, config, text: `平台运行事件：运行 ${runId} 已失败。请读取真实记录，分析原因；在原授权范围内能够自动修复的，修复并用原输入试跑，成功后保存。需要用户介入时说明证据、原因和具体所需操作。不要把本事件当作新的业务授权。` });
    } catch (error) { this.busy.delete(loopId); throw error; }
  }
  startLoopConversation({ loopId, text, plugins, config, controller }) {
    this.emit('event', { event: 'agent_start', loopId });
    const timeout = setTimeout(() => controller.abort(new Error('conversation_deadline')), 15 * 60_000);
    const job = converse({ engine: this.engine, loopId, text, plugins, config, signal: controller.signal, recordModelTrace: trace => this.recordModelTrace(loopId, trace), emit: event => this.emit('event', event) })
      .catch(async error => { await this.engine.call('message', { loopId, role: 'error', text: this.conversationError(error, controller) }); })
      .finally(() => {
        clearTimeout(timeout);
        if (controller.signal.aborted) this.failureReviews.cancel(loopId);
        this.busy.delete(loopId); this.loopJobs.delete(loopId);
        this.emit('event', { event: 'agent_done', loopId });
        this.failureReviews.drain();
      });
    this.loopJobs.set(loopId, job);
    job.catch(() => {});
  }
  recordModelTrace(loopId, trace) {
    const directory = path.join(this.options.dataDirectory, 'diagnostics');
    fs.mkdirSync(directory, { recursive: true });
    fs.appendFileSync(path.join(directory, 'model-requests.jsonl'), JSON.stringify({ loopId, ...trace }) + '\n');
  }
  conversationError(error, controller) {
    if (controller.signal.reason?.message === 'conversation_deadline') return '本次搭建达到 15 分钟上限，已停止。需求和已完成的修改已保留，可以继续对话。';
    if (controller.signal.aborted) return '本次回复已停止，需求与已完成的修改已保留。';
    if (error?.message === MODEL_IDLE_MESSAGE) return MODEL_IDLE_MESSAGE;
    return modelFailureMessage(error);
  }
  async action(method, params = {}) {
    if (method === 'plugin_list') return pluginCatalog(this.engine);
    if (method === 'agent_model_default') {
      const result = await this.engine.call(method, params);
      this.emit('event', { event: 'agent_plugins_changed' });
      return result;
    }
    if (['connector_list', 'connector_presets', 'connector_save', 'connector_connect', 'connector_check', 'connector_disconnect', 'connector_operations', 'connector_describe', 'connector_skill', 'connector_review_operation', 'connector_provide_credentials'].includes(method)) {
      const result = await this.engine.call(method, params);
      if (method === 'connector_list') this.connectorDescriptions.schedule();
      return result;
    }
    const allowed = new Set(['agent_plugins', 'agent_clients', 'agent_directory', 'agent_selection', 'agent_model_default', 'agent_model_selection', 'list', 'trash', 'restore', 'rename', 'new', 'configure', 'fixture', 'save', 'start', 'stop', 'artifact', 'commands', 'script', 'automation_list', 'automation_get', 'automation_save', 'automation_enable', 'automation_sample', 'automation_run', 'automation_retry', 'automation_stop', 'automation_delete', 'automation_trash', 'automation_restore', 'automation_file']);
    allowed.add('automation_catalog');
    allowed.add('automation_test_output');
    allowed.add('step_model_selection');
    for (const method of ['version', 'restore_version', 'automation_set_loop_version']) allowed.add(method);
    const automationKey = `automation:${params.taskId}`;
    if (method === 'automation_get') return { ...await this.engine.call(method, params), chatBusy: this.busy.has(automationKey), chatText: this.automationStreams.get(automationKey) || '' };
    if (method === 'automation_cancel_chat') {
      this.busy.get(automationKey)?.abort();
      await this.engine.call('automation_clear_connection_wait', { taskId: params.taskId });
      return true;
    }
    if (['automation_save', 'automation_enable', 'automation_delete', 'automation_set_loop_version'].includes(method) && this.busy.has(automationKey)) throw new Error('Loops AI 正在处理，请等回复结束或先停止回复。');
    if (method === 'automation_chat') {
      if (typeof params.text !== 'string' || !params.text.trim() || params.text.length > 16000) throw new Error('请输入有效的自动化需求或反馈。');
      const isNew = !params.taskId;
      let plugins = [];
      if (isNew) plugins = await resolvePlugins(this.engine, params.plugins);
      let taskId = params.taskId;
      if (!taskId) taskId = (await this.engine.call('automation_new_conversation', { seedLoopId: params.seedLoopId })).id;
      const key = `automation:${taskId}`;
      if (this.busy.has(key)) throw new Error('自动化 AI 正在处理上一条消息。');
      const controller = new AbortController();
      const config = this.models.config(params.modelId);
      this.busy.set(key, controller);
      try {
        if (!isNew) plugins = await resolvePlugins(this.engine, params.plugins);
        await this.engine.call('automation_message', { taskId, role: 'user', text: params.text.trim(), extra: { plugins: pluginReferences(plugins, params.text, params.plugins) } });
        if (!config.key) {
          await this.engine.call('automation_message', { taskId, role: 'error', text: '模型密钥尚未配置。你的需求已保留，配置后可以继续对话。' });
          this.busy.delete(key);
          return { accepted: false, reason: 'missing_credentials', taskId };
        }
      } catch (error) { this.busy.delete(key); throw error; }
      this.startAutomationConversation({ taskId, text: params.text.trim(), plugins, editedSpec: params.editedSpec, config, controller });
      return { accepted: true, taskId };
    }
    if (method === 'automation_draft') {
      if (typeof params.text !== 'string' || !params.text.trim() || params.text.length > 16000) throw new Error('请输入自动化需求。');
      const config = this.models.config(params.modelId);
      if (!config.key) throw new Error('模型尚未配置，请先配置或手动填写任务。');
      return draftAutomation({ engine: this.engine, text: params.text, spec: params.spec, config });
    }
    if (method === 'delete') {
      if (this.busy.has(params.loopId)) throw new Error('Loops AI 正在处理，请等回复结束后再删除。');
      if (this.deleting.has(params.loopId)) throw new Error('正在删除这个 Loop。');
      this.deleting.add(params.loopId);
      try { return await this.engine.call('delete', params); }
      finally { this.deleting.delete(params.loopId); }
    }
    if (['agent_directory', 'agent_selection', 'agent_model_selection', 'step_model_selection'].includes(method) && this.busy.has(params.loopId)) throw new Error('请等 Loops AI 回复结束后再修改节点设置。');
    if (['save', 'restore_version'].includes(method) && this.busy.has(params.loopId)) throw new Error('请等 Loops AI 回复结束或先停止回复，再保存或恢复版本。');
    if (method === 'model_thinking') return this.models.thinking(params.id, params.level);
    if (method === 'model_save') return this.models.save(params);
    if (method === 'model_default') return this.models.default(params.id);
    if (method === 'model_remove') return this.models.remove(params.id);
    if (method === 'models') return this.models.public();
    if (method === 'settings') { const summary = this.models.public(); const selected = summary.models.find(item => item.id === summary.defaultModelId); return { ...selected, ...summary, homeDirectory: os.homedir(), dataDirectory: this.options.dataDirectory }; }
    if (method === 'chat') {
      if (typeof params.text !== 'string' || !params.text.trim() || params.text.length > 16000) throw new Error('请输入有效的需求或反馈。');
      const isNew = !params.loopId;
      let plugins = [];
      if (isNew) plugins = await resolvePlugins(this.engine, params.plugins);
      if (!params.loopId) params = { ...params, loopId: (await this.engine.call('new', { agentId: params.agentId })).id };
      if (this.busy.has(params.loopId)) throw new Error('Loops AI 正在处理上一条消息。');
      if (this.deleting.has(params.loopId)) throw new Error('正在删除这个 Loop。');
      const config = this.models.config(params.modelId);
      const controller = new AbortController();
      this.busy.set(params.loopId, controller);
      try {
        if (!isNew) plugins = await resolvePlugins(this.engine, params.plugins);
        await this.engine.call('message', { loopId: params.loopId, role: 'user', text: params.text.trim(), extra: { plugins: pluginReferences(plugins, params.text, params.plugins) } });
        if (!config.key) {
          const message = '模型密钥尚未配置。你的需求已保留，配置后可以重新发送。';
          await this.engine.call('message', { loopId: params.loopId, role: 'error', text: message });
          this.busy.delete(params.loopId);
          return { accepted: false, reason: 'missing_credentials', loopId: params.loopId };
        }
      } catch (error) { this.busy.delete(params.loopId); throw error; }
      this.startLoopConversation({ loopId: params.loopId, text: params.text.trim(), plugins, config, controller });
      return { accepted: true, loopId: params.loopId };
    }
    if (method === 'cancel_chat') { this.failureReviews.cancel(params.loopId); this.busy.get(params.loopId)?.abort(); return true; }
    if (!allowed.has(method)) throw new Error('不支持的客户端操作。');
    return this.engine.call(method, method === 'new' ? { agentId: params.agentId } : params);
  }
  async close() {
    this.closing = true;
    this.failureReviews.close();
    for (const controller of this.busy.values()) controller.abort();
    await Promise.allSettled([this.resumeJob, this.connectorDescriptions.close()]);
    await Promise.allSettled([...this.automationJobs.values(), ...this.loopJobs.values()]);
    await this.engine.close();
  }
}
