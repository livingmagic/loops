export function workflowOutputs(definition) {
  const declared = new Map(definition.steps.flatMap((step) => (step.outputs || []).map((output) => ['${steps.' + step.id + '.outputs.' + output.id + '}', output])));
  return definition.finalOutputs.map((ref) => declared.get(ref)).filter(Boolean);
}

// Contract types come from the selected definition, never a previous run.
export function outputType(output) {
  if (output.kind === 'value') return ({ string: '文本', object: 'JSON 对象', array: 'JSON 数组', number: '数字', boolean: '布尔值', null: '空值' })[output.dataType] || 'JSON';
  if (output.kind === 'json') return 'JSON 文件';
  const extension = /\.([a-z0-9]{1,10})$/i.exec(output.path || '')?.[1];
  return extension ? `${extension.toUpperCase()} 文件` : '文件';
}

export function inputType(definition, reference) {
  const input = /^\$\{inputs\.([^}]+)\}$/.exec(reference);
  if (input) {
    const item = definition.inputs.find(item => item.id === input[1]);
    return item ? (item.kind === 'file' ? '文件' : '文本') : '';
  }
  const upstream = /^\$\{steps\.([^.]+)\.outputs\.([^}]+)\}$/.exec(reference);
  if (upstream) {
    const output = definition.steps.find(step => step.id === upstream[1])?.outputs?.find(item => item.id === upstream[2]);
    return output ? outputType(output) : '';
  }
  return reference.includes('${') ? '' : '文本';
}

export function inputLabel(definition, reference) {
  const input = /^\$\{inputs\.([^}]+)\}$/.exec(reference);
  if (input) return definition.inputs.find(item => item.id === input[1])?.label || input[1];
  const upstream = /^\$\{steps\.([^.]+)\.outputs\.([^}]+)\}$/.exec(reference);
  if (upstream) {
    const step = definition.steps.find(step => step.id === upstream[1]);
    const output = step?.outputs?.find(item => item.id === upstream[2]);
    return `${step?.label || upstream[1]} · ${output?.label || upstream[2]}`;
  }
  return undefined;
}
