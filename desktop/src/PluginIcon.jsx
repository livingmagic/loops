const brandIcons = { codex: 'codex.png', mimo: 'mimo.ico', feishu: 'feishu-official.png', 'feishu-project': 'feishu-project.png', wecom: 'wecom.png', tapd: 'tapd.png', 'tencent-docs': 'tencent-docs.ico', github: 'github.svg' };
// Normalize the visible mark to ~90% of the slot, accounting for source padding.
// Shared by cards, the slash picker, editable mentions and saved messages.
const brandMetrics = {
  codex: { scale: 1.5 }, mimo: { scale: .9 }, feishu: { scale: 1, x: -1, y: 3 },
  'feishu-project': { scale: .9 }, wecom: { scale: .9, y: 1 }, tapd: { scale: .9 },
  'tencent-docs': { scale: .93, x: 1.5 }, github: { scale: .9 },
};

// A linked pair inside an open ring: plugins extend the Loops workspace.
export function PluginsIcon({ size = 20 }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.65" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M14.8 20.7a9 9 0 1 1 5.9-6.3c-.8 2.8-4.2 3.8-6.3 1.7"/><path d="m9.1 8.1-2.5 2.5a3 3 0 0 0 4.2 4.2l2.5-2.5m-2.6-.6 2.5-2.5a3 3 0 0 1 4.2 4.2l-2.5 2.5M9 6.8l2.2 2.3m3.7 5.8 2.3 2.3"/></svg>;
}

export function PluginIcon({ plugin, size = 22 }) {
  const provider = plugin.provider || plugin.id;
  const style = { width: size, height: size, fontSize: Math.max(9, size * .47) };
  const asset = brandIcons[provider];
  if (asset) {
    const { scale, x = 0, y = 0 } = brandMetrics[provider];
    return <span className="plugin-identity-icon plugin-brand-frame" style={style} aria-hidden="true"><img style={{ width: `${scale * 100}%`, height: `${scale * 100}%`, transform: `translate(${x}%, ${y}%)` }} src={`./assets/connectors/${asset}`} alt="" /></span>;
  }
  // Custom plugins receive a stable identity mark, distinct even for equal names.
  const key = `${plugin.type}:${plugin.id}`;
  const hash = [...key].reduce((value, char) => (value * 31 + char.codePointAt(0)) >>> 0, 0);
  const label = [...(plugin.name || plugin.id || '?')][0];
  return <span className="plugin-identity-icon plugin-monogram" style={{ ...style, color: `hsl(${hash % 360} 55% 37%)`, background: `hsl(${hash % 360} 65% 93%)` }} aria-hidden="true">{label}<i>{plugin.type === 'cli' ? '›' : plugin.type === 'mcp' ? '·' : '*'}</i></span>;
}
