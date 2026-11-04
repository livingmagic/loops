// Provider context gets the precise parse location; durable audit keeps the
// complete model arguments and error. Never silently repair executable input.
export function localizeArgumentError(message, messages) {
  if (message.role !== 'toolResult' || !message.isError) return message;
  const call = messages.flatMap(item => item.role === 'assistant' ? item.content || [] : [])
    .find(part => part.type === 'toolCall' && part.id === message.toolCallId);
  const error = call?.argumentsError;
  if (typeof error !== 'string') return message;
  const separator = '\n\nReceived arguments:\n';
  const split = error.indexOf(separator);
  if (split < 0) return message;
  const reason = error.slice(0, split), raw = error.slice(split + separator.length);
  const match = /position (\d+)/.exec(reason);
  if (!match) return message;
  const at = Number(match[1]);
  if (at > raw.length) return message;
  const context = raw.slice(Math.max(0, at - 180), at) + ' <<<JSON 出错位置>>> ' + raw.slice(at, at + 180);
  return { ...message, content: [{ type: 'text', text: `工具 ${message.toolName} 的参数 JSON 无法解析：${reason}\n出错位置附近：\n${context}\n请检查此处对象和数组的闭合关系。` }] };
}
