export const DEFAULT_THINKING_LEVEL = 'medium';
// DeepSeek documents medium as a compatibility alias for high. Resolve it
// explicitly: Pi's bundled catalog currently marks medium unsupported and
// would otherwise clamp it down to low instead of following the API contract.
export function thinkingLevel(config) {
  const level = config.thinkingLevel ?? DEFAULT_THINKING_LEVEL;
  return (!config.provider || config.provider === 'deepseek') && level === 'medium' ? 'high' : level;
}
