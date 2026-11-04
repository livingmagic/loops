export function AgentPicker({ clients, value, onChange, disabled, inherit, label = '执行 Agent' }) {
  const entries = clients || [];
  const known = entries.some(client => client.id === value);
  return <label className="agent-picker"><span>{label}</span><select aria-label={label} value={value || ''} disabled={disabled || !clients} onChange={event => onChange(event.target.value)}>
    {inherit && <option value="">跟随流程 · {inherit}</option>}
    {!inherit && !value && <option value="">{clients ? '暂无可用 Agent' : '正在检测…'}</option>}
    {value && !known && <option value={value}>{value} · {clients ? '不可用' : '检测中'}</option>}
    {entries.map(client => <option key={client.id} value={client.id} disabled={!client.ready} title={client.detail}>{client.name}{client.ready ? '' : client.installed ? ' · 需要检查' : ' · 未安装'}</option>)}
  </select></label>;
}
