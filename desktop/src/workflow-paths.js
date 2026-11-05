import { displayPath } from './path-display.js';

export function workflowPaths(loop, selectedRun) {
  const candidates = (loop.runs || []).filter(run => run.revision === loop.revision);
  const run = candidates.find(run => run.id === selectedRun) || candidates.at(-1);
  const bundle = run?.directory || `${loop.workdir}/runs/{runId}`;
  const workspace = `${bundle}/workspace`;
  const absolute = value => value.startsWith('/') || value.startsWith('~') || value.startsWith('～') ? value : `${workspace}/${value.replace(/^\.\//, '')}`;
  const resolve = (value, file = false) => {
    if (typeof value !== 'string') return JSON.stringify(value);
    if (Object.hasOwn(loop.definition.scripts || {}, value)) return `${bundle}/${value}`;
    const text = value.replace(/\$\{([^}]+)\}/g, (original, ref) => {
      const [kind, id, , outputId] = ref.split('.');
      if (kind === 'workspace') return workspace;
      if (kind === 'bundle') return bundle;
      if (kind === 'resources') return loop.config.resources?.find(item => item.id === id)?.path || original;
      if (kind === 'inputs') {
        const input = loop.definition.inputs?.find(item => item.id === id);
        return input?.kind === 'file' ? (run?.inputs?.[id] || loop.config.inputDefaults?.[id] || original) : `本次输入 · ${input?.label || id}`;
      }
      if (kind === 'steps') {
        const step = loop.definition.steps.find(item => item.id === id);
        const output = step?.outputs?.find(item => item.id === outputId);
        if (output?.kind !== 'value' && output?.path) return resolve(output.path, true);
        return `${step?.label || id} · ${output?.label || outputId}`;
      }
      return { failure: '上次失败信息', checks: '实际检查记录' }[ref] || original;
    });
    return file ? absolute(text) : text;
  };
  return { run, workspace, bundle, resolve };
}

export function shortPathText(text, home) {
  if (!home) return text;
  const root = home.replace(/\/+$/, '');
  // Handle paths embedded in instructions/commands as well as standalone paths.
  return text === root ? displayPath(text, home) : text.split(root + '/').join('～/');
}
