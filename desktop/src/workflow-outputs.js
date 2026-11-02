export function workflowOutputs(definition) {
  const declared = new Map(definition.steps.flatMap((step) => (step.outputs || []).map((output) => ['${steps.' + step.id + '.outputs.' + output.id + '}', output])));
  return definition.finalOutputs.map((ref) => declared.get(ref)).filter(Boolean);
}
