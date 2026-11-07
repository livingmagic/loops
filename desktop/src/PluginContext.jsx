import { PluginIcon } from './PluginIcon.jsx';
import { messagePluginParts, pluginKey } from './plugin-selection.mjs';
import { createContext, useEffect, useState } from 'react';
import { createPluginStore } from './plugin-store.mjs';
import { request, subscribe } from './api.js';

export const PluginContext = createContext({ plugins: [], selections: {}, setSelection: () => {}, refresh: async () => {}, loading: false, error: '' });

export function PluginProvider({ children, warmup }) {
  const [store] = useState(() => createPluginStore(request));
  const [snapshot, setSnapshot] = useState(store.getSnapshot);
  const [selections, setSelections] = useState({});
  useEffect(() => {
    const offState = store.subscribe(setSnapshot);
    const offEvents = subscribe(store.handleEvent);
    return () => { offState(); offEvents(); };
  }, [store]);
  useEffect(() => {
    if (!warmup) return;
    const idle = window.requestIdleCallback(() => {
      void store.ensureLoaded();
    }, { timeout: 1000 });
    return () => window.cancelIdleCallback(idle);
  }, [store, warmup]);
  const setSelection = (key, items) => setSelections(old => ({ ...old, [key]: items }));
  return <PluginContext.Provider value={{ ...snapshot, selections, setSelection, ensureLoaded: store.ensureLoaded, refresh: store.refresh, refreshConnectors: store.refreshConnectors, refreshAgents: store.refreshAgents, setAgents: store.setAgents }}>{children}</PluginContext.Provider>;
}

export function MessagePlugins({ plugins }) {
  if (!plugins?.length) return null;
  return <div className="message-plugins" aria-label="选用的插件">{plugins.map(item => <span key={`${item.type}:${item.id}`}>/{item.name}</span>)}</div>;
}

export function PluginMessage({ text, plugins = [] }) {
  const { parts, remaining } = messagePluginParts(text, plugins);
  return <><MessagePlugins plugins={remaining} /><p className="message-text">{parts.map((part,index) => typeof part === 'string' ? part : <span className="inline-plugin" key={`${pluginKey(part)}:${index}`} title={part.name}><PluginIcon plugin={part} size={17} />{part.name}</span>)}</p></>;
}
