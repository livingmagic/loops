"""Shared connector broker. Public manifests never contain credentials."""
import asyncio
import base64
import copy
import hashlib
import json
import os
import re
import signal
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from .connector_presets import presets, preset, TOKEN_PAGES
from .connector_tools import ManagedTools, ToolInstallError, builtin
from .connector_results import connector_response, RESPONSE_CONTRACT
from .store import now

DESCRIPTION_VERSION = 1

class AuthorizationRequired(RuntimeError):
    pass


class CredentialRequired(AuthorizationRequired):
    def __init__(self, kind, message, *, secret_required=False, secret_supported=False):
        super().__init__(message)
        self.request = {'kind': kind, 'secretRequired': secret_required, 'secretSupported': secret_supported}


class ConnectorFailure(RuntimeError):
    pass


async def terminate(process):
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            await asyncio.wait_for(process.wait(), 2)
        except (ProcessLookupError, asyncio.TimeoutError):
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


class KeychainVault:
    """No plaintext fallback: an unavailable keychain is an actionable error."""
    def __init__(self, root):
        self.namespace = "Loops.connectors." + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:16]
        self.cache = {}

    def get(self, key):
        if key not in self.cache:
            import keyring
            try:
                value = keyring.get_password(self.namespace, key)
                self.cache[key] = json.loads(value) if value else {}
            except Exception as error:
                raise ConnectorFailure("无法读取系统钥匙串，请解锁钥匙串后重试连接。") from error
        return copy.deepcopy(self.cache[key])

    def set(self, key, value):
        import keyring
        try:
            if value:
                keyring.set_password(self.namespace, key, json.dumps(value))
            else:
                try:
                    keyring.delete_password(self.namespace, key)
                except keyring.errors.PasswordDeleteError:
                    pass
        except Exception as error:
            raise ConnectorFailure("无法保存到系统钥匙串；授权未保存，请解锁钥匙串后重试。") from error
        self.cache[key] = copy.deepcopy(value)

    def redact(self, value):
        text = str(value)
        def strings(item):
            if isinstance(item, dict):
                for v in item.values():
                    yield from strings(v)
            elif isinstance(item, list):
                for v in item:
                    yield from strings(v)
            elif isinstance(item, str) and len(item) >= 6:
                yield item
        private = []
        for data in self.cache.values():
            private.append(data.get('config', {}))
            private.append({k:v for k,v in data.get('tokens', {}).items() if k not in {'scope', 'token_type'}})
            private.append({k:data.get('client', {}).get(k) for k in ('client_secret', 'registration_access_token')})
            authorization = data.get('config', {}).get('headers', {}).get('Authorization', '')
            if isinstance(authorization, str) and authorization.partition(' ')[0].lower() in {'bearer', 'basic'}:
                private.append(authorization.partition(' ')[2])
        # Discovery URLs, issuer, scopes and public client IDs are metadata,
        # otherwise legitimate document URLs would be corrupted by redaction.
        for secret in sorted(set(strings(private)), key=len, reverse=True):
            text = text.replace(secret, "[已隐藏]")
        return text


def validate_manifest(manifest):
    value = copy.deepcopy(manifest)
    if not isinstance(value, dict) or not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}", str(value.get("id", ""))):
        raise ValueError("插件 ID 只能包含字母、数字、下划线或连字符，且以字母开头。")
    if not isinstance(value.get("name"), str) or not value["name"].strip():
        raise ValueError("请填写插件名称。")
    if value.get("type") not in {"mcp", "cli"}:
        raise ValueError("插件类型需要为 mcp 或 cli。")
    # Validate configuration structure, without mistaking schema field names
    # (e.g. a pagination "token") for actual credential values.
    public_fields = {"id", "name", "description", "type", "version", "provider", "identity", "command", "package", "auth", "operations", "transport", "url"}
    if set(value) - public_fields:
        raise ValueError("定义包含不支持的字段；凭证、请求头和环境变量请放在独立凭证配置中。")
    auth = value.setdefault("auth", {"mode": "none"})
    if not isinstance(auth, dict) or set(auth) - {"mode", "status", "login", "logout", "refresh", "scope", "issuer", "callbackPort"}:
        raise ValueError("授权定义只包含方式和命令；凭证请在独立凭证配置中填写。")
    if auth.get("mode") not in {"none", "token", "oauth", "cli", "auto"}:
        raise ValueError("不支持的授权方式。")
    if "callbackPort" in auth and (type(auth["callbackPort"]) is not int or not 1024 <= auth["callbackPort"] <= 65535):
        raise ValueError("OAuth 本机回调端口需要在 1024 到 65535 之间。")
    def command(args):
        if not isinstance(args, list) or not args or any(not isinstance(x, str) or "\x00" in x for x in args):
            raise ValueError("命令必须为非空的程序和参数数组，不经过 shell。")
    if value["type"] == "cli" or value.get("transport", "http") == "stdio":
        command(value.get("command"))
    if value["type"] == "mcp":
        if auth["mode"] in {"cli", "auto"} and not (preset(value) and value['provider'] == 'github'):
            raise ValueError("MCP 请使用 none、token 或 oauth 授权方式。")
        if value.get("transport", "http") not in {"http", "sse", "stdio"}:
            raise ValueError("MCP 支持 http、sse 和 stdio。")
        if value.get("transport", "http") != "stdio":
            u = urlparse(value.get("url", ""))
            if u.username or u.password or u.query or u.fragment or not u.hostname or (u.scheme != "https" and not (u.scheme == "http" and u.hostname in {"localhost", "127.0.0.1", "::1"})):
                raise ValueError("MCP 地址使用 HTTPS（本机可用 HTTP），不能包含凭证、查询参数或片段。")
        if auth["mode"] != "none" and value.get("transport", "http") == "stdio":
            raise ValueError("本地 MCP 请使用持久凭证环境变量；OAuth 适用于远程 MCP。")
    else:
        if value.get("provider") == "feishu" and value.get("identity", "user") not in {"user", "bot"}:
            raise ValueError("飞书身份需要选择 user 或 bot。")
        if auth["mode"] not in {"cli", "none", "auto"} and not (preset(value) and value['provider'] == 'tapd' and auth['mode'] == 'token'):
            raise ValueError("CLI 由其自身管理长期授权，敏感环境变量使用独立凭证配置。")
        for key in ("status", "login", "logout", "refresh"):
            if key in auth:
                command(auth[key])
        if auth["mode"] == "cli" and not all(key in auth for key in ("status", "login")):
            raise ValueError("CLI 授权需要 status 和 login 命令。")
        seen = set()
        if not isinstance(value.get("operations", []), list):
            raise ValueError("CLI operations 需要操作列表。")
        for operation in value.get("operations", []):
            if not isinstance(operation, dict) or set(operation) - {"name", "description", "effect", "inputSchema", "args", "format", "identity"}:
                raise ValueError("CLI 操作包含不支持的字段。")
            name = operation.get("name", "")
            if not re.fullmatch(r"[a-zA-Z][\w.-]{0,100}", name) or name in seen:
                raise ValueError("操作名称需要有效且唯一。")
            seen.add(name)
            command(operation.get("args"))
            if operation.get("effect") not in {"read", "write"}:
                raise ValueError("每项 CLI 操作需要声明 read 或 write。")
            from jsonschema import Draft202012Validator
            Draft202012Validator.check_schema(operation.get("inputSchema", {"type": "object"}))
    return value


