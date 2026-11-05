// Local package verification: real Python, hand-written workflow, no model calls.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

const desktop = process.cwd();
const app = path.resolve(process.argv[2] || 'out/Loops-darwin-arm64/Loops.app');
const resources = path.join(app, 'Contents/Resources');
const application = path.join(resources, 'app');
assert.equal(JSON.parse(await fs.readFile(path.join(application, 'package.json'), 'utf8')).name, 'Loops');
const runtime = path.join(resources, 'runtime');
const evidence = path.join(desktop, 'qa/artifacts/open-source');
await fs.mkdir(evidence, { recursive: true });
const dataDirectory = await fs.mkdtemp(path.join(evidence, 'package-smoke-'));
const configDirectory = path.join(dataDirectory, 'config');
await fs.mkdir(configDirectory);
for (const name of Object.keys(process.env)) if (name.startsWith('DEEPSEEK_')) delete process.env[name];
delete process.env.LOOPS_PYTHON;
const python = path.join(runtime, 'python/bin/python3');
const distributions = JSON.parse(execFileSync(python, ['-c', 'import json,importlib.metadata as m; print(json.dumps({d.metadata["Name"].lower().replace("_","-"):d.version for d in m.distributions()}))'], { encoding: 'utf8' }));
const expected = Object.fromEntries((await fs.readFile('../runtime/requirements.lock', 'utf8')).trim().split('\n').map((line) => { const [name, version] = line.split('=='); return [name.toLowerCase().replaceAll('_', '-'), version]; }));
assert.deepEqual(distributions, expected, 'The package must contain only the locked Python distributions.');
for (const file of ['LICENSE', 'NOTICE', 'npm-notices.txt', 'electron-LICENSE', 'LICENSES.chromium.html', 'codex-NOTICE.txt']) {
  assert.ok((await fs.stat(path.join(resources, 'legal', file))).size > 0);
}
assert.ok((await fs.stat(path.join(resources, 'legal/python-notices.txt'))).size > 0);
for (const file of ['.env.local', 'connections.local.json', 'qa', 'design', 'scripts', 'node_modules/@electron/packager']) {
  assert.equal(await fs.stat(path.join(application, file)).then(() => true, () => false), false, `Unexpected bundled file: ${file}`);
}
const { Host } = await import(pathToFileURL(path.join(application, 'host/service.mjs')));
const { clientDataPaths } = await import(pathToFileURL(path.join(application, 'host/data-paths.mjs')));
assert.deepEqual(clientDataPaths({ isPackaged: true, appData: evidence, root: application, env: {} }), {
  dataDirectory: path.join(evidence, 'Loops'), workspaceRoot: path.join(evidence, 'Loops/workspaces'),
});
const options = { dataDirectory, configDirectory, runtimeDirectory: runtime };
let host = new Host(options);
try {
  assert.deepEqual(await host.action('list'), []);
  const loop = await host.action('new');
  assert.equal(loop.workdir, path.join(await fs.realpath(dataDirectory), 'workspaces', loop.id));
  const definition = {
    schemaVersion: 2, title: '本机打包验证', goal: '将输入转成大写', completion: '输出文件存在',
    inputs: [{ id: 'text', label: '文本', kind: 'text', required: true }],
    finalOutputs: ['${steps.convert.outputs.report}'],
    scripts: { 'scripts/convert.py': "import json,os\nfrom pathlib import Path\nc=json.loads(os.environ['LOOPS_CONTEXT'])\nPath(c['outputs']['report']).write_text(c['inputs']['text'].upper())\n" },
    steps: [{ id: 'convert', label: '转换文本', instructions: '转成大写', inputs: { text: '${inputs.text}' },
      execution: { tool: 'python', script: 'scripts/convert.py' }, outputs: [{ id: 'report', label: '结果', path: 'result.txt' }], checks: [], onFailure: { action: 'stop' } }],
  };
  await host.engine.call('definition', { loopId: loop.id, definition });
  let onEvent, timer;
  const finished = new Promise((resolve, reject) => {
    timer = setTimeout(() => reject(new Error('Package smoke timed out.')), 30000);
    onEvent = (event) => {
      const run = event.loops?.find((item) => item.id === loop.id)?.runs.at(-1);
      if (run && ['success', 'failed', 'stopped'].includes(run.status)) resolve(run);
    };
    host.on('event', onEvent);
  });
  // Attach rejection handling before start, including when startup itself fails.
  finished.catch(() => {});
  let run;
  try {
    await host.action('start', { loopId: loop.id, inputs: { text: 'hello loops' } });
    run = await finished;
  } finally { clearTimeout(timer); host.off('event', onEvent); }
  assert.equal(run.status, 'success', run.reason);
  assert.equal(await fs.readFile(run.artifacts[0].path, 'utf8'), 'HELLO LOOPS');
  const saved = await host.action('save', { loopId: loop.id });
  await host.close();
  host = new Host(options);
  const restored = (await host.action('list')).find((item) => item.id === loop.id);
  assert.equal(restored.savedRevision, saved.savedRevision);
  assert.deepEqual(restored.runs.find((item) => item.id === run.id), run);
  const report = { passed: true, app, dataDirectory, configDirectory, pythonDistributions: distributions,
    realPython: true, handWrittenWorkflow: true, modelCalls: false, savedAndRestored: true,
    binaryDistributionReady: false, licenseReview: 'See THIRD_PARTY.md', runId: run.id };
  await fs.writeFile(path.join(evidence, 'package-smoke.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report));
} finally { await host.close(); }
