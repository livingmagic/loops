export function finalOptions(catalog, nodes) {
  return nodes.flatMap(node => {
    const loop = catalog.find(l => l.id === node.loopId);
    const version = loop?.versions.find(v => v.revision === node.revision);
    return (version?.outputs || []).map(o => ({ value: `${node.id}.${o.key}`, label: `${loop.title} · ${o.label}` }));
  });
}
export function sourceTemplate(kind, field = 'source', inputKind = 'file') {
  const emit = inputKind === 'file' ? `path = Path(ctx.config['path'])\n    ctx.emit(hashlib.sha256(path.read_bytes()).hexdigest(), {${JSON.stringify(field)}: ctx.file(path)})` : `ctx.emit(hashlib.sha256(ctx.config['text'].encode()).hexdigest(), {${JSON.stringify(field)}: ctx.text(ctx.config['text'])})`;
  const sample = `from pathlib import Path\nimport hashlib\n\ndef sample(ctx):\n    ${emit}\n`;
  if (kind === 'interval') return { lifecycle: 'service', config: { path: '', text: '', interval: 3600 }, script: `${sample}\ndef run(ctx):\n    import time\n    interval = max(1, float(ctx.config['interval']))\n    while True:\n        ${inputKind === 'file' ? `path = Path(ctx.config['path'])\n        inputs = {${JSON.stringify(field)}: ctx.file(path)}` : `inputs = {${JSON.stringify(field)}: ctx.text(ctx.config['text'])}`}\n        ctx.emit('tick:' + str(int(time.time() // interval)), inputs, checkpoint={'at': time.time()})\n        time.sleep(interval)\n` };
  if (kind === 'webhook') return { lifecycle: 'service', config: { port: 8788, path: '', text: '' }, script: `${sample}\ndef run(ctx):\n    import json\n    from http.server import BaseHTTPRequestHandler, HTTPServer\n    class Handler(BaseHTTPRequestHandler):\n        def do_POST(self):\n            length = int(self.headers.get('Content-Length', 0))\n            if not 0 < length <= 1000000:\n                self.send_error(413); return\n            raw = self.rfile.read(length)\n            key = self.headers.get('X-Event-ID') or hashlib.sha256(raw).hexdigest()\n            try:\n                ${inputKind === 'file' ? `path = Path(ctx.workspace) / (hashlib.sha256(key.encode()).hexdigest() + '.json')\n                path.write_bytes(raw)\n                inputs = {${JSON.stringify(field)}: ctx.file(path)}` : `inputs = {${JSON.stringify(field)}: ctx.text(raw.decode('utf-8'))}`}\n                result = ctx.emit(key, inputs)\n                self.send_response(202)\n                self.end_headers()\n                self.wfile.write(json.dumps(result).encode())\n            except Exception:\n                self.send_error(503, 'Input not accepted')\n        def log_message(self, *args): pass\n    from socketserver import TCPServer\n    class LocalServer(HTTPServer):\n        def server_bind(self):\n            TCPServer.server_bind(self)\n            self.server_name = 'localhost'\n            self.server_port = self.server_address[1]\n    server = LocalServer(('127.0.0.1', int(ctx.config['port'])), Handler)\n    ctx.log('Webhook listening on 127.0.0.1:' + str(ctx.config['port']))\n    server.serve_forever()\n` };
  if (kind === 'watch') return { lifecycle: 'service', config: { directory: '', path: '', text: '', interval: 2 }, script: `${sample}\ndef run(ctx):\n    import time\n    while True:\n        for path in sorted(Path(ctx.config['directory']).glob('*')):\n            if path.is_file() and path.stat().st_size <= 10000000:\n                key = str(path.resolve()) + ':' + hashlib.sha256(path.read_bytes()).hexdigest()\n                ctx.emit(key, {${JSON.stringify(field)}: ${inputKind === 'file' ? 'ctx.file(path)' : "ctx.text(path.read_text())"}})\n        time.sleep(max(1, float(ctx.config.get('interval', 2))))\n` };
  return { lifecycle: 'once', config: inputKind === 'file' ? { path: '' } : { text: '' }, script: `${sample}\ndef run(ctx):\n    sample(ctx)\n` };
}
export const outputScript = `from pathlib import Path\nimport hashlib\nimport os\n\ndef run(ctx):\n    source = Path(ctx.inputs['report'])\n    directory = Path(ctx.config['directory']).expanduser()\n    if not ctx.config['directory'].strip():\n        raise ValueError('请配置输出目录')\n    directory.mkdir(parents=True, exist_ok=True)\n    target = directory / (hashlib.sha256(ctx.delivery_key.encode()).hexdigest()[:16] + source.suffix)\n    temporary = target.with_suffix(target.suffix + '.tmp')\n    temporary.write_bytes(source.read_bytes())\n    os.replace(temporary, target)\n    ctx.receipt(target=str(target.resolve()), sha256=hashlib.sha256(target.read_bytes()).hexdigest())\n`;
export function addLoop(catalog, nodes, loopId) {
  const loop = catalog.find(l => l.id === loopId) || catalog[0];
  if (!loop) return null;
  const version = loop.versions.at(-1);
  const previous = finalOptions(catalog, nodes);
  return { id: `loop_${crypto.randomUUID().replaceAll('-', '').slice(0, 10)}`, loopId: loop.id, revision: version.revision,
    bindings: Object.fromEntries(version.inputs.map(i => [i.id, i.kind === 'file' && previous.length ? previous[0].value : `input.${i.id}`])) };
}
export function newSpec(catalog, loopId) {
  const node = addLoop(catalog, [], loopId);
  const first = node && catalog.find(l => l.id === node.loopId).versions.at(-1).inputs[0];
  return { title: '', source: sourceTemplate('once', first?.id, first?.kind), nodes: node ? [node] : [], outputs: [], policy: { queueLimit: 100, scriptTimeout: 180, sourceRestarts: 3 } };
}
