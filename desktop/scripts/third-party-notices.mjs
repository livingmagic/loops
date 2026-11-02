import fs from 'node:fs/promises';
import path from 'node:path';

// Some upstream npm tarballs omit their repository's license file.
// The exact upstream sources of these supplemental texts are in licenses/sources.json.
const supplemental = {
  '@earendil-works/pi-agent-core': 'pi-LICENSE.txt',
  '@earendil-works/pi-ai': 'pi-LICENSE.txt',
  '@earendil-works/pi-telemetry': 'pi-LICENSE.txt',
  'standardwebhooks': 'standardwebhooks-LICENSE.txt',
};

export async function writeNpmNotices(root, output) {
  const lock = JSON.parse(await fs.readFile(path.join(root, 'package-lock.json'), 'utf8'));
  const sections = [];
  const unresolved = [];
  for (const [location, info] of Object.entries(lock.packages).sort(([a], [b]) => a.localeCompare(b))) {
    if (!location || info.dev) continue;
    const directory = path.join(root, location);
    let files;
    try { files = await fs.readdir(directory, { withFileTypes: true }); }
    catch (error) { if (error.code === 'ENOENT' && info.optional) continue; throw error; }
    const pkg = JSON.parse(await fs.readFile(path.join(directory, 'package.json'), 'utf8'));
    if (pkg.version !== info.version) throw new Error(`依赖与锁文件不一致：${location}`);
    const licenses = files.filter((file) => file.isFile() && /^(licen[sc]e|copying|notice)([.-]|$)/i.test(file.name));
    const texts = await Promise.all(licenses.map((file) => fs.readFile(path.join(directory, file.name), 'utf8')));
    if (!texts.length && supplemental[pkg.name]) texts.push(await fs.readFile(path.join(root, 'licenses', supplemental[pkg.name]), 'utf8'));
    if (!texts.length && pkg.name.startsWith('@aws-sdk/')) texts.push(await fs.readFile(path.join(root, 'node_modules/@aws-sdk/core/LICENSE'), 'utf8'));
    if (!texts.length && pkg.name === 'data-uri-to-buffer') {
      const readme = await fs.readFile(path.join(directory, 'README.md'), 'utf8');
      const start = readme.indexOf('Copyright (c)');
      if (start !== -1) texts.push(readme.slice(start));
    }
    if (!texts.length && pkg.name === 'proxy-agent-negotiate' && pkg.version === '1.1.0') {
      const issue = `${pkg.name}@${pkg.version}: npm declares MIT but the upstream license text is missing. Attribution review is required before binary redistribution.`;
      unresolved.push(issue);
      texts.push(`${issue}\nAuthor metadata: ${typeof pkg.author === 'string' ? pkg.author : JSON.stringify(pkg.author)}\nRepository: ${pkg.repository?.url || ''}`);
    }
    if (!texts.length) throw new Error(`缺少第三方许可文本：${pkg.name}@${pkg.version}`);
    sections.push(`${pkg.name}@${pkg.version}\n${pkg.license || info.license || 'See license below'}\n${texts.join('\n\n')}`);
  }
  await fs.writeFile(output, sections.join('\n\n' + '='.repeat(72) + '\n\n') + '\n');
  if (unresolved.length) {
    await fs.writeFile(path.join(path.dirname(output), 'UNRESOLVED-NPM-LICENSES.txt'), unresolved.join('\n') + '\n');
    console.warn('仅用于本机验证：第三方署名核对未完成，详见 THIRD_PARTY.md。');
  }
  return sections.length;
}
