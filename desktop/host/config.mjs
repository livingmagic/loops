import fs from 'node:fs';
import path from 'node:path';

export function readConfig(directory, fallbackDirectory) {
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
  const key = process.env.DEEPSEEK_API_KEY || values.DEEPSEEK_API_KEY || '';
  return { key, model: process.env.DEEPSEEK_MODEL || values.DEEPSEEK_MODEL || 'deepseek-flash', configPath: filename };
}
export function publicConfig(config) { return { provider: 'DeepSeek', model: config.model, configured: Boolean(config.key.trim()), configPath: config.configPath }; }
