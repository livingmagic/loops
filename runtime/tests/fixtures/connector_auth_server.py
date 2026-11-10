"""Explicit local MCP authentication fixture for tests and UI acceptance only."""
import argparse
import json
import threading
from urllib.parse import parse_qs
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class AuthServer:
    def __init__(self, mode='token', port=0):
        self.mode, self.token, self.requests = mode, 'fixture-static-token', []
        self.auth_methods = ['none']
        self.authorization_path = '/authorize'
        self.scopes = []
        self.tool_calls = []
        self.tool_result = {'content': [{'type': 'text', 'text': 'fixture response'}], 'isError': False}
        self.tools = [{'name': 'lookup_notes', 'description': 'Search local notes', 'inputSchema': {'type': 'object'}, 'annotations': {'readOnlyHint': True}}]
        owner = self
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass
            def reply(self, status, value=None, challenge=False):
                body = json.dumps(value).encode() if value is not None else b''
                self.send_response(status)
                self.send_header('Content-Length', str(len(body)))
                if body: self.send_header('Content-Type', 'application/json')
                if challenge: self.send_header('WWW-Authenticate', 'Bearer realm="local-fixture"')
                self.end_headers(); self.wfile.write(body)
            def do_GET(self):
                owner.requests.append(('GET', self.path))
                if owner.mode in {'registered', 'outage'}:
                    if self.path.startswith('/.well-known/oauth-protected-resource'):
                        return self.reply(200, {'resource': owner.url + '/mcp', 'authorization_servers': [owner.url], 'scopes_supported': owner.scopes})
                    if self.path.startswith('/.well-known/oauth-authorization-server'):
                        return self.reply(200, {'issuer': owner.url, 'authorization_endpoint': owner.url + owner.authorization_path,
                            'token_endpoint': owner.url + '/token', 'response_types_supported': ['code'],
                            'code_challenge_methods_supported': ['S256'], 'token_endpoint_auth_methods_supported': owner.auth_methods})
                self.reply(404)
            def do_DELETE(self): self.reply(200)
            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                owner.requests.append(('POST', self.path))
                if self.path == '/register':
                    return self.reply(503 if owner.mode == 'outage' else 404)
                if self.path == '/token' and owner.mode == 'registered':
                    if owner.auth_methods == ['client_secret_post'] and parse_qs(raw.decode()).get('client_secret') != ['fixture-client-secret']:
                        return self.reply(401, {'error': 'invalid_client'})
                    return self.reply(200, {'access_token': 'fixture-oauth-token', 'refresh_token': 'fixture-refresh-token',
                                           'token_type': 'Bearer', 'expires_in': 3600})
                expected = owner.token if owner.mode == 'token' else 'fixture-oauth-token'
                if owner.mode != 'open' and self.headers.get('Authorization') != 'Bearer ' + expected:
                    return self.reply(401, challenge=True)
                message = json.loads(raw)
                if 'id' not in message: return self.reply(202)
                if message['method'] == 'initialize':
                    result = {'protocolVersion': '2025-11-25', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'local-auth-fixture', 'version': '1'}}
                elif message['method'] == 'tools/list':
                    result = {'tools': owner.tools}
                elif message['method'] == 'tools/call':
                    owner.tool_calls.append(message['params']['name'])
                    result = owner.tool_result
                else: result = {}
                self.reply(200, {'jsonrpc': '2.0', 'id': message['id'], 'result': result})
        self.server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
        self.server.daemon_threads = True
        self.url = f'http://127.0.0.1:{self.server.server_port}'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--mode', choices=['token', 'open', 'registered', 'outage'], default='token'); parser.add_argument('--port', type=int, default=0)
    args = parser.parse_args(); server = AuthServer(args.mode, args.port)
    print(server.url + '/mcp', flush=True)
    try: threading.Event().wait()
    finally: server.close()