class Connectors:
    def __init__(self, root, notify, vault=None):
        self.root, self.notify = Path(root), notify
        self.root.mkdir(parents=True, exist_ok=True)
        self.file = self.root / "connectors.json"
        self.items = json.loads(self.file.read_text()) if self.file.exists() else {}
        self.vault = vault or KeychainVault(self.root)
        self.locks, self.jobs, self.auth_views = {}, {}, {}
        self.maintenance_task = None
        self.tools = ManagedTools(self.root)
        self.recovering, self.retries = set(), {}
        self.discoverers = {}
        self.private_values = set()
        migrated = False
        for entry in self.items.values():
            manifest = entry['manifest']
            if (manifest.get('id') == manifest.get('provider') == 'tapd'
                and manifest.get('type') == 'cli' and manifest.get('command') == ['tapd-cli']
                and manifest.get('package') == 'tapd-cli' and manifest.get('auth') == {'mode': 'token'}
                and 'operations' not in manifest):
                entry.update(manifest=next(p for p in presets() if p['id'] == 'tapd'),
                    revision=entry.get('revision', 0) + 1, enabled=False, autoReconnect=False,
                    status='disconnected', message='点击连接，在浏览器中授权 TAPD。', tools=[],
                    authorizationInfo=None, credentialRequest=None)
                entry.pop('capabilities', None)
                migrated = True
            entry.setdefault("autoReconnect", entry.get("enabled", False) or entry.get("status") in {"connecting", "authorization_required", "error"})
            if entry.get("status") == "connecting":
                entry.update(status="disconnected", enabled=False, message="上次连接已中断，等待自动检查已有授权。")
        if migrated:
            self.persist()

    def persist(self):
        temporary = self.file.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.items, ensure_ascii=False, indent=2))
        temporary.chmod(0o600)
        temporary.replace(self.file)
        self.notify({"event": "connector_state"})

    def entry(self, key):
        if key not in self.items:
            raise ValueError("插件不存在，请先添加。")
        return self.items[key]

    def public(self, key):
        entry = self.entry(key)
        value = {**copy.deepcopy(entry), "builtin": bool(preset(entry["manifest"])), "authorization": self.auth_views.get(key)}
        if entry.get('credentialRequest') and preset(entry['manifest']) and key in TOKEN_PAGES:
            value['authorization'] = {'url': TOKEN_PAGES[key]}
        if value.get('generatedDescription'):
            context = self.description_context(key)
            if not context or value['generatedDescription']['sourceHash'] != context['sourceHash']:
                value.pop('generatedDescription', None)
        return value

    def description_context(self, key):
        entry = self.entry(key)
        if preset(entry['manifest']) or not entry.get('tools'):
            return None
        def short(value, limit):
            return value[:limit] if isinstance(value, str) else ''
        tools = []
        for tool in entry['tools']:
            schema = tool.get('inputSchema') or {}
            properties = schema.get('properties', {}) if isinstance(schema, dict) else {}
            tools.append({'name': short(tool.get('name'), 256), 'title': short(tool.get('title'), 256), 'description': short(tool.get('description'), 1200),
                'effect': tool.get('effect', 'unknown'), 'parameters': [
                    {'name': short(name, 128), 'description': short(field.get('description'), 300)}
                    for name, field in sorted(properties.items()) if isinstance(field, dict)]})
        tools.sort(key=lambda item: item['name'])
        source = {'type': entry['manifest']['type'], 'tools': tools}
        safe = json.loads(self.redact(json.dumps(source, ensure_ascii=False)))
        source_hash = hashlib.sha256(json.dumps({'descriptionVersion': DESCRIPTION_VERSION, **safe}, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return {'connectorId': key, 'revision': entry['revision'], 'sourceHash': source_hash, **safe}

    def description_contexts(self):
        result = []
        for key, entry in self.items.items():
            context = self.description_context(key)
            if context and entry.get('generatedDescription', {}).get('sourceHash') != context['sourceHash']:
                result.append(context)
        return result

    def set_description(self, params):
        key = params['connectorId']
        context = self.description_context(key)
        if not context or context['sourceHash'] != params.get('sourceHash') or context['revision'] != params.get('revision'):
            return False
        text = params.get('text')
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 60 or '\n' in text or '\r' in text:
            raise ValueError('插件简介需要不超过 60 字的一句话。')
        self.entry(key)['generatedDescription'] = {'text': self.redact(text.strip()), 'sourceHash': context['sourceHash'], 'generatedAt': now()}
        self.persist()
        return True

    def list(self):
        return [self.public(key) for key in self.items]

    def save(self, manifest, secrets=None):
        manifest = validate_manifest(manifest)
        key = manifest["id"]
        old = self.items.get(key)
        if self.locks.get(key) and self.locks[key].locked():
            raise ValueError("插件正在执行，请等本次调用结束后再修改定义或凭证。")
        if key in self.jobs and not self.jobs[key].done():
            raise ValueError("正在连接，请先取消连接再修改。")
        if secrets is not None:
            if not isinstance(secrets, dict) or set(secrets) - {"headers", "env", "token", "client_id", "client_secret"}:
                raise ValueError("凭证仅支持 headers、env、token、client_id 和 client_secret。")
            for group in ("headers", "env"):
                if group in secrets and (not isinstance(secrets[group], dict) or any(not isinstance(v, str) for v in secrets[group].values())):
                    raise ValueError("凭证请求头与环境变量需要字符串键值对象。")
            current = self.vault.get(key).get("config", {})
            merged = {**current, **secrets}
            for group in ("headers", "env"):
                if group in secrets:
                    merged[group] = {**current.get(group, {}), **secrets[group]}
            # Editing credentials must not retain tokens for a previous account.
            self.vault.set(key, {"config": merged})
        elif old and old["manifest"] != manifest and old["manifest"]["type"] == "mcp":
            # A changed endpoint/command must never inherit OAuth tokens.
            self.vault.set(key, {})
        changed = not old or old["manifest"] != manifest
        revision = (old.get("revision", 0) + int(changed)) if old else 1
        self.items[key] = {"manifest": manifest, "revision": revision, "enabled": False, "autoReconnect": False, "status": "disconnected", "message": "尚未检查连接", "tools": copy.deepcopy(old.get("tools", [])) if old and not changed else [], "updatedAt": now()}
        if old and not changed and old.get('generatedDescription'):
            self.items[key]['generatedDescription'] = copy.deepcopy(old['generatedDescription'])
        if old and not changed and old.get('mcpReviews'):
            self.items[key]['mcpReviews'] = copy.deepcopy(old['mcpReviews'])
        self.auth_views.pop(key, None)
        if changed:
            self.discoverers.pop(key, None)
        self.persist()
        return self.public(key)

    def state(self, key, status, message, **details):
        self.entry(key).update(status=status, message=message, **details, checkedAt=now())
        self.persist()

    def redact(self, value):
        text = self.vault.redact(value)
        for secret in sorted(self.private_values, key=len, reverse=True):
            text = text.replace(secret, '[已隐藏]')
        return text

    def mcp_auth_mode(self, key):
        if key == 'tapd' and preset(self.entry(key)['manifest']):
            return 'oauth'  # Legacy PAT credentials must not override browser authorization.
        mode = self.entry(key)['manifest'].get('auth', {}).get('mode', 'none')
        if mode != 'none' and self.vault.get(key).get('credentialMode') == 'token':
            return 'token'
        return mode

    def credential_request(self, key, error):
        if isinstance(error, CredentialRequired):
            return error.request
        if hasattr(error, 'exceptions'):
            for child in error.exceptions:
                request = self.credential_request(key, child)
                if request:
                    return request
        if (self.is_auth_error(error) and self.entry(key)['manifest']['type'] == 'mcp'
            and self.mcp_auth_mode(key) == 'token'):
            return {'kind': 'token'}
        return None

    async def provide_credentials(self, key, kind, values):
        entry = self.entry(key)
        request = entry.get('credentialRequest')
        if not request or request['kind'] != kind or (entry['manifest']['type'] != 'mcp' and not (preset(entry['manifest']) and key == 'tapd' and kind == 'token')):
            raise ValueError('当前连接没有请求这些信息，请先点击连接。')
        if self.locks.get(key) and self.locks[key].locked() or key in self.jobs and not self.jobs[key].done():
            raise ValueError('请等待当前连接结束后再继续。')
        allowed = {'token'} if kind == 'token' else {'client_id', *({'client_secret'} if request.get('secretSupported') else set())}
        if not isinstance(values, dict) or set(values) - allowed or any(not isinstance(v, str) or len(v) > 16000 for v in values.values()):
            raise ValueError('请填写服务请求的连接信息。')
        cleaned = {k: v.strip() for k, v in values.items() if v.strip()}
        required = {'token'} if kind == 'token' else {'client_id', *({'client_secret'} if request.get('secretRequired') else set())}
        if required - cleaned.keys():
            raise ValueError('请填写必需的连接信息。')
        data = self.vault.get(key)
        config = {**data.get('config', {}), **cleaned}
        for field in ('tokens', 'client', 'expiresAt'):
            data.pop(field, None)
        if kind == 'token':
            data['credentialMode'] = 'token'
        else:
            data.pop('credentialMode', None)
            config.pop('token', None)
            if 'client_secret' not in cleaned:
                config.pop('client_secret', None)
        data['config'] = config
        self.vault.set(key, data)
        entry['credentialRequest'] = None
        return await self.connect(key)

    def executable(self, command, key=None):
        provider = builtin(self.entry(key)["manifest"]) if key else None
        if provider:
            binary = self.tools.path(provider)
            if not binary.is_file():
                raise ConnectorFailure("连接工具尚未准备好，请点击连接自动完成准备。")
            return [str(binary), *command[1:]]
        binary = command[0]
        search = os.pathsep.join(filter(None, [str(self.root / "connector-tools/node_modules/.bin"), "/opt/homebrew/bin", "/usr/local/bin", os.environ.get("PATH")]))
        found = shutil.which(binary, path=search)
        if not found:
            raise ConnectorFailure(f"尚未安装 {Path(binary).name}，请先安装插件工具。")
        return [found, *command[1:]]

    def environment(self, key):
        env = {k: os.environ[k] for k in ("HOME", "PATH", "LANG", "LC_ALL", "TMPDIR", "SYSTEMROOT", "USER") if k in os.environ}
        env["PATH"] = os.pathsep.join([str(self.root / "connector-tools/node_modules/.bin"), "/opt/homebrew/bin", "/usr/local/bin", env.get("PATH", "")])
        manifest = self.entry(key)["manifest"]
        env.update(self.vault.get(key).get("config", {}).get("env", {}))
        if preset(manifest) and manifest['provider'] == 'tapd':
            token = self.vault.get(key).get('config', {}).get('token')
            if token:
                env['TAPD_TOKEN'] = token
            env.update(TAPD_API_ENDPOINT='https://api.tapd.cn', TAPD_CLI_LOG='silent')
        return env

    async def process(self, key, args, timeout=45):
        manifest = self.entry(key)["manifest"]
        work = self.root / "connector-work" / key
        work.mkdir(parents=True, exist_ok=True)
        process = await asyncio.create_subprocess_exec(*self.executable(manifest["command"], key), *args, env=self.environment(key), cwd=work,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, limit=2_000_000, start_new_session=True)
        async def bounded(stream):
            chunks, size = [], 0
            while chunk := await stream.read(65536):
                size += len(chunk)
                if size > 10_000_000:
                    raise ConnectorFailure("插件响应超过 10MB。")
                chunks.append(chunk)
            return b"".join(chunks)
        try:
            stdout, stderr, _ = await asyncio.wait_for(asyncio.gather(bounded(process.stdout), bounded(process.stderr), process.wait()), timeout)
            return process.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
        finally:
            await terminate(process)

    async def cli_auth(self, key):
        manifest = self.entry(key)['manifest']
        if manifest.get('auth', {}).get('mode') == 'auto':
            discovery = self.discovery(key)
            if not discovery:
                raise ConnectorFailure('CLI 未提供自动发现接口。')
            return await discovery.auth()
        return {**manifest.get('auth', {'mode': 'none'}), 'kind': {'feishu': 'lark', 'wecom': 'wecom', 'github': 'github', 'tapd': 'tapd'}.get(manifest.get('provider') if preset(manifest) else None, 'generic')}

    async def cli_check(self, key, allow_refresh=True):
        manifest = self.entry(key)["manifest"]
        auth = await self.cli_auth(key)
        if auth.get('kind') == 'tapd':
            if not self.vault.get(key).get('config', {}).get('token'):
                raise CredentialRequired('token', '请在 TAPD 个人设置获取个人访问令牌后继续。')
            code, output, diagnostic = await self.process(key, ['user', 'info'])
            # This no-argument identity endpoint returns 422 for an invalid token;
            # do not classify a business operation's generic 422 as an auth failure.
            if code and re.search(r'^API 错误 (?:401|422):', diagnostic, re.M):
                raise CredentialRequired('token', 'TAPD 个人访问令牌已失效，请更新。')
            if code:
                raise ConnectorFailure('TAPD 用户信息核验失败，请检查令牌权限和网络。')
            try:
                result = json.loads(output)
            except ValueError:
                raise ConnectorFailure('TAPD 用户信息响应无效，请检查网络后重试。')
            self.tapd_result(result, code)
            data = result.get('data', {})
            user = data.get('User', data) if isinstance(data, dict) else {}
            return {'mode': 'token', 'identity': 'user', 'renewable': False, 'account': user.get('nick') or user.get('name')}
        if auth.get('mode') == 'none':
            self.executable(manifest["command"], key)
            return {"identity": "none"}
        code, output, _ = await self.process(key, auth['status'])
        if auth.get('kind') == 'github':
            try:
                accounts = json.loads(output)['hosts'].get('github.com', [])
                account = next((item for item in accounts if item.get('active') is True), None)
            except (ValueError, KeyError, TypeError, AttributeError):
                raise ConnectorFailure('无法检查 GitHub 登录状态，请检查网络后重试。')
            if account and account.get('state') == 'timeout':
                raise ConnectorFailure('GitHub 授权核验超时，请检查网络后重试。')
            if code or not account or account.get('state') != 'success':
                raise AuthorizationRequired('GitHub 授权不可用，请重新连接。')
            return {'mode': 'cli', 'identity': 'user', 'account': account.get('login'), 'renewable': account.get('refreshable') is True,
                    'expiresAt': account.get('tokenExpiresAt'), 'refreshExpiresAt': account.get('refreshTokenExpiresAt')}
        if auth.get('kind') == 'lark':
            try:
                result = json.loads(output)
                identity = manifest.get("identity", "user")
                info = result.get("identities", {}).get(identity, {})
            except ValueError:
                raise ConnectorFailure("飞书登录状态响应无效。")
            if identity == "user" and info.get("status") == "needs_refresh" and allow_refresh:
                refresh_code, _, _ = await self.process(key, ["api", "GET", "/open-apis/authen/v1/user_info", "--as", "user", "--json"])
                if refresh_code:
                    raise ConnectorFailure("飞书令牌刷新请求未完成，请检查服务和网络。")
                return await self.cli_check(key, allow_refresh=False)
            if info.get("status") == "verify_failed":
                raise ConnectorFailure("飞书服务端授权核验失败，请检查网络与账户权限后重试。")
            if not info.get("available") or not info.get("verified"):
                raise AuthorizationRequired(f"飞书{ '用户' if identity == 'user' else '应用' }授权不可用，请重新连接；不会自动切换身份。")
            if identity == "user" and not info.get("refreshExpiresAt"):
                raise AuthorizationRequired("飞书仅签发了短期令牌，不能用于长期自动化；请开通 offline_access 后重新连接。")
            return {"identity": identity, "expiresAt": info.get("expiresAt"), "refreshExpiresAt": info.get("refreshExpiresAt"), "renewable": True, "account": info.get("userName") or info.get("appName")}
        if auth.get('kind') == 'wecom':
            if code or output.strip() != "authorized":
                raise AuthorizationRequired("企微尚未授权，请扫码连接并保存机器人长期凭据。")
            return {"identity": "bot", 'renewable': True}
        status = None
        if manifest.get('auth', {}).get('mode') == 'auto':
            try:
                status = json.loads(output)
                if not isinstance(status, dict) or type(status.get('authorized')) is not bool:
                    raise ValueError()
                if 'renewable' in status and type(status['renewable']) is not bool:
                    raise ValueError()
            except ValueError:
                raise ConnectorFailure('CLI auth status 需要返回包含 authorized 布尔值的 JSON，不能从文字猜测授权状态。')
            if status['authorized'] is not True:
                code = code or 1
        if code and allow_refresh and auth.get('refresh'):
            refresh_code, refresh_output, refresh_diagnostic = await self.process(key, auth['refresh'])
            if not refresh_code:
                return await self.cli_check(key, allow_refresh=False)
            if manifest.get('auth', {}).get('mode') == 'auto':
                try:
                    failure = json.loads(refresh_output if refresh_output.strip() else refresh_diagnostic)
                except ValueError:
                    failure = {}
                if isinstance(failure, dict):
                    detail = failure.get('error', {})
                    if (isinstance(detail, dict) and detail.get('type') in {'AuthError', 'authentication'}
                        or failure.get('authorized') is False and failure.get('renewable') is False):
                        raise AuthorizationRequired('CLI 长期授权已失效，请重新连接。')
                raise ConnectorFailure('CLI 授权续期暂未完成，请检查服务与网络；已有凭据保留，将自动重试。')
        if code:
            raise AuthorizationRequired("CLI 授权不可用，请重新连接。")
        return {'identity': 'cli', **({k: status[k] for k in ('renewable', 'expiresAt', 'refreshExpiresAt', 'account') if k in status} if status else {})}

    @staticmethod
    def tapd_result(result, code=0):
        if not isinstance(result, dict):
            raise ConnectorFailure('TAPD 返回了无效响应。')
        # TAPD's documented API envelope uses status=1 for success.
        if code or result.get('status') != 1:
            if result.get('status') == 401 or result.get('httpStatus') == 401:
                raise CredentialRequired('token', 'TAPD 个人访问令牌已失效，请更新。')
            raise ConnectorFailure('TAPD 请求未成功，请检查访问令牌、项目权限和网络。')

    @asynccontextmanager
    async def session(self, key, interactive=False):
        from mcp import StdioServerParameters
        from .mcp_session import ValidatingClientSession
        from mcp.client.stdio import stdio_client
        from mcp.client.streamable_http import streamable_http_client
        from mcp.client.sse import sse_client
        from contextlib import AsyncExitStack
        import httpx
        manifest = self.entry(key)["manifest"]
        transport = manifest.get("transport", "http")
        async with AsyncExitStack() as stack:
            if transport == "stdio":
                command = self.executable(manifest["command"])
                work = self.root / "connector-work" / key
                work.mkdir(parents=True, exist_ok=True)
                # stderr is intentionally not forwarded to application logs.
                sink = stack.enter_context(open(os.devnull, "w"))
                read, write = await stack.enter_async_context(stdio_client(StdioServerParameters(command=command[0], args=command[1:], env=self.environment(key), cwd=str(work)), errlog=sink))
            else:
                auth_mode = self.mcp_auth_mode(key)
                config = self.vault.get(key).get("config", {}) if auth_mode != "none" else {}
                headers = dict(config.get("headers", {}))
                if auth_mode == "token":
                    if not config.get("token") and not headers:
                        raise CredentialRequired('token', '此服务需要访问密钥，请填写服务方提供的密钥后继续。')
                    if config.get("token"):
                        headers["Authorization"] = 'Bearer ' + config["token"]
                elif auth_mode == 'cli' and preset(manifest) and key == 'github':
                    code, token, _ = await self.process(key, ['auth', 'token', '--hostname', 'github.com'])
                    token = token.strip()
                    if code or not token or any(c.isspace() for c in token):
                        raise AuthorizationRequired('GitHub 登录凭据不可用，请重新连接。')
                    self.private_values.add(token)
                    headers['Authorization'] = 'Bearer ' + token
                auth = await self.oauth(key, interactive) if auth_mode == "oauth" else None
                if auth:
                    stack.push_async_callback(auth.close)
                async def secure_endpoint(request):
                    if request.url.scheme != "https" and request.url.host not in {"localhost", "127.0.0.1", "::1"}:
                        raise ConnectorFailure("连接与授权端点必须使用 HTTPS，仅本机允许 HTTP。")
                client = await stack.enter_async_context(httpx.AsyncClient(headers=headers, auth=auth, timeout=45, follow_redirects=False, trust_env=False, event_hooks={"request": [secure_endpoint]}))
                if transport == "sse":
                    def factory(**kwargs):
                        return httpx.AsyncClient(**{**kwargs, "auth": auth, "headers": headers, "follow_redirects": False, "trust_env": False, "event_hooks": {"request": [secure_endpoint]}})
                    read, write = await stack.enter_async_context(sse_client(manifest["url"], httpx_client_factory=factory))
                else:
                    read, write, _ = await stack.enter_async_context(streamable_http_client(manifest["url"], http_client=client))
            session = await stack.enter_async_context(ValidatingClientSession(read, write))
            await session.initialize()
            yield session

    async def oauth(self, key, interactive):
        from .connector_oauth import provider
        return await provider(self, key, interactive)

    def discovery(self, key):
        manifest = self.entry(key)['manifest']
        if manifest['type'] != 'cli' or (not builtin(manifest) and 'operations' in manifest):
            return None
        from .cli_discovery import CliDiscovery
        return self.discoverers.setdefault(key, CliDiscovery(self, key))

    async def discover(self, key, interactive=False):
        manifest = self.entry(key)["manifest"]
        if manifest["type"] == "cli":
            identity = await self.cli_check(key)
            discovery = self.discovery(key)
            if discovery:
                catalog = await discovery.load(refresh=True)
                self.entry(key)['capabilities'] = {'discovery': catalog.get('profile', 'lark'), 'toolVersion': catalog['toolVersion'],
                    'catalogHash': catalog['catalogHash'], 'operationCount': len(catalog['operations'])}
                return [{'name': item['name'], 'description': item['description'],
                         'effect': 'read' if item['risk'] == 'read' else 'write' if item['risk'] in {'write', 'high-risk-write'} else 'unknown',
                         'risk': item['risk']} for item in catalog['operations'].values()], identity
            tools = [{**{k: v for k, v in op.items() if k not in {"args", "format"}}, 'risk': op['effect']} for op in manifest.get("operations", [])
                     if not op.get("identity") or op["identity"] == manifest.get("identity")]
            from .cli_discovery import digest
            for tool in tools:
                tool['sourceHash'] = digest({'manifest': manifest, 'tool': tool})
            return tools, identity
        from .cli_discovery import declared_risk
        cli_info = await self.cli_check(key) if manifest['auth']['mode'] == 'cli' else None
        async with self.session(key, interactive) as session:
            tools, cursor = [], None
            while True:
                page = await session.list_tools(cursor=cursor)
                for tool in page.tools:
                    item = tool.model_dump(mode="json", exclude_none=True)
                    item['risk'] = declared_risk({'annotations': item.get('annotations', {})})
                    item['effect'] = 'read' if item['risk'] == 'read' else 'write'
                    item['sourceHash'] = self.mcp_source_hash(item)
                    # A conservative destructive hint alone does not establish
                    # whether the operation writes. Keep its risk until reviewed.
                    item['riskReviewable'] = item.get('annotations', {}).get('readOnlyHint') is None
                    review = self.entry(key).get('mcpReviews', {}).get(tool.name)
                    if item['riskReviewable'] and review and review['sourceHash'] == item['sourceHash']:
                        item.update(risk=review['risk'], effect='read' if review['risk'] == 'read' else 'write', riskAssessment=review, riskReviewable=False)
                    tools.append(item)
                cursor = page.nextCursor
                if not cursor:
                    break
                if len(tools) > 2000:
                    raise ConnectorFailure("插件工具列表过大。")
            info = cli_info or {"mode": self.mcp_auth_mode(key)}
            if info["mode"] == "oauth":
                credentials = self.vault.get(key)
                if not credentials.get('tokens'):
                    info['mode'] = 'none'
                else:
                    info.update(renewable=bool(credentials.get("tokens", {}).get("refresh_token")),
                                expiresAt=credentials.get("expiresAt"))
            return tools, info

    @staticmethod
    def mcp_source_hash(tool):
        from .cli_discovery import digest
        return digest({k:v for k,v in tool.items() if k not in {'effect', 'risk', 'sourceHash', 'riskAssessment', 'riskReviewable'}})

    @staticmethod
    def frozen_mcp(tool, kind='mcp'):
        from .cli_discovery import digest
        if tool.get('risk') not in {'read', 'write'}:
            raise ConnectorFailure('请先检查并评估这项插件操作；未知或高风险操作不能用于无人值守任务：' + tool['name'])
        value = {**copy.deepcopy(tool), 'metadataKind': kind}
        value['contractHash'] = digest(value)
        return value

    async def preview(self, key, operation, arguments):
        response = await self.call(key, operation, arguments, role='input')
        remaining, truncated = 20000, False
        def sample(value, depth=0):
            nonlocal remaining, truncated
            if remaining <= 0 or depth > 10:
                truncated = True
                return '[预览省略]'
            if isinstance(value, str):
                limit = min(remaining, 1600)
                remaining -= min(len(value), limit)
                if len(value) > limit:
                    truncated = True
                    return value[:limit] + '…[预览截短]'
                return value
            if isinstance(value, dict):
                if len(value) > 40:
                    truncated = True
                result = {}
                for name, item in list(value.items())[:40]:
                    remaining -= len(str(name))
                    result[name] = sample(item, depth + 1)
                return result
            if isinstance(value, list):
                if len(value) > 3:
                    truncated = True
                return [sample(item, depth + 1) for item in value[:3]]
            return value
        result = {'response': sample(response)}
        # MCP text content may carry JSON rather than structuredContent. Decode
        # that protocol envelope for inspection without changing runtime calls.
        raw = response.get('result', {})
        if isinstance(raw, dict):
            decoded = []
            parts = raw.get('content', [])
            for index, part in enumerate(parts if isinstance(parts, list) else []):
                if isinstance(part, dict) and part.get('type') == 'text':
                    try:
                        value = json.loads(part.get('text', ''))
                    except (TypeError, ValueError):
                        continue
                    decoded.append({'path': f'/result/content/{index}/text', 'value': sample(value)})
            if decoded:
                result['decodedText'] = decoded
        result['truncated'] = truncated
        return result

    async def tool_operation(self, key, method, params):
        # MCP and manually declared CLI operations share a flat tool index.
        if method == 'connector_skill':
            raise ValueError('这个插件未提供可读取的 Skill。')
        async with self.locks.setdefault(key, asyncio.Lock()):
            tools, _ = await self.discover(key)
            tools = json.loads(self.redact(json.dumps(tools, ensure_ascii=False)))
            if method == 'connector_operations':
                self.entry(key)['tools'] = tools; self.persist()
                offset, limit = params.get('offset', 0), params.get('limit', 60)
                query = params.get('query', '')
                if not isinstance(offset, int) or offset < 0 or not isinstance(limit, int) or not 1 <= limit <= 100 or not isinstance(query, str) or len(query) > 200:
                    raise ValueError('请提供有效的操作索引分页或检索条件。')
                matches = sorted((t for t in tools if not query or query.casefold() in
                                  ' '.join(str(t.get(k, '')) for k in ('name', 'title', 'description')).casefold()), key=lambda t: t['name'])
                operations = [{k: t[k] for k in ('name', 'title', 'effect', 'risk', 'riskReviewable') if k in t}
                              | {'description': t.get('description', '')[:240]} for t in matches[offset:offset + limit]]
                return {'operations': operations, 'total': len(matches), 'offset': offset,
                        'nextOffset': offset + limit if offset + limit < len(matches) else None}
            tool = next((t for t in tools if t['name'] == params['operation']), None)
            if not tool:
                raise ValueError('插件中没有这项操作。')
            if method == 'connector_review_operation':
                risk, evidence = params['risk'], params['evidence']
                if not tool.get('riskReviewable', tool['risk'] == 'unknown'):
                    raise ValueError('只能评估未声明风险的操作，不能覆盖服务声明或已有评估。')
                if self.entry(key)['manifest']['type'] == 'mcp' and params.get('sourceHash') != tool['sourceHash']:
                    raise ValueError('请重新检查完整操作契约，并提交该契约的 sourceHash；过期评估不能应用于新契约。')
                if risk not in {'read', 'write', 'high-risk-write'} or not isinstance(evidence, str) or not 10 <= len(evidence) <= 2000:
                    raise ValueError('请根据完整 Schema 和说明提供读写影响及判断依据。')
                review = {'source': 'task-author', 'sourceHash': tool['sourceHash'], 'risk': risk, 'evidence': self.redact(evidence)}
                self.entry(key).setdefault('mcpReviews', {})[tool['name']] = review
                tool.update(risk=risk, effect='read' if risk == 'read' else 'write', riskAssessment=review)
                if 'riskReviewable' in tool:
                    tool['riskReviewable'] = False
            self.entry(key)['tools'] = tools; self.persist()
            return copy.deepcopy(tool)

    async def check(self, key, interactive=False):
        async with self.locks.setdefault(key, asyncio.Lock()):
            was_enabled = self.entry(key)["enabled"]
            try:
                tools, info = await self.discover(key, interactive)
                tools = json.loads(self.redact(json.dumps(tools, ensure_ascii=False)))
                message = "连接可用"
                if info.get("mode") == "oauth" and not info.get("renewable"):
                    message = "连接可用；服务未签发刷新令牌，授权过期后需要重新连接。"
                self.state(key, "connected", message, tools=tools, authorizationInfo=info, enabled=True, autoReconnect=True, credentialRequest=None)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                needs_auth = self.is_auth_error(error)
                request = self.credential_request(key, error)
                message = self.safe_error(error) if request or interactive or not needs_auth else '授权已失效，请重新连接。'
                self.state(key, "authorization_required" if needs_auth else "error", message, enabled=False if needs_auth else was_enabled, credentialRequest=request)
                if request:
                    raise CredentialRequired(request['kind'], message,
                        secret_required=request.get('secretRequired', False), secret_supported=request.get('secretSupported', False)) from error
                raise AuthorizationRequired(message) if needs_auth else ConnectorFailure(self.safe_error(error)) from error
            return self.public(key)

    @staticmethod
    def is_auth_error(error):
        if isinstance(error, AuthorizationRequired):
            return True
        if hasattr(error, "exceptions"):
            return any(Connectors.is_auth_error(e) for e in error.exceptions)
        response = getattr(error, "response", None)
        return response is not None and response.status_code == 401

    def safe_error(self, error):
        from .mcp_session import ResultContractError
        if isinstance(error, ResultContractError):
            return str(error)
        if hasattr(error, "exceptions"):
            return "; ".join(self.safe_error(e) for e in error.exceptions)[:1000]
        # SDK HTTP errors can contain authenticated URLs. Return status only.
        response = getattr(error, "response", None)
        if response is not None:
            return f"插件 HTTP 请求失败（{response.status_code}）。"
        if isinstance(error, (ConnectorFailure, AuthorizationRequired, ToolInstallError, ValueError, FileNotFoundError)):
            return self.redact(str(error))[:1000]
        return f"插件调用未完成（{type(error).__name__}），请检查服务与网络。"

    async def call(self, key, operation, arguments, *, role, expected_revision=None, delivery_key="", expected_contract=None):
        from jsonschema import validate
        # On reopen, let the one shared read-only recovery finish before a task
        # interprets the temporary disabled state as a need for user login.
        if key in self.recovering and key in self.jobs:
            await asyncio.shield(self.jobs[key])
        entry = self.entry(key)
        if expected_revision is not None and entry["revision"] != expected_revision:
            raise ConnectorFailure("插件定义已修改，请重新保存自动化任务后运行。")
        if not entry["enabled"]:
            if entry["status"] == "error":
                raise ConnectorFailure("插件暂不可用，请检查服务和网络后重试连接。")
            raise AuthorizationRequired("插件已断开或授权失效，请在插件页面重新连接。")
        async with self.locks.setdefault(key, asyncio.Lock()):
            entry = self.entry(key)
            if not entry["enabled"]:
                raise AuthorizationRequired("插件已断开或授权失效，请重新连接。")
            if expected_revision is not None and entry["revision"] != expected_revision:
                raise ConnectorFailure("插件定义已修改，请重新保存自动化任务后运行。")
            try:
                # Discover again so a revoked identity/tool cannot be used from stale UI state.
                discovery = self.discovery(key)
                if discovery:
                    from .cli_discovery import ADAPTER_VERSION, digest
                    if expected_revision is not None and expected_contract is None:
                        raise ConnectorFailure('此任务缺少 CLI 能力快照，请重新检查并保存自动化任务。')
                    if expected_contract is not None:
                        if (expected_contract.get('name') != operation or expected_contract.get('adapterVersion') != ADAPTER_VERSION
                            or expected_contract.get('contractHash') != digest({k: v for k, v in expected_contract.items() if k != 'contractHash'})
                            or discovery.fingerprint() != expected_contract.get('binaryHash')
                            or expected_contract.get('commandHash', discovery.command_hash) != discovery.command_hash):
                            raise ConnectorFailure('CLI 版本或固定操作契约已改变，请重新检查并保存任务。')
                        tool = expected_contract
                    else:
                        tool = await discovery.operation(operation)
                    await self.cli_check(key)
                    await discovery.verify_frozen(tool)
                else:
                    tools, _ = await self.discover(key)
                    tool = next((t for t in tools if t["name"] == operation), None)
                if not tool:
                    raise ConnectorFailure("插件中没有这项操作，请重新检查连接。")
                if not discovery and expected_contract is not None:
                    from .cli_discovery import digest
                    if (expected_contract.get('metadataKind') != entry['manifest']['type'] or expected_contract.get('name') != operation
                        or expected_contract.get('contractHash') != digest({k:v for k,v in expected_contract.items() if k != 'contractHash'})
                        or expected_contract.get('sourceHash') != tool.get('sourceHash')
                        or expected_contract.get('risk') != tool.get('risk')):
                        raise ConnectorFailure('插件操作说明、参数或读写评估已改变，请重新检查并保存任务。')
                    tool = expected_contract
                if not self.entry(key)["enabled"]:
                    raise AuthorizationRequired("插件已断开，本次操作未发起。")
                if role in {"input", "sample"} and tool["effect"] != "read":
                    raise ConnectorFailure("输入采集只能调用声明为只读的插件操作。")
                from jsonschema.exceptions import ValidationError
                try:
                    validate(arguments, tool.get("inputSchema", {"type": "object"}))
                except ValidationError as error:
                    raise ConnectorFailure("插件参数无效：" + error.message[:500]) from error
                manifest = entry["manifest"]
                if manifest["type"] == "cli":
                    if discovery:
                        from .cli_discovery import argv
                        args = argv(tool, arguments, manifest.get('identity', 'user'), delivery_key)
                        output_format = 'json'
                    else:
                        op = next(o for o in manifest["operations"] if o["name"] == operation)
                        args = self.manual_arguments(op, arguments, manifest, delivery_key)
                        output_format = op.get('format')
                    code, stdout, stderr = await self.process(key, args)
                    if code and preset(manifest) and key == 'tapd' and re.search(r'^API 错误 401:', stderr, re.M):
                        raise CredentialRequired('token', 'TAPD 个人访问令牌已失效，请更新。')
                    try:
                        data = json.loads(stdout if stdout.strip() else stderr)
                    except ValueError:
                        if output_format == "text" and not code:
                            data = {"text": stdout}
                        else:
                            raise ConnectorFailure("CLI 未返回有效 JSON，请检查操作的输出格式。")
                    if code or (isinstance(data, dict) and (data.get("error") or data.get("ok") is False or data.get("success") is False or data.get("errcode", 0) != 0 or data.get("code", 0) != 0)):
                        error_data = data.get("error", data) if isinstance(data, dict) else {}
                        error_type = error_data.get("type") if isinstance(error_data, dict) else None
                        if error_type in {"AuthError", "authentication"}:
                            raise AuthorizationRequired("CLI 授权已失效。")
                        raise ConnectorFailure("CLI 操作失败；未确认外部操作结果，请核对目标系统。")
                    if preset(manifest) and key == 'tapd':
                        self.tapd_result(data)
                else:
                    async with self.session(key) as session:
                        response = await session.call_tool(operation, arguments)
                        if response.isError:
                            raise ConnectorFailure("MCP 工具返回失败；请核对目标系统，不自动重放。")
                        data = response.model_dump(mode="json", exclude_none=True)
                result = json.loads(self.redact(json.dumps(data, ensure_ascii=False)))
                return connector_response(key, operation, result, mcp=manifest['type'] == 'mcp')
            except asyncio.CancelledError:
                raise
            except Exception as error:
                if self.is_auth_error(error):
                    self.state(key, "authorization_required", "授权失效，请重新连接后恢复任务。", enabled=False,
                               credentialRequest=self.credential_request(key, error))
                    raise AuthorizationRequired(f"插件 {entry['manifest']['name']} 需要重新授权。") from error
                raise ConnectorFailure(self.safe_error(error)) from error

    @staticmethod
    def manual_arguments(op, arguments, manifest, delivery_key):
        values = {**arguments, "arguments": json.dumps(arguments, ensure_ascii=False), "identity": manifest.get("identity", "user"),
                  "delivery_key": hashlib.sha256(delivery_key.encode()).hexdigest()[:48] if delivery_key else ""}
        def expand(arg):
            def replacement(match):
                if match[1] not in values:
                    raise ConnectorFailure(f"插件参数缺少 {match[1]}。")
                v = values[match[1]]
                return v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
            return re.sub(r"\$\{([\w.-]+)\}", replacement, arg)
        return [expand(arg) for arg in op['args']]

    def require_connected(self, ids):
        unavailable = []
        for key in ids:
            entry = self.entry(key)
            if not entry['enabled'] or entry['status'] != 'connected':
                unavailable.append(entry['manifest']['name'])
        if unavailable:
            raise AuthorizationRequired('请先在左侧“插件”中连接以下服务：' + '、'.join(unavailable)
                                        + '。连接后平台会自动继续搭建任务草稿。')

    async def prepare_contracts(self, grants):
        self.require_connected([grant['id'] for grant in grants])
        for grant in grants:
            discovery = self.discovery(grant['id'])
            if discovery:
                async with self.locks.setdefault(grant['id'], asyncio.Lock()):
                    for name in grant['operations']:
                        await discovery.operation(name)
            else:
                async with self.locks.setdefault(grant['id'], asyncio.Lock()):
                    tools, _ = await self.discover(grant['id'])
                    tools = json.loads(self.redact(json.dumps(tools, ensure_ascii=False)))
                    self.entry(grant['id'])['tools'] = tools
                    for name in grant['operations']:
                        tool = next((t for t in tools if t['name'] == name), None)
                        if not tool:
                            raise ConnectorFailure('插件中没有这项操作：' + name)
                        self.frozen_mcp(tool, self.entry(grant['id'])['manifest']['type'])
                    self.persist()

    def snapshot_contracts(self, grants):
        result = {}
        for grant in grants:
            discovery = self.discovery(grant['id'])
            if discovery:
                result[grant['id']] = {name: discovery.frozen(name) for name in grant['operations']}
            else:
                tools = {t['name']:t for t in self.entry(grant['id'])['tools']}
                result[grant['id']] = {name: self.frozen_mcp(tools[name], self.entry(grant['id'])['manifest']['type']) for name in grant['operations']}
        return result

    async def connect(self, key):
        if key in self.recovering and key in self.jobs:
            await asyncio.shield(self.jobs[key])
            if self.entry(key)['enabled'] and self.entry(key)['status'] == 'connected':
                return self.public(key)
        if key in self.jobs and not self.jobs[key].done():
            return self.public(key)
        self.auth_views[key] = None
        was_enabled = self.entry(key)["enabled"]
        self.retries.pop(key, None)
        self.state(key, "connecting", "正在检查和连接…", autoReconnect=True, credentialRequest=None)
        async def job():
            try:
                manifest = self.entry(key)["manifest"]
                provider = builtin(manifest)
                if provider:
                    self.state(key, "connecting", f"正在准备{manifest['name']}连接…")
                    await self.tools.ensure(provider, progress=lambda message: self.state(key, 'connecting', message))
                    self.state(key, "connecting", "正在检查授权…")
                    if provider == "feishu":
                        await self.prepare_feishu(key)
                if manifest["type"] == "cli" or manifest['auth']['mode'] == 'cli':
                    try:
                        await self.cli_check(key)
                    except AuthorizationRequired:
                        if preset(manifest) and key == 'tapd':
                            raise
                        self.state(key, "connecting", "请完成授权")
                        await self.cli_login(key)
                await self.check(key, interactive=True)
            except asyncio.CancelledError:
                self.state(key, "disconnected", "已取消连接", enabled=False)
            except Exception as error:
                request = self.credential_request(key, error)
                self.state(key, "authorization_required" if self.is_auth_error(error) else "error", self.safe_error(error), enabled=False if self.is_auth_error(error) else was_enabled, credentialRequest=request)
            finally:
                self.auth_views.pop(key, None)
                self.persist()
        self.jobs[key] = asyncio.create_task(job())
        return self.public(key)

    async def prepare_feishu(self, key):
        # Official CLI onboarding is itself a browser flow. Reuse an existing
        # app; never overwrite shared CLI configuration or expose its secrets.
        code, output, diagnostic = await self.process(key, ["config", "show"])
        try:
            result = json.loads(output if output.strip() else diagnostic)
            if not isinstance(result, dict):
                raise ValueError()
        except ValueError:
            raise ConnectorFailure("无法检查飞书配置，请稍后重新连接。")
        if not code and result.get("appId"):
            return
        error = result.get("error", {})
        # Missing application configuration is a typed vendor error on stderr.
        # A malformed file, permissions error, or broken selected profile must
        # not be treated as consent to overwrite an existing configuration.
        if code and not (isinstance(error, dict) and error.get("type") == "config"
                         and error.get("subtype") == "not_configured" and not error.get("field")):
            raise ConnectorFailure("无法检查飞书配置，请稍后重新连接。")
        self.state(key, "connecting", "请在飞书页面完成首次连接设置")
        await self.cli_login(key, ["config", "init", "--new", "--brand", "feishu", "--lang", "zh"])

    async def cli_login(self, key, login_args=None):
        manifest = self.entry(key)["manifest"]
        self.auth_views.pop(key, None)
        qrcode = self.root / "connector-work" / key / "authorization.png"
        qrcode.parent.mkdir(parents=True, exist_ok=True)
        qrcode.unlink(missing_ok=True)
        auth = await self.cli_auth(key)
        if not login_args and not auth.get('login'):
            raise ConnectorFailure('CLI 未提供可用的登录入口。')
        args = [arg.replace("${qrcode}", str(qrcode)) for arg in (login_args or auth['login'])]
        command = self.executable(manifest["command"], key)
        process = await asyncio.create_subprocess_exec(*command, *args, env=self.environment(key), cwd=qrcode.parent,
            stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        buffer = ""
        async def collect():
            nonlocal buffer
            while chunk := await process.stdout.read(1024):
                buffer = (buffer + chunk.decode(errors="replace"))[-12000:]
                urls = re.findall(r"https://[^\s<>\x1b\"']+", buffer)
                if urls:
                    view = {"url": urls[-1]}
                    if preset(manifest) and key == 'github':
                        match = re.search(r'one-time code: ([A-Z0-9]{4}-[A-Z0-9]{4})', buffer)
                        if match:
                            view['code'] = match[1]
                    previous = self.auth_views.get(key)
                    if previous != view:
                        self.auth_views[key] = view
                        self.notify({"event": "connector_state"})
                        if preset(manifest) and key == 'github' and (previous or {}).get('url') != view['url']:
                            self.notify({'event': 'connector_authorization', 'connectorId': key, 'url': view['url']})
        reader = asyncio.create_task(collect())
        try:
            async with asyncio.timeout(310):
                while process.returncode is None:
                    if qrcode.exists() and qrcode.stat().st_size < 1_000_000 and not (self.auth_views.get(key) or {}).get("qrcode"):
                        self.auth_views[key] = {"qrcode": "data:image/png;base64," + base64.b64encode(qrcode.read_bytes()).decode()}
                        self.notify({"event": "connector_state"})
                    try:
                        await asyncio.wait_for(process.wait(), 1)
                    except asyncio.TimeoutError:
                        pass
                await reader
                if process.returncode:
                    raise AuthorizationRequired(f"{manifest['name']}授权未完成，请重新连接并完成页面上的授权。")
        except asyncio.TimeoutError as error:
            raise AuthorizationRequired("授权等待超时，请重新连接并完成授权。") from error
        finally:
            await terminate(process)
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
            qrcode.unlink(missing_ok=True)

    async def disconnect(self, key):
        self.entry(key)["autoReconnect"] = False
        self.retries.pop(key, None)
        job = self.jobs.get(key)
        if job and not job.done():
            job.cancel()
            await asyncio.gather(job, return_exceptions=True)
        # Disable locally; official CLI accounts may be shared by other applications.
        self.auth_views.pop(key, None)
        self.state(key, "disconnected", "已断开，自动化任务无法继续使用此连接", enabled=False, credentialRequest=None)
        return self.public(key)

    def recover_connections(self, initial=False):
        for key, entry in list(self.items.items()):
            if not entry.get("autoReconnect") or (key in self.jobs and not self.jobs[key].done()):
                continue
            retry = self.retries.get(key, {})
            if not initial and (entry["status"] != "error" or time.time() < retry.get("at", 0)):
                continue
            self.recovering.add(key)
            self.state(key, "connecting", "正在恢复连接…", enabled=False)
            self.jobs[key] = asyncio.create_task(self.restore(key))

    async def restore(self, key):
        try:
            # Never run login, install tools, or replay business operations here.
            # CLI refresh and MCP OAuth both reuse the durable local credentials.
            await self.check(key, interactive=False)
            self.retries.pop(key, None)
        except asyncio.CancelledError:
            raise
        except AuthorizationRequired:
            # A confirmed invalid credential is retried only on the next launch
            # or an explicit Connect, not by an endless background login loop.
            self.retries.pop(key, None)
        except Exception:
            attempts = self.retries.get(key, {}).get("attempts", 0) + 1
            self.retries[key] = {"attempts": attempts, "at": time.time() + min(30 * 2 ** min(attempts - 1, 5), 600)}
            self.entry(key)["message"] += " 将自动重试。"
        finally:
            self.recovering.discard(key)
            self.entry(key)["maintainedAt"] = time.time()
            self.persist()

    def snapshot(self, ids):
        result = {}
        for key in ids:
            entry = self.entry(key)
            result[key] = entry["revision"]
        return result

    async def close(self):
        if self.maintenance_task:
            self.maintenance_task.cancel()
        for job in self.jobs.values():
            job.cancel()
        await asyncio.gather(*self.jobs.values(), *([self.maintenance_task] if self.maintenance_task else []), return_exceptions=True)

    def startup(self):
        if not self.maintenance_task:
            # Register shared recovery before accepting a business call. Doing
            # this only in the scheduled maintenance task can disable a
            # connection halfway through that first call's discovery.
            self.recover_connections(initial=True)
            self.maintenance_task = asyncio.create_task(self.maintain())

    async def maintain(self):
        while True:
            self.recover_connections()
            for key, entry in list(self.items.items()):
                if not entry["enabled"] or (key in self.jobs and not self.jobs[key].done()):
                    continue
                manifest = entry["manifest"]
                next_check = entry.get("maintainedAt", 0) + 3600
                if manifest["type"] == "mcp" and self.mcp_auth_mode(key) == 'oauth':
                    try:
                        expires = self.vault.get(key).get("expiresAt")
                        if expires is not None:
                            next_check = min(next_check, expires)
                    except ConnectorFailure:
                        # The normal guarded maintenance path reports a locked keychain.
                        next_check = 0
                if time.time() < next_check:
                    continue
                async with self.locks.setdefault(key, asyncio.Lock()):
                    try:
                        manifest = entry["manifest"]
                        if manifest.get("provider") == "feishu" and manifest.get("identity", "user") == "user":
                            # A real read request lets the official CLI refresh user credentials even
                            # when the business schedule has no events for several days.
                            code, output, _ = await self.process(key, ["api", "GET", "/open-apis/authen/v1/user_info", "--as", "user", "--json"])
                            if code:
                                await self.cli_check(key)
                                raise ConnectorFailure("飞书授权维护请求未完成，请检查网络和账户权限。")
                            data = json.loads(output)
                            if data.get("ok") is False or data.get("code", 0):
                                raise ConnectorFailure("飞书授权维护失败，请检查账户权限。")
                            info = await self.cli_check(key)
                            entry["authorizationInfo"] = info
                        elif manifest["type"] == "mcp" and self.mcp_auth_mode(key) == 'oauth':
                            # initialize/list_tools are read-only; the SDK refreshes expiring tokens.
                            await self.discover(key)
                        elif manifest['type'] == 'cli' or manifest['auth']['mode'] == 'cli':
                            entry['authorizationInfo'] = await self.cli_check(key)
                        entry["maintainedAt"] = time.time()
                        entry["maintenanceMessage"] = "长期授权检查完成"
                        self.persist()
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:
                        entry["maintainedAt"] = time.time()
                        entry["maintenanceMessage"] = self.safe_error(error)
                        if self.is_auth_error(error):
                            self.state(key, "authorization_required", "长期授权失效，请重新连接。", enabled=False)
                        else:
                            self.state(key, "error", self.safe_error(error) + " 将自动重试。", enabled=False)
            await asyncio.sleep(60)

    async def handle(self, method, params):
        self.startup()
        key = params.get("connectorId")
        if method == "connector_list":
            return self.list()
        if method == "connector_presets":
            return presets()
        if method == 'connector_preview':
            return await self.preview(key, params['operation'], params.get('arguments', {}))
        if method == 'connector_description_contexts':
            return self.description_contexts()
        if method == 'connector_set_description':
            return self.set_description(params)
        if method in {'connector_operations', 'connector_describe', 'connector_skill', 'connector_review_operation'}:
            discovery = self.discovery(key)
            if not discovery:
                result = await self.tool_operation(key, method, params)
                return {**result, 'responseContract': RESPONSE_CONTRACT} if method == 'connector_describe' else result
            async with self.locks.setdefault(key, asyncio.Lock()):
                if method == 'connector_operations':
                    result = await discovery.catalog(params.get('domain'))
                elif method == 'connector_describe':
                    result = await discovery.operation(params['operation'])
                elif method == 'connector_skill':
                    result = await discovery.skill(params['path'])
                else:
                    result = await discovery.review(params['operation'], params['risk'], params['evidence'])
                catalog = discovery.index
                self.entry(key)['capabilities'] = {'discovery': catalog.get('profile', 'lark'), 'toolVersion': catalog['toolVersion'],
                    'catalogHash': catalog['catalogHash'], 'operationCount': len(catalog['operations'])}
                self.entry(key)['tools'] = [{'name': item['name'], 'description': item['description'], 'risk': item['risk'],
                    'effect': 'read' if item['risk'] == 'read' else 'write' if item['risk'] in {'write', 'high-risk-write'} else 'unknown'}
                    for item in catalog['operations'].values()]
                self.persist()
                result = json.loads(self.redact(json.dumps(result, ensure_ascii=False)))
                return {**result, 'responseContract': RESPONSE_CONTRACT} if method == 'connector_describe' else result
        if method == "connector_save":
            return self.save(params["manifest"], params.get("secrets"))
        if method == "connector_connect":
            return await self.connect(key)
        if method == "connector_check":
            return await self.check(key)
        if method == 'connector_provide_credentials':
            return await self.provide_credentials(key, params['kind'], params['values'])
        if method == "connector_disconnect":
            return await self.disconnect(key)
        raise ValueError("不支持的插件操作。")
