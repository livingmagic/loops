// One-time upgrade only. Runtime requests never read environment credentials.
import fs from 'node:fs';
import path from 'node:path';
import { DEFAULT_THINKING_LEVEL } from './config.mjs';

export function readLegacyModelConfig(directory, fallbackDirectory) {
  const primary = path.join(directory, '.env.local');
  const fallback = fallbackDirectory && path.join(fallbackDirectory, '.env.local');
  const filename = !fs.existsSync(primary) && fallback && fs.existsSync(fallback) ? fallback : primary;
  const values = {};
  if (fs.existsSync(filename)) {
    for (const line of fs.readFileSync(filename, 'utf8').split(/\r?\n/)) {
      const match = line.match(/^\s*([A-Z_]+)\s*=\s*(.*?)\s*$/);
      if (match) values[match[1]] = match[2].replace(/^(['"])(.*)\1$/, '$2');
    }
  }
  const key = values.DEEPSEEK_API_KEY || '';
  const level = values.DEEPSEEK_THINKING_LEVEL || DEFAULT_THINKING_LEVEL;
  if (!['off', 'low', 'medium', 'high', 'max'].includes(level)) throw new Error('DeepSeek 思考档位应为 off、low、medium、high 或 max。');
  return { key, model: values.DEEPSEEK_MODEL || 'deepseek-flash', thinkingLevel: level, configPath: filename };
}
