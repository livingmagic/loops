import { randomUUID } from 'node:crypto';

const labels = {
  list_plugins:'查看插件', read_connector_skill:'读取插件用法',
  inspect_project:'查看项目与执行能力', list_connectors:'发现已连接的工具', list_connector_operations:'查找可用操作',
  inspect_connector_operation:'查看工具契约', preview_connector_operation:'预览工具返回', review_connector_operation:'检查操作影响',
  update_loop:'更新流程', save_loop:'保存流程', start_trial:'启动试跑', get_run_result:'检查运行结果',
  read_project_file:'读取项目文件', read_input:'读取输入', set_execution_agent:'选择执行工具',
};
export class ConversationProgress {
  constructor(engine, loopId, emit) {
    this.engine = engine; this.loopId = loopId; this.emit = emit; this.id = randomUUID();
    this.value = {startedAt:new Date().toISOString(),status:'running',events:[],current:'正在处理…'};
    this.queue = Promise.resolve();
  }
  async start() {
    await this.engine.call('message', {loopId:this.loopId,role:'process',text:'',extra:{id:this.id,process:this.value}});
  }
  persist() {
    const process = structuredClone(this.value);
    this.queue = this.queue.then(() => this.engine.call('message_update', {loopId:this.loopId,messageId:this.id,process}));
    // Keep rejection handled until finish awaits and reports it.
    this.queue.catch(() => {});
  }
  status(text, phase) {
    this.value.current = text;
    if (phase === 'retrying' || phase === 'slow') this.value.events.push({type:'status',text,at:new Date().toISOString()});
    if (phase === 'waiting' || phase === 'retrying') this.emit({event:'agent_delta_reset',loopId:this.loopId});
    this.persist();
  }
  preparing(name) {
    this.status(`正在准备：${labels[name] || name}`, 'preparing');
  }
  tool(event) {
    const at = new Date().toISOString();
    const operation = typeof event.args?.operation === 'string' ? ` · ${event.args.operation.slice(0, 120)}` : '';
    if (event.type === 'tool_execution_start') {
      this.value.events.push({id:event.toolCallId,type:'tool',text:(labels[event.toolName] || event.toolName) + operation,status:'running',at});
      this.value.current = labels[event.toolName] || event.toolName;
    } else {
      const item = this.value.events.find(e => e.id === event.toolCallId);
      if (item) { item.status = event.isError ? 'failed' : 'success'; item.durationMs = Date.now() - Date.parse(item.at); }
    }
    this.persist();
  }
  note(text) {
    if (text) { this.value.events.push({type:'text',text,at:new Date().toISOString()}); this.persist(); }
    this.emit({event:'agent_delta_reset',loopId:this.loopId});
  }
  async finish(status) {
    this.value.status = status; this.value.endedAt = new Date().toISOString();
    this.value.durationMs = Date.now() - Date.parse(this.value.startedAt);
    for (const item of this.value.events) if (item.status === 'running') item.status = 'interrupted';
    this.persist(); await this.queue;
  }
}
